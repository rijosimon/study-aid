import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request, Response

SESSION_TTL_HOURS = 2

_store: dict[str, dict] = {}


def create_session() -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    session = {
        "session_id": session_id,
        "source_text": None,
        "quiz": None,
        "attempts": [],
        "failure_counts": {},
        "created_at": now,
        "expires_at": now + timedelta(hours=SESSION_TTL_HOURS),
    }
    _store[session_id] = session
    return session


def get_session(session_id: str) -> Optional[dict]:
    session = _store.get(session_id)
    if session is None:
        return None
    if datetime.now(timezone.utc) > session["expires_at"]:
        del _store[session_id]
        return None
    return session


def update_session(session_id: str, data: dict) -> Optional[dict]:
    session = get_session(session_id)
    if session is None:
        return None
    session.update(data)
    return session


def delete_session(session_id: str) -> None:
    _store.pop(session_id, None)


def purge_expired_sessions() -> int:
    now = datetime.now(timezone.utc)
    expired = [sid for sid, s in list(_store.items()) if now > s["expires_at"]]
    for sid in expired:
        del _store[sid]
    return len(expired)


# --- FastAPI helpers ---

def get_current_session(request: Request) -> Optional[dict]:
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None
    return get_session(session_id)


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_HOURS * 3600,
    )
