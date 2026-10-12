"""SQLite storage. One file, no server, trivially backed up or synced."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import RawNotice

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "autow.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id             INTEGER PRIMARY KEY,
    source         TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    url            TEXT NOT NULL,
    title          TEXT NOT NULL,
    published_at   TEXT,
    published_raw  TEXT,
    category       TEXT,
    body           TEXT,
    first_seen_at  TEXT NOT NULL,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY,
    notice_id   INTEGER NOT NULL REFERENCES notices(id),
    title       TEXT NOT NULL,
    detail      TEXT,
    due_at      TEXT,
    priority    TEXT NOT NULL DEFAULT 'normal',
    status      TEXT NOT NULL DEFAULT 'open',
    dedupe_key  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_todos_status_due ON todos(status, due_at);

CREATE TABLE IF NOT EXISTS llm_cache (
    key         TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- notices

    def upsert_notice(self, source: str, notice: RawNotice) -> tuple[int, bool]:
        """Return (notice_id, is_new)."""
        cur = self.conn.execute(
            "SELECT id FROM notices WHERE source = ? AND external_id = ?",
            (source, notice.external_id),
        )
        row = cur.fetchone()
        if row:
            self.conn.execute(
                """UPDATE notices SET title = ?, published_at = ?, published_raw = ?,
                       category = ?, body = ? WHERE id = ?""",
                (
                    notice.title_full or notice.title,
                    notice.published_at,
                    notice.published_text,
                    notice.category,
                    notice.body,
                    row["id"],
                ),
            )
            self.conn.commit()
            return row["id"], False

        cur = self.conn.execute(
            """INSERT INTO notices
                   (source, external_id, url, title, published_at, published_raw,
                    category, body, first_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source,
                notice.external_id,
                notice.url,
                notice.title_full or notice.title,
                notice.published_at,
                notice.published_text,
                notice.category,
                notice.body,
                now_iso(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid, True

    def has_notice(self, source: str, external_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM notices WHERE source = ? AND external_id = ?",
                (source, external_id),
            ).fetchone()
            is not None
        )

    # ------------------------------------------------------------------ todos

    def add_todo(
        self,
        notice_id: int,
        title: str,
        detail: str,
        due_at: str | None,
        priority: str,
        dedupe_key: str,
    ) -> bool:
        """Return True if a new todo was created."""
        try:
            self.conn.execute(
                """INSERT INTO todos
                       (notice_id, title, detail, due_at, priority, dedupe_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (notice_id, title, detail, due_at, priority, dedupe_key, now_iso()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def open_todos(self, include_done: bool = False) -> list[sqlite3.Row]:
        where = "" if include_done else "WHERE status = 'open'"
        return list(
            self.conn.execute(
                f"""SELECT t.*, n.url, n.category, n.published_at
                    FROM todos t JOIN notices n ON n.id = t.notice_id
                    {where}
                    ORDER BY (t.due_at IS NULL), t.due_at, t.id DESC"""
            )
        )

    # -------------------------------------------------------------- llm cache

    def cache_get(self, key: str, model: str) -> str | None:
        row = self.conn.execute(
            "SELECT response FROM llm_cache WHERE key = ? AND model = ?", (key, model)
        ).fetchone()
        return row["response"] if row else None

    def cache_put(self, key: str, model: str, response: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache (key, model, response, created_at) VALUES (?, ?, ?, ?)",
            (key, model, response, now_iso()),
        )
        self.conn.commit()

    def all_todos(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT t.*, n.url, n.category, n.published_at, n.title AS notice_title
                   FROM todos t JOIN notices n ON n.id = t.notice_id
                   ORDER BY (t.due_at IS NULL), t.due_at, t.id DESC"""
            )
        )
