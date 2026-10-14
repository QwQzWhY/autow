"""Write the dashboard data file that the phone PWA reads.

GitHub Pages serves the `docs/` folder, so the generated JSON doubles as the
API and as the deployment artifact.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import Store
from . import clock

DOCS_DATA = Path(__file__).resolve().parent.parent / "docs" / "data"

SOURCE_LABELS = {
    "lzu-ai": "学院通知",
    "lzu-news": "学校通知",
    "qq": "QQ 群",
}


def build(store: Store) -> dict:
    todos = []
    for row in store.all_todos():
        source = row["dedupe_key"].split(":", 1)[0]
        todos.append(
            {
                "key": row["dedupe_key"],
                "title": row["title"],
                "detail": row["detail"] or "",
                "due_at": row["due_at"],
                "priority": row["priority"],
                "needs_recommendation": bool(row["needs_recommendation"]),
                "source": source,
                "source_label": SOURCE_LABELS.get(source, source),
                "url": row["url"],
                "category": row["category"] or "",
                "published_at": row["published_at"] or "",
            }
        )
    todos.sort(key=lambda t: (t["due_at"] is None, t["due_at"] or "", t["priority"] != "high"))
    return {
        "generated_at": clock.iso_now(),
        "count": len(todos),
        "todos": todos,
    }


def data_dir() -> Path:
    """Where the phone reads its data from.

    Overridable so tests never scribble over the real dashboard files that are
    committed to the repository.
    """
    override = os.environ.get("AUTOW_DOCS_DIR")
    return Path(override) if override else DOCS_DATA


def write(store: Store, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "todos.json"
    path.write_text(
        json.dumps(build(store), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
