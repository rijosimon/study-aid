import json

import pytest

from quiz_engine import QuizGenerationError, generate_quiz
from tests.fake_claude import FakeAnthropicClient

VALID_QUIZ = json.dumps(
    {
        "questions": [
            {
                "id": "q1",
                "concept": "Photosynthesis",
                "type": "multiple_choice",
                "question": "What pigment absorbs light in photosynthesis?",
                "options": ["Chlorophyll", "Keratin", "Collagen", "Melanin"],
                "correct_answer": "Chlorophyll",
                "explanation": "Chlorophyll absorbs light energy for photosynthesis.",
            },
            {
                "id": "q2",
                "concept": "Photosynthesis",
                "type": "true_false",
                "question": "Photosynthesis occurs in the mitochondria.",
                "options": ["True", "False"],
                "correct_answer": "False",
                "explanation": "Photosynthesis occurs in the chloroplast, not the mitochondria.",
            },
            {
                "id": "q3",
                "concept": "Cell Division",
                "type": "short_answer",
                "question": "Name the phase of mitosis where chromosomes align at the cell's center.",
                "correct_answer": "Metaphase",
                "explanation": "Chromosomes align at the metaphase plate during metaphase.",
            },
        ]
    }
)


def test_generate_quiz_returns_questions_with_required_fields():
    client = FakeAnthropicClient(responses=[VALID_QUIZ])

    questions = generate_quiz("some source text", client=client)

    assert len(questions) == 3
    for q in questions:
        for field in ("id", "concept", "type", "question", "correct_answer"):
            assert field in q
    assert client.messages.calls == 1


def test_multiple_choice_questions_have_four_options():
    client = FakeAnthropicClient(responses=[VALID_QUIZ])

    questions = generate_quiz("some source text", client=client)

    mc = next(q for q in questions if q["type"] == "multiple_choice")
    assert len(mc["options"]) == 4


def test_short_answer_questions_have_no_options_field():
    client = FakeAnthropicClient(responses=[VALID_QUIZ])

    questions = generate_quiz("some source text", client=client)

    sa = next(q for q in questions if q["type"] == "short_answer")
    assert "options" not in sa


def test_generate_quiz_retries_once_on_malformed_json():
    client = FakeAnthropicClient(responses=["not json at all", VALID_QUIZ])

    questions = generate_quiz("some source text", client=client)

    assert len(questions) == 3
    assert client.messages.calls == 2


def test_generate_quiz_raises_after_second_malformed_response():
    client = FakeAnthropicClient(responses=["not json", "still not json"])

    with pytest.raises(QuizGenerationError):
        generate_quiz("some source text", client=client)

    assert client.messages.calls == 2


def test_generate_quiz_retries_on_missing_required_field():
    malformed = json.dumps({"questions": [{"id": "q1", "type": "short_answer"}]})
    client = FakeAnthropicClient(responses=[malformed, VALID_QUIZ])

    questions = generate_quiz("some source text", client=client)

    assert len(questions) == 3
    assert client.messages.calls == 2


def test_generate_quiz_strips_markdown_code_fences():
    fenced = f"```json\n{VALID_QUIZ}\n```"
    client = FakeAnthropicClient(responses=[fenced])

    questions = generate_quiz("some source text", client=client)

    assert len(questions) == 3
