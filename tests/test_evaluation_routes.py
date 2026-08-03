import pytest
from fastapi.testclient import TestClient

import db
from main import app


def _make_quiz(n: int) -> list:
    quiz = []
    for i in range(1, n + 1):
        quiz.append(
            {
                "id": f"q{i}",
                "concept": f"Concept {(i - 1) % 3}",
                "type": "true_false",
                "question": f"Statement number {i} is true.",
                "options": ["True", "False"],
                "correct_answer": "True",
                "explanation": f"Explanation for question {i}.",
            }
        )
    return quiz


SAMPLE_QUIZ = _make_quiz(12)


@pytest.fixture(autouse=True)
def clear_store():
    db.reset_db()
    yield
    db.reset_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def _session_with_quiz(client) -> str:
    session = db.create_quiz("some source text")
    session["quiz"] = [dict(q) for q in SAMPLE_QUIZ]
    db.save_quiz(session)
    return session["session_id"]


def _set_failure_counts(session_id: str, failure_counts: dict) -> None:
    """Fetch the current persisted state, inject failure_counts, and save —
    never overwrite with a stale copy fetched before other app-side mutations."""
    session = db.get_quiz(session_id)
    session["failure_counts"] = failure_counts
    db.save_quiz(session)


def _complete_attempt(client, session_id, answers: dict) -> None:
    """answers: {question_id: user_answer}, submitted in the order the attempt
    presents them. Questions not named in `answers` default to "True" (the
    correct_answer for every question in SAMPLE_QUIZ)."""
    session = db.get_quiz(session_id)
    active_ids = session["attempts"][-1]["question_ids"]
    for qid in active_ids:
        client.post(f"/answer/{session_id}", data={"question_id": qid, "user_answer": answers.get(qid, "True")})


def test_retry_practice_creates_new_full_attempt(client):
    session_id = _session_with_quiz(client)

    resp = client.post(f"/retry/{session_id}?mode=practice")

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/quiz/{session_id}?mode=practice"
    session = db.get_quiz(session_id)
    attempt = session["attempts"][-1]
    assert attempt["mode"] == "practice"
    assert set(attempt["question_ids"]) == {q["id"] for q in SAMPLE_QUIZ}


def test_retry_evaluation_selects_weighted_questions_and_redirects(client):
    session_id = _session_with_quiz(client)
    _set_failure_counts(session_id, {"q1": 3, "q2": 1})

    resp = client.post(f"/retry/{session_id}?mode=evaluation")

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/quiz/{session_id}?mode=evaluation"
    session = db.get_quiz(session_id)
    attempt = session["attempts"][-1]
    assert attempt["mode"] == "evaluation"
    assert {"q1", "q2"}.issubset(set(attempt["question_ids"]))
    assert len(attempt["question_ids"]) == len(set(attempt["question_ids"]))  # no duplicates


def test_quiz_evaluation_mode_uses_preselected_subset_not_full_quiz(client):
    session_id = _session_with_quiz(client)
    _set_failure_counts(session_id, {"q1": 2, "q2": 1, "q3": 1})
    client.post(f"/retry/{session_id}?mode=evaluation")

    resp = client.get(f"/quiz/{session_id}?mode=evaluation")

    assert resp.status_code == 200
    session = db.get_quiz(session_id)
    attempt = session["attempts"][-1]
    # 3 failed -> N = max(10, 6) = 10, not the full 12-question quiz
    assert len(attempt["question_ids"]) == 10
    assert "Question 1 of 10" in resp.text


def test_direct_navigation_to_evaluation_mode_without_retry_redirects_to_results(client):
    session_id = _session_with_quiz(client)

    resp = client.get(f"/quiz/{session_id}?mode=evaluation")

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/results/{session_id}"


def test_quiz_page_shows_attempt_number_and_mode_label(client):
    session_id = _session_with_quiz(client)

    # Attempt 1: practice
    client.get(f"/quiz/{session_id}?mode=practice")

    # Start an evaluation attempt (attempt 2) without finishing attempt 1 —
    # only the attempt count matters for this test.
    _set_failure_counts(session_id, {"q1": 1})
    client.post(f"/retry/{session_id}?mode=evaluation")
    quiz_resp = client.get(f"/quiz/{session_id}?mode=evaluation")

    assert "Attempt 2" in quiz_resp.text
    assert "Evaluation Mode" in quiz_resp.text


def test_full_evaluation_attempt_after_practice_failures_includes_failed_questions(client):
    session_id = _session_with_quiz(client)

    # Practice attempt: fail q1, q2, q3, q4, q5 (answer "True" is correct; give "False" for those)
    client.get(f"/quiz/{session_id}?mode=practice")
    wrong = {"q1": "False", "q2": "False", "q3": "False", "q4": "False", "q5": "False"}
    _complete_attempt(client, session_id, wrong)

    session = db.get_quiz(session_id)
    assert session["failure_counts"] == {"q1": 1, "q2": 1, "q3": 1, "q4": 1, "q5": 1}

    # Evaluation attempt should be heavily weighted toward those 5
    client.post(f"/retry/{session_id}?mode=evaluation")
    session = db.get_quiz(session_id)
    eval_attempt = session["attempts"][-1]
    assert eval_attempt["mode"] == "evaluation"
    assert {"q1", "q2", "q3", "q4", "q5"}.issubset(set(eval_attempt["question_ids"]))
    # 5 failed -> N = max(10, 10) = 10
    assert len(eval_attempt["question_ids"]) == 10

    # Complete the evaluation attempt too, answering correctly this time
    _complete_attempt(client, session_id, {qid: "True" for qid in eval_attempt["question_ids"]})
    session = db.get_quiz(session_id)
    assert session["attempts"][-1]["overall_score"] == 1.0


def test_running_two_evaluation_attempts_accumulates_failure_counts(client):
    session_id = _session_with_quiz(client)

    client.get(f"/quiz/{session_id}?mode=practice")
    _complete_attempt(client, session_id, {"q1": "False", "q2": "False"})
    session = db.get_quiz(session_id)
    assert session["failure_counts"] == {"q1": 1, "q2": 1}

    client.post(f"/retry/{session_id}?mode=evaluation")
    session = db.get_quiz(session_id)
    eval_ids_1 = session["attempts"][-1]["question_ids"]
    # Fail q1 again in evaluation attempt 1, get everything else right
    answers_1 = {qid: ("False" if qid == "q1" else "True") for qid in eval_ids_1}
    _complete_attempt(client, session_id, answers_1)
    session = db.get_quiz(session_id)
    assert session["failure_counts"]["q1"] == 2
    assert session["failure_counts"]["q2"] == 1

    client.post(f"/retry/{session_id}?mode=evaluation")
    session = db.get_quiz(session_id)
    eval_ids_2 = session["attempts"][-1]["question_ids"]
    answers_2 = {qid: "True" for qid in eval_ids_2}  # all correct this time
    _complete_attempt(client, session_id, answers_2)
    session = db.get_quiz(session_id)

    # Failure counts are cumulative and unaffected by a fully-correct attempt
    assert session["failure_counts"]["q1"] == 2
    assert session["failure_counts"]["q2"] == 1
