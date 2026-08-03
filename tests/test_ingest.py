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


def test_ingest_duplicate_of_completed_quiz_redirects_to_results_without_creating_a_row(client):
    text = "This is the exact same study material, pasted twice by mistake. " * 2
    quiz = db.create_quiz(text.strip())
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [
        {
            "mode": "practice",
            "answers": {"q1": {"user_answer": "x", "correct": True, "score": 1.0}},
            "overall_score": 0.8,
            "concept_scores": {},
        }
    ]
    db.save_quiz(quiz)

    before_count = len(db.list_quizzes())

    resp = client.post("/ingest", data={"text": text})

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/results/{quiz['session_id']}?flash=duplicate_content"
    assert len(db.list_quizzes()) == before_count


def test_ingest_duplicate_of_never_attempted_quiz_redirects_to_quiz(client):
    text = "This is the exact same study material, pasted twice by mistake. " * 2
    quiz = db.create_quiz(text.strip())
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)

    before_count = len(db.list_quizzes())

    resp = client.post("/ingest", data={"text": text})

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/quiz/{quiz['session_id']}?flash=duplicate_content"
    assert len(db.list_quizzes()) == before_count


def test_ingest_duplicate_of_in_progress_quiz_redirects_to_quiz_with_mode(client):
    text = "This is the exact same study material, pasted twice by mistake. " * 2
    quiz = db.create_quiz(text.strip())
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "evaluation", "answers": {}, "overall_score": None}]
    db.save_quiz(quiz)

    before_count = len(db.list_quizzes())

    resp = client.post("/ingest", data={"text": text})

    assert resp.status_code == 303
    assert (
        resp.headers["location"]
        == f"/quiz/{quiz['session_id']}?mode=evaluation&flash=duplicate_content"
    )
    assert len(db.list_quizzes()) == before_count


def test_ingest_duplicate_of_still_generating_quiz_redirects_to_generating(client):
    text = "This is the exact same study material, pasted twice by mistake. " * 2
    quiz = db.create_quiz(text.strip())

    before_count = len(db.list_quizzes())

    resp = client.post("/ingest", data={"text": text})

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/generating/{quiz['session_id']}?flash=duplicate_content"
    assert len(db.list_quizzes()) == before_count


def test_ingest_first_time_content_is_unaffected_by_dedup_logic(client):
    text = "Brand new study material nobody has uploaded before. " * 2

    resp = client.post("/ingest", data={"text": text})

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/generating/")
    assert "flash" not in location
    session_id = location.removeprefix("/generating/")
    assert db.get_quiz(session_id) is not None


def test_ingest_duplicate_flash_message_renders_on_results_page(client):
    text = "This is the exact same study material, pasted twice by mistake. " * 2
    quiz = db.create_quiz(text.strip())
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [
        {
            "mode": "practice",
            "answers": {"q1": {"user_answer": "x", "correct": True, "score": 1.0}},
            "overall_score": 0.8,
            "concept_scores": {},
        }
    ]
    db.save_quiz(quiz)

    resp = client.post("/ingest", data={"text": text})
    follow = client.get(resp.headers["location"])

    assert follow.status_code == 200
    assert "You&#39;ve already created a quiz from this material." in follow.text


def test_ingest_duplicate_flash_message_renders_on_quiz_page(client):
    text = "This is the exact same study material, pasted twice by mistake. " * 2
    quiz = db.create_quiz(text.strip())
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)

    resp = client.post("/ingest", data={"text": text})
    follow = client.get(resp.headers["location"])

    assert follow.status_code == 200
    assert "You&#39;ve already created a quiz from this material." in follow.text


def test_ingest_duplicate_flash_message_renders_on_generating_page(client):
    text = "This is the exact same study material, pasted twice by mistake. " * 2
    quiz = db.create_quiz(text.strip())

    resp = client.post("/ingest", data={"text": text})
    follow = client.get(resp.headers["location"])

    assert follow.status_code == 200
    assert "You&#39;ve already created a quiz from this material." in follow.text
