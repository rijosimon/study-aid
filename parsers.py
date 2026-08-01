import io

from docx import Document
from pypdf import PdfReader

MIN_TEXT_LENGTH = 50


class ExtractionError(ValueError):
    """Raised when source material can't be turned into usable quiz text."""


def extract_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if not text:
        raise ExtractionError(
            "This PDF appears to contain only images. Please paste the text manually."
        )
    return text


def extract_docx(file_bytes: bytes) -> str:
    document = Document(io.BytesIO(file_bytes))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
    if not text:
        raise ExtractionError(
            "This document has no extractable text. Please paste the text manually."
        )
    return text


def extract_text(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) < MIN_TEXT_LENGTH:
        raise ExtractionError(
            f"Text must be at least {MIN_TEXT_LENGTH} characters long."
        )
    return text
