import pytest
from fastapi.testclient import TestClient

import main
import session_store
from main import app

SAMPLE_QUIZ = [
    {
        "id": "q1",
        "concept": "Photosynthesis",
        "type": "multiple_choice",
        "question": "Which pigment absorbs light for photosynthesis?",
        "options": ["Chlorophyll", "Keratin", "Collagen", "Melanin"],
        "correct_answer": "Chlorophyll",
        "explanation": "Chlorophyll absorbs light energy used in photosynthesis.",
    },
    {
        "id": "q2",
        "concept": "Photosynthesis",
        "type": "true_false",
        "question": "Photosynthesis occurs in the mitochondria.",
        "options": ["True", "False"],
        "correct_answer": "False",
        "explanation": "Photosynthesis occurs in the chloroplast.",
    },
    {
        "id": "q3",
        "concept": "Cell Division",
        "type": "short_answer",
        "question": "Name the phase where chromosomes align at the cell's center.",
        "correct_answer": "Metaphase",
        "explanation": "Chromosomes align at the metaphase plate during metaphase.",
    },
]


@pytest.fixture(autouse=True)
def clear_store():
    session_store._store.clear()
    yield
    session_store._store.clear()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def _session_with_quiz(client) -> str:
    session = session_store.create_session()
    session["source_text"] = "some source text"
    session["quiz"] = [dict(q) for q in SAMPLE_QUIZ]
    client.cookies.set("session_id", session["session_id"])
    return session["session_id"]


def test_quiz_page_renders_first_question_and_creates_attempt(client):
    session_id = _session_with_quiz(client)

    resp = client.get(f"/quiz/{session_id}")

    assert resp.status_code == 200
    assert "Which pigment absorbs light" in resp.text
    assert "Question 1 of 3" in resp.text

    session = session_store.get_session(session_id)
    assert len(session["attempts"]) == 1
    assert session["attempts"][0]["mode"] == "practice"
    assert session["attempts"][0]["answers"] == {}


def test_quiz_page_redirects_to_generating_if_no_quiz_yet(client):
    session = session_store.create_session()
    session["source_text"] = "some source text"

    resp = client.get(f"/quiz/{session['session_id']}")

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/generating/{session['session_id']}"


def test_quiz_page_redirects_to_landing_for_unknown_session(client):
    resp = client.get("/quiz/does-not-exist")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=session_expired"


def test_answer_correct_mc_returns_next_question_with_feedback(client):
    session_id = _session_with_quiz(client)
    client.get(f"/quiz/{session_id}")

    resp = client.post(f"/answer/{session_id}", data={"question_id": "q1", "user_answer": "Chlorophyll"})

    assert resp.status_code == 200
    assert "Correct!" in resp.text
    assert "Question 2 of 3" in resp.text
    assert "mitochondria" in resp.text.lower()


def test_answer_incorrect_mc_shows_correct_answer_in_feedback(client):
    session_id = _session_with_quiz(client)
    client.get(f"/quiz/{session_id}")

    resp = client.post(f"/answer/{session_id}", data={"question_id": "q1", "user_answer": "Keratin"})

    assert resp.status_code == 200
    assert "Not quite" in resp.text
    assert "Chlorophyll" in resp.text  # correct answer shown


def test_answer_true_false_is_case_insensitive(client):
    session_id = _session_with_quiz(client)
    client.get(f"/quiz/{session_id}")
    client.post(f"/answer/{session_id}", data={"question_id": "q1", "user_answer": "Chlorophyll"})

    resp = client.post(f"/answer/{session_id}", data={"question_id": "q2", "user_answer": "FALSE"})

    assert "Correct!" in resp.text
    session = session_store.get_session(session_id)
    assert session["attempts"][-1]["answers"]["q2"]["correct"] is True


def test_answer_short_answer_uses_grade_short_answer(client, monkeypatch):
    session_id = _session_with_quiz(client)
    client.get(f"/quiz/{session_id}")
    client.post(f"/answer/{session_id}", data={"question_id": "q1", "user_answer": "Chlorophyll"})
    client.post(f"/answer/{session_id}", data={"question_id": "q2", "user_answer": "False"})

    calls = []

    def fake_grade(question, correct_answer, user_answer):
        calls.append((question, correct_answer, user_answer))
        return {"passed": True, "feedback": "Nicely explained."}

    monkeypatch.setattr(main, "grade_short_answer", fake_grade)

    resp = client.post(f"/answer/{session_id}", data={"question_id": "q3", "user_answer": "Metaphase, I think"})

    assert len(calls) == 1
    assert calls[0][1] == "Metaphase"
    # last question -> redirect to results, not a rendered partial
    assert resp.headers["hx-redirect"] == f"/results/{session_id}"

    session = session_store.get_session(session_id)
    answer = session["attempts"][-1]["answers"]["q3"]
    assert answer["correct"] is True
    assert answer["user_answer"] == "Metaphase, I think"


def test_answer_double_submission_returns_error(client):
    session_id = _session_with_quiz(client)
    client.get(f"/quiz/{session_id}")
    client.post(f"/answer/{session_id}", data={"question_id": "q1", "user_answer": "Chlorophyll"})

    resp = client.post(f"/answer/{session_id}", data={"question_id": "q1", "user_answer": "Chlorophyll"})

    assert resp.status_code == 409


def test_answer_unknown_question_id_returns_error(client):
    session_id = _session_with_quiz(client)
    client.get(f"/quiz/{session_id}")

    resp = client.post(f"/answer/{session_id}", data={"question_id": "not-a-real-id", "user_answer": "x"})

    assert resp.status_code == 400


def test_answer_redirects_to_quiz_start_when_no_attempt_exists(client):
    session_id = _session_with_quiz(client)
    # No GET /quiz call yet, so there's no attempt to answer against.

    resp = client.post(f"/answer/{session_id}", data={"question_id": "q1", "user_answer": "Chlorophyll"})

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/quiz/{session_id}"


def test_answer_unknown_session_redirects_to_landing(client):
    resp = client.post("/answer/does-not-exist", data={"question_id": "q1", "user_answer": "x"})

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=session_expired"


def test_full_practice_quiz_walkthrough_populates_all_answers(client, monkeypatch):
    session_id = _session_with_quiz(client)
    monkeypatch.setattr(
        main, "grade_short_answer", lambda q, c, u: {"passed": False, "feedback": "Close, but missing detail."}
    )
    client.get(f"/quiz/{session_id}")

    r1 = client.post(f"/answer/{session_id}", data={"question_id": "q1", "user_answer": "Chlorophyll"})
    assert r1.status_code == 200

    r2 = client.post(f"/answer/{session_id}", data={"question_id": "q2", "user_answer": "wrong-value"})
    assert r2.status_code == 200

    r3 = client.post(f"/answer/{session_id}", data={"question_id": "q3", "user_answer": "no idea"})
    assert r3.headers["hx-redirect"] == f"/results/{session_id}"

    session = session_store.get_session(session_id)
    answers = session["attempts"][-1]["answers"]
    assert set(answers.keys()) == {"q1", "q2", "q3"}
    assert answers["q1"]["correct"] is True
    assert answers["q2"]["correct"] is False
    assert answers["q3"]["correct"] is False
