"""Stealth browser session.

ai.lzu.edu.cn sits behind a JS-challenge WAF: a plain HTTP client only ever
gets a 412 challenge page, and it rejects headless Chrome unless two things are
fixed — the User-Agent must not say "HeadlessChrome", and the
`--enable-automation` switch must be dropped. With those handled the real
Nuxt-rendered page comes through.

Each run uses a fresh browser profile. Persisting one sounded like a good idea
(fewer challenges) but in practice the stored WAF cookies go stale and the site
then rejects the whole profile outright with an empty 412, so a clean session
every time is both simpler and more reliable.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

DEFAULT_PROFILE = Path(__file__).resolve().parent.parent / ".browser-profile"


def _channel() -> str | None:
    """Which browser binary to drive.

    Locally we reuse the installed Google Chrome, so nothing has to be
    downloaded. On a plain Linux box (CI) set AUTOW_BROWSER_CHANNEL="" to fall
    back to Playwright's bundled Chromium.
    """
    value = os.environ.get("AUTOW_BROWSER_CHANNEL", "chrome")
    return value or None


@contextmanager
def stealth_context(
    profile_dir: str | Path | None = None,
    headless: bool = True,
) -> Iterator[BrowserContext]:
    with sync_playwright() as pw:
        launch_args = dict(
            channel=_channel(),
            headless=headless,
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                # Chromium in CI dies on the small /dev/shm containers give you.
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        context_kwargs = dict(
            user_agent=CHROME_UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
        )

        if profile_dir:
            path = Path(profile_dir)
            path.mkdir(parents=True, exist_ok=True)
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(path), **launch_args, **context_kwargs
            )
        else:
            browser = pw.chromium.launch(**launch_args)
            context = browser.new_context(**context_kwargs)

        context.set_default_timeout(30_000)
        try:
            yield context
        finally:
            context.close()


def open_page(context: BrowserContext, url: str, ready_selector: str) -> Page:
    """Navigate and wait past the WAF challenge.

    Both LZU sites sit behind the same JS challenge: the first response is a 412
    carrying the challenge script, and the browser then re-requests the page and
    renders it for real. So we wait for a selector that only exists on the
    genuine page, reloading once if it never shows up.
    """
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        page.wait_for_selector(ready_selector, state="attached", timeout=20_000)
    except Exception as first_error:
        page.reload(wait_until="domcontentloaded", timeout=45_000)
        try:
            page.wait_for_selector(ready_selector, state="attached", timeout=25_000)
        except Exception:
            _dump_failure(page, url)
            raise first_error
    return page


def _dump_failure(page: Page, url: str) -> None:
    """Leave enough evidence behind to explain a failed load."""
    if not os.environ.get("AUTOW_DUMP"):
        return
    out = Path(".debug")
    out.mkdir(exist_ok=True)
    slug = url.replace("https://", "").replace("/", "_").replace("?", "_")[:60]
    try:
        (out / f"{slug}.html").write_text(page.content(), encoding="utf-8")
        (out / f"{slug}.txt").write_text(
            page.evaluate("() => document.body.innerText"), encoding="utf-8"
        )
        page.screenshot(path=str(out / f"{slug}.png"), full_page=True)
        print(f"    [debug] dumped .debug/{slug}.*  title={page.title()!r}")
    except Exception as exc:  # pragma: no cover - diagnostics only
        print("    [debug] dump failed:", exc)
