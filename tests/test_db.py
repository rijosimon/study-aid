import time

import pytest

import db


@pytest.fixture(autouse=True)
def clear_store():
    db.reset_db()
    yield
    db.reset_db()


def test_create_quiz_returns_a_fresh_row():
    quiz = db.create_quiz("some source text")

    assert quiz["source_text"] == "some source text"
    assert quiz["truncated"] is False
    assert quiz["quiz"] is None
    assert quiz["attempts"] == []
    assert quiz["failure_counts"] == {}
    assert quiz["created_at"]  # non-empty ISO timestamp
    assert quiz["session_id"]  # a generated id


def test_create_quiz_accepts_truncated_flag():
    quiz = db.create_quiz("some source text", truncated=True)

    assert quiz["truncated"] is True


def test_get_quiz_returns_none_for_unknown_id():
    assert db.get_quiz("does-not-exist") is None


def test_save_quiz_persists_mutations_and_get_quiz_reads_them_back():
    quiz = db.create_quiz("some source text")
    quiz["quiz"] = [{"id": "q1", "concept": "X"}]
    quiz["attempts"].append({"mode": "practice", "answers": {}})
    quiz["failure_counts"]["q1"] = 2
    quiz["truncated"] = True

    db.save_quiz(quiz)

    reloaded = db.get_quiz(quiz["session_id"])
    assert reloaded["quiz"] == [{"id": "q1", "concept": "X"}]
    assert reloaded["attempts"] == [{"mode": "practice", "answers": {}}]
    assert reloaded["failure_counts"] == {"q1": 2}
    assert reloaded["truncated"] is True


def test_get_quiz_returns_a_fresh_dict_each_time_not_a_shared_reference():
    quiz = db.create_quiz("some source text")

    a = db.get_quiz(quiz["session_id"])
    b = db.get_quiz(quiz["session_id"])
    a["quiz"] = [{"id": "q1"}]  # mutate one copy only, don't save

    assert b["quiz"] is None  # unaffected by mutating `a`


def test_reset_db_clears_all_rows():
    quiz = db.create_quiz("one")

    db.reset_db()

    assert db.get_quiz(quiz["session_id"]) is None


def test_delete_quiz_removes_only_the_targeted_quiz():
    keep = db.create_quiz("keep me")
    remove = db.create_quiz("remove me")

    db.delete_quiz(remove["session_id"])

    assert db.get_quiz(remove["session_id"]) is None
    assert db.get_quiz(keep["session_id"]) is not None


def test_delete_quiz_is_a_no_op_for_unknown_id():
    db.delete_quiz("does-not-exist")  # should not raise


def test_list_quizzes_orders_most_recent_first():
    first = db.create_quiz("first")
    time.sleep(0.01)
    second = db.create_quiz("second")
    time.sleep(0.01)
    third = db.create_quiz("third")

    ids = [s["session_id"] for s in db.list_quizzes()]

    assert ids == [third["session_id"], second["session_id"], first["session_id"]]


def test_list_quizzes_empty_when_no_quizzes():
    assert db.list_quizzes() == []


def test_list_quizzes_truncates_long_preview():
    long_text = "word " * 100
    db.create_quiz(long_text)

    summary = db.list_quizzes()[0]

    assert len(summary["preview"]) == db.PREVIEW_LENGTH + 1  # + the ellipsis char
    assert summary["preview"].endswith("…")


def test_list_quizzes_reflects_generation_state():
    quiz = db.create_quiz("some text")

    summary = db.list_quizzes()[0]
    assert summary["has_quiz"] is False
    assert summary["question_count"] == 0
    assert summary["latest_mode"] is None
    assert summary["latest_score"] is None
    assert summary["latest_in_progress"] is False

    quiz["quiz"] = [{"id": "q1"}, {"id": "q2"}]
    db.save_quiz(quiz)

    summary = db.list_quizzes()[0]
    assert summary["has_quiz"] is True
    assert summary["question_count"] == 2


def test_list_quizzes_reflects_in_progress_attempt():
    quiz = db.create_quiz("some text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "practice", "answers": {}, "overall_score": None}]
    db.save_quiz(quiz)

    summary = db.list_quizzes()[0]

    assert summary["latest_mode"] == "practice"
    assert summary["latest_score"] is None
    assert summary["latest_in_progress"] is True


def test_list_quizzes_reflects_completed_attempt():
    quiz = db.create_quiz("some text")
    quiz["quiz"] = [{"id": "q1"}]
    quiz["attempts"] = [{"mode": "evaluation", "answers": {"q1": {}}, "overall_score": 0.75}]
    db.save_quiz(quiz)

    summary = db.list_quizzes()[0]

    assert summary["latest_mode"] == "evaluation"
    assert summary["latest_score"] == 0.75
    assert summary["latest_in_progress"] is False


def test_hash_content_is_deterministic_for_the_same_text():
    assert db._hash_content("some source text") == db._hash_content("some source text")


def test_hash_content_differs_for_different_text():
    assert db._hash_content("some source text") != db._hash_content("other source text")


def test_find_quiz_by_content_hash_returns_none_when_no_match():
    db.create_quiz("some source text")

    assert db.find_quiz_by_content_hash(db._hash_content("unrelated text")) is None


def test_find_quiz_by_content_hash_returns_the_matching_row():
    quiz = db.create_quiz("some source text")

    found = db.find_quiz_by_content_hash(db._hash_content("some source text"))

    assert found is not None
    assert found["session_id"] == quiz["session_id"]
    assert found["source_text"] == "some source text"
