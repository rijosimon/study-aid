"""Unit tests for main._quiz_destination — the shared routing decision used
by both /dashboard (linking to existing quizzes) and /ingest (redirecting
duplicate uploads straight to wherever the user left off)."""

from main import _quiz_destination


def test_quiz_destination_points_to_generating_when_quiz_not_ready():
    summary = {
        "session_id": "abc123",
        "has_quiz": False,
        "latest_mode": None,
        "latest_in_progress": False,
    }

    assert _quiz_destination(summary) == "/generating/abc123"


def test_quiz_destination_points_to_quiz_when_never_attempted():
    summary = {
        "session_id": "abc123",
        "has_quiz": True,
        "latest_mode": None,
        "latest_in_progress": False,
    }

    assert _quiz_destination(summary) == "/quiz/abc123"


def test_quiz_destination_points_to_quiz_with_mode_when_attempt_in_progress():
    summary = {
        "session_id": "abc123",
        "has_quiz": True,
        "latest_mode": "evaluation",
        "latest_in_progress": True,
    }

    assert _quiz_destination(summary) == "/quiz/abc123?mode=evaluation"


def test_quiz_destination_points_to_results_when_attempt_complete():
    summary = {
        "session_id": "abc123",
        "has_quiz": True,
        "latest_mode": "practice",
        "latest_in_progress": False,
    }

    assert _quiz_destination(summary) == "/results/abc123"
