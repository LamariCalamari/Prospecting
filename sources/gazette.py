"""The Gazette client (official UK public record, free, no key).

The Gazette publishes statutory notices: insolvency, bankruptcy, company
strike-off, appointments, etc. We surface matching notices as links for the
user to review — we never characterise a person's situation from them.

Implementation note: the site's `data.json` endpoint currently 500s, but the
Atom `data.feed` endpoint is reliable, so we parse that with feedparser. The
search is full-text, so for common names expect company-name matches too — it
is a review aid, not an assertion about the person.
"""
from __future__ import annotations

import feedparser

import config
from core.http import SESSION
from core.models import GazetteNotice

_FEED_URL = config.GAZETTE_DATA_URL.replace("data.json", "data.feed")
_BASE = "https://www.thegazette.co.uk"


class GazetteError(RuntimeError):
    """Raised for network/HTTP failures talking to The Gazette."""


def search_notices(query: str, limit: int = 10) -> list[GazetteNotice]:
    if not query.strip():
        return []
    params = {"text": f'"{query}"'}  # quoted phrase for precision
    try:
        resp = SESSION.get(_FEED_URL, params=params, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - network/parse are best-effort
        raise GazetteError(f"Network error contacting The Gazette: {exc}") from exc

    parsed = feedparser.parse(resp.content)
    notices: list[GazetteNotice] = []
    for entry in parsed.entries[:limit]:
        link = getattr(entry, "link", "") or ""
        if link.startswith("/"):
            link = _BASE + link
        notices.append(
            GazetteNotice(
                title=getattr(entry, "title", "(untitled notice)"),
                link=link,
                published=getattr(entry, "published", None),
                notice_type=None,
            )
        )
    return notices
