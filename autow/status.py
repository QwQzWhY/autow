"""勾选和删除，放在数据库之外的一个小文件里。

以前这两种操作要走一次 GitHub Actions：排队、跑、提交、发布，碰上官网扫描在跑
还得排在后面十几分钟。现在页面直接写这个文件（一次 HTTP 请求就落地），扫描任务
下次开跑时把里面的状态并进数据库。

文件放在 docs/data/ 下，和服务发布出去的那份数据在一起，页面读同一个路径。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

VALID = {"open", "done", "deleted"}


def path(docs_data: Path | None = None) -> Path:
    """Override-able so tests never touch the published file."""
    if docs_data is not None:
        return docs_data / "status.json"
    override = os.environ.get("AUTOW_DOCS_DIR")
    base = Path(override) if override else Path(__file__).resolve().parent.parent / "docs" / "data"
    return base / "status.json"


def load(docs_data: Path | None = None) -> dict[str, str]:
    """{key: status}. Anything unreadable reads as "no statuses" — losing a file
    must not take the whole scan down."""
    try:
        data = json.loads(path(docs_data).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in items.items()
        if str(value) in VALID
    }


def apply_to(store, docs_data: Path | None = None) -> int:
    """把文件里的状态写进数据库，返回改了几条。

    扫描是唯一写数据库的进程，所以由它在开跑时合并一次；合并之后发布出去的
    todos.json 才会真正少掉那些行。
    """
    changed = 0
    for key, state in load(docs_data).items():
        changed += int(store.set_todo_status(key, state))
    return changed
