import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from db import create_quiz, get_quiz, init_db, save_quiz
from parsers import ExtractionError, extract_docx, extract_pdf, extract_text
from quiz_engine import (
    QuizGenerationError,
    calculate_scores,
    generate_quiz,
    grade_choice,
    grade_short_answer,
    select_evaluation_questions,
    update_failure_counts,
)

load_dotenv()

logger = logging.getLogger("study_aid")

# In-progress generation lock, keyed by quiz id. Deliberately NOT persisted —
# it's a per-process concurrency guard (see /generate), not durable quiz
# state. If the server restarts mid-generation the attempt is simply retried.
_generating: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

FLASH_MESSAGES = {
    "quiz_not_found": "Quiz not found. Please start over.",
}


def _quiz_not_found_redirect() -> RedirectResponse:
    return RedirectResponse(url="/?flash=quiz_not_found", status_code=303)


@app.get("/")
async def index(request: Request):
    flash = FLASH_MESSAGES.get(request.query_params.get("flash", ""))
    return templates.TemplateResponse(request, "index.html", {"flash": flash})


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


FILE_PARSERS = {".pdf": extract_pdf, ".docx": extract_docx}
MAX_SOURCE_TEXT_CHARS = 80_000


async def _extract_source_text(file: Optional[UploadFile], text: Optional[str]) -> str:
    if file is not None and file.filename:
        extension = Path(file.filename).suffix.lower()
        parser = FILE_PARSERS.get(extension)
        if parser is None:
            raise ExtractionError("Unsupported file type. Please upload a PDF or DOCX file.")
        file_bytes = await file.read()
        if not file_bytes:
            raise ExtractionError("The uploaded file is empty. Please choose a file or paste text.")
        return extract_text(parser(file_bytes))

    if text and text.strip():
        return extract_text(text)

    raise ExtractionError("Please upload a file or paste some text.")


@app.post("/ingest")
async def ingest(
    request: Request,
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
):
    try:
        source_text = await _extract_source_text(file, text)
    except ExtractionError as exc:
        return templates.TemplateResponse(
            request, "error.html", {"message": str(exc)}, status_code=400
        )
    except Exception:
        logger.exception("Unexpected error while parsing uploaded file")
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "message": (
                    "We couldn't read that file. It may be corrupted, password-protected, "
                    "or in an unsupported format. Please try a different file or paste the "
                    "text instead."
                )
            },
            status_code=400,
        )

    truncated = len(source_text) > MAX_SOURCE_TEXT_CHARS
    if truncated:
        source_text = source_text[:MAX_SOURCE_TEXT_CHARS]

    session = create_quiz(source_text, truncated)

    return RedirectResponse(url=f"/generating/{session['session_id']}", status_code=303)


