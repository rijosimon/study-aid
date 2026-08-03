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

from db import (
    _hash_content,
    create_quiz,
    delete_quiz,
    find_quiz_by_content_hash,
    get_quiz,
    get_quiz_summary,
    init_db,
    list_quizzes,
    rename_quiz,
    save_quiz,
)
from parsers import ExtractionError, extract_docx, extract_pdf, extract_text
from quiz_engine import (
    QuizGenerationError,
    calculate_scores,
    generate_quiz,
    grade_choice,
    grade_short_answer,
    renumber_questions,
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
    "duplicate_content": "You've already created a quiz from this material.",
}


def _quiz_not_found_redirect() -> RedirectResponse:
    return RedirectResponse(url="/?flash=quiz_not_found", status_code=303)


def _redirect(url: str, flash: str = "", status_code: int = 303) -> RedirectResponse:
    """RedirectResponse to `url`, optionally tacking on a `flash` query param
    (appended with `&` if `url` already has a query string)."""
    if flash:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}flash={flash}"
    return RedirectResponse(url=url, status_code=status_code)


def _quiz_destination(summary: dict) -> str:
    """Where a user should land for a given quiz, based on its generation
    and attempt state. Shared by /dashboard (existing quizzes) and /ingest
    (duplicate-content redirects)."""
    session_id = summary["session_id"]
    if not summary["has_quiz"]:
        return f"/generating/{session_id}"
    if summary["latest_mode"] is None:
        return f"/quiz/{session_id}"
    if summary["latest_in_progress"]:
        return f"/quiz/{session_id}?mode={summary['latest_mode']}"
    return f"/results/{session_id}"


@app.get("/")
async def index(request: Request):
    flash = FLASH_MESSAGES.get(request.query_params.get("flash", ""))
    return templates.TemplateResponse(request, "index.html", {"flash": flash})


def _quiz_status_label(summary: dict) -> str:
    if not summary["has_quiz"]:
        return "Generating…"
    if summary["latest_mode"] is None:
        return "Not yet attempted"
    if summary["latest_in_progress"]:
        return f"{summary['latest_mode'].capitalize()} in progress"
    return f"{round(summary['latest_score'] * 100)}% ({summary['latest_mode'].capitalize()})"


def _quiz_card(summary: dict) -> dict:
    """Everything templates/partials/quiz_card.html needs to render one
    dashboard card — shared by /dashboard (the full list) and the rename
    routes below (which only need to re-render a single card)."""
    return {
        **summary,
        "href": _quiz_destination(summary),
        "status_label": _quiz_status_label(summary),
        "display_name": summary.get("name") or summary["preview"],
    }


@app.get("/dashboard")
async def dashboard(request: Request):
    cards = [_quiz_card(summary) for summary in list_quizzes()]
    return templates.TemplateResponse(request, "dashboard.html", {"quizzes": cards})


@app.delete("/quiz/{session_id}")
async def delete_quiz_route(session_id: str):
    delete_quiz(session_id)
    return Response(status_code=200)


@app.get("/quiz/{session_id}/card")
async def quiz_card_partial(request: Request, session_id: str):
    """Re-renders a single dashboard card in its normal (non-editing) state
    — used by the rename form's Cancel control to swap back without
    persisting anything."""
    summary = get_quiz_summary(session_id)
    if summary is None:
        return _quiz_not_found_redirect()
    return templates.TemplateResponse(
        request, "partials/quiz_card.html", {"quiz": _quiz_card(summary), "editing": False}
    )


@app.get("/quiz/{session_id}/name/edit")
async def quiz_name_edit(request: Request, session_id: str):
    summary = get_quiz_summary(session_id)
    if summary is None:
        return _quiz_not_found_redirect()
    return templates.TemplateResponse(
        request, "partials/quiz_card.html", {"quiz": _quiz_card(summary), "editing": True}
    )


@app.post("/quiz/{session_id}/name")
async def quiz_name_save(request: Request, session_id: str, name: str = Form("")):
    summary = get_quiz_summary(session_id)
    if summary is None:
        return _quiz_not_found_redirect()
    rename_quiz(session_id, name)
    updated_summary = get_quiz_summary(session_id)
    return templates.TemplateResponse(
        request, "partials/quiz_card.html", {"quiz": _quiz_card(updated_summary), "editing": False}
    )


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

    content_hash = _hash_content(source_text)
    existing = find_quiz_by_content_hash(content_hash)
    if existing is not None:
        # Same content was already ingested — send the user straight to
        # wherever they left off instead of creating a duplicate row and
        # triggering another (billed) Claude generation call.
        attempts = existing["attempts"]
        latest = attempts[-1] if attempts else None
        summary = {
            "session_id": existing["session_id"],
            "has_quiz": existing["quiz"] is not None,
            "latest_mode": latest["mode"] if latest else None,
            "latest_in_progress": bool(latest and latest.get("overall_score") is None),
        }
        return _redirect(_quiz_destination(summary), flash="duplicate_content")

    session = create_quiz(source_text, truncated)

    return RedirectResponse(url=f"/generating/{session['session_id']}", status_code=303)


GENERATING_COPY = {
    "initial": (
        "Talking to Claude…",
        "Claude is reading through your material and writing quiz questions.",
    ),
    "expand": (
        "Generating more questions…",
        "Claude is reading through your material again and writing new questions that don't repeat what's already there.",
    ),
    "regenerate": (
        "Regenerating quiz…",
        "Claude is starting over and writing a brand new set of questions for this material.",
    ),
}


