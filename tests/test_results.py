import pytest
from fastapi.testclient import TestClient

import db
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
        "type": "true_false",
        "question": "Mitosis produces two identical daughter cells.",
        "options": ["True", "False"],
        "correct_answer": "True",
        "explanation": "Mitosis produces two genetically identical daughter cells.",
    },
]


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


def _complete_quiz(client, session_id, answers: dict) -> None:
    client.get(f"/quiz/{session_id}")
    for question_id, user_answer in answers.items():
        client.post(f"/answer/{session_id}", data={"question_id": question_id, "user_answer": user_answer})


def test_results_page_renders_with_scores_after_completed_attempt(client):
    session_id = _session_with_quiz(client)
    # q1 correct, q2 correct, q3 wrong
    _complete_quiz(client, session_id, {"q1": "Chlorophyll", "q2": "False", "q3": "False"})

    resp = client.get(f"/results/{session_id}")

    assert resp.status_code == 200
    assert "67%" in resp.text  # 2/3 correct
    assert "Photosynthesis" in resp.text
    assert "Cell Division" in resp.text
    assert "100%" in resp.text  # Photosynthesis concept: 2/2 correct
    assert "0%" in resp.text  # Cell Division concept: 0/1 correct


def test_results_redirects_to_quiz_when_no_completed_attempt(client):
    session_id = _session_with_quiz(client)
    client.get(f"/quiz/{session_id}")  # attempt started but not finished

    resp = client.get(f"/results/{session_id}")

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/quiz/{session_id}?mode=practice"


def test_results_redirects_to_landing_for_unknown_session(client):
    resp = client.get("/results/does-not-exist")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_results_evaluation_mode_enabled_when_a_question_was_missed(client):
    session_id = _session_with_quiz(client)
    _complete_quiz(client, session_id, {"q1": "Chlorophyll", "q2": "False", "q3": "False"})

    resp = client.get(f"/results/{session_id}")

    assert f'action="/retry/{session_id}?mode=evaluation"' in resp.text


def test_results_evaluation_mode_disabled_when_all_correct(client):
    session_id = _session_with_quiz(client)
    _complete_quiz(client, session_id, {"q1": "Chlorophyll", "q2": "False", "q3": "True"})

    resp = client.get(f"/results/{session_id}")

    assert f'action="/retry/{session_id}?mode=evaluation"' not in resp.text
    assert "cursor-not-allowed" in resp.text


def test_failure_counts_updated_in_session_after_completed_attempt(client):
    session_id = _session_with_quiz(client)
    _complete_quiz(client, session_id, {"q1": "Chlorophyll", "q2": "False", "q3": "False"})

    session = db.get_quiz(session_id)
    assert session["failure_counts"] == {"q3": 1}


def test_failure_counts_accumulate_across_two_attempts(client):
    session_id = _session_with_quiz(client)
    _complete_quiz(client, session_id, {"q1": "wrong", "q2": "False", "q3": "False"})

    # Second attempt: q1 now correct, q3 wrong again
    _complete_quiz(client, session_id, {"q1": "Chlorophyll", "q2": "False", "q3": "False"})

    session = db.get_quiz(session_id)
    assert session["failure_counts"] == {"q1": 1, "q3": 2}
