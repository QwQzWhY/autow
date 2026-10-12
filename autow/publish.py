"""Write the dashboard data file that the phone PWA reads.

GitHub Pages serves the `docs/` folder, so the generated JSON doubles as the
API and as the deployment artifact.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .store import Store

DOCS_DATA = Path(__file__).resolve().parent.parent / "docs" / "data"

SOURCE_LABELS = {
    "lzu-ai": "学院通知",
    "lzu-main": "学校通知",
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
                "source": source,
                "source_label": SOURCE_LABELS.get(source, source),
                "url": row["url"],
                "category": row["category"] or "",
                "published_at": row["published_at"] or "",
            }
        )
    todos.sort(key=lambda t: (t["due_at"] is None, t["due_at"] or "", t["priority"] != "high"))
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(todos),
        "todos": todos,
    }


def write(store: Store, out_dir: Path = DOCS_DATA) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "todos.json"
    path.write_text(
        json.dumps(build(store), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
