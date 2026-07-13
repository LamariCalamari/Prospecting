"""Prospecting signal derivation.

Turns the raw, sourced facts already in a OneSheet into the at-a-glance signals
a prospector scans for: number of active directorships, the person's largest
*disclosed* ownership stake (Companies House PSC band — verbatim, never
computed), sourced net worth, leverage / insolvency flags, and quick LinkedIn
lookup links.

Design rule (unchanged): we only surface data a source actually returned. The
'stake' is Companies House's own PSC band, shown as a range. We never estimate a
company valuation or a net-worth figure — where a number isn't published we show
the real signals a human uses to judge it, each still linked to its source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

from core.models import OneSheet

# "75-to-100" style share bands, weakest -> strongest, for picking the top one.
_BAND_ORDER = ["25-to-50", "50-to-75", "75-to-100"]
_NAME_STOPWORDS = {"mr", "mrs", "ms", "miss", "dr", "sir", "dame", "the", "of", "and"}


@dataclass
class OwnershipStake:
    """A person's disclosed control of one company (from a PSC filing)."""
    company_name: str
    company_number: str
    controls: list[str] = field(default_factory=list)  # humanized nature-of-control
    source_url: str = ""
    ceased_on: Optional[str] = None  # set when this control has ended (former stake)


@dataclass
class PersonSignals:
    """At-a-glance prospecting signals, all derived from sourced facts."""
    active_directorships: int = 0
    resigned_directorships: int = 0
    stakes: list[OwnershipStake] = field(default_factory=list)        # current
    former_stakes: list[OwnershipStake] = field(default_factory=list)  # ceased
    top_ownership: Optional[str] = None       # strongest *current* share band
    top_former_ownership: Optional[str] = None  # strongest *former* share band
    companies_with_charges: int = 0
    insolvency_companies: int = 0
    net_worth: Optional[str] = None      # from Wikidata, sourced, verbatim
    linkedin_url: Optional[str] = None   # verified handle from Wikidata
    linkedin_search_url: str = ""        # deep-link to LinkedIn people search
    google_linkedin_url: str = ""        # Google fallback: site:linkedin.com/in


def humanize_control(nature: str) -> str:
    """'ownership-of-shares-75-to-100-percent' -> 'Ownership of shares 75–100%'."""
    text = (
        nature.replace("-to-", "–")   # en dash between the two numbers
        .replace("-percent", "%")
        .replace("-", " ")
        .strip()
    )
    return text[:1].upper() + text[1:] if text else text


def _name_tokens(name: str) -> set[str]:
    toks = re.findall(r"[a-z]+", (name or "").lower())
    return {t for t in toks if len(t) > 1 and t not in _NAME_STOPWORDS}


def names_match(a: str, b: str) -> bool:
    """True when one name's significant tokens are contained in the other's.

    Handles 'STORONSKY Nikolay' vs 'Nikolay Storonsky' vs 'Mr Nikolay Storonsky'.
    Deliberately loose — matches are surfaced as *leads to verify*, never as a
    confirmed identification.
    """
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small.issubset(large)


def _band_rank(nature: str) -> int:
    for i, band in enumerate(_BAND_ORDER):
        if band in nature:
            return i
    return -1


def linkedin_search_url(name: str, company: str = "") -> str:
    keywords = f"{name} {company}".strip()
    return (
        "https://www.linkedin.com/search/results/people/?keywords="
        + quote_plus(keywords)
    )


def google_linkedin_url(name: str, company: str = "") -> str:
    query = f'site:linkedin.com/in "{name}" {company}'.strip()
    return "https://www.google.com/search?q=" + quote_plus(query)


def derive_signals(sheet: OneSheet, person_name: str) -> PersonSignals:
    """Compute prospecting signals from the already-assembled, sourced facts."""
    sig = PersonSignals()

    sig.active_directorships = sum(1 for a in sheet.appointments if a.status == "active")
    sig.resigned_directorships = sum(
        1 for a in sheet.appointments if a.status == "resigned"
    )

    # Ownership stakes: PSC entries whose name matches the confirmed person.
    # Current (not ceased) and former (ceased) are tracked separately — a ceased
    # 25–50% stake is still a strong prospecting signal, just historical.
    # Each nature-of-control is shown verbatim (humanized), never computed.
    best_current, best_current_nature = -1, None
    best_former, best_former_nature = -1, None
    for psc in sheet.psc_filings:
        if not psc.name or not names_match(psc.name, person_name):
            continue
        stake = OwnershipStake(
            company_name=psc.company_name,
            company_number=psc.company_number,
            controls=[humanize_control(n) for n in psc.natures_of_control],
            source_url=psc.source_url,
            ceased_on=psc.ceased_on,
        )
        if psc.ceased_on:
            sig.former_stakes.append(stake)
            for nature in psc.natures_of_control:
                rank = _band_rank(nature)
                if rank > best_former:
                    best_former, best_former_nature = rank, nature
        else:
            sig.stakes.append(stake)
            for nature in psc.natures_of_control:
                rank = _band_rank(nature)
                if rank > best_current:
                    best_current, best_current_nature = rank, nature
    if best_current_nature is not None:
        sig.top_ownership = humanize_control(best_current_nature)
    if best_former_nature is not None:
        sig.top_former_ownership = humanize_control(best_former_nature)

    sig.companies_with_charges = sum(1 for c in sheet.companies if c.has_charges)
    sig.insolvency_companies = sum(
        1 for c in sheet.companies if c.has_insolvency_history
    )

    if sheet.wikidata:
        sig.net_worth = sheet.wikidata.net_worth
        sig.linkedin_url = sheet.wikidata.linkedin

    # Bias the LinkedIn lookups with the strongest company signal we have.
    company_hint = ""
    if sig.stakes:
        company_hint = sig.stakes[0].company_name
    elif sig.former_stakes:
        company_hint = sig.former_stakes[0].company_name
    elif sheet.appointments:
        active = [a for a in sheet.appointments if a.status == "active"]
        company_hint = (active or sheet.appointments)[0].company_name
    sig.linkedin_search_url = linkedin_search_url(person_name, company_hint)
    sig.google_linkedin_url = google_linkedin_url(person_name, company_hint)

    return sig
