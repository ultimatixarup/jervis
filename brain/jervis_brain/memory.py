"""Jervis's memory: SQLite with FTS5, at ~/.jervis/memory.db.

PLAN.md §2 names tables `facts`, `preferences`, `tasks` and `conversations`. Facts,
preferences and tasks differ only by a label, and keeping one FTS index in step with
three base tables is a needless source of bugs, so they live in one `memories` table
with a `kind` column. Views named `facts`, `preferences` and `tasks` are created so
those names still work for anyone opening the database with sqlite3.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

MAX_CONVERSATION_MESSAGES = 20


class Kind(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    TASK = "task"


@dataclass(frozen=True)
class Memory:
    id: int
    kind: Kind
    text: str
    created_at: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kind, text)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(text, content='memories', content_rowid='id', tokenize='porter unicode61');

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;

-- PLAN.md §2 names these; they are views onto one table (see the module docstring).
CREATE VIEW IF NOT EXISTS facts AS
    SELECT id, text, created_at FROM memories WHERE kind = 'fact';
CREATE VIEW IF NOT EXISTS preferences AS
    SELECT id, text, created_at FROM memories WHERE kind = 'preference';
CREATE VIEW IF NOT EXISTS tasks AS
    SELECT id, text, created_at FROM memories WHERE kind = 'task';

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_session
    ON conversations (session_id, id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH query.

    User speech contains quotes, hyphens and stray operators that FTS5 would treat as
    syntax and reject. Quoting each word makes every term a literal.
    """
    words = [w for w in "".join(c if c.isalnum() else " " for c in text).split() if w]
    return " OR ".join(f'"{w}"' for w in words)


class MemoryStore:
    """Facts, preferences, tasks and per-session conversation history."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- facts / preferences / tasks -----------------------------------------------

    def remember(self, kind: Kind | str, text: str) -> int:
        """Store one memory. Identical (kind, text) is refreshed, never duplicated."""
        text = text.strip()
        if not text:
            raise ValueError("refusing to remember an empty string")
        kind = Kind(kind)
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT id FROM memories WHERE kind = ? AND text = ?", (kind.value, text))
            row = cur.fetchone()
            if row is not None:
                cur.execute("UPDATE memories SET created_at = ? WHERE id = ?", (_now(), row["id"]))
                self._conn.commit()
                return int(row["id"])
            cur.execute(
                "INSERT INTO memories (kind, text, created_at) VALUES (?, ?, ?)",
                (kind.value, text, _now()),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def recall(self, query: str, k: int = 8) -> list[Memory]:
        """Search memories. Exact matches rank first, then FTS relevance."""
        match = _fts_query(query)
        if not match:
            return []
        needle = query.strip().lower()
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                SELECT m.id, m.kind, m.text, m.created_at,
                       CASE
                           WHEN lower(m.text) = ?            THEN 0
                           WHEN instr(lower(m.text), ?) > 0  THEN 1
                           ELSE 2
                       END AS exactness,
                       bm25(memories_fts) AS score
                FROM memories_fts
                JOIN memories m ON m.id = memories_fts.rowid
                WHERE memories_fts MATCH ?
                ORDER BY exactness ASC, score ASC, m.created_at DESC
                LIMIT ?
                """,
                (needle, needle, match, k),
            )
            return [
                Memory(int(r["id"]), Kind(r["kind"]), r["text"], r["created_at"])
                for r in cur.fetchall()
            ]

    def recent_tasks(self, n: int = 5) -> list[Memory]:
        return self._recent(Kind.TASK, n)

    def _recent(self, kind: Kind, n: int) -> list[Memory]:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT id, kind, text, created_at FROM memories "
                "WHERE kind = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (kind.value, n),
            )
            return [
                Memory(int(r["id"]), Kind(r["kind"]), r["text"], r["created_at"])
                for r in cur.fetchall()
            ]

    def forget(self, memory_id: int) -> bool:
        with closing(self._conn.cursor()) as cur:
            cur.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # --- conversation history -------------------------------------------------------

    def append_message(self, session_id: str, role: str, content: Any) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO conversations (session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, role, json.dumps(content, default=str), _now()),
            )
            self._conn.commit()

    def history(
        self, session_id: str, limit: int = MAX_CONVERSATION_MESSAGES
    ) -> list[dict[str, Any]]:
        """The last *limit* messages for a session, oldest first."""
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT role, content FROM conversations WHERE session_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
            rows = list(cur.fetchall())
        messages = [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]
        messages.reverse()
        return _drop_leading_tool_results(messages)

    def replace_history(self, session_id: str, messages: Sequence[dict[str, Any]]) -> None:
        """Persist a whole conversation window, replacing what was there."""
        with closing(self._conn.cursor()) as cur:
            cur.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            cur.executemany(
                "INSERT INTO conversations (session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                [
                    (session_id, m["role"], json.dumps(m["content"], default=str), _now())
                    for m in messages
                ],
            )
            self._conn.commit()

    def sessions(self) -> list[str]:
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT DISTINCT session_id FROM conversations ORDER BY session_id")
            return [r["session_id"] for r in cur.fetchall()]


def _drop_leading_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop orphaned tool_result blocks at the front of a truncated window.

    Truncating to the last N messages can cut between an assistant's tool_use and the
    user message carrying its tool_result. The API rejects a tool_result whose
    tool_use is not in the conversation, so trim until the window starts clean.
    """
    while messages and _is_tool_result(messages[0]):
        messages.pop(0)
    return messages


def _is_tool_result(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def summarise_memories(memories: Iterable[Memory]) -> str:
    return "\n".join(f"- ({m.kind.value}) {m.text}" for m in memories)
