"""S8.6: integration tests for the Epic 8 hardening stories (S8.1-S8.4, S8.7)."""

import pytest
from fastapi.testclient import TestClient

import db
import main
from main import app
from quiz_engine import QuizGenerationError
from tests.pdf_fixtures import build_image_only_pdf


@pytest.fixture(autouse=True)
def clear_store():
    db.reset_db()
    yield
    db.reset_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


# --- S8.1: missing quiz -> redirect to / with flash message ---


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/generating/does-not-exist"),
        ("post", "/generate/does-not-exist"),
        ("get", "/quiz/does-not-exist"),
        ("post", "/retry/does-not-exist"),
        ("post", "/answer/does-not-exist"),
        ("get", "/results/does-not-exist"),
    ],
)
def test_missing_session_redirects_with_flash_message(client, method, path):
    if method == "get":
        resp = client.get(path)
    else:
        data = {"question_id": "q1", "user_answer": "x"} if "/answer/" in path else {}
        resp = client.post(path, data=data)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_landing_page_shows_flash_message(client):
    resp = client.get("/?flash=quiz_not_found")

    assert resp.status_code == 200
    assert "Quiz not found. Please start over." in resp.text


def test_landing_page_shows_no_banner_without_flash_param(client):
    resp = client.get("/")

    assert "Quiz not found" not in resp.text


def test_landing_page_ignores_unknown_flash_values(client):
    resp = client.get("/?flash=not-a-real-key")

    assert resp.status_code == 200
    assert "Quiz not found" not in resp.text


# --- S8.2: image-only PDF (already covered in test_ingest.py; smoke-check here too) ---


def test_image_only_pdf_shows_expected_message(client):
    pdf_bytes = build_image_only_pdf()

    resp = client.post("/ingest", files={"file": ("scan.pdf", pdf_bytes, "application/pdf")})

    assert resp.status_code == 400
    assert "only images" in resp.text


# --- S8.3: Claude API errors show a retry option instead of crashing ---


def test_generation_timeout_shows_retry_link_back_to_generating_page(client, monkeypatch):
    session = db.create_quiz("some source text")

    def _raise_timeout(text):
        raise TimeoutError("Request timed out")

    monkeypatch.setattr(main, "generate_quiz", _raise_timeout)

    resp = client.post(f"/generate/{session['session_id']}")

    assert resp.status_code == 200  # not a crash
    assert f'href="/generating/{session["session_id"]}"' in resp.text


def test_malformed_quiz_response_shows_retry_link(client, monkeypatch):
    session = db.create_quiz("some source text")

    def _raise(text):
        raise QuizGenerationError("malformed JSON after retry")

    monkeypatch.setattr(main, "generate_quiz", _raise)

    resp = client.post(f"/generate/{session['session_id']}")

    assert resp.status_code == 200
    assert f'href="/generating/{session["session_id"]}"' in resp.text


# --- S8.4: very long documents are truncated with a warning banner ---


def test_long_pasted_text_is_truncated_with_warning_banner(client):
    long_text = "word " * 20_000  # ~100k chars, over the 80k limit

    resp = client.post("/ingest", data={"text": long_text})
    session_id = resp.headers["location"].removeprefix("/generating/")

    session = db.get_quiz(session_id)
    assert len(session["source_text"]) == main.MAX_SOURCE_TEXT_CHARS
    assert session["truncated"] is True

    page = client.get(f"/generating/{session_id}")
    assert "Document was truncated to fit AI limits." in page.text


def test_short_pasted_text_is_not_truncated(client):
    text = "This is a normal length piece of study text, well under the limit. " * 3

    resp = client.post("/ingest", data={"text": text})
    session_id = resp.headers["location"].removeprefix("/generating/")

    session = db.get_quiz(session_id)
    assert session["truncated"] is False

    page = client.get(f"/generating/{session_id}")
    assert "Document was truncated" not in page.text


# --- S8.7: dev-only debug session endpoint ---


def test_debug_session_endpoint_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("DEBUG", raising=False)
    session = db.create_quiz("")

    resp = client.get(f"/debug/session/{session['session_id']}")

    assert resp.status_code == 404


def test_debug_session_endpoint_returns_session_json_when_enabled(client, monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    session = db.create_quiz("hello world")

    resp = client.get(f"/debug/session/{session['session_id']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session["session_id"]
    assert body["source_text"] == "hello world"
    assert "created_at" in body


def test_debug_session_endpoint_404s_for_unknown_session_even_when_enabled(client, monkeypatch):
    monkeypatch.setenv("DEBUG", "true")

    resp = client.get("/debug/session/does-not-exist")

    assert resp.status_code == 404
