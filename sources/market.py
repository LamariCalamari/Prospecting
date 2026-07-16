"""Listed-market lookup (Yahoo Finance public endpoints, free, no key).

Whether a company someone runs is *publicly listed* is a wealth-class marker:
listed-company directors have disclosed pay, usually equity, and their company
has a public valuation. We use two open Yahoo endpoints (search + chart);
they're unofficial, so everything is best-effort and a failure just leaves
the section blank.
"""
from __future__ import annotations

import re
from typing import Optional

import requests

import config
from core.http import SESSION
from core.models import ListedQuote

_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# Yahoo rejects the default python-requests UA.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ProspectingResearch/1.0)"}

_NOISE = re.compile(
    r"\b(plc|ltd|limited|llp|holdings?|group|uk|the)\b", re.IGNORECASE
)


def _clean(name: str) -> set[str]:
    text = _NOISE.sub(" ", name or "").lower()
    return {t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 1}


def same_company(ch_name: str, quote_name: str) -> bool:
    """Do a CH company name and a Yahoo listing name refer to the same company?

    Compare the distinctive tokens after stripping legal/structural noise
    ('CERILLION TECHNOLOGIES LIMITED' vs 'Cerillion Plc' -> {cerillion,
    technologies} vs {cerillion}) — one side must contain the other.
    """
    a, b = _clean(ch_name), _clean(quote_name)
    if not a or not b:
        return False
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return small.issubset(large)


def find_listed(company_name: str) -> Optional[ListedQuote]:
    """Is this company listed? Returns a quote when a confident match exists."""
    if not company_name.strip():
        return None
    try:
        resp = SESSION.get(
            _SEARCH_URL,
            params={"q": company_name, "quotesCount": 5, "newsCount": 0},
            headers=_HEADERS,
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        quotes = resp.json().get("quotes") or []
    except (requests.RequestException, ValueError):
        return None

    equities = [
        q for q in quotes
        if q.get("quoteType") == "EQUITY"
        and same_company(company_name, q.get("longname") or q.get("shortname") or "")
    ]
    if not equities:
        return None
    # Prefer the London listing when the same company trades on several venues.
    equities.sort(key=lambda q: 0 if str(q.get("symbol", "")).endswith(".L") else 1)
    best = equities[0]
    symbol = best.get("symbol", "")

    price = currency = exchange = None
    try:
        resp = SESSION.get(
            _CHART_URL.format(symbol=symbol),
            params={"range": "1d", "interval": "1d"},
            headers=_HEADERS,
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        currency = meta.get("currency")
        exchange = meta.get("fullExchangeName")
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        pass  # listing confirmed even if the quote fetch failed

    return ListedQuote(
        company_name=company_name,
        symbol=symbol,
        exchange=exchange or best.get("exchange"),
        price=price,
        currency=currency,
        source_url=f"https://finance.yahoo.com/quote/{symbol}",
    )
