"""FCA Financial Services Register client (free key + registered email).

Highly relevant for finance/fintech people: shows whether an individual is (or
was) approved by the FCA to perform regulated roles, and at which firms.

Auth: two headers — X-Auth-Email (the email you registered) and X-Auth-Key
(your API key). Both come from config/env; the module is inert without them.

Docs: https://register.fca.org.uk/Developer/s/
"""
from __future__ import annotations

from urllib.parse import quote

import requests

import config
from core.http import SESSION
from core.models import FCARecord

_REGISTER_WEB = "https://register.fca.org.uk/s/search?q="


class FCAError(RuntimeError):
    """Raised for network/HTTP/auth failures talking to the FCA register."""


def _headers() -> dict:
    return {
        "X-Auth-Email": config.FCA_API_EMAIL,
        "X-Auth-Key": config.FCA_API_KEY,
        "Accept": "application/json",
    }


def _get(path: str, params: dict | None = None) -> dict:
    if not config.fca_configured():
        raise FCAError("FCA not configured (need FCA_API_EMAIL and FCA_API_KEY).")
    url = f"{config.FCA_API_BASE_URL}{path}"
    try:
        resp = SESSION.get(
            url, params=params, headers=_headers(), timeout=config.REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:  # pragma: no cover - network
        raise FCAError(f"Network error contacting FCA register: {exc}") from exc
    if resp.status_code in (401, 403):
        raise FCAError("FCA rejected the credentials (check email + key).")
    if not resp.ok:
        raise FCAError(f"FCA register returned HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError:
        return {}


def search_individuals(name: str, max_records: int = 5) -> list[FCARecord]:
    """Search approved individuals by name; enrich the top matches with roles."""
    if not name.strip():
        return []
    data = _get("/Search", params={"q": name, "type": "individual"})
    records: list[FCARecord] = []
    for item in (data.get("Data") or [])[:max_records]:
        irn = item.get("Reference Number") or item.get("ReferenceNumber") or ""
        rec = FCARecord(
            name=item.get("Name", name),
            reference_number=irn,
            status=item.get("Status"),
            source_url=f"{_REGISTER_WEB}{quote(irn or name)}",
        )
        if irn:
            _add_controlled_functions(rec)
        records.append(rec)
    return records


def _add_controlled_functions(rec: FCARecord) -> None:
    """Attach current controlled functions (roles) + firms, best-effort."""
    try:
        data = _get(f"/Individuals/{rec.reference_number}/CF")
    except FCAError:
        return
    payload = data.get("Data") or {}
    # The CF endpoint groups arrangements under Current/Previous keys.
    buckets = []
    if isinstance(payload, dict):
        buckets = (payload.get("Current") or []) + (payload.get("Previous") or [])
    elif isinstance(payload, list):
        buckets = payload
    roles: list[str] = []
    firms: list[str] = []
    for arr in buckets:
        if not isinstance(arr, dict):
            continue
        role = arr.get("Name") or arr.get("Controlled Function")
        firm = arr.get("Firm Name") or arr.get("FirmName")
        if role and role not in roles:
            roles.append(role)
        if firm and firm not in firms:
            firms.append(firm)
    rec.roles = roles[:8]
    rec.firms = firms[:8]
