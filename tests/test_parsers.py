import pytest

from parsers import ExtractionError, extract_docx, extract_pdf, extract_text
from tests.pdf_fixtures import (
    build_docx_with_text,
    build_empty_docx,
    build_image_only_pdf,
    build_pdf_with_text,
)


def test_extract_pdf_returns_non_empty_string():
    pdf_bytes = build_pdf_with_text("Hello Study Aid")

    text = extract_pdf(pdf_bytes)

    assert text.strip() != ""
    assert "Hello Study Aid" in text


def test_extract_pdf_raises_for_image_only_pdf():
    pdf_bytes = build_image_only_pdf()

    with pytest.raises(ExtractionError):
        extract_pdf(pdf_bytes)


def test_extract_docx_returns_non_empty_string():
    docx_bytes = build_docx_with_text("Hello Study Aid from DOCX")

    text = extract_docx(docx_bytes)

    assert text.strip() != ""
    assert "Hello Study Aid from DOCX" in text


def test_extract_docx_raises_for_empty_document():
    docx_bytes = build_empty_docx()

    with pytest.raises(ExtractionError):
        extract_docx(docx_bytes)


def test_extract_text_raises_for_empty_string():
    with pytest.raises(ExtractionError):
        extract_text("")


def test_extract_text_raises_for_text_under_minimum_length():
    with pytest.raises(ExtractionError):
        extract_text("too short")


def test_extract_text_returns_stripped_text_when_long_enough():
    raw = "  " + ("word " * 15) + "  "

    text = extract_text(raw)

    assert text == raw.strip()
