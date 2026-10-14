"""SQLite storage. One file, no server, trivially backed up or synced."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import RawNotice
from . import clock

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
    needs_recommendation INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
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

-- 只记"收到过一条消息、判断结果是什么"，不留群名、人名和原文。
-- 这个数据库会跟着仓库走，公开仓库里不能出现聊天内容。
CREATE TABLE IF NOT EXISTS qq_log (
    id          INTEGER PRIMARY KEY,
    received_at TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    title       TEXT,
    length      INTEGER,
    nonce       TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` does not
# touch an existing table, so each one is applied by hand when missing.
MIGRATIONS = (
    ("todos", "needs_recommendation", "INTEGER NOT NULL DEFAULT 0"),
    ("todos", "content_hash", "TEXT"),
)


def now_iso() -> str:
    return clock.iso_now()


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        for table, column, declaration in MIGRATIONS:
            existing = {
                row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

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

    def stored_notice(self, source: str, external_id: str) -> RawNotice | None:
        """One notice as it was stored, so it can be re-judged without the site.

        A prompt change has to reach the notices already on the board. Fetching
        their pages again to do that adds only a way to fail: the runner is
        throttled by the school's WAF, and a failed fetch used to leave the old
        verdict standing — which is how a rule change silently never applied and
        an item that should have been dropped stayed on the board.
        """
        row = self.conn.execute(
            """SELECT external_id, url, title, published_at, published_raw, category, body
               FROM notices WHERE source = ? AND external_id = ?""",
            (source, external_id),
        ).fetchone()
        if row is None:
            return None
        return RawNotice(
            url=row["url"],
            title=row["title"],
            title_full=row["title"],
            published_at=row["published_at"],
            published_text=row["published_raw"] or "",
            category=row["category"] or "",
            body=row["body"] or "",
            external_id=row["external_id"],
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
        needs_recommendation: bool = False,
        content_hash: str = "",
    ) -> str:
        """Insert or refresh a todo. Returns 'created', 'updated' or 'unchanged'.

        Refreshing matters when the extractor changes — switching from the rule
        fallback to a real model used to leave the old verdict stuck to the
        notice forever, because a duplicate key simply did not insert. `status`
        is deliberately left alone so a ticked-off item stays ticked off.
        """
        row = self.conn.execute(
            """SELECT id, title, detail, due_at, priority, needs_recommendation, content_hash
               FROM todos WHERE dedupe_key = ?""",
            (dedupe_key,),
        ).fetchone()
        flag = int(bool(needs_recommendation))
        content_hash = content_hash or None
        if row is None:
            self.conn.execute(
                """INSERT INTO todos
                       (notice_id, title, detail, due_at, priority, needs_recommendation,
                        content_hash, dedupe_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    notice_id,
                    title,
                    detail,
                    due_at,
                    priority,
                    flag,
                    content_hash,
                    dedupe_key,
                    now_iso(),
                ),
            )
            self.conn.commit()
            return "created"

        if (
            row["title"],
            row["detail"],
            row["due_at"],
            row["priority"],
            row["needs_recommendation"],
            row["content_hash"],
        ) == (
            title,
            detail,
            due_at,
            priority,
            flag,
            content_hash,
        ):
            return "unchanged"

        self.conn.execute(
            """UPDATE todos
               SET title = ?, detail = ?, due_at = ?, priority = ?,
                   needs_recommendation = ?, content_hash = ?
               WHERE id = ?""",
            (title, detail, due_at, priority, flag, content_hash, row["id"]),
        )
        self.conn.commit()
        return "updated"

    def find_open_todo_by_content(self, content_hash: str) -> sqlite3.Row | None:
        """An open todo that came from the same notice text.

        The same announcement is routinely posted into several class groups, and
        each arrives as its own message. Without this it becomes one todo per
        group; with it they all refresh a single entry.
        """
        if not content_hash:
            return None
        return self.conn.execute(
            """SELECT id, notice_id, dedupe_key FROM todos
               WHERE content_hash = ? AND status = 'open'
               ORDER BY id LIMIT 1""",
            (content_hash,),
        ).fetchone()

    def content_was_dismissed(self, content_hash: str) -> bool:
        """Whether the user already threw away a todo with exactly this text.

        The same announcement goes into several class groups, so deleting one
        copy used to leave the other copies free to arrive later and put it back
        on the board. Deleting is the user's decision, not a gap to be filled in.
        """
        if not content_hash:
            return False
        return (
            self.conn.execute(
                """SELECT 1 FROM todos
                   WHERE content_hash = ? AND status = 'deleted' LIMIT 1""",
                (content_hash,),
            ).fetchone()
            is not None
        )

    def set_todo_status(self, dedupe_key: str, status: str) -> bool:
        """Record what the user did with one todo on the board.

        Ticking and deleting used to live only in the phone's localStorage, which
        made them invisible everywhere else: a second device showed the item as
        open again, clearing site data brought deleted items back, and the
        published file kept listing things that were already done.

        `status` is the one field `add_todo` deliberately never touches, so
        re-analysing a notice or the same message arriving twice cannot undo it.
        """
        cursor = self.conn.execute(
            "UPDATE todos SET status = ? WHERE dedupe_key = ?", (status, dedupe_key)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def remove_open_todo(self, dedupe_key: str) -> bool:
        """Drop a todo that a fresh analysis no longer considers actionable.

        Only unstamped ones: a completed item is the user's record, not ours.
        """
        cursor = self.conn.execute(
            "DELETE FROM todos WHERE dedupe_key = ? AND status = 'open'", (dedupe_key,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------- meta

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

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

    # --------------------------------------------------------------- qq log

    def log_qq(
        self,
        verdict: str,
        title: str = "",
        length: int = 0,
        nonce: str = "",
        received_at: str | None = None,
    ) -> None:
        """Record the outcome of one incoming QQ message.

        Deliberately stores no group name, sender or message body: this database
        is committed to the repository, and the dashboard repository is meant to
        be public. A digest is kept in `notices.external_id` for dedupe instead.
        """
        self.conn.execute(
            """INSERT INTO qq_log (received_at, verdict, title, length, nonce)
               VALUES (?, ?, ?, ?, ?)""",
            (received_at or now_iso(), verdict, title[:120], length, nonce[:64]),
        )
        self.conn.commit()

    def qq_recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM qq_log ORDER BY id DESC LIMIT ?", (limit,)
            )
        )

    def qq_counts(self) -> dict[str, int]:
        today = clock.today().isoformat()
        one = lambda sql, *args: self.conn.execute(sql, args).fetchone()[0]
        return {
            "total_received": one("SELECT COUNT(*) FROM qq_log"),
            "total_todos": one("SELECT COUNT(*) FROM qq_log WHERE verdict = 'todo'"),
            "today_received": one(
                "SELECT COUNT(*) FROM qq_log WHERE substr(received_at, 1, 10) = ?", today
            ),
            "today_todos": one(
                "SELECT COUNT(*) FROM qq_log WHERE verdict = 'todo' AND substr(received_at, 1, 10) = ?",
                today,
            ),
        }
