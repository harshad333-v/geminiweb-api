"""SQLite persistence for user-scoped conversation sessions."""
import json
from contextlib import closing
import os
import sqlite3
import time
import uuid
from pathlib import Path


def _database_path():
    return Path(os.environ.get("GEMINI_WEB2API_DB_PATH", "./data/gemini-web2api.sqlite3"))


def _connect():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _initialize(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            title TEXT NOT NULL,
            messages_json TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS sessions_owner_updated ON sessions(owner_id, updated_at DESC)")
    connection.commit()


def create_session(owner_id, title="New conversation"):
    now = int(time.time())
    session_id = "sess_" + uuid.uuid4().hex
    with closing(_connect()) as connection:
        _initialize(connection)
        connection.execute(
            "INSERT INTO sessions(id, owner_id, title, messages_json, created_at, updated_at) VALUES (?, ?, ?, '[]', ?, ?)",
            (session_id, owner_id, str(title or "New conversation")[:160], now, now),
        )
        connection.commit()
    return {"id": session_id, "object": "session", "title": str(title or "New conversation")[:160],
            "messages": [], "created_at": now, "updated_at": now}


def list_sessions(owner_id):
    with closing(_connect()) as connection:
        _initialize(connection)
        rows = connection.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE owner_id=? ORDER BY updated_at DESC",
            (owner_id,),
        ).fetchall()
    return [{"id": row["id"], "object": "session", "title": row["title"],
             "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]


def get_session(owner_id, session_id):
    with closing(_connect()) as connection:
        _initialize(connection)
        row = connection.execute(
            "SELECT id, title, messages_json, created_at, updated_at FROM sessions WHERE id=? AND owner_id=?",
            (session_id, owner_id),
        ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "object": "session", "title": row["title"],
            "messages": json.loads(row["messages_json"]), "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def append_messages(owner_id, session_id, messages):
    with closing(_connect()) as connection:
        _initialize(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT messages_json FROM sessions WHERE id=? AND owner_id=?",
            (session_id, owner_id),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        existing = json.loads(row["messages_json"])
        existing.extend(messages)
        connection.execute(
            "UPDATE sessions SET messages_json=?, updated_at=? WHERE id=? AND owner_id=?",
            (json.dumps(existing, ensure_ascii=False), int(time.time()), session_id, owner_id),
        )
        connection.commit()
    return True


def delete_session(owner_id, session_id):
    with closing(_connect()) as connection:
        _initialize(connection)
        cursor = connection.execute("DELETE FROM sessions WHERE id=? AND owner_id=?", (session_id, owner_id))
        deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
