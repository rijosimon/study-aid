from quiz_engine import compute_weights, select_evaluation_questions


def _quiz(n: int) -> list:
    return [{"id": f"q{i}", "concept": "Concept"} for i in range(1, n + 1)]


def test_compute_weights_returns_1_for_never_failed_questions():
    quiz = _quiz(3)

    weights = compute_weights(quiz, {})

    assert weights == [1, 1, 1]


def test_compute_weights_returns_n_plus_1_for_n_failures():
    quiz = _quiz(3)

    weights = compute_weights(quiz, {"q1": 1, "q2": 4})

    assert weights == [2, 5, 1]


def test_select_evaluation_questions_always_includes_all_failed_questions():
    quiz = _quiz(20)
    failure_counts = {"q3": 1, "q7": 2, "q15": 5}

    selected = select_evaluation_questions(quiz, failure_counts, seed=0)
    selected_ids = {q["id"] for q in selected}

    assert {"q3", "q7", "q15"}.issubset(selected_ids)


def test_n_calculation_doubles_failed_count_when_above_floor():
    quiz = _quiz(30)
    failure_counts = {f"q{i}": 1 for i in range(1, 11)}  # 10 failed

    selected = select_evaluation_questions(quiz, failure_counts, seed=0)

    assert len(selected) == 20


def test_n_calculation_uses_minimum_floor_of_10():
    quiz = _quiz(30)
    failure_counts = {f"q{i}": 1 for i in range(1, 4)}  # 3 failed

    selected = select_evaluation_questions(quiz, failure_counts, seed=0)

    assert len(selected) == 10


def test_n_calculation_caps_at_total_question_count():
    quiz = _quiz(50)
    failure_counts = {f"q{i}": 1 for i in range(1, 31)}  # 30 failed -> would be 60

    selected = select_evaluation_questions(quiz, failure_counts, seed=0)

    assert len(selected) == 50
    # All questions selected, no duplicates
    assert len({q["id"] for q in selected}) == 50


def test_select_evaluation_questions_is_deterministic_given_same_seed():
    quiz = _quiz(30)
    failure_counts = {f"q{i}": 1 for i in range(1, 6)}

    first = select_evaluation_questions(quiz, failure_counts, seed=123)
    second = select_evaluation_questions(quiz, failure_counts, seed=123)

    assert [q["id"] for q in first] == [q["id"] for q in second]


def test_select_evaluation_questions_no_failures_falls_back_to_floor_of_10():
    quiz = _quiz(30)

    selected = select_evaluation_questions(quiz, {}, seed=0)

    assert len(selected) == 10
    assert len({q["id"] for q in selected}) == 10
