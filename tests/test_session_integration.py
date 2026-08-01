import asyncio

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

import main
import session_store
from session_store import create_session, get_current_session, set_session_cookie


@pytest.fixture(autouse=True)
def clear_store():
    session_store._store.clear()
    yield
    session_store._store.clear()


def _build_cookie_test_app() -> FastAPI:
    """Minimal app exercising the get_current_session/set_session_cookie helpers,
    standing in for the real session-creating routes added in later epics."""
    app = FastAPI()

    @app.post("/test-session")
    async def create(response: Response):
        session = create_session()
        set_session_cookie(response, session["session_id"])
        return {"session_id": session["session_id"]}

    @app.get("/test-session")
    async def read(request: Request):
        session = get_current_session(request)
        if session is None:
            return {"found": False}
        return {"found": True, "session_id": session["session_id"]}

    return app


def test_cookie_set_on_creation_and_read_on_next_request():
    client = TestClient(_build_cookie_test_app())

    create_resp = client.post("/test-session")
    assert create_resp.status_code == 200
    assert "session_id" in create_resp.cookies

    session_id = create_resp.json()["session_id"]
    read_resp = client.get("/test-session")
    assert read_resp.json() == {"found": True, "session_id": session_id}


def test_missing_cookie_returns_no_session():
    client = TestClient(_build_cookie_test_app())

    resp = client.get("/test-session")

    assert resp.json() == {"found": False}


def test_cleanup_loop_purges_expired_sessions_periodically(monkeypatch):
    monkeypatch.setattr(main, "CLEANUP_INTERVAL_SECONDS", 0.01)
    calls = []
    monkeypatch.setattr(main, "purge_expired_sessions", lambda: calls.append(1))

    async def run():
        task = asyncio.create_task(main._cleanup_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())

    assert len(calls) >= 2


def test_app_lifespan_starts_and_stops_cleanly():
    with TestClient(main.app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
