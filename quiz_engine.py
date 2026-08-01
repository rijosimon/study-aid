import json
import logging
import os
import random
from typing import Optional

import anthropic

logger = logging.getLogger("study_aid")

MODEL = os.environ.get("QUIZ_MODEL", "claude-haiku-4-5")
MAX_TOKENS = 8000
GRADING_MAX_TOKENS = 300

GRADING_SYSTEM_PROMPT = (
    "You are grading a student's short-answer quiz response. Return valid JSON only."
)

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


def grade_choice(question: dict, user_answer: str) -> bool:
    """Grade a multiple_choice or true_false answer by exact (case/whitespace
    insensitive) match against the question's correct_answer."""
    correct_answer = (question.get("correct_answer") or "").strip().lower()
    given = (user_answer or "").strip().lower()
    return given == correct_answer


def grade_short_answer(
    question: str,
    correct_answer: str,
    user_answer: str,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    client = client or anthropic.Anthropic()
    prompt = (
        f"Question: {question}\n"
        f"Correct answer: {correct_answer}\n"
        f"User's answer: {user_answer}\n\n"
        'Grade the user\'s answer. Reply with JSON: {"passed": true|false, "feedback": "one sentence"}.'
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=GRADING_MAX_TOKENS,
        system=GRADING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(_strip_code_fence(raw_text))
    return {"passed": bool(data["passed"]), "feedback": str(data["feedback"])}


def calculate_scores(attempt: dict, quiz: list) -> dict:
    """Compute overall_score and concept_scores (both 0.0-1.0 fractions) from
    an attempt's answers, scoped to the questions actually answered in it."""
    answers = attempt.get("answers") or {}
    if not answers:
        return {"overall_score": 0.0, "concept_scores": {}}

    overall_score = sum(a["score"] for a in answers.values()) / len(answers)

    concept_lookup = {q["id"]: q["concept"] for q in quiz}
    concept_totals: dict[str, list] = {}
    for question_id, answer in answers.items():
        concept = concept_lookup.get(question_id, "Unknown")
        concept_totals.setdefault(concept, []).append(answer["score"])

    concept_scores = {
        concept: sum(scores) / len(scores) for concept, scores in concept_totals.items()
    }

    return {"overall_score": overall_score, "concept_scores": concept_scores}


def update_failure_counts(failure_counts: dict, attempt: dict) -> dict:
    """Increment failure_counts[question_id] by 1 for each wrong answer in
    the attempt. Cumulative across attempts — call once per completed attempt."""
    for question_id, answer in (attempt.get("answers") or {}).items():
        if not answer.get("correct"):
            failure_counts[question_id] = failure_counts.get(question_id, 0) + 1
    return failure_counts


def compute_weights(questions: list, failure_counts: dict) -> list:
    """weight = 1 + failures: never-failed questions get baseline weight 1,
    a question failed N times gets weight N+1."""
    return [1 + failure_counts.get(q["id"], 0) for q in questions]


def _weighted_sample_without_replacement(rng: random.Random, items: list, weights: list, k: int) -> list:
    """Efraimidis-Spirakis weighted sampling without replacement: each item
    gets a random key raised to 1/weight, and the top-k keys win."""
    keyed = [(rng.random() ** (1.0 / w), item) for item, w in zip(items, weights)]
    keyed.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in keyed[:k]]


def select_evaluation_questions(
    quiz: list, failure_counts: dict, seed: Optional[int] = None
) -> list:
    """Select N unique questions for an evaluation attempt, weighted toward
    previously-failed questions. Every question with at least one failure is
    guaranteed to be included; remaining slots are filled by weighted random
    sampling from the rest. N = min(total, max(10, failed_count * 2))."""
    total = len(quiz)
    failed_ids = {qid for qid, count in failure_counts.items() if count > 0}
    n = min(total, max(10, len(failed_ids) * 2))

    weights = compute_weights(quiz, failure_counts)
    rng = random.Random(seed)

    guaranteed = [q for q in quiz if q["id"] in failed_ids]
    remaining_pool = [(q, w) for q, w in zip(quiz, weights) if q["id"] not in failed_ids]

    slots_left = max(0, n - len(guaranteed))
    chosen: list = []
    if slots_left and remaining_pool:
        pool_questions, pool_weights = zip(*remaining_pool)
        k = min(slots_left, len(pool_questions))
        chosen = _weighted_sample_without_replacement(rng, list(pool_questions), list(pool_weights), k)

    selected = guaranteed + chosen
    rng.shuffle(selected)
    return selected
