import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from parsers import ExtractionError, extract_docx, extract_pdf, extract_text
from session_store import create_session, purge_expired_sessions, set_session_cookie

load_dotenv()

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

    session = create_session()
    session["source_text"] = source_text

    response = RedirectResponse(url=f"/generating/{session['session_id']}", status_code=303)
    set_session_cookie(response, session["session_id"])
    return response
