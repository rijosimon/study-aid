import pytest
from fastapi.testclient import TestClient

import db
from main import app
from tests.pdf_fixtures import build_image_only_pdf, build_pdf_with_text


@pytest.fixture(autouse=True)
def clear_store():
    db.reset_db()
    yield
    db.reset_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def test_ingest_pdf_creates_session_and_redirects_to_generating(client):
    pdf_bytes = build_pdf_with_text("Photosynthesis converts light energy into chemical energy.")

    resp = client.post(
        "/ingest",
        files={"file": ("notes.pdf", pdf_bytes, "application/pdf")},
    )

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/generating/")
    session_id = location.removeprefix("/generating/")

    session = db.get_quiz(session_id)
    assert session is not None
    assert "Photosynthesis" in session["source_text"]


def test_ingest_pasted_text_creates_session(client):
    long_text = "This is a long enough piece of study text to pass the minimum length check. " * 2

    resp = client.post("/ingest", data={"text": long_text})

    assert resp.status_code == 303
    session_id = resp.headers["location"].removeprefix("/generating/")
    session = db.get_quiz(session_id)
    assert session is not None
    assert session["source_text"] == long_text.strip()


def test_ingest_unsupported_file_type_renders_error_page(client):
    resp = client.post(
        "/ingest",
        files={"file": ("notes.txt", b"some content", "text/plain")},
    )

    assert resp.status_code == 400
    assert "Unsupported file type" in resp.text
    assert "session_id" not in resp.cookies


def test_ingest_image_only_pdf_renders_error_page(client):
    pdf_bytes = build_image_only_pdf()

    resp = client.post(
        "/ingest",
        files={"file": ("scanned.pdf", pdf_bytes, "application/pdf")},
    )

    assert resp.status_code == 400
    assert "only images" in resp.text


def test_ingest_short_pasted_text_renders_error_page(client):
    resp = client.post("/ingest", data={"text": "too short"})

    assert resp.status_code == 400
    assert "at least 50 characters" in resp.text


def test_ingest_empty_file_renders_error_page(client):
    resp = client.post(
        "/ingest",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )

    assert resp.status_code == 400
    assert "empty" in resp.text.lower()


def test_ingest_with_neither_file_nor_text_renders_error_page(client):
    resp = client.post("/ingest", data={})

    assert resp.status_code == 400
    assert "upload a file or paste" in resp.text.lower()


def test_ingest_corrupted_pdf_renders_error_page_instead_of_crashing(client):
    resp = client.post(
        "/ingest",
        files={"file": ("broken.pdf", b"not actually a pdf file", "application/pdf")},
    )

    assert resp.status_code == 400
    assert "corrupted" in resp.text.lower()