@app.get("/generating/{session_id}")
async def generating(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()
    if session.get("quiz"):
        return RedirectResponse(url=f"/quiz/{session_id}", status_code=303)
    return templates.TemplateResponse(
        request,
        "generating.html",
        {"session_id": session_id, "truncated": session.get("truncated", False)},
    )


@app.post("/generate/{session_id}")
async def generate(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()

    if session.get("quiz"):
        return Response(headers={"HX-Redirect": f"/quiz/{session_id}"})

    if session_id in _generating:
        # Another poll already kicked off generation for this quiz; the
        # Claude call runs off the event loop, so overlapping HTMX polls can
        # arrive before it finishes. Tell this one there's nothing to do yet
        # rather than starting a second (paid) generation call. HX-Reswap:
        # none stops htmx from swapping this empty body into the spinner
        # (it would otherwise wipe out the polling element and stall it).
        return Response(status_code=202, headers={"HX-Reswap": "none"})

    _generating.add(session_id)
    try:
        session["quiz"] = await run_in_threadpool(generate_quiz, session["source_text"])
        save_quiz(session)
    except QuizGenerationError:
        logger.exception("Quiz generation failed for session %s", session_id)
        return templates.TemplateResponse(
            request,
            "partials/generation_error.html",
            {
                "message": "We couldn't generate your quiz. Please try again.",
                "retry_url": f"/generating/{session_id}",
            },
        )
    except Exception:
        logger.exception("Unexpected error while generating quiz for session %s", session_id)
        return templates.TemplateResponse(
            request,
            "partials/generation_error.html",
            {
                "message": "Something went wrong while generating your quiz. Please try again.",
                "retry_url": f"/generating/{session_id}",
            },
        )
    finally:
        _generating.discard(session_id)

    return Response(headers={"HX-Redirect": f"/quiz/{session_id}"})


def _question_context(
    session_id: str,
    active_questions: list,
    question: dict,
    mode: str,
    attempt_number: int,
    feedback: Optional[dict] = None,
) -> dict:
    total = len(active_questions)
    number = next(i for i, q in enumerate(active_questions) if q["id"] == question["id"]) + 1
    return {
        "session_id": session_id,
        "question": question,
        "question_number": number,
        "total_questions": total,
        "mode": mode,
        "attempt_number": attempt_number,
        "progress_pct": round((number - 1) / total * 100),
        "feedback": feedback,
    }


def _active_questions(quiz_questions: list, attempt: dict) -> list:
    """The ordered subset of quiz_questions this attempt covers — the full
    quiz for practice mode, or the pre-selected list for evaluation mode."""
    question_ids = attempt.get("question_ids") or [q["id"] for q in quiz_questions]
    by_id = {q["id"]: q for q in quiz_questions}
    return [by_id[qid] for qid in question_ids if qid in by_id]


@app.get("/quiz/{session_id}")
async def quiz(request: Request, session_id: str, mode: str = "practice"):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()

    quiz_questions = session.get("quiz")
    if not quiz_questions:
        return RedirectResponse(url=f"/generating/{session_id}", status_code=303)

    attempts = session.setdefault("attempts", [])
    latest = attempts[-1] if attempts else None

    if latest and latest.get("mode") == mode and not latest.get("answers"):
        # Already initialised for this mode (typically by /retry) and not yet
        # started — reuse it rather than discarding its question selection.
        attempt = latest
    elif mode == "evaluation":
        # Evaluation attempts must be set up via POST /retry, which knows the
        # failure_counts needed to select questions.
        return RedirectResponse(url=f"/results/{session_id}", status_code=303)
    else:
        attempt = {
            "mode": "practice",
            "answers": {},
            "overall_score": None,
            "concept_scores": None,
            "question_ids": [q["id"] for q in quiz_questions],
        }
        attempts.append(attempt)
        save_quiz(session)

    active_questions = _active_questions(quiz_questions, attempt)
    context = _question_context(
        session_id, active_questions, active_questions[0], attempt["mode"], len(attempts)
    )
    return templates.TemplateResponse(request, "quiz.html", context)


@app.post("/retry/{session_id}")
async def retry(session_id: str, mode: str = "practice"):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()

    quiz_questions = session.get("quiz") or []
    if not quiz_questions:
        return RedirectResponse(url=f"/generating/{session_id}", status_code=303)

    if mode == "evaluation":
        failure_counts = session.get("failure_counts") or {}
        selected = select_evaluation_questions(quiz_questions, failure_counts)
        question_ids = [q["id"] for q in selected]
    else:
        mode = "practice"
        question_ids = [q["id"] for q in quiz_questions]

    attempt = {
        "mode": mode,
        "answers": {},
        "overall_score": None,
        "concept_scores": None,
        "question_ids": question_ids,
    }
    session.setdefault("attempts", []).append(attempt)
    save_quiz(session)

    return RedirectResponse(url=f"/quiz/{session_id}?mode={mode}", status_code=303)


@app.post("/answer/{session_id}")
async def answer(
    request: Request,
    session_id: str,
    question_id: str = Form(...),
    user_answer: str = Form(""),
):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()

    quiz_questions = session.get("quiz") or []
    attempts = session.get("attempts") or []
    if not quiz_questions or not attempts:
        return RedirectResponse(url=f"/quiz/{session_id}", status_code=303)

    attempt = attempts[-1]
    active_questions = _active_questions(quiz_questions, attempt)
    question = next((q for q in active_questions if q["id"] == question_id), None)
    if question is None:
        return Response(status_code=400, content="Unknown question for this quiz")

    if question_id in attempt["answers"]:
        return Response(status_code=409, content="This question was already answered")

    if question["type"] == "short_answer":
        try:
            grading = await run_in_threadpool(
                grade_short_answer, question["question"], question["correct_answer"], user_answer
            )
            correct = grading["passed"]
            explanation = grading["feedback"]
        except Exception:
            logger.exception("Short-answer grading failed for question %s", question_id)
            correct = False
            explanation = "We couldn't grade this automatically; marked as incorrect."
    else:
        correct = grade_choice(question, user_answer)
        explanation = question.get("explanation", "")

    attempt["answers"][question_id] = {
        "user_answer": user_answer,
        "correct": correct,
        "score": 1.0 if correct else 0.0,
    }

    if len(attempt["answers"]) >= len(active_questions):
        scores = calculate_scores(attempt, quiz_questions)
        attempt["overall_score"] = scores["overall_score"]
        attempt["concept_scores"] = scores["concept_scores"]
        update_failure_counts(session.setdefault("failure_counts", {}), attempt)
        save_quiz(session)
        return Response(headers={"HX-Redirect": f"/results/{session_id}"})

    save_quiz(session)
    next_question = next(q for q in active_questions if q["id"] not in attempt["answers"])
    feedback = {
        "correct": correct,
        "explanation": explanation,
        "correct_answer": question["correct_answer"],
    }
    context = _question_context(
        session_id, active_questions, next_question, attempt["mode"], len(attempts), feedback
    )
    return templates.TemplateResponse(request, "partials/question.html", context)


def _score_bar_color(fraction: float) -> str:
    if fraction >= 0.8:
        return "bg-green-500"
    if fraction >= 0.5:
        return "bg-yellow-500"
    return "bg-red-500"


def _score_text_color(fraction: float) -> str:
    if fraction >= 0.8:
        return "text-green-600"
    if fraction >= 0.5:
        return "text-yellow-600"
    return "text-red-600"


@app.get("/results/{session_id}")
async def results(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()

    attempts = session.get("attempts") or []
    if not attempts or attempts[-1].get("overall_score") is None:
        mode = attempts[-1].get("mode", "practice") if attempts else "practice"
        return RedirectResponse(url=f"/quiz/{session_id}?mode={mode}", status_code=303)

    attempt = attempts[-1]
    overall_score = attempt["overall_score"]
    concept_scores = attempt.get("concept_scores") or {}

    concept_bars = [
        {
            "concept": concept,
            "percent": round(score * 100),
            "color_class": _score_bar_color(score),
        }
        for concept, score in concept_scores.items()
    ]

    can_evaluate = any(not a["correct"] for a in attempt["answers"].values())

    context = {
        "session_id": session_id,
        "mode": attempt.get("mode", "practice"),
        "overall_percent": round(overall_score * 100),
        "overall_color_class": _score_text_color(overall_score),
        "concept_bars": concept_bars,
        "can_evaluate": can_evaluate,
    }
    return templates.TemplateResponse(request, "results.html", context)


@app.get("/debug/session/{session_id}")
async def debug_session(session_id: str):
    if os.environ.get("DEBUG", "").lower() != "true":
        raise HTTPException(status_code=404)

    session = get_quiz(session_id)
    if session is None:
        raise HTTPException(status_code=404)

    return JSONResponse(jsonable_encoder(session))
