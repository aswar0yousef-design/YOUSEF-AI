"""Persistent memory in a local SQLite file.

Holds tasks and their steps, the conversation, and "lessons":
- rule:    a standing instruction the user taught (always given to agents)
- success: a task the user marked as worked, with their note
- failure: a task the user marked as failed, with their note
Lessons relevant to a new request are given to the planner and agents. This is
how YOUSEF AI learns: from remembered outcomes, not from retraining the model.
"""

from __future__ import annotations

import json
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
CREATE TABLE IF NOT EXISTS changes(
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    step_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    old_text TEXT,
    new_text TEXT NOT NULL,
    diff TEXT NOT NULL,
    added INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS changes_step ON changes(step_id);
CREATE TABLE IF NOT EXISTS actions(
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    step_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_step ON actions(step_id);
CREATE TABLE IF NOT EXISTS social_posts(
    id INTEGER PRIMARY KEY,
    task_id INTEGER,
    action_id INTEGER,
    account_id TEXT NOT NULL,
    account TEXT NOT NULL,
    platform TEXT NOT NULL,
    text TEXT NOT NULL,
    images TEXT NOT NULL DEFAULT '[]',
    link TEXT,
    scheduled_at REAL,
    native INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    remote_id TEXT,
    url TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS social_due ON social_posts(status, scheduled_at);
"""

ACTIVE_STATUSES = ("planning", "running", "waiting")
TASK_SOURCES = ("text", "voice", "vscode")
LESSON_KINDS = ("rule", "success", "failure")

_TASK_FIELDS = {"status", "final", "error", "feedback"}
_STEP_FIELDS = {"status", "output", "provider", "model", "error", "started_at", "finished_at", "trace"}
CHANGE_STATUSES = ("pending", "applied", "rejected", "conflict", "error")
ACTION_STATUSES = ("pending", "done", "rejected", "failed", "skipped", "conflict")
# scheduled: waiting for its time (Yousef publishes it, or Facebook when native); missed: Yousef was off at the time
POST_STATUSES = ("scheduled", "publishing", "published", "failed", "cancelled", "missed")
_POST_FIELDS = {"status", "remote_id", "url", "error", "scheduled_at", "native", "action_id"}

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
            self._migrate()
            self._db.commit()

    def _migrate(self) -> None:
        """Upgrade databases created by older versions."""
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(tasks)").fetchall()}
        if "source" not in columns:  # added in 0.2.0: "text" or "voice"
            self._db.execute("ALTER TABLE tasks ADD COLUMN source TEXT NOT NULL DEFAULT 'text'")
        if "workspace" not in columns:  # added in 0.3.0: project folder name
            self._db.execute("ALTER TABLE tasks ADD COLUMN workspace TEXT")
        if "context" not in columns:  # added in 0.3.0: editor context as JSON
            self._db.execute("ALTER TABLE tasks ADD COLUMN context TEXT")
        if "store" not in columns:  # added in 0.4.0: store the user picked, by name
            self._db.execute("ALTER TABLE tasks ADD COLUMN store TEXT")
        step_columns = {r["name"] for r in self._db.execute("PRAGMA table_info(steps)").fetchall()}
        if "trace" not in step_columns:  # added in 0.3.0: tools the agent used, JSON
            self._db.execute("ALTER TABLE steps ADD COLUMN trace TEXT")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- tasks -------------------------------------------------------------
    def create_task(self, request: str, source: str = "text", workspace: str | None = None,
                    context: dict | None = None, store: str | None = None) -> int:
        if source not in TASK_SOURCES:
            raise ValueError(f"source must be one of {TASK_SOURCES}")
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO tasks(request, status, source, workspace, context, store, created_at, updated_at) "
                "VALUES (?, 'planning', ?, ?, ?, ?, ?, ?)",
                (request, source, workspace, json.dumps(context, ensure_ascii=False) if context else None, store,
                 now, now),
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
            changes = self._db.execute(
                "SELECT id, step_id, path, old_text IS NULL AS new_file, diff, added, removed, status, note "
                "FROM changes WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
            actions = self._db.execute(
                "SELECT id, step_id, kind, payload, status, result FROM actions WHERE task_id = ? ORDER BY id",
                (task_id,)).fetchall()
        task = dict(row)
        task["context"] = _loads(task.get("context"))
        by_step: dict[int, list] = {}
        for c in changes:
            d = dict(c)
            d["new_file"] = bool(d["new_file"])
            by_step.setdefault(d.pop("step_id"), []).append(d)
        acts: dict[int, list] = {}
        for a in actions:
            d = dict(a)
            d["payload"] = _loads(d["payload"]) or {}
            acts.setdefault(d.pop("step_id"), []).append(d)
        task["steps"] = [self._step_dict(s, by_step.get(s["id"], []), acts.get(s["id"], [])) for s in steps]
        return task

    @staticmethod
    def _step_dict(row, changes: list | None = None, actions: list | None = None) -> dict:
        d = dict(row)
        d["needs_confirmation"] = bool(d["needs_confirmation"])
        d["trace"] = _loads(d.get("trace")) or []
        d["changes"] = changes or []
        d["actions"] = actions or []
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
            # a post cut off mid-publish may or may not be online: never retry it blindly
            self._db.execute(
                "UPDATE social_posts SET status = 'failed', updated_at = ?, error = ? WHERE status = 'publishing'",
                (now, "انقطع النشر عند إغلاق يوسف؛ تحقّق من الحساب قبل إعادة المحاولة"))
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

    # ---- proposed file changes --------------------------------------------------
    def add_changes(self, task_id: int, step_id: int, changes: list[dict]) -> list[int]:
        ids = []
        now = time.time()
        with self._lock:
            for c in changes:
                cur = self._db.execute(
                    "INSERT INTO changes(task_id, step_id, path, old_text, new_text, diff, added, removed, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, step_id, c["path"], c["old_text"], c["new_text"], c["diff"], c["added"],
                     c["removed"], now),
                )
                ids.append(int(cur.lastrowid))
            self._db.commit()
        return ids

    def get_changes(self, step_id: int, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM changes WHERE step_id = ?"
        args: tuple = (step_id,)
        if status:
            query += " AND status = ?"
            args = (step_id, status)
        with self._lock:
            rows = self._db.execute(query + " ORDER BY id", args).fetchall()
        return [dict(r) for r in rows]

    def get_change(self, change_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
        return dict(row) if row else None

    def add_actions(self, task_id: int, step_id: int, actions: list[dict]) -> list[int]:
        ids = []
        with self._lock:
            for a in actions:
                cur = self._db.execute(
                    "INSERT INTO actions(task_id, step_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                    (task_id, step_id, a["kind"], json.dumps(a["payload"], ensure_ascii=False), time.time()))
                ids.append(int(cur.lastrowid))
            self._db.commit()
        return ids

    def get_actions(self, step_id: int, status: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM actions WHERE step_id = ?", (step_id,)
        if status:
            query, args = query + " AND status = ?", (step_id, status)
        with self._lock:
            rows = self._db.execute(query + " ORDER BY id", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = _loads(d["payload"]) or {}
            out.append(d)
        return out

    def update_action(self, action_id: int, status: str, result: str = "") -> None:
        if status not in ACTION_STATUSES:
            raise ValueError(f"status must be one of {ACTION_STATUSES}")
        with self._lock:
            self._db.execute("UPDATE actions SET status = ?, result = ? WHERE id = ?", (status, result or None, action_id))
            self._db.commit()

    def update_change(self, change_id: int, status: str, note: str = "") -> None:
        if status not in CHANGE_STATUSES:
            raise ValueError(f"status must be one of {CHANGE_STATUSES}")
        with self._lock:
            self._db.execute("UPDATE changes SET status = ?, note = ? WHERE id = ?", (status, note or None, change_id))
            self._db.commit()

    # ---- social posts ------------------------------------------------------
    def add_social_post(self, account_id: str, account: str, platform: str, text: str, images: list,
                        status: str, task_id: int | None = None, action_id: int | None = None,
                        link: str | None = None, scheduled_at: float | None = None, native: bool = False,
                        remote_id: str | None = None, url: str | None = None, error: str | None = None) -> int:
        if status not in POST_STATUSES:
            raise ValueError(f"status must be one of {POST_STATUSES}")
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO social_posts(task_id, action_id, account_id, account, platform, text, images, link, "
                "scheduled_at, native, status, remote_id, url, error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, action_id, account_id, account, platform, text, json.dumps(images, ensure_ascii=False), link,
                 scheduled_at, int(native), status, remote_id, url, error, now, now))
            self._db.commit()
            return int(cur.lastrowid)

    def update_social_post(self, post_id: int, **fields) -> None:
        unknown = set(fields) - _POST_FIELDS
        if unknown:
            raise ValueError(f"unknown fields: {unknown}")
        if "status" in fields and fields["status"] not in POST_STATUSES:
            raise ValueError(f"status must be one of {POST_STATUSES}")
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?"
        with self._lock:
            self._db.execute(f"UPDATE social_posts SET {sets} WHERE id = ?", (*fields.values(), time.time(), post_id))
            self._db.commit()

    def claim_social_post(self, post_id: int, from_status: str = "scheduled") -> bool:
        """Mark a post as being published, unless another thread got there first."""
        with self._lock:
            cur = self._db.execute("UPDATE social_posts SET status = 'publishing', updated_at = ? WHERE id = ? AND status = ?",
                                   (time.time(), post_id, from_status))
            self._db.commit()
            return cur.rowcount == 1

    def get_social_post(self, post_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM social_posts WHERE id = ?", (post_id,)).fetchone()
        return _post(row) if row else None

    def list_social_posts(self, statuses: tuple | None = None, limit: int = 50) -> list[dict]:
        query, args = "SELECT * FROM social_posts", []
        if statuses:
            query += f" WHERE status IN ({', '.join('?' for _ in statuses)})"
            args = list(statuses)
        query += " ORDER BY COALESCE(scheduled_at, created_at) DESC, id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(query, (*args, limit)).fetchall()
        return [_post(r) for r in rows]

    def due_social_posts(self, now: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM social_posts WHERE status = 'scheduled' AND scheduled_at <= ? "
                                    "ORDER BY scheduled_at, id", (now,)).fetchall()
        return [_post(r) for r in rows]

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


def _loads(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _post(row) -> dict:
    d = dict(row)
    d["images"] = _loads(d["images"]) or []
    d["native"] = bool(d["native"])
    return d
