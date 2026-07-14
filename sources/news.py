"""News search via Google News RSS (free, no API key).

We surface headlines + links only. We never assert the content of an article
as fact — the user reviews the source. Google News RSS returns an Atom/RSS
feed we parse with feedparser.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

import feedparser

import config
from core.models import NewsItem

_DATE_RE = re.compile(r"\d{1,2} \w{3} \d{4}")


def _short_date(published: str | None) -> str | None:
    """'Sun, 12 Jul 2026 17:15:00 GMT' -> '12 Jul 2026'."""
    if not published:
        return None
    m = _DATE_RE.search(published)
    return m.group(0) if m else published


def search_news(query: str, limit: int = 10) -> list[NewsItem]:
    if not query.strip():
        return []
    params = {
        "q": query,
        "hl": config.NEWS_HL,
        "gl": config.NEWS_GL,
        "ceid": config.NEWS_CEID,
    }
    feed_url = f"{config.GOOGLE_NEWS_RSS_URL}?{urlencode(params)}"

    # feedparser fetches the URL itself; pass a UA so Google serves the feed.
    parsed = feedparser.parse(feed_url, request_headers={"User-Agent": config.USER_AGENT})

    items: list[NewsItem] = []
    for entry in parsed.entries[:limit]:
        # Google News prefixes the title with the outlet after a " - " sometimes;
        # the source is also available structured via entry.source.
        source = None
        if getattr(entry, "source", None):
            source = getattr(entry.source, "title", None)
        title = getattr(entry, "title", "(no title)")
        # Google News appends " - <outlet>" to titles; the outlet is shown
        # separately, so drop the duplicate suffix.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")]
        items.append(
            NewsItem(
                title=title,
                link=getattr(entry, "link", ""),
                source=source,
                published=_short_date(getattr(entry, "published", None)),
            )
        )
    return items
