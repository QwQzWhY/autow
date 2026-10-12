"""A machine-readable answer to "did this run actually work?".

The first cloud scans quietly fell back to the rule extractor, which is only
visible by reading the CI log — and the log needs authentication. Publishing the
extractor's own status alongside the todos means the same question can be
answered by fetching one public JSON file.

Nothing secret belongs here: the endpoint and model name are configuration, not
credentials, and the API key is never included in an error string.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from . import llm

DOCS_DATA = Path(__file__).resolve().parent.parent / "docs" / "data"


def data_dir() -> Path:
    override = os.environ.get("AUTOW_DOCS_DIR")
    return Path(override) if override else DOCS_DATA


def build() -> dict:
    cfg = llm.config()
    if cfg is None:
        extractor = {
            "configured": False,
            "mode": "off",
            "note": "未设置 LLM_API_KEY，全部走规则抽取",
        }
    else:
        attempts = int(llm.STATS["attempts"])
        failures = int(llm.STATS["failures"])
        extractor = {
            "configured": True,
            "mode": cfg["mode"],
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            "probe": llm.STATS["probe"],
            "attempts": attempts,
            "failures": failures,
            "ok": attempts > 0 and failures == 0,
            "last_error": llm.STATS["last_error"],
        }
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "extractor": extractor,
    }


def write(out_dir: Path | None = None) -> Path:
    out_dir = out_dir or data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "health.json"
    path.write_text(json.dumps(build(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
