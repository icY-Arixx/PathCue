"""Single-process SQLite session persistence with atomic request-level updates."""

import json
import sqlite3
import threading
from pathlib import Path


class SessionStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL, owner_id TEXT)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            if "owner_id" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN owner_id TEXT")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def lock_for(self, session_id: str):
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.RLock())

    def get(self, session_id, owner_id=None):
        with self.connect() as db:
            if owner_id is None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            else:
                row = db.execute("SELECT state FROM sessions WHERE id=? AND owner_id=?", (session_id, owner_id)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, state, owner_id=None):
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions (id,state,owner_id) VALUES (?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, owner_id=COALESCE(excluded.owner_id,sessions.owner_id)",
                (state["session_id"], json.dumps(state, ensure_ascii=False), owner_id),
            )
