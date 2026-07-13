"""Wikidata client (free, no key).

Given the *confirmed* Wikipedia page title, resolve the linked Wikidata entity
and pull structured facts that free-text Wikipedia hides: net worth, positions
held, education, occupation, official website, and verified social handles.

Accuracy notes:
- We resolve the entity via the Wikipedia page's `wikibase_item` pageprop, so
  this is tied to the exact page the user already confirmed — no fuzzy name
  matching, no risk of grabbing a different person.
- Net worth is shown verbatim (amount + currency + the statement's point-in-time
  date). We never convert currencies or infer a figure.
"""
from __future__ import annotations

from typing import Optional

import requests

import config
from core.http import SESSION
from core.models import WikidataFacts

# Property ids we care about.
P_NET_WORTH = "P2218"
P_OCCUPATION = "P106"
P_POSITION = "P39"
P_EDUCATED_AT = "P69"
P_OFFICIAL_WEBSITE = "P856"
P_TWITTER = "P2002"
P_LINKEDIN = "P6634"
P_POINT_IN_TIME = "P585"  # qualifier on net worth


class WikidataError(RuntimeError):
    """Raised for network/HTTP failures talking to Wikidata."""


def _qid_for_title(title: str) -> Optional[str]:
    """Resolve an English Wikipedia page title to its Wikidata QID."""
    params = {
        "action": "query",
        "prop": "pageprops",
        "ppprop": "wikibase_item",
        "redirects": 1,
        "titles": title,
        "format": "json",
    }
    try:
        resp = SESSION.get(
            config.WIKIPEDIA_API_URL, params=params, timeout=config.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:  # pragma: no cover - network
        raise WikidataError(f"Network error resolving Wikidata id: {exc}") from exc
    except ValueError:
        return None
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        qid = (page.get("pageprops") or {}).get("wikibase_item")
        if qid:
            return qid
    return None


def _get_entities(ids: list[str], props: str) -> dict:
    if not ids:
        return {}
    params = {
        "action": "wbgetentities",
        "ids": "|".join(ids[:50]),  # API caps at 50 ids per call
        "props": props,
        "languages": "en",
        "format": "json",
    }
    try:
        resp = SESSION.get(
            config.WIKIDATA_API_URL, params=params, timeout=config.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json().get("entities", {})
    except requests.RequestException as exc:  # pragma: no cover - network
        raise WikidataError(f"Network error contacting Wikidata: {exc}") from exc
    except ValueError:
        return {}


def _claim_values(claims: dict, prop: str) -> list[dict]:
    """Return the mainsnak datavalues for a property (with the full claim)."""
    return claims.get(prop, []) or []


def _entity_id_of(claim: dict) -> Optional[str]:
    snak = claim.get("mainsnak", {})
    val = (snak.get("datavalue") or {}).get("value")
    if isinstance(val, dict):
        return val.get("id")
    return None


def _string_of(claim: dict) -> Optional[str]:
    snak = claim.get("mainsnak", {})
    return (snak.get("datavalue") or {}).get("value")


def _format_net_worth(claim: dict, unit_labels: dict) -> Optional[str]:
    snak = claim.get("mainsnak", {})
    value = (snak.get("datavalue") or {}).get("value")
    if not isinstance(value, dict):
        return None
    amount = (value.get("amount") or "").lstrip("+")
    if not amount:
        return None
    try:
        pretty = f"{float(amount):,.0f}"
    except ValueError:
        pretty = amount
    unit_uri = value.get("unit", "")
    unit_qid = unit_uri.rsplit("/", 1)[-1] if unit_uri and unit_uri != "1" else ""
    unit = unit_labels.get(unit_qid, "")
    # Point-in-time qualifier, if present.
    as_of = ""
    for q in (claim.get("qualifiers", {}) or {}).get(P_POINT_IN_TIME, []):
        t = (q.get("datavalue") or {}).get("value", {})
        time_str = t.get("time", "")
        if time_str:
            as_of = time_str.lstrip("+")[:4]  # year
            break
    out = pretty + (f" {unit}" if unit else "")
    if as_of:
        out += f" (as of {as_of})"
    return out


def enrich_person(wikipedia_title: str) -> Optional[WikidataFacts]:
    """Build WikidataFacts for the confirmed person, or None if no entity."""
    qid = _qid_for_title(wikipedia_title)
    if not qid:
        return None

    entities = _get_entities([qid], props="claims")
    entity = entities.get(qid, {})
    claims = entity.get("claims", {}) or {}

    # Collect referenced entity ids (occupations, positions, schools, net-worth
    # currency units) so we can resolve their labels in one batched call.
    ref_ids: set[str] = set()
    for prop in (P_OCCUPATION, P_POSITION, P_EDUCATED_AT):
        for claim in _claim_values(claims, prop):
            eid = _entity_id_of(claim)
            if eid:
                ref_ids.add(eid)
    for claim in _claim_values(claims, P_NET_WORTH):
        value = (claim.get("mainsnak", {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict):
            unit_uri = value.get("unit", "")
            if unit_uri and unit_uri != "1":
                ref_ids.add(unit_uri.rsplit("/", 1)[-1])

    label_entities = _get_entities(sorted(ref_ids), props="labels") if ref_ids else {}

    def label(eid: Optional[str]) -> Optional[str]:
        if not eid:
            return None
        ent = label_entities.get(eid, {})
        return (ent.get("labels", {}).get("en") or {}).get("value")

    facts = WikidataFacts(
        qid=qid,
        source_url=f"https://www.wikidata.org/wiki/{qid}",
    )

    # Net worth (first statement). Pass a qid->label map for currency units.
    unit_labels = {eid: label(eid) or "" for eid in ref_ids}
    for claim in _claim_values(claims, P_NET_WORTH):
        nw = _format_net_worth(claim, unit_labels)
        if nw:
            facts.net_worth = nw
            break

    facts.occupations = [
        lbl for c in _claim_values(claims, P_OCCUPATION)
        if (lbl := label(_entity_id_of(c)))
    ][:6]
    facts.positions = [
        lbl for c in _claim_values(claims, P_POSITION)
        if (lbl := label(_entity_id_of(c)))
    ][:8]
    facts.educated_at = [
        lbl for c in _claim_values(claims, P_EDUCATED_AT)
        if (lbl := label(_entity_id_of(c)))
    ][:5]

    website = None
    for c in _claim_values(claims, P_OFFICIAL_WEBSITE):
        website = _string_of(c)
        if website:
            break
    facts.official_website = website

    for c in _claim_values(claims, P_TWITTER):
        handle = _string_of(c)
        if handle:
            facts.twitter = f"https://twitter.com/{handle}"
            break
    for c in _claim_values(claims, P_LINKEDIN):
        lid = _string_of(c)
        if lid:
            facts.linkedin = f"https://www.linkedin.com/in/{lid}"
            break

    # If literally nothing was found, treat as no useful entity.
    if not any(
        [facts.net_worth, facts.occupations, facts.positions, facts.educated_at,
         facts.official_website, facts.twitter, facts.linkedin]
    ):
        return None
    return facts
