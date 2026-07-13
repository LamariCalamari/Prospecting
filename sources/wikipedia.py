"""Wikipedia client using the public MediaWiki + REST APIs (no key needed).

- search(): MediaWiki action API, list=search  -> disambiguation candidates
- get_summary(): REST page/summary endpoint  -> confident bio for the sheet

We only ever return what the API gives us; empty results yield [] / None.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

import requests

import config
from core.http import SESSION
from core.models import WikiCandidate, WikiSummary


class WikipediaError(RuntimeError):
    """Raised for network/HTTP failures talking to Wikipedia."""


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def search(query: str, limit: int = 8) -> list[WikiCandidate]:
    if not query.strip():
        return []
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
    }
    try:
        resp = SESSION.get(
            config.WIKIPEDIA_API_URL,
            params=params,
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:  # pragma: no cover - network
        raise WikipediaError(f"Network error contacting Wikipedia: {exc}") from exc
    except ValueError:
        return []

    results: list[WikiCandidate] = []
    for item in data.get("query", {}).get("search", []):
        title = item.get("title", "")
        results.append(
            WikiCandidate(
                title=title,
                pageid=item.get("pageid", 0),
                snippet=_strip_html(item.get("snippet", "")),
                url=f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            )
        )
    return results


def get_summary(title: str) -> Optional[WikiSummary]:
    """Fetch the REST summary for an exact page title."""
    if not title.strip():
        return None
    url = f"{config.WIKIPEDIA_REST_URL}/page/summary/{quote(title.replace(' ', '_'))}"
    try:
        resp = SESSION.get(url, timeout=config.REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:  # pragma: no cover - network
        raise WikipediaError(f"Network error contacting Wikipedia: {exc}") from exc
    except ValueError:
        return None

    # Skip disambiguation pages — they are not a confident single-person match.
    if data.get("type") == "disambiguation":
        return None

    extract = data.get("extract", "")
    if not extract:
        return None

    content_urls = (data.get("content_urls") or {}).get("desktop") or {}
    thumbnail = (data.get("thumbnail") or {}).get("source")
    return WikiSummary(
        title=data.get("title", title),
        extract=extract,
        description=data.get("description"),
        url=content_urls.get("page")
        or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
        thumbnail=thumbnail,
    )
