import pytest
from fastapi.testclient import TestClient

import db
from main import app


@pytest.fixture(autouse=True)
def clear_store():
    db.reset_db()
    yield
    db.reset_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def test_dashboard_shows_empty_state_with_no_quizzes(client):
    resp = client.get("/dashboard")

    assert resp.status_code == 200
    assert "haven't created any quizzes yet" in resp.text
    assert 'href="/"' in resp.text


def test_dashboard_lists_quizzes_with_preview_and_status(client):
    quiz = db.create_quiz("Photosynthesis converts light energy into chemical energy.")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "practice", "answers": {"q1": {}}, "overall_score": 0.9}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert resp.status_code == 200
    assert "Photosynthesis converts light energy" in resp.text
    assert "90% (Practice)" in resp.text


def test_dashboard_link_points_to_generating_when_quiz_not_ready(client):
    quiz = db.create_quiz("some source text")

    resp = client.get("/dashboard")

    assert f'href="/generating/{quiz["session_id"]}"' in resp.text
    assert "Generating…" in resp.text


def test_dashboard_link_points_to_quiz_when_never_attempted(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert f'href="/quiz/{quiz["session_id"]}"' in resp.text
    assert "Not yet attempted" in resp.text


def test_dashboard_link_points_to_quiz_with_mode_when_attempt_in_progress(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "evaluation", "answers": {}, "overall_score": None}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert f'href="/quiz/{quiz["session_id"]}?mode=evaluation"' in resp.text
    assert "Evaluation in progress" in resp.text


def test_dashboard_link_points_to_results_when_attempt_complete(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "practice", "answers": {"q1": {}}, "overall_score": 0.5}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert f'href="/results/{quiz["session_id"]}"' in resp.text


def test_dashboard_orders_quizzes_most_recent_first(client):
    db.create_quiz("older quiz text")
    import time

    time.sleep(0.01)
    db.create_quiz("newer quiz text")

    resp = client.get("/dashboard")

    older_pos = resp.text.find("older quiz text")
    newer_pos = resp.text.find("newer quiz text")
    assert 0 <= newer_pos < older_pos


def test_dashboard_click_through_resumes_practice_correctly(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [
        {
            "id": "q1",
            "concept": "X",
            "type": "true_false",
            "question": "Is this true?",
            "options": ["True", "False"],
            "correct_answer": "True",
            "explanation": "Because.",
        }
    ]
    db.save_quiz(quiz)

    dashboard_resp = client.get("/dashboard")
    assert f'href="/quiz/{quiz["session_id"]}"' in dashboard_resp.text

    quiz_resp = client.get(f"/quiz/{quiz['session_id']}")
    assert quiz_resp.status_code == 200
    assert "Is this true?" in quiz_resp.text


def test_nav_link_to_dashboard_present_on_landing_page(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert 'href="/dashboard"' in resp.text


def test_dashboard_card_has_delete_button(client):
    quiz = db.create_quiz("some source text")

    resp = client.get("/dashboard")

    assert f'id="quiz-card-{quiz["session_id"]}"' in resp.text
    assert f'hx-delete="/quiz/{quiz["session_id"]}"' in resp.text
    assert f'hx-target="#quiz-card-{quiz["session_id"]}"' in resp.text
    assert "hx-confirm=" in resp.text


def test_delete_quiz_route_removes_quiz_and_returns_empty_body(client):
    quiz = db.create_quiz("some source text")

    resp = client.delete(f"/quiz/{quiz['session_id']}")

    assert resp.status_code == 200
    assert resp.text == ""
    assert db.get_quiz(quiz["session_id"]) is None


def test_delete_quiz_route_is_idempotent_for_unknown_id(client):
    resp = client.delete("/quiz/does-not-exist")

    assert resp.status_code == 200


def test_deleted_quiz_no_longer_appears_on_dashboard(client):
    quiz = db.create_quiz("some source text")

    client.delete(f"/quiz/{quiz['session_id']}")
    resp = client.get("/dashboard")

    assert quiz["session_id"] not in resp.text


def test_dashboard_card_has_generate_more_and_regenerate_links_when_quiz_exists(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert f'href="/quiz/{quiz["session_id"]}/expand"' in resp.text
    assert f'href="/quiz/{quiz["session_id"]}/regenerate"' in resp.text
    assert "Generate more questions" in resp.text
    assert "Regenerate quiz" in resp.text
    assert "confirm(" in resp.text


def test_dashboard_card_omits_generate_more_and_regenerate_when_quiz_not_ready(client):
    db.create_quiz("some source text")  # no quiz generated yet

    resp = client.get("/dashboard")

    assert "/expand" not in resp.text
    assert "/regenerate" not in resp.text
