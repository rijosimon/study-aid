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
