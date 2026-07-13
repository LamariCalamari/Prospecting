"""Charity Commission for England & Wales client (free subscription key).

Important scope note: the official API is charity-centric and does NOT support
searching trustees by person name. So this module searches the register for
*charities whose name matches the query* — which reliably surfaces the
eponymous foundations wealthy individuals often establish (e.g. "The <Name>
Foundation"). It does not, and cannot via this API, list every trusteeship a
person holds. Treat results as leads to open and verify.

Auth: header `Ocp-Apim-Subscription-Key`. Inert without a key.
Register your key at https://api-portal.charitycommission.gov.uk/ .
"""
from __future__ import annotations

from urllib.parse import quote

import requests

import config
from core.http import SESSION
from core.models import CharityRecord

_REGISTER_WEB = (
    "https://register-of-charities.charitycommission.gov.uk/charity-search"
)


class CharityCommissionError(RuntimeError):
    """Raised for network/HTTP/auth failures talking to the Charity Commission."""


def search_charities(name: str, limit: int = 5) -> list[CharityRecord]:
    """Search the register for charities whose name matches `name`."""
    if not name.strip():
        return []
    if not config.charity_commission_configured():
        raise CharityCommissionError(
            "Charity Commission not configured (need CHARITY_COMMISSION_API_KEY)."
        )
    url = f"{config.CHARITY_COMMISSION_API_BASE}/searchCharityName/{quote(name)}"
    headers = {
        "Ocp-Apim-Subscription-Key": config.CHARITY_COMMISSION_API_KEY,
        "Accept": "application/json",
    }
    try:
        resp = SESSION.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
    except requests.RequestException as exc:  # pragma: no cover - network
        raise CharityCommissionError(
            f"Network error contacting Charity Commission: {exc}"
        ) from exc
    if resp.status_code in (401, 403):
        raise CharityCommissionError("Charity Commission rejected the subscription key.")
    if not resp.ok:
        raise CharityCommissionError(
            f"Charity Commission returned HTTP {resp.status_code}."
        )
    try:
        data = resp.json()
    except ValueError:
        return []

    items = data if isinstance(data, list) else data.get("value") or data.get("Data") or []
    records: list[CharityRecord] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        number = (
            item.get("reg_charity_number")
            or item.get("organisation_number")
            or item.get("charity_number")
        )
        records.append(
            CharityRecord(
                name=item.get("charity_name") or item.get("name") or "(unnamed charity)",
                charity_number=str(number) if number else None,
                status=item.get("charity_registration_status") or item.get("status"),
                activities=item.get("charity_activities") or item.get("activities"),
                source_url=(
                    f"{_REGISTER_WEB}/-/charity-details/{number}"
                    if number
                    else _REGISTER_WEB
                ),
            )
        )
    return records
