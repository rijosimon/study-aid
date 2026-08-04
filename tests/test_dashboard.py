import pytest
from fastapi.testclient import TestClient

import db
import main
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
    assert "Photosynthesis converts light…" in resp.text
    assert 'title="Practice — 90%"' in resp.text
    assert "90%" in resp.text


def test_dashboard_link_points_to_generating_when_quiz_not_ready(client):
    quiz = db.create_quiz("some source text")

    resp = client.get("/dashboard")

    assert f'href="/generating/{quiz["session_id"]}"' in resp.text
    assert 'title="Generating quiz…"' in resp.text


def test_dashboard_link_points_to_quiz_when_never_attempted(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert f'href="/quiz/{quiz["session_id"]}"' in resp.text
    assert 'title="Not yet attempted"' in resp.text


def test_dashboard_link_points_to_quiz_with_mode_when_attempt_in_progress(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "evaluation", "answers": {}, "overall_score": None}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert f'href="/quiz/{quiz["session_id"]}?mode=evaluation"' in resp.text
    assert 'title="Evaluation in progress"' in resp.text


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


# --- Epic 8: icon actions + legend ---


def test_dashboard_shows_legend_explaining_the_three_action_icons(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    # legend (1) + the card's icon button's aria-label and title (2) = 3
    assert resp.text.count("Generate more questions") == 3
    assert resp.text.count("Regenerate quiz") == 3
    assert resp.text.count("Delete quiz") == 3


def test_dashboard_legend_omitted_when_no_quizzes(client):
    resp = client.get("/dashboard")

    assert "Generate more questions" not in resp.text
    assert "Regenerate quiz" not in resp.text


def test_dashboard_card_action_icons_have_aria_labels(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert 'aria-label="Generate more questions"' in resp.text
    assert 'aria-label="Regenerate quiz"' in resp.text
    assert 'aria-label="Delete quiz"' in resp.text
    assert 'aria-label="Rename quiz"' in resp.text


# --- Epic 8: click-to-edit custom quiz names ---


def test_quiz_name_edit_route_returns_card_in_editing_state(client):
    quiz = db.create_quiz("some source text")

    resp = client.get(f"/quiz/{quiz['session_id']}/name/edit")

    assert resp.status_code == 200
    assert f'id="quiz-card-{quiz["session_id"]}"' in resp.text
    assert "<input" in resp.text
    assert f'hx-post="/quiz/{quiz["session_id"]}/name"' in resp.text


def test_quiz_name_edit_route_prefills_existing_name(client):
    quiz = db.create_quiz("some source text")
    db.rename_quiz(quiz["session_id"], "Existing name")

    resp = client.get(f"/quiz/{quiz['session_id']}/name/edit")

    assert 'value="Existing name"' in resp.text


def test_quiz_name_edit_route_redirects_to_landing_for_unknown_session(client):
    resp = client.get("/quiz/does-not-exist/name/edit")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_quiz_name_save_persists_and_returns_card_in_display_state(client):
    quiz = db.create_quiz("some source text")

    resp = client.post(f"/quiz/{quiz['session_id']}/name", data={"name": "My custom title"})

    assert resp.status_code == 200
    assert "<input" not in resp.text
    assert "My custom title" in resp.text
    assert db.get_quiz(quiz["session_id"])["name"] == "My custom title"


def test_quiz_name_save_with_empty_name_falls_back_to_preview(client):
    quiz = db.create_quiz("Photosynthesis converts light energy into chemical energy.")

    resp = client.post(f"/quiz/{quiz['session_id']}/name", data={"name": ""})

    assert resp.status_code == 200
    assert "Photosynthesis converts light…" in resp.text
    assert db.get_quiz(quiz["session_id"])["name"] is None


def test_quiz_name_save_redirects_to_landing_for_unknown_session(client):
    resp = client.post("/quiz/does-not-exist/name", data={"name": "x"})

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


def test_dashboard_reflects_a_previously_saved_name(client):
    quiz = db.create_quiz("some source text")
    client.post(f"/quiz/{quiz['session_id']}/name", data={"name": "My custom title"})

    resp = client.get("/dashboard")

    assert "My custom title" in resp.text


def test_quiz_card_partial_route_returns_display_state(client):
    quiz = db.create_quiz("some source text")
    db.rename_quiz(quiz["session_id"], "My custom title")

    resp = client.get(f"/quiz/{quiz['session_id']}/card")

    assert resp.status_code == 200
    assert "<input" not in resp.text
    assert "My custom title" in resp.text


def test_quiz_card_partial_route_redirects_to_landing_for_unknown_session(client):
    resp = client.get("/quiz/does-not-exist/card")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?flash=quiz_not_found"


# --- Epic 9: status dot states ---


def test_status_dot_completed_100_percent_is_green_with_no_percentage_text(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "practice", "answers": {"q1": {}}, "overall_score": 1.0}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert "bg-green-500" in resp.text
    assert 'title="Practice — 100%"' in resp.text  # full detail still in the tooltip
    # ...but no *visible* percentage span next to a green dot
    assert '<span class="text-sm font-medium text-gray-500">100%</span>' not in resp.text


def test_status_dot_completed_30_percent_is_red_with_percentage_text(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "practice", "answers": {"q1": {}}, "overall_score": 0.3}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert "bg-red-500" in resp.text
    assert 'title="Practice — 30%"' in resp.text
    assert "30%" in resp.text


def test_status_dot_completed_95_percent_is_also_red_not_a_middle_tier(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "practice", "answers": {"q1": {}}, "overall_score": 0.95}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert "bg-red-500" in resp.text
    assert "bg-amber-400" not in resp.text


def test_status_dot_in_progress_is_amber(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "practice", "answers": {}, "overall_score": None}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert "bg-amber-400" in resp.text


def test_status_dot_never_attempted_is_solid_grey_not_pulsing(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)

    resp = client.get("/dashboard")

    assert "bg-gray-300" in resp.text
    assert "animate-pulse" not in resp.text


def test_status_dot_not_yet_generated_pulses_grey(client):
    db.create_quiz("some source text")  # no quiz generated yet

    resp = client.get("/dashboard")

    assert "bg-gray-300" in resp.text
    assert "animate-pulse" in resp.text
    assert 'title="Generating quiz…"' in resp.text


# --- Epic 9: busy state (generate more / regenerate / initial generation in flight) ---


def test_status_dot_busy_with_existing_quiz_shows_updating_title(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "practice", "answers": {"q1": {}}, "overall_score": 1.0}]
    db.save_quiz(quiz)

    main._generating.add(quiz["session_id"])
    try:
        resp = client.get("/dashboard")
    finally:
        main._generating.discard(quiz["session_id"])

    assert "animate-pulse" in resp.text
    assert 'title="Updating quiz…"' in resp.text
    # busy takes priority — the completed-attempt green dot must not show through
    assert "bg-green-500" not in resp.text


def test_card_becomes_noninteractive_while_busy(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)
    session_id = quiz["session_id"]

    main._generating.add(session_id)
    try:
        resp = client.get("/dashboard")
    finally:
        main._generating.discard(session_id)

    assert f'href="/quiz/{session_id}"' not in resp.text
    assert f'href="/quiz/{session_id}/name/edit"' not in resp.text
    assert f'href="/quiz/{session_id}/expand"' not in resp.text
    assert f'href="/quiz/{session_id}/regenerate"' not in resp.text
    assert f'hx-delete="/quiz/{session_id}"' not in resp.text
    assert "disabled" in resp.text
    assert "cursor-not-allowed" in resp.text


def test_card_is_interactive_again_once_no_longer_busy(client):
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1"}]
    db.save_quiz(quiz)
    session_id = quiz["session_id"]

    main._generating.add(session_id)
    client.get("/dashboard")  # busy snapshot, not asserted here
    main._generating.discard(session_id)

    resp = client.get("/dashboard")

    assert f'href="/quiz/{session_id}"' in resp.text
    assert f'hx-delete="/quiz/{session_id}"' in resp.text
    assert "disabled" not in resp.text
