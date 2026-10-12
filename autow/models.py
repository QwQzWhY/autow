"""Shared data structures.

Deliberately free of third-party imports. The QQ ingest path runs in a CI job
that installs nothing at all, so anything reachable from `autow.cli` on that
path must stay on the standard library — importing Playwright here would drag
a browser download into every incoming chat message.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RawNotice:
    """A notice or chat message before and after its body has been fetched."""

    url: str
    title: str
    date_text: str = ""
    title_full: str = ""
    published_text: str = ""
    published_at: str | None = None
    category: str = ""
    body: str = ""
    attachments: list[str] = field(default_factory=list)

    # Stable identity for dedupe. Derived from the URL for notices; callers that
    # have no URL (QQ messages) set it explicitly.
    external_id: str = ""

    def __post_init__(self) -> None:
        if not self.external_id and self.url:
            self.external_id = self.url.rstrip("/").rsplit("/", 1)[-1]
