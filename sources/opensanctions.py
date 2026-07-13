"""OpenSanctions client (PEP / sanctions / watchlist screening).

Turns "News & controversies" into real due-diligence screening: is this person
a Politically Exposed Person, sanctioned, or on a watchlist? We surface matches
with their datasets and topics as links for the user to verify — a name match
is a lead to check, never a confirmed identification.

Auth: header `Authorization: ApiKey <key>`. Free tier keys are available at
https://www.opensanctions.org/api/ . Inert without a key.
"""
from __future__ import annotations

import requests

import config
from core.http import SESSION
from core.models import SanctionsHit


class OpenSanctionsError(RuntimeError):
    """Raised for network/HTTP/auth failures talking to OpenSanctions."""


def search_person(name: str, limit: int = 5) -> list[SanctionsHit]:
    if not name.strip():
        return []
    if not config.opensanctions_configured():
        raise OpenSanctionsError("OpenSanctions not configured (need OPENSANCTIONS_API_KEY).")

    url = f"{config.OPENSANCTIONS_API_URL}/search/default"
    params = {"q": name, "limit": limit, "schema": "Person"}
    headers = {"Authorization": f"ApiKey {config.OPENSANCTIONS_API_KEY}"}
    try:
        resp = SESSION.get(url, params=params, headers=headers, timeout=config.REQUEST_TIMEOUT)
    except requests.RequestException as exc:  # pragma: no cover - network
        raise OpenSanctionsError(f"Network error contacting OpenSanctions: {exc}") from exc
    if resp.status_code in (401, 403):
        raise OpenSanctionsError("OpenSanctions rejected the API key.")
    if not resp.ok:
        raise OpenSanctionsError(f"OpenSanctions returned HTTP {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError:
        return []

    hits: list[SanctionsHit] = []
    for ent in data.get("results", [])[:limit]:
        props = ent.get("properties") or {}
        entity_id = ent.get("id", "")
        hits.append(
            SanctionsHit(
                name=ent.get("caption", name),
                schema=ent.get("schema"),
                topics=_as_list(ent.get("topics") or props.get("topics")),
                datasets=_as_list(ent.get("datasets")),
                countries=_as_list(props.get("country") or props.get("nationality")),
                source_url=f"https://www.opensanctions.org/entities/{entity_id}/"
                if entity_id
                else "https://www.opensanctions.org/",
                score=ent.get("score"),
            )
        )
    return hits


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]
