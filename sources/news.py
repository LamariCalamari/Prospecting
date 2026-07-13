"""News search via Google News RSS (free, no API key).

We surface headlines + links only. We never assert the content of an article
as fact — the user reviews the source. Google News RSS returns an Atom/RSS
feed we parse with feedparser.
"""
from __future__ import annotations

from urllib.parse import urlencode

import feedparser

import config
from core.models import NewsItem


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
        items.append(
            NewsItem(
                title=getattr(entry, "title", "(no title)"),
                link=getattr(entry, "link", ""),
                source=source,
                published=getattr(entry, "published", None),
            )
        )
    return items
