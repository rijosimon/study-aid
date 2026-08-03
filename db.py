import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

_connection: Optional[sqlite3.Connection] = None


def _hash_content(text: str) -> str:
    """SHA-256 hex digest of the (post-truncation) source text, used to
    detect duplicate uploads."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def init_db(path: Optional[str] = None) -> None:
    """(Re)initialize the database connection and ensure the schema exists.
    Call once at app startup. Tests call this with a throwaway path to get
    an isolated database."""
    global _connection
    if path is None:
        path = os.environ.get("DB_PATH", "study_aid.db")
    _connection = sqlite3.connect(path, check_same_thread=False)
    _connection.row_factory = sqlite3.Row
    _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS quizzes (
            id TEXT PRIMARY KEY,
            source_text TEXT,
            truncated INTEGER NOT NULL DEFAULT 0,
            quiz TEXT,
            attempts TEXT NOT NULL,
            failure_counts TEXT NOT NULL,
            created_at TEXT NOT NULL,
            content_hash TEXT
        )
        """
    )
    _connection.commit()

    # CREATE TABLE IF NOT EXISTS won't add columns to a database file that
    # already existed before content_hash was introduced — migrate it in
    # place and backfill hashes for any pre-existing rows.
    columns = {row["name"] for row in _connection.execute("PRAGMA table_info(quizzes)")}
    if "content_hash" not in columns:
        _connection.execute("ALTER TABLE quizzes ADD COLUMN content_hash TEXT")
        _connection.commit()
        rows = _connection.execute("SELECT id, source_text FROM quizzes").fetchall()
        for row in rows:
            _connection.execute(
                "UPDATE quizzes SET content_hash = ? WHERE id = ?",
                (_hash_content(row["source_text"] or ""), row["id"]),
            )
        _connection.commit()


def _conn() -> sqlite3.Connection:
    if _connection is None:
        init_db()
    return _connection


def _row_to_quiz(row: sqlite3.Row) -> dict:
    return {
        "session_id": row["id"],
        "source_text": row["source_text"],
        "truncated": bool(row["truncated"]),
        "quiz": json.loads(row["quiz"]) if row["quiz"] is not None else None,
        "attempts": json.loads(row["attempts"]),
        "failure_counts": json.loads(row["failure_counts"]),
        "created_at": row["created_at"],
    }


def create_quiz(source_text: str, truncated: bool = False) -> dict:
    conn = _conn()
    quiz_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    content_hash = _hash_content(source_text)
    conn.execute(
        "INSERT INTO quizzes (id, source_text, truncated, quiz, attempts, failure_counts, created_at, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (quiz_id, source_text, int(truncated), None, "[]", "{}", created_at, content_hash),
    )
    conn.commit()
    return get_quiz(quiz_id)


def get_quiz(quiz_id: str) -> Optional[dict]:
    conn = _conn()
    row = conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    if row is None:
        return None
    return _row_to_quiz(row)


def find_quiz_by_content_hash(content_hash: str) -> Optional[dict]:
    """Look up an existing quiz by the hash of its source text, used at
    ingest time to detect duplicate uploads. Returns None if no quiz has
    that content hash."""
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM quizzes WHERE content_hash = ? ORDER BY created_at DESC LIMIT 1",
        (content_hash,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_quiz(row)


def save_quiz(quiz: dict) -> None:
    """Persist the current state of a quiz dict (as returned by create_quiz
    or get_quiz, after in-place mutation) back to the database."""
    conn = _conn()
    conn.execute(
        "UPDATE quizzes SET source_text = ?, truncated = ?, quiz = ?, attempts = ?, "
        "failure_counts = ? WHERE id = ?",
        (
            quiz["source_text"],
            int(quiz.get("truncated", False)),
            json.dumps(quiz["quiz"]) if quiz.get("quiz") is not None else None,
            json.dumps(quiz.get("attempts", [])),
            json.dumps(quiz.get("failure_counts", {})),
            quiz["session_id"],
        ),
    )
    conn.commit()


def delete_quiz(quiz_id: str) -> None:
    conn = _conn()
    conn.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
    conn.commit()


def reset_db() -> None:
    """Test helper: wipe all rows without dropping the table."""
    conn = _conn()
    conn.execute("DELETE FROM quizzes")
    conn.commit()


PREVIEW_LENGTH = 120


def list_quizzes() -> list:
    """Lightweight summaries for the dashboard, most-recent-first. Deliberately
    doesn't return full question/attempt payloads — just enough to render a
    card and link into the quiz."""
    conn = _conn()
    rows = conn.execute("SELECT * FROM quizzes ORDER BY created_at DESC").fetchall()

    summaries = []
    for row in rows:
        quiz = _row_to_quiz(row)
        attempts = quiz["attempts"]
        latest = attempts[-1] if attempts else None

        source_text = (quiz["source_text"] or "").strip()
        preview = source_text[:PREVIEW_LENGTH]
        if len(source_text) > PREVIEW_LENGTH:
            preview += "…"

        summaries.append(
            {
                "session_id": quiz["session_id"],
                "preview": preview or "(no preview available)",
                "created_at": quiz["created_at"],
                "has_quiz": quiz["quiz"] is not None,
                "question_count": len(quiz["quiz"]) if quiz["quiz"] else 0,
                "latest_mode": latest["mode"] if latest else None,
                "latest_score": latest.get("overall_score") if latest else None,
                "latest_in_progress": bool(latest and latest.get("overall_score") is None),
            }
        )
    return summaries
