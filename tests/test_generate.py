import pytest
from fastapi.testclient import TestClient

import db
import main
from main import app
from quiz_engine import QuizGenerationError

SAMPLE_QUIZ = [
    {
        "id": "q1",
        "concept": "Photosynthesis",
        "type": "short_answer",
        "question": "What organelle performs photosynthesis?",
        "correct_answer": "Chloroplast",
        "explanation": "The chloroplast is where photosynthesis occurs.",
    }
]


@pytest.fixture(autouse=True)
def clear_store():
    db.reset_db()
    yield
    db.reset_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def _create_session(client) -> str:
    long_text = "Photosynthesis converts light energy into chemical energy. " * 3
    resp = client.post("/ingest", data={"text": long_text})
    return resp.headers["location"].removeprefix("/generating/")


def test_generating_page_renders_and_polls_generate(client):
    session_id = _create_session(client)

    resp = client.get(f"/generating/{session_id}")

    assert resp.status_code == 200
    assert "Talking to Claude" in resp.text
    assert f"/generate/{session_id}" in resp.text


def test_generating_page_redirects_to_landing_for_unknown_session(client):
    resp = client.get("/generating/does-not-exist")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_generate_populates_quiz_and_returns_hx_redirect(client, monkeypatch):
    session_id = _create_session(client)
    calls = []
    monkeypatch.setattr(main, "generate_quiz", lambda text: (calls.append(text), SAMPLE_QUIZ)[1])

    resp = client.post(f"/generate/{session_id}")

    assert resp.status_code == 200
    assert resp.headers["hx-redirect"] == f"/quiz/{session_id}"
    session = db.get_quiz(session_id)
    assert session["quiz"] == SAMPLE_QUIZ
    assert len(calls) == 1


def test_generate_is_idempotent_and_does_not_recall_claude(client, monkeypatch):
    session_id = _create_session(client)
    calls = []
    monkeypatch.setattr(main, "generate_quiz", lambda text: (calls.append(text), SAMPLE_QUIZ)[1])

    client.post(f"/generate/{session_id}")
    resp = client.post(f"/generate/{session_id}")

    assert resp.status_code == 200
    assert resp.headers["hx-redirect"] == f"/quiz/{session_id}"
    assert len(calls) == 1


def test_generating_page_redirects_to_quiz_once_generated(client, monkeypatch):
    session_id = _create_session(client)
    monkeypatch.setattr(main, "generate_quiz", lambda text: SAMPLE_QUIZ)
    client.post(f"/generate/{session_id}")

    resp = client.get(f"/generating/{session_id}")

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/quiz/{session_id}"


def test_generate_renders_inline_error_partial_on_generation_failure(client, monkeypatch):
    session_id = _create_session(client)

    def _raise(text):
        raise QuizGenerationError("boom")

    monkeypatch.setattr(main, "generate_quiz", _raise)

    resp = client.post(f"/generate/{session_id}")

    # 200, not an error status: htmx only auto-swaps 2xx responses by
    # default, and this partial needs to swap into the spinner to be seen.
    assert resp.status_code == 200
    assert "generate your quiz" in resp.text.lower()
    assert "<html" not in resp.text.lower()  # partial, not the full error page


def test_generate_renders_inline_error_partial_on_unexpected_exception(client, monkeypatch):
    session_id = _create_session(client)

    def _raise(text):
        raise TypeError("no api key configured")

    monkeypatch.setattr(main, "generate_quiz", _raise)

    resp = client.post(f"/generate/{session_id}")

    assert resp.status_code == 200
    assert "something went wrong" in resp.text.lower()


def test_generate_redirects_to_landing_for_unknown_session(client):
    resp = client.post("/generate/does-not-exist")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_generate_returns_202_without_recalling_claude_when_already_in_progress(client, monkeypatch):
    session_id = _create_session(client)
    calls = []
    monkeypatch.setattr(main, "generate_quiz", lambda text: (calls.append(text), SAMPLE_QUIZ)[1])
    # Simulate another in-flight poll already having claimed this quiz — the
    # in-progress lock is a plain in-memory set now, not a persisted field.
    main._generating.add(session_id)
    try:
        resp = client.post(f"/generate/{session_id}")
    finally:
        main._generating.discard(session_id)

    assert resp.status_code == 202
    assert resp.headers["hx-reswap"] == "none"
    assert len(calls) == 0


def test_generate_clears_in_progress_lock_after_failure(client, monkeypatch):
    session_id = _create_session(client)

    def _raise(text):
        raise QuizGenerationError("boom")

    monkeypatch.setattr(main, "generate_quiz", _raise)
    client.post(f"/generate/{session_id}")

    assert session_id not in main._generating
