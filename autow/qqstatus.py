"""The data behind the QQ connection page.

Answers two questions the user actually has: "is the phone still talking to
GitHub?" and "what happened to the message I just sent?".

Everything written here is public — it goes into the same GitHub Pages site as
the todo board. So the log carries only timestamps, verdicts, counts and the
extracted task title. Group names, senders and raw message text never appear.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import Store
from . import clock

DOCS_DATA = Path(__file__).resolve().parent.parent / "docs" / "data"

VERDICTS = {
    "todo": "已生成待办",
    "ignored": "判断为无需行动",
    "duplicate": "重复消息，已忽略",
    "no-model": "收到消息，但没配置大模型",
    "empty": "空消息",
}


def build(store: Store) -> dict:
    counts = store.qq_counts()
    recent = [
        {
            "at": row["received_at"],
            "verdict": row["verdict"],
            "verdict_label": VERDICTS.get(row["verdict"], row["verdict"]),
            "title": row["title"] or "",
            "length": row["length"] or 0,
            "nonce": row["nonce"] or "",
        }
        for row in store.qq_recent(10)
    ]
    return {
        "generated_at": clock.iso_now(),
        "connected": counts["total_received"] > 0,
        **counts,
        "recent": recent,
    }


def data_dir() -> Path:
    override = os.environ.get("AUTOW_DOCS_DIR")
    return Path(override) if override else DOCS_DATA


def write(store: Store, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "qq-status.json"
    path.write_text(
        json.dumps(build(store), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
