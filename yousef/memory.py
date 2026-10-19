"""Persistent memory in a local SQLite file.

Holds tasks and their steps, the conversation, and "lessons":
- rule:    a standing instruction the user taught (always given to agents)
- success: a task the user marked as worked, with their note
- failure: a task the user marked as failed, with their note
Lessons relevant to a new request are given to the planner and agents. This is
how YOUSEF AI learns: from remembered outcomes, not from retraining the model.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
    id INTEGER PRIMARY KEY,
    request TEXT NOT NULL,
    status TEXT NOT NULL,
    final TEXT,
    error TEXT,
    feedback INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS steps(
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    agent TEXT NOT NULL,
    instruction TEXT NOT NULL,
    needs_confirmation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    output TEXT,
    provider TEXT,
    model TEXT,
    error TEXT,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS steps_task ON steps(task_id, idx);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    task_id INTEGER,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons(
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    task_id INTEGER,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

ACTIVE_STATUSES = ("planning", "running", "waiting")
LESSON_KINDS = ("rule", "success", "failure")

_TASK_FIELDS = {"status", "final", "error", "feedback"}
_STEP_FIELDS = {"status", "output", "provider", "model", "error", "started_at", "finished_at"}

_DIACRITICS = re.compile(r"[ً-ْـ]")
_TOKEN = re.compile(r"\w{3,}")


def normalize(text: str) -> str:
    text = _DIACRITICS.sub("", text.lower())
    return (
        text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        .replace("ة", "ه").replace("ى", "ي")
    )


def tokens(text: str) -> set[str]:
    out = set()
    for tok in _TOKEN.findall(normalize(text)):
        if tok.startswith("ال") and len(tok) > 4:
            tok = tok[2:]
        out.add(tok)
    return out


class Memory:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- tasks -------------------------------------------------------------
    def create_task(self, request: str) -> int:
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO tasks(request, status, created_at, updated_at) VALUES (?, 'planning', ?, ?)",
                (request, now, now),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def update_task(self, task_id: int, **fields) -> None:
        unknown = set(fields) - _TASK_FIELDS
        if unknown:
            raise ValueError(f"unknown task fields: {unknown}")
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))
            self._db.commit()

    def get_task(self, task_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            steps = self._db.execute(
                "SELECT * FROM steps WHERE task_id = ? ORDER BY idx", (task_id,)
            ).fetchall()
        task = dict(row)
        task["steps"] = [self._step_dict(s) for s in steps]
        return task

    @staticmethod
    def _step_dict(row) -> dict:
        d = dict(row)
        d["needs_confirmation"] = bool(d["needs_confirmation"])
        return d

    def list_tasks(self, limit: int = 50, since: float | None = None) -> list[dict]:
        since = self.conversation_start() if since is None else since
        with self._lock:
            ids = [
                r["id"]
                for r in self._db.execute(
                    "SELECT id FROM tasks WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                    (since, max(1, min(limit, 200))),
                ).fetchall()
            ]
        return [t for t in (self.get_task(i) for i in reversed(ids)) if t]

    def mark_interrupted(self) -> int:
        """Called at startup: tasks that were running when the app closed."""
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                f"UPDATE tasks SET status = 'interrupted', updated_at = ? "
                f"WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
                (now, *ACTIVE_STATUSES),
            )
            self._db.execute(
                "UPDATE steps SET status = 'skipped' WHERE status IN ('pending', 'running', 'awaiting_approval') "
                "AND task_id IN (SELECT id FROM tasks WHERE status = 'interrupted')"
            )
            self._db.commit()
            return cur.rowcount

    # ---- steps -------------------------------------------------------------
    def add_steps(self, task_id: int, steps: list[dict]) -> list[int]:
        ids = []
        with self._lock:
            for i, step in enumerate(steps):
                cur = self._db.execute(
                    "INSERT INTO steps(task_id, idx, agent, instruction, needs_confirmation, status) "
                    "VALUES (?, ?, ?, ?, ?, 'pending')",
                    (task_id, i, step["agent"], step["instruction"], int(bool(step.get("needs_confirmation")))),
                )
                ids.append(int(cur.lastrowid))
            self._db.commit()
        return ids

    def update_step(self, step_id: int, **fields) -> None:
        unknown = set(fields) - _STEP_FIELDS
        if unknown:
            raise ValueError(f"unknown step fields: {unknown}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE steps SET {cols} WHERE id = ?", (*fields.values(), step_id))
            self._db.commit()

    def get_step(self, step_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
        return self._step_dict(row) if row else None

    # ---- conversation ------------------------------------------------------
    def add_message(self, role: str, content: str, task_id: int | None = None) -> None:
        if role not in ("user", "assistant"):
            raise ValueError("role must be user or assistant")
        with self._lock:
            self._db.execute(
                "INSERT INTO messages(role, content, task_id, created_at) VALUES (?, ?, ?, ?)",
                (role, content, task_id, time.time()),
            )
            self._db.commit()

    def recent_turns(self, turns: int, exclude_task: int | None = None) -> list[dict]:
        """Last N completed user/assistant pairs of the current conversation, oldest first."""
        if turns <= 0:
            return []
        start = self.conversation_start()
        with self._lock:
            rows = self._db.execute(
                "SELECT t.request, t.final FROM tasks t WHERE t.status = 'done' AND t.final IS NOT NULL "
                "AND t.created_at >= ? AND (? IS NULL OR t.id != ?) ORDER BY t.id DESC LIMIT ?",
                (start, exclude_task, exclude_task, turns),
            ).fetchall()
        out: list[dict] = []
        for row in reversed(rows):
            out.append({"role": "user", "content": row["request"]})
            out.append({"role": "assistant", "content": row["final"]})
        return out

    def conversation_start(self) -> float:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = 'conversation_start'").fetchone()
        return float(row["value"]) if row else 0.0

    def new_conversation(self) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES ('conversation_start', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(time.time()),),
            )
            self._db.commit()

    # ---- lessons -----------------------------------------------------------
    def add_lesson(self, kind: str, text: str, task_id: int | None = None) -> int:
        if kind not in LESSON_KINDS:
            raise ValueError(f"kind must be one of {LESSON_KINDS}")
        text = (text or "").strip()
        if not text:
            raise ValueError("lesson text is empty")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO lessons(kind, text, task_id, created_at) VALUES (?, ?, ?, ?)",
                (kind, text[:2000], task_id, time.time()),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def list_lessons(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM lessons ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_lesson(self, lesson_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
            self._db.commit()
            return cur.rowcount > 0

    def relevant_lessons(self, query: str, limit: int = 5, max_rules: int = 10) -> list[dict]:
        """All recent rules, plus the past outcomes that share the most words with the query."""
        lessons = self.list_lessons()
        rules = [l for l in lessons if l["kind"] == "rule"][:max_rules]
        q = tokens(query)
        scored = []
        for lesson in lessons:
            if lesson["kind"] == "rule":
                continue
            overlap = len(q & tokens(lesson["text"]))
            if overlap:
                scored.append((overlap, lesson["id"], lesson))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return rules + [l for _, _, l in scored[:limit]]
