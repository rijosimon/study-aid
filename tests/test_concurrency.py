"""Load test: 10 concurrent sessions generating quizzes simultaneously.

Also guards against the regression this project already hit once —
POST /generate calling generate_quiz() synchronously inside the async
route blocks the entire event loop for every session, not just the one
generating (see main.py's use of run_in_threadpool). If that ever
regresses, this test's wall-clock assertion catches it.
"""

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

import db
import main
from main import app

FAKE_QUIZ = [
    {
        "id": "q1",
        "concept": "Concept",
        "type": "true_false",
        "question": "Statement is true.",
        "options": ["True", "False"],
        "correct_answer": "True",
        "explanation": "Explanation.",
    }
]

GENERATION_DELAY = 0.2


@pytest.fixture(autouse=True)
def clear_store():
    db.reset_db()
    yield
    db.reset_db()


def test_ten_concurrent_sessions_generate_quizzes_simultaneously(monkeypatch):
    def fake_generate_quiz(text):
        time.sleep(GENERATION_DELAY)  # simulate a blocking Claude call
        return FAKE_QUIZ

    monkeypatch.setattr(main, "generate_quiz", fake_generate_quiz)

    async def run():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            session_ids = []
            for _ in range(10):
                resp = await client.post(
                    "/ingest", data={"text": "Some study material. " * 10}
                )
                session_ids.append(resp.headers["location"].removeprefix("/generating/"))

            start = time.monotonic()
            results = await asyncio.gather(
                *[client.post(f"/generate/{sid}") for sid in session_ids]
            )
            elapsed = time.monotonic() - start
            return results, elapsed

    results, elapsed = asyncio.run(run())

    assert all(r.status_code == 200 for r in results)
    assert all(r.headers.get("hx-redirect", "").startswith("/quiz/") for r in results)
    for sid in [r.headers["hx-redirect"].removeprefix("/quiz/") for r in results]:
        assert db.get_quiz(sid)["quiz"] == FAKE_QUIZ

    # If generation were serialized (blocking the event loop), 10 sessions at
    # GENERATION_DELAY each would take >= 10 * GENERATION_DELAY = 2s. Running
    # concurrently should stay close to a single GENERATION_DELAY.
    assert elapsed < GENERATION_DELAY * 5, f"generation took {elapsed:.2f}s — looks serialized"
