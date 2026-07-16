"""Electoral Commission donations register (free, no key).

Registered UK political donations are public record and a *direct* wealth
signal: someone who gives £100k to a party demonstrably has £100k to give.

Search strategy: the API's full-text search ANDs tokens, so "Nick Candy"
misses "Mr Nicholas Candy". We query the surname (most selective single
token that's guaranteed present), then keep only Individual donors whose
name matches the person (nickname-aware) — matches are still leads to
verify, as ever.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote_plus

import requests

import config
from core import prospecting
from core.http import SESSION
from core.models import Donation

EC_API = "https://search.electoralcommission.org.uk/api/search/Donations"
EC_WEB = "https://search.electoralcommission.org.uk/?query={q}&et=pp&et=ppm&et=tp&et=perpar&et=rd"

_EPOCH_RE = re.compile(r"/Date\((\d+)")


class ElectoralCommissionError(RuntimeError):
    """Raised for network/HTTP failures talking to the Electoral Commission."""


def parse_ec_date(raw: Optional[str]) -> Optional[str]:
    """'/Date(1759276800000)/' -> 'YYYY-MM-DD'."""
    if not raw:
        return None
    m = _EPOCH_RE.search(raw)
    if not m:
        return raw
    from datetime import datetime, timezone

    try:
        dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def search_donations(person_name: str, limit: int = 100) -> list[Donation]:
    """Registered donations by individuals matching this person's name."""
    tokens = [t for t in re.findall(r"[A-Za-z]+", person_name) if len(t) > 1]
    if not tokens:
        return []
    surname = tokens[-1]
    try:
        resp = SESSION.get(
            EC_API,
            params={
                "rows": limit,
                "query": surname,
                "sort": "AcceptedDate",
                "order": "desc",
            },
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:  # pragma: no cover - network
        raise ElectoralCommissionError(
            f"Network error contacting the Electoral Commission: {exc}"
        ) from exc
    except ValueError:
        return []

    donations: list[Donation] = []
    for item in data.get("Result") or []:
        if (item.get("DonorStatus") or "") != "Individual":
            continue
        donor = (item.get("DonorName") or "").strip()
        if not donor or not prospecting.names_match(donor, person_name):
            continue
        donations.append(
            Donation(
                donor_name=donor,
                recipient=item.get("RegulatedEntityName") or "(unknown recipient)",
                value=item.get("Value"),
                date=parse_ec_date(item.get("AcceptedDate")),
                source_url=EC_WEB.format(q=quote_plus(donor)),
            )
        )
    return donations
