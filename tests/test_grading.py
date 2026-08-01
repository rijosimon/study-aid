import json

import pytest

from quiz_engine import grade_choice, grade_short_answer
from tests.fake_claude import FakeAnthropicClient


def test_grade_choice_returns_true_for_correct_multiple_choice_option():
    question = {"type": "multiple_choice", "correct_answer": "Chlorophyll"}

    assert grade_choice(question, "Chlorophyll") is True


def test_grade_choice_returns_false_for_wrong_multiple_choice_option():
    question = {"type": "multiple_choice", "correct_answer": "Chlorophyll"}

    assert grade_choice(question, "Keratin") is False


@pytest.mark.parametrize("given", ["True", "true", "TRUE", "  true  "])
def test_grade_choice_true_false_is_case_and_whitespace_insensitive(given):
    question = {"type": "true_false", "correct_answer": "True"}

    assert grade_choice(question, given) is True


def test_grade_choice_true_false_rejects_wrong_value():
    question = {"type": "true_false", "correct_answer": "True"}

    assert grade_choice(question, "False") is False


def test_grade_short_answer_returns_passed_and_feedback():
    response = json.dumps({"passed": True, "feedback": "Correct — well explained."})
    client = FakeAnthropicClient(responses=[response])

    result = grade_short_answer("What is mitosis?", "Cell division", "It's how cells divide", client=client)

    assert result == {"passed": True, "feedback": "Correct — well explained."}


def test_grade_short_answer_returns_failed_grade():
    response = json.dumps({"passed": False, "feedback": "Missing key detail about chromosomes."})
    client = FakeAnthropicClient(responses=[response])

    result = grade_short_answer("What is mitosis?", "Cell division", "I don't know", client=client)

    assert result["passed"] is False
    assert "chromosomes" in result["feedback"]
