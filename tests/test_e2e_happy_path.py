"""S8.5: full end-to-end happy path — ingest PDF -> generate quiz ->
practice mode -> results -> evaluation mode -> results — all driven
through the real HTTP routes with Claude calls mocked out."""

import pytest
from fastapi.testclient import TestClient

import main
import session_store
from main import app
from tests.pdf_fixtures import build_pdf_with_text

FAKE_QUIZ = [
    {
        "id": f"q{i}",
        "concept": f"Concept {i % 3}",
        "type": "true_false",
        "question": f"Statement {i} is true.",
        "options": ["True", "False"],
        "correct_answer": "True",
        "explanation": f"Explanation {i}.",
    }
    for i in range(1, 10)
] + [
    {
        "id": "q10",
        "concept": "Concept 0",
        "type": "short_answer",
        "question": "Explain the main idea in one sentence.",
        "correct_answer": "The main idea",
        "explanation": "Should mention the core concept.",
    }
]


@pytest.fixture(autouse=True)
def clear_store():
    session_store._store.clear()
    yield
    session_store._store.clear()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def test_full_happy_path_pdf_to_evaluation_results(client, monkeypatch):
    monkeypatch.setattr(main, "generate_quiz", lambda text: FAKE_QUIZ)
    monkeypatch.setattr(
        main, "grade_short_answer", lambda q, c, u: {"passed": True, "feedback": "Good summary."}
    )

    # 1. Ingest a real PDF
    pdf_bytes = build_pdf_with_text(
        "Photosynthesis converts light energy into chemical energy. " * 5
    )
    resp = client.post(
        "/ingest", files={"file": ("notes.pdf", pdf_bytes, "application/pdf")}
    )
    assert resp.status_code == 303
    session_id = resp.headers["location"].removeprefix("/generating/")
    client.cookies.set("session_id", session_id)

    # 2. Generating page renders and polling triggers real generation (mocked)
    resp = client.get(f"/generating/{session_id}")
    assert resp.status_code == 200
    resp = client.post(f"/generate/{session_id}")
    assert resp.status_code == 200
    assert resp.headers["hx-redirect"] == f"/quiz/{session_id}"

    session = session_store.get_session(session_id)
    assert session["quiz"] == FAKE_QUIZ

    # 3. Practice mode: answer every question, one wrong on purpose (q2)
    resp = client.get(f"/quiz/{session_id}?mode=practice")
    assert resp.status_code == 200
    assert "Attempt 1" in resp.text

    for qid in [q["id"] for q in FAKE_QUIZ]:
        answer = "False" if qid == "q2" else ("True" if qid != "q10" else "a full sentence")
        resp = client.post(f"/answer/{session_id}", data={"question_id": qid, "user_answer": answer})

    assert resp.headers["hx-redirect"] == f"/results/{session_id}"

    # 4. Results page after practice
    resp = client.get(f"/results/{session_id}")
    assert resp.status_code == 200
    assert "90%" in resp.text  # 9/10 correct
    session = session_store.get_session(session_id)
    assert session["failure_counts"] == {"q2": 1}

    # 5. Switch to evaluation mode
    resp = client.post(f"/retry/{session_id}?mode=evaluation")
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/quiz/{session_id}?mode=evaluation"

    resp = client.get(f"/quiz/{session_id}?mode=evaluation")
    assert resp.status_code == 200
    assert "Attempt 2" in resp.text
    assert "Evaluation Mode" in resp.text

    eval_attempt = session["attempts"][-1]
    assert "q2" in eval_attempt["question_ids"]  # previously-failed question guaranteed present

    # 6. Complete evaluation attempt, answering everything correctly this time
    for qid in eval_attempt["question_ids"]:
        answer = "True" if qid != "q10" else "a full sentence"
        resp = client.post(f"/answer/{session_id}", data={"question_id": qid, "user_answer": answer})

    assert resp.headers["hx-redirect"] == f"/results/{session_id}"

    # 7. Results after evaluation attempt: perfect score, failure_counts unchanged (no new fails)
    resp = client.get(f"/results/{session_id}")
    assert resp.status_code == 200
    assert "100%" in resp.text
    session = session_store.get_session(session_id)
    assert session["failure_counts"] == {"q2": 1}
    assert len(session["attempts"]) == 2
