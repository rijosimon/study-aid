import uuid
from datetime import datetime, timedelta, timezone

import pytest

import session_store
from session_store import (
    create_session,
    delete_session,
    get_session,
    purge_expired_sessions,
    update_session,
)


@pytest.fixture(autouse=True)
def clear_store():
    session_store._store.clear()
    yield
    session_store._store.clear()


def test_create_session_returns_valid_uuid_and_stores_data():
    session = create_session()
    uuid.UUID(session["session_id"])  # raises ValueError if not a valid UUID
    assert session_store._store[session["session_id"]] is session


def test_get_session_returns_none_for_unknown_id():
    assert get_session("does-not-exist") is None


def test_get_session_returns_none_past_ttl():
    session = create_session()
    session["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert get_session(session["session_id"]) is None
    assert session["session_id"] not in session_store._store


def test_update_session_merges_without_overwriting_unrelated_keys():
    session_id = create_session()["session_id"]

    updated = update_session(session_id, {"source_text": "hello world"})

    assert updated["source_text"] == "hello world"
    assert updated["quiz"] is None
    assert updated["failure_counts"] == {}
    assert updated["attempts"] == []


def test_update_session_returns_none_for_unknown_session():
    assert update_session("does-not-exist", {"source_text": "x"}) is None


def test_delete_session_removes_it():
    session_id = create_session()["session_id"]

    delete_session(session_id)

    assert get_session(session_id) is None


def test_delete_session_is_a_noop_for_unknown_id():
    delete_session("does-not-exist")  # should not raise


def test_purge_expired_sessions_removes_only_expired():
    fresh = create_session()
    expired = create_session()
    expired["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    removed = purge_expired_sessions()

    assert removed == 1
    assert fresh["session_id"] in session_store._store
    assert expired["session_id"] not in session_store._store