@app.get("/generating/{session_id}")
async def generating(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()
    flash_param = request.query_params.get("flash", "")
    if session.get("quiz"):
        return _redirect(f"/quiz/{session_id}", flash=flash_param)
    heading, subheading = GENERATING_COPY["initial"]
    return templates.TemplateResponse(
        request,
        "generating.html",
        {
            "session_id": session_id,
            "generate_url": f"/generate/{session_id}",
            "heading": heading,
            "subheading": subheading,
            "truncated": session.get("truncated", False),
            "flash": FLASH_MESSAGES.get(flash_param),
        },
    )


async def _run_locked_generation(request: Request, session_id: str, generate_fn, retry_url: str):
    """Runs `generate_fn` (a zero-arg callable returning list[dict]) in a
    threadpool under the `_generating` lock, keyed by session id. Shared by
    the initial-generation route and the dashboard "generate more"/
    "regenerate" actions so none of them can run an overlapping (paid)
    Claude call for the same quiz. Returns (questions, None) on success, or
    (None, response) if the lock was already held or generation failed —
    the caller should return `response` immediately in that case."""
    if session_id in _generating:
        # Another poll already kicked off generation for this quiz. HX-Reswap:
        # none stops htmx from swapping this empty body into the spinner
        # (it would otherwise wipe out the polling element and stall it).
        return None, Response(status_code=202, headers={"HX-Reswap": "none"})

    _generating.add(session_id)
    try:
        questions = await run_in_threadpool(generate_fn)
        return questions, None
    except QuizGenerationError:
        logger.exception("Quiz generation failed for session %s", session_id)
        return None, templates.TemplateResponse(
            request,
            "partials/generation_error.html",
            {
                "message": "We couldn't generate your quiz. Please try again.",
                "retry_url": retry_url,
            },
        )
    except Exception:
        logger.exception("Unexpected error while generating quiz for session %s", session_id)
        return None, templates.TemplateResponse(
            request,
            "partials/generation_error.html",
            {
                "message": "Something went wrong while generating your quiz. Please try again.",
                "retry_url": retry_url,
            },
        )
    finally:
        _generating.discard(session_id)


@app.post("/generate/{session_id}")
async def generate(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()

    if session.get("quiz"):
        return Response(headers={"HX-Redirect": f"/quiz/{session_id}"})

    questions, early = await _run_locked_generation(
        request,
        session_id,
        lambda: generate_quiz(session["source_text"]),
        f"/generating/{session_id}",
    )
    if early is not None:
        return early

    session["quiz"] = questions
    save_quiz(session)
    return Response(headers={"HX-Redirect": f"/quiz/{session_id}"})


@app.get("/quiz/{session_id}/expand")
async def quiz_expand(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()
    heading, subheading = GENERATING_COPY["expand"]
    return templates.TemplateResponse(
        request,
        "generating.html",
        {
            "session_id": session_id,
            "generate_url": f"/generate/{session_id}/expand",
            "heading": heading,
            "subheading": subheading,
        },
    )


@app.post("/generate/{session_id}/expand")
async def generate_expand(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()

    quiz_questions = session.get("quiz") or []
    existing_texts = [q["question"] for q in quiz_questions]

    questions, early = await _run_locked_generation(
        request,
        session_id,
        lambda: generate_quiz(session["source_text"], existing_questions=existing_texts),
        f"/quiz/{session_id}/expand",
    )
    if early is not None:
        return early

    new_questions = renumber_questions(questions, start=len(quiz_questions) + 1)
    session["quiz"] = quiz_questions + new_questions
    save_quiz(session)
    return Response(headers={"HX-Redirect": f"/quiz/{session_id}"})


@app.get("/quiz/{session_id}/regenerate")
async def quiz_regenerate(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()
    heading, subheading = GENERATING_COPY["regenerate"]
    return templates.TemplateResponse(
        request,
        "generating.html",
        {
            "session_id": session_id,
            "generate_url": f"/generate/{session_id}/regenerate",
            "heading": heading,
            "subheading": subheading,
        },
    )


@app.post("/generate/{session_id}/regenerate")
async def generate_regenerate(request: Request, session_id: str):
    session = get_quiz(session_id)
    if session is None:
        return _quiz_not_found_redirect()

    questions, early = await _run_locked_generation(
        request,
        session_id,
        lambda: generate_quiz(session["source_text"]),
        f"/quiz/{session_id}/regenerate",
    )
    if early is not None:
        return early

    session["quiz"] = questions
    session["attempts"] = []
    session["failure_counts"] = {}
    save_quiz(session)
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

    flash_param = request.query_params.get("flash", "")

    quiz_questions = session.get("quiz")
    if not quiz_questions:
        return _redirect(f"/generating/{session_id}", flash=flash_param)

    attempts = session.setdefault("attempts", [])
    latest = attempts[-1] if attempts else None

    if latest and latest.get("mode") == mode and not latest.get("answers"):
        # Already initialised for this mode (typically by /retry) and not yet
        # started — reuse it rather than discarding its question selection.
        attempt = latest
    elif mode == "evaluation":
        # Evaluation attempts must be set up via POST /retry, which knows the
        # failure_counts needed to select questions.
        return _redirect(f"/results/{session_id}", flash=flash_param)
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
    context["flash"] = FLASH_MESSAGES.get(flash_param)
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
    flash_param = request.query_params.get("flash", "")
    if not attempts or attempts[-1].get("overall_score") is None:
        mode = attempts[-1].get("mode", "practice") if attempts else "practice"
        return _redirect(f"/quiz/{session_id}?mode={mode}", flash=flash_param)

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
        "flash": FLASH_MESSAGES.get(flash_param),
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
