import pytest

from quiz_engine import calculate_scores, update_failure_counts

QUIZ = [
    {"id": "q1", "concept": "Photosynthesis"},
    {"id": "q2", "concept": "Photosynthesis"},
    {"id": "q3", "concept": "Cell Division"},
]


def _attempt(answers: dict) -> dict:
    return {"mode": "practice", "answers": answers, "overall_score": None, "concept_scores": None}


def test_calculate_scores_returns_100_percent_when_all_correct():
    attempt = _attempt(
        {
            "q1": {"user_answer": "a", "correct": True, "score": 1.0},
            "q2": {"user_answer": "b", "correct": True, "score": 1.0},
            "q3": {"user_answer": "c", "correct": True, "score": 1.0},
        }
    )

    scores = calculate_scores(attempt, QUIZ)

    assert scores["overall_score"] == 1.0
    assert scores["concept_scores"]["Photosynthesis"] == 1.0
    assert scores["concept_scores"]["Cell Division"] == 1.0


def test_calculate_scores_returns_0_percent_for_concept_all_wrong():
    attempt = _attempt(
        {
            "q1": {"user_answer": "a", "correct": True, "score": 1.0},
            "q2": {"user_answer": "b", "correct": True, "score": 1.0},
            "q3": {"user_answer": "c", "correct": False, "score": 0.0},
        }
    )

    scores = calculate_scores(attempt, QUIZ)

    assert scores["concept_scores"]["Cell Division"] == 0.0
    assert scores["concept_scores"]["Photosynthesis"] == 1.0


def test_calculate_scores_averages_within_a_concept():
    attempt = _attempt(
        {
            "q1": {"user_answer": "a", "correct": True, "score": 1.0},
            "q2": {"user_answer": "b", "correct": False, "score": 0.0},
            "q3": {"user_answer": "c", "correct": True, "score": 1.0},
        }
    )

    scores = calculate_scores(attempt, QUIZ)

    assert scores["concept_scores"]["Photosynthesis"] == 0.5
    assert scores["overall_score"] == pytest.approx(2 / 3)


def test_calculate_scores_with_no_answers_returns_zero():
    attempt = _attempt({})

    scores = calculate_scores(attempt, QUIZ)

    assert scores == {"overall_score": 0.0, "concept_scores": {}}


def test_update_failure_counts_increments_for_wrong_answers_only():
    failure_counts = {}
    attempt = _attempt(
        {
            "q1": {"user_answer": "a", "correct": True, "score": 1.0},
            "q2": {"user_answer": "b", "correct": False, "score": 0.0},
        }
    )

    update_failure_counts(failure_counts, attempt)

    assert failure_counts == {"q2": 1}


def test_update_failure_counts_accumulates_across_two_attempts():
    failure_counts = {}
    attempt_1 = _attempt(
        {
            "q1": {"user_answer": "a", "correct": False, "score": 0.0},
            "q2": {"user_answer": "b", "correct": True, "score": 1.0},
        }
    )
    attempt_2 = _attempt(
        {
            "q1": {"user_answer": "a", "correct": False, "score": 0.0},
            "q2": {"user_answer": "b", "correct": False, "score": 0.0},
        }
    )

    update_failure_counts(failure_counts, attempt_1)
    update_failure_counts(failure_counts, attempt_2)

    assert failure_counts == {"q1": 2, "q2": 1}
