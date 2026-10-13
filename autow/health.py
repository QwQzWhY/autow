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
from urllib.parse import urlparse

from . import llm
from . import clock

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
        problem = llm.validate_config(cfg)
        # A run that never calls the model — a QQ ingest, say — would otherwise
        # publish `probe: null` and make a healthy system look unverified.
        probe = llm.STATS["probe"] or _previous_probe()
        # `ok` used to require attempts > 0, which meant a perfectly healthy run
        # that simply had no new notices to judge published `ok: false` — exactly
        # the misleading signal this file exists to prevent. A run with nothing
        # to do is fine as long as the self-check passes and nothing failed.
        probe_ok = isinstance(probe, dict) and probe.get("ok") is True
        extractor = {
            "configured": True,
            "mode": cfg["mode"],
            "model": _safe(cfg["model"], cfg["api_key"]),
            # Only the hostname: a whole URL is one paste-mistake away from
            # being the key itself.
            "base_url_host": _safe_host(cfg["base_url"]),
            "probe": _scrub(probe, cfg["api_key"]),
            "attempts": attempts,
            "failures": failures,
            "config_error": problem,
            "ok": problem is None and failures == 0 and probe_ok,
            "last_error": _scrub(llm.STATS["last_error"], cfg["api_key"]),
        }
    return {
        "generated_at": clock.iso_now(),
        "extractor": extractor,
    }


def _previous_probe():
    """The last self-check result, if this run did not perform one."""
    try:
        data = json.loads((data_dir() / "health.json").read_text(encoding="utf-8"))
        probe = data["extractor"]["probe"]
    except Exception:
        return None
    if not isinstance(probe, dict):
        return None
    return {**probe, "carried_over": True}


def _safe(value: str, api_key: str = "") -> str:
    """Never publish something that could be a credential."""
    text = (value or "").strip()
    if not text:
        return ""
    if llm.secret_like(text) or (api_key and api_key in text):
        return "(看起来是密钥，已隐藏)"
    return text


def _scrub(value, api_key: str = ""):
    """Last line of defence: strip the key out of anything we publish.

    Error strings are written by whatever raised, so they can quote the
    endpoint — which is the key when it was pasted into the wrong field.
    """
    if isinstance(value, dict):
        return {k: _scrub(v, api_key) for k, v in value.items()}
    if isinstance(value, str):
        text = value.replace(api_key, "(已隐藏)") if api_key else value
        return _safe(text, api_key)
    return value


def _safe_host(base_url: str) -> str:
    host = urlparse(base_url).hostname or ""
    return host if "." in host else "(无效)"


def write(out_dir: Path | None = None) -> Path:
    out_dir = out_dir or data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "health.json"
    path.write_text(json.dumps(build(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
