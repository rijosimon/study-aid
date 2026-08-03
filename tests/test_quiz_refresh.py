import pytest
from fastapi.testclient import TestClient

import db
import main
from main import app
from quiz_engine import QuizGenerationError

EXISTING_QUIZ = [
    {
        "id": "q1",
        "concept": "Photosynthesis",
        "type": "short_answer",
        "question": "What organelle performs photosynthesis?",
        "correct_answer": "Chloroplast",
        "explanation": "The chloroplast is where photosynthesis occurs.",
    },
    {
        "id": "q2",
        "concept": "Photosynthesis",
        "type": "true_false",
        "question": "Photosynthesis occurs in the mitochondria.",
        "correct_answer": "False",
        "explanation": "It occurs in the chloroplast.",
    },
]

NEW_QUIZ = [
    {
        "id": "q1",  # Claude always starts back at q1 — must get renumbered on merge
        "concept": "Photosynthesis",
        "type": "short_answer",
        "question": "What pigment absorbs light?",
        "correct_answer": "Chlorophyll",
        "explanation": "Chlorophyll absorbs light energy.",
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


def _quiz_with_existing_quiz(attempts=None):
    quiz = db.create_quiz("Photosynthesis converts light energy into chemical energy. " * 3)
    quiz["quiz"] = EXISTING_QUIZ
    quiz["attempts"] = attempts or []
    quiz["failure_counts"] = {"q1": 2} if attempts else {}
    db.save_quiz(quiz)
    return quiz


# --- GET /quiz/{id}/expand and /quiz/{id}/regenerate: spinner pages ---


def test_expand_spinner_page_renders_with_distinct_copy(client):
    quiz = _quiz_with_existing_quiz()

    resp = client.get(f"/quiz/{quiz['session_id']}/expand")

    assert resp.status_code == 200
    assert "Generating more questions" in resp.text
    assert f"/generate/{quiz['session_id']}/expand" in resp.text


def test_regenerate_spinner_page_renders_with_distinct_copy(client):
    quiz = _quiz_with_existing_quiz()

    resp = client.get(f"/quiz/{quiz['session_id']}/regenerate")

    assert resp.status_code == 200
    assert "Regenerating quiz" in resp.text
    assert f"/generate/{quiz['session_id']}/regenerate" in resp.text


def test_expand_spinner_redirects_to_landing_for_unknown_session(client):
    resp = client.get("/quiz/does-not-exist/expand")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_regenerate_spinner_redirects_to_landing_for_unknown_session(client):
    resp = client.get("/quiz/does-not-exist/regenerate")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


# --- POST /generate/{id}/expand: generate more questions ---


def test_generate_expand_appends_renumbered_questions(client, monkeypatch):
    quiz = _quiz_with_existing_quiz()
    calls = []

    def _fake(text, existing_questions=None):
        calls.append(existing_questions)
        return NEW_QUIZ

    monkeypatch.setattr(main, "generate_quiz", _fake)

    resp = client.post(f"/generate/{quiz['session_id']}/expand")

    assert resp.status_code == 200
    assert resp.headers["hx-redirect"] == f"/quiz/{quiz['session_id']}"
    updated = db.get_quiz(quiz["session_id"])
    assert len(updated["quiz"]) == 3
    assert [q["id"] for q in updated["quiz"]] == ["q1", "q2", "q3"]
    # existing questions untouched, new one appended with renumbered id
    assert updated["quiz"][2]["question"] == "What pigment absorbs light?"
    # passed the existing questions' text as context to avoid repeats
    assert calls == [["What organelle performs photosynthesis?", "Photosynthesis occurs in the mitochondria."]]


def test_generate_expand_preserves_existing_attempts_and_failure_counts(client, monkeypatch):
    attempt = {"mode": "practice", "answers": {"q1": {"correct": True, "score": 1.0}}, "overall_score": 0.5}
    quiz = _quiz_with_existing_quiz(attempts=[attempt])
    monkeypatch.setattr(main, "generate_quiz", lambda text, existing_questions=None: NEW_QUIZ)

    client.post(f"/generate/{quiz['session_id']}/expand")

    updated = db.get_quiz(quiz["session_id"])
    assert updated["attempts"] == [attempt]
    assert updated["failure_counts"] == {"q1": 2}


def test_generate_expand_redirects_to_landing_for_unknown_session(client):
    resp = client.post("/generate/does-not-exist/expand")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_generate_expand_renders_inline_error_partial_on_failure(client, monkeypatch):
    quiz = _quiz_with_existing_quiz()

    def _raise(text, existing_questions=None):
        raise QuizGenerationError("boom")

    monkeypatch.setattr(main, "generate_quiz", _raise)

    resp = client.post(f"/generate/{quiz['session_id']}/expand")

    assert resp.status_code == 200
    assert "generate your quiz" in resp.text.lower()
    # existing quiz untouched on failure
    assert db.get_quiz(quiz["session_id"])["quiz"] == EXISTING_QUIZ


# --- POST /generate/{id}/regenerate: wipe and start over ---


def test_generate_regenerate_replaces_quiz_entirely(client, monkeypatch):
    quiz = _quiz_with_existing_quiz()
    monkeypatch.setattr(main, "generate_quiz", lambda text, existing_questions=None: NEW_QUIZ)

    resp = client.post(f"/generate/{quiz['session_id']}/regenerate")

    assert resp.status_code == 200
    assert resp.headers["hx-redirect"] == f"/quiz/{quiz['session_id']}"
    updated = db.get_quiz(quiz["session_id"])
    assert updated["quiz"] == NEW_QUIZ


def test_generate_regenerate_clears_attempts_and_failure_counts(client, monkeypatch):
    attempt = {"mode": "practice", "answers": {"q1": {"correct": True, "score": 1.0}}, "overall_score": 0.5}
    quiz = _quiz_with_existing_quiz(attempts=[attempt])
    monkeypatch.setattr(main, "generate_quiz", lambda text, existing_questions=None: NEW_QUIZ)

    client.post(f"/generate/{quiz['session_id']}/regenerate")

    updated = db.get_quiz(quiz["session_id"])
    assert updated["attempts"] == []
    assert updated["failure_counts"] == {}


def test_generate_regenerate_does_not_pass_existing_questions_as_context(client, monkeypatch):
    quiz = _quiz_with_existing_quiz()
    calls = []
    monkeypatch.setattr(
        main, "generate_quiz", lambda text, existing_questions=None: (calls.append(existing_questions), NEW_QUIZ)[1]
    )

    client.post(f"/generate/{quiz['session_id']}/regenerate")

    assert calls == [None]


def test_generate_regenerate_redirects_to_landing_for_unknown_session(client):
    resp = client.post("/generate/does-not-exist/regenerate")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_generate_regenerate_renders_inline_error_partial_on_failure(client, monkeypatch):
    attempt = {"mode": "practice", "answers": {"q1": {"correct": True, "score": 1.0}}, "overall_score": 0.5}
    quiz = _quiz_with_existing_quiz(attempts=[attempt])

    def _raise(text, existing_questions=None):
        raise QuizGenerationError("boom")

    monkeypatch.setattr(main, "generate_quiz", _raise)

    resp = client.post(f"/generate/{quiz['session_id']}/regenerate")

    assert resp.status_code == 200
    assert "generate your quiz" in resp.text.lower()
    # existing quiz and attempts untouched on failure
    updated = db.get_quiz(quiz["session_id"])
    assert updated["quiz"] == EXISTING_QUIZ
    assert updated["attempts"] == [attempt]


# --- shared _generating lock across all three generation actions ---


@pytest.mark.parametrize(
    "path",
    ["/generate/{id}/expand", "/generate/{id}/regenerate"],
)
def test_generation_action_returns_202_when_lock_already_held(client, monkeypatch, path):
    quiz = _quiz_with_existing_quiz()
    calls = []
    monkeypatch.setattr(
        main, "generate_quiz", lambda text, existing_questions=None: (calls.append(1), NEW_QUIZ)[1]
    )
    main._generating.add(quiz["session_id"])
    try:
        resp = client.post(path.format(id=quiz["session_id"]))
    finally:
        main._generating.discard(quiz["session_id"])

    assert resp.status_code == 202
    assert resp.headers["hx-reswap"] == "none"
    assert len(calls) == 0


def test_expand_and_regenerate_lock_is_cleared_after_failure(client, monkeypatch):
    quiz = _quiz_with_existing_quiz()

    def _raise(text, existing_questions=None):
        raise QuizGenerationError("boom")

    monkeypatch.setattr(main, "generate_quiz", _raise)

    client.post(f"/generate/{quiz['session_id']}/expand")
    assert quiz["session_id"] not in main._generating

    client.post(f"/generate/{quiz['session_id']}/regenerate")
    assert quiz["session_id"] not in main._generating
