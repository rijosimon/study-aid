import json
import logging
import os
from typing import Optional

import anthropic

logger = logging.getLogger("study_aid")

MODEL = os.environ.get("QUIZ_MODEL", "claude-opus-4-8")
MAX_TOKENS = 8000

SYSTEM_PROMPT = (
    "You are a study quiz generator. Given source material, produce a "
    "comprehensive set of questions that thoroughly covers the content. "
    "Tag each question with the concept it tests. Return valid JSON only."
)

REQUIRED_FIELDS = {"id", "concept", "type", "question", "correct_answer", "explanation"}
QUESTION_TYPES = {"multiple_choice", "true_false", "short_answer"}


class QuizGenerationError(RuntimeError):
    """Raised when Claude fails to produce a usable quiz after a retry."""


def _build_user_prompt(source_text: str) -> str:
    return (
        "Generate a quiz from the following study material. Return JSON matching "
        "this schema exactly:\n\n"
        '{"questions": [{"id": "q1", "concept": "string", '
        '"type": "multiple_choice | true_false | short_answer", '
        '"question": "string", "options": ["A", "B", "C", "D"], '
        '"correct_answer": "string", "explanation": "string"}]}\n\n'
        "Rules:\n"
        "- multiple_choice questions must have exactly 4 items in options\n"
        '- true_false questions must have exactly 2 items in options: ["True", "False"]\n'
        "- short_answer questions must omit the options field entirely\n"
        "- Cover all major concepts in the material thoroughly\n"
        "- Respond with JSON only, no other text\n\n"
        f"Study material:\n{source_text}"
    )


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`")
    if text.startswith("json"):
        text = text[4:]
    return text.strip()


def _parse_questions(raw_response: str) -> list[dict]:
    data = json.loads(_strip_code_fence(raw_response))
    questions = data["questions"]
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions must be a non-empty list")

    for q in questions:
        missing = REQUIRED_FIELDS - q.keys()
        if missing:
            raise ValueError(f"question {q.get('id')} missing fields: {missing}")
        if q["type"] not in QUESTION_TYPES:
            raise ValueError(f"question {q['id']} has invalid type: {q['type']}")

        options = q.get("options")
        if q["type"] == "multiple_choice":
            if not isinstance(options, list) or len(options) != 4:
                raise ValueError(f"question {q['id']} must have exactly 4 options")
        elif q["type"] == "true_false":
            if not isinstance(options, list) or len(options) != 2:
                raise ValueError(f"question {q['id']} must have exactly 2 options")
        elif q["type"] == "short_answer" and options is not None:
            raise ValueError(f"question {q['id']} must not have an options field")

    return questions


def generate_quiz(source_text: str, client: Optional[anthropic.Anthropic] = None) -> list[dict]:
    client = client or anthropic.Anthropic()
    user_prompt = _build_user_prompt(source_text)

    last_error: Optional[Exception] = None
    for attempt in range(2):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return _parse_questions(raw_text)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            last_error = exc
            logger.warning("Quiz generation attempt %d failed: %s", attempt + 1, exc)

    raise QuizGenerationError(f"Claude returned malformed quiz JSON after retry: {last_error}")
