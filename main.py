import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from parsers import ExtractionError, extract_docx, extract_pdf, extract_text
from quiz_engine import (
    QuizGenerationError,
    calculate_scores,
    generate_quiz,
    grade_choice,
    grade_short_answer,
    update_failure_counts,
)
from session_store import create_session, get_session, purge_expired_sessions, set_session_cookie

load_dotenv()

logger = logging.getLogger("study_aid")

CLEANUP_INTERVAL_SECONDS = 15 * 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _cleanup_loop():
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        purge_expired_sessions()


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


FILE_PARSERS = {".pdf": extract_pdf, ".docx": extract_docx}


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

    session = create_session()
    session["source_text"] = source_text

    response = RedirectResponse(url=f"/generating/{session['session_id']}", status_code=303)
    set_session_cookie(response, session["session_id"])
    return response


@app.get("/generating/{session_id}")
async def generating(request: Request, session_id: str):
    session = get_session(session_id)
    if session is None:
        return RedirectResponse(url="/", status_code=303)
    if session.get("quiz"):
        return RedirectResponse(url=f"/quiz/{session_id}", status_code=303)
    return templates.TemplateResponse(request, "generating.html", {"session_id": session_id})


@app.post("/generate/{session_id}")
async def generate(request: Request, session_id: str):
    session = get_session(session_id)
    if session is None:
        return RedirectResponse(url="/", status_code=303)

    if session.get("quiz"):
        return Response(headers={"HX-Redirect": f"/quiz/{session_id}"})

    if session.get("_generating"):
        # Another poll already kicked off generation for this session; the
        # Claude call runs off the event loop, so overlapping HTMX polls can
        # arrive before it finishes. Tell this one there's nothing to do yet
        # rather than starting a second (paid) generation call. HX-Reswap:
        # none stops htmx from swapping this empty body into the spinner
        # (it would otherwise wipe out the polling element and stall it).
        return Response(status_code=202, headers={"HX-Reswap": "none"})

    session["_generating"] = True
    try:
        session["quiz"] = await run_in_threadpool(generate_quiz, session["source_text"])
    except QuizGenerationError:
        logger.exception("Quiz generation failed for session %s", session_id)
        return templates.TemplateResponse(
            request,
            "partials/generation_error.html",
            {"message": "We couldn't generate your quiz. Please try again."},
        )
    except Exception:
        logger.exception("Unexpected error while generating quiz for session %s", session_id)
        return templates.TemplateResponse(
            request,
            "partials/generation_error.html",
            {"message": "Something went wrong while generating your quiz. Please try again."},
        )
    finally:
        session["_generating"] = False

    return Response(headers={"HX-Redirect": f"/quiz/{session_id}"})


def _question_context(
    session_id: str,
    quiz_questions: list,
    question: dict,
    mode: str,
    feedback: Optional[dict] = None,
) -> dict:
    total = len(quiz_questions)
    number = next(i for i, q in enumerate(quiz_questions) if q["id"] == question["id"]) + 1
    return {
        "session_id": session_id,
        "question": question,
        "question_number": number,
        "total_questions": total,
        "mode": mode,
        "progress_pct": round((number - 1) / total * 100),
        "feedback": feedback,
    }


@app.get("/quiz/{session_id}")
async def quiz(request: Request, session_id: str, mode: str = "practice"):
    session = get_session(session_id)
    if session is None:
        return RedirectResponse(url="/", status_code=303)

    quiz_questions = session.get("quiz")
    if not quiz_questions:
        return RedirectResponse(url=f"/generating/{session_id}", status_code=303)

    attempt = {"mode": mode, "answers": {}, "overall_score": None, "concept_scores": None}
    session.setdefault("attempts", []).append(attempt)

    context = _question_context(session_id, quiz_questions, quiz_questions[0], mode)
    return templates.TemplateResponse(request, "quiz.html", context)


@app.post("/answer/{session_id}")
async def answer(
    request: Request,
    session_id: str,
    question_id: str = Form(...),
    user_answer: str = Form(""),
):
    session = get_session(session_id)
    if session is None:
        return RedirectResponse(url="/", status_code=303)

    quiz_questions = session.get("quiz") or []
    attempts = session.get("attempts") or []
    if not quiz_questions or not attempts:
        return RedirectResponse(url=f"/quiz/{session_id}", status_code=303)

    attempt = attempts[-1]
    question = next((q for q in quiz_questions if q["id"] == question_id), None)
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

    if len(attempt["answers"]) >= len(quiz_questions):
        scores = calculate_scores(attempt, quiz_questions)
        attempt["overall_score"] = scores["overall_score"]
        attempt["concept_scores"] = scores["concept_scores"]
        update_failure_counts(session.setdefault("failure_counts", {}), attempt)
        return Response(headers={"HX-Redirect": f"/results/{session_id}"})

    next_question = next(q for q in quiz_questions if q["id"] not in attempt["answers"])
    feedback = {
        "correct": correct,
        "explanation": explanation,
        "correct_answer": question["correct_answer"],
    }
    context = _question_context(session_id, quiz_questions, next_question, attempt["mode"], feedback)
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
    session = get_session(session_id)
    if session is None:
        return RedirectResponse(url="/", status_code=303)

    attempts = session.get("attempts") or []
    if not attempts or attempts[-1].get("overall_score") is None:
        return RedirectResponse(url=f"/quiz/{session_id}", status_code=303)

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
