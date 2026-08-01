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
from quiz_engine import QuizGenerationError, generate_quiz
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
        # rather than starting a second (paid) generation call.
        return Response(status_code=202)

    session["_generating"] = True
    try:
        session["quiz"] = await run_in_threadpool(generate_quiz, session["source_text"])
    except QuizGenerationError:
        logger.exception("Quiz generation failed for session %s", session_id)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": "We couldn't generate your quiz. Please try again."},
            status_code=502,
        )
    finally:
        session["_generating"] = False

    return Response(headers={"HX-Redirect": f"/quiz/{session_id}"})
