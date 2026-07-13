"""Assembly / orchestration layer.

Sits between the source modules and the UI. Two jobs:
  1. Gather disambiguation candidates (CH officers + Wikipedia hits).
  2. After the user confirms a specific person, build the full OneSheet.

Keeping this here (not in the UI) means the app logic is testable and the
Streamlit layer stays thin. Adding an LLM narrative layer later means adding
one function here, not touching every source.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import config
from core.models import (
    Appointment,
    CompanyInfo,
    NewsItem,
    OfficerCandidate,
    OneSheet,
    PSC,
    WikiCandidate,
    WikiSummary,
)
from sources import charity_commission as charity
from sources import companies_house as ch
from sources import fca
from sources import gazette
from sources import news as news_source
from sources import opensanctions
from sources import wikidata
from sources import wikipedia as wiki

# How many companies to pull deep-dive filing/charge data for (bounds API cost).
_DEEP_DIVE_COMPANY_LIMIT = 8


@dataclass
class Candidates:
    """Everything the disambiguation screen needs."""
    officers: list[OfficerCandidate] = field(default_factory=list)
    wiki: list[WikiCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # per-source, non-fatal


def find_candidates(name: str, context: str = "") -> Candidates:
    """Query Companies House and Wikipedia for possible matches.

    Errors from one source do not block the other — they are collected and
    surfaced to the user so they know a source was unavailable.
    """
    result = Candidates()
    query = name.strip()

    # Companies House officers
    try:
        officers = ch.search_officers(query)
        # Enrich the top few with a couple of company names for matching.
        # One page each is enough for a preview; fetch in parallel to stay fast.
        to_enrich = officers[:8]

        def _enrich(officer: OfficerCandidate) -> None:
            try:
                _, top = ch.get_officer_appointments(officer.officer_id, max_items=6)
                officer.top_companies = top
            except ch.CompaniesHouseError:
                pass  # keep the candidate; just without company enrichment

        if to_enrich:
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(_enrich, to_enrich))
        result.officers = officers
    except ch.CompaniesHouseError as exc:
        result.errors.append(str(exc))

    # Wikipedia — bias the query with context words if provided.
    wiki_query = f"{query} {context}".strip() if context.strip() else query
    try:
        result.wiki = wiki.search(wiki_query)
        if not result.wiki and context.strip():
            # Retry on the bare name if the context-loaded query found nothing.
            result.wiki = wiki.search(query)
    except wiki.WikipediaError as exc:
        result.errors.append(str(exc))

    return result


def build_one_sheet(
    confirmed_name: str,
    officer: Optional[OfficerCandidate],
    wiki_title: Optional[str],
    context: str = "",
    include_news: bool = True,
) -> OneSheet:
    """Assemble the full one-sheet for a *confirmed* person.

    Either an officer, a wiki_title, or both may be provided depending on what
    the user confirmed. Missing sources leave their sections blank.
    """
    sheet = OneSheet(confirmed_name=confirmed_name, context=context)

    # --- Companies House: appointments, companies, PSC ---
    if officer is not None:
        sheet.officer = officer
        appointments, _ = ch.get_officer_appointments(officer.officer_id)
        sheet.appointments = appointments

        # Deduplicate company numbers (preserving order), then fetch profile +
        # PSC for each in parallel — a prolific director can have many companies
        # and doing this sequentially is the main source of latency.
        seen: set[str] = set()
        company_numbers: list[str] = []
        for appt in appointments:
            num = appt.company_number
            if num and num not in seen:
                seen.add(num)
                company_numbers.append(num)

        # Only the first N companies get the deeper (filing history + charges)
        # treatment, to keep the number of API calls bounded.
        deep_set = set(company_numbers[:_DEEP_DIVE_COMPANY_LIMIT])

        def _fetch_company(num: str):
            profile = psc = None
            try:
                profile = ch.get_company_profile(num)
            except ch.CompaniesHouseError:
                pass
            try:
                psc = ch.get_psc(num)
            except ch.CompaniesHouseError:
                pass
            if profile and num in deep_set:
                try:
                    profile.recent_filings = ch.get_filing_history(num)
                except ch.CompaniesHouseError:
                    pass
                if profile.has_charges:  # only call the charges endpoint if any exist
                    try:
                        profile.charges = ch.get_charges(num)
                    except ch.CompaniesHouseError:
                        pass
            return profile, psc

        if company_numbers:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for profile, psc in pool.map(_fetch_company, company_numbers):
                    if profile:
                        sheet.companies.append(profile)
                    if psc:
                        sheet.psc_filings.extend(psc)

    # --- Wikipedia: confident summary only ---
    if wiki_title:
        try:
            summary = wiki.get_summary(wiki_title)
            if summary:
                sheet.wiki = summary
        except wiki.WikipediaError:
            pass

    # --- Person-level deep-dive sources (run concurrently) ---
    _add_person_sources(sheet, confirmed_name, wiki_title, officer, context, include_news)

    return sheet


def _add_person_sources(
    sheet: OneSheet,
    confirmed_name: str,
    wiki_title: Optional[str],
    officer: Optional[OfficerCandidate],
    context: str,
    include_news: bool,
) -> None:
    """Fetch the independent person-level sources in parallel and attach them.

    Every source is best-effort: a failure or a missing key adds a note to
    sheet.source_notes and leaves that section blank — it never aborts the sheet.
    """
    def _wikidata():
        if not wiki_title:
            return
        try:
            sheet.wikidata = wikidata.enrich_person(wiki_title)
        except wikidata.WikidataError as exc:
            sheet.source_notes.append(f"Wikidata: {exc}")

    def _news():
        if not include_news:
            return
        try:
            sheet.news = news_source.search_news(
                _news_query(confirmed_name, officer, context)
            )
        except Exception:  # noqa: BLE001 - best-effort
            sheet.news = []

    def _gazette():
        try:
            sheet.gazette_notices = gazette.search_notices(confirmed_name)
        except gazette.GazetteError as exc:
            sheet.source_notes.append(f"The Gazette: {exc}")

    def _fca():
        if not config.fca_configured():
            sheet.source_notes.append("FCA register: not configured (add FCA key + email).")
            return
        try:
            sheet.fca_records = fca.search_individuals(confirmed_name)
        except fca.FCAError as exc:
            sheet.source_notes.append(f"FCA register: {exc}")

    def _sanctions():
        if not config.opensanctions_configured():
            sheet.source_notes.append("OpenSanctions: not configured (add API key).")
            return
        try:
            sheet.sanctions_hits = opensanctions.search_person(confirmed_name)
        except opensanctions.OpenSanctionsError as exc:
            sheet.source_notes.append(f"OpenSanctions: {exc}")

    def _charity():
        if not config.charity_commission_configured():
            sheet.source_notes.append("Charity Commission: not configured (add API key).")
            return
        try:
            sheet.charities = charity.search_charities(confirmed_name)
        except charity.CharityCommissionError as exc:
            sheet.source_notes.append(f"Charity Commission: {exc}")

    tasks = [_wikidata, _news, _gazette, _fca, _sanctions, _charity]
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        list(pool.map(lambda fn: fn(), tasks))


def _news_query(
    name: str, officer: Optional[OfficerCandidate], context: str
) -> str:
    """Build a focused news query. Prefer name + a strong company signal."""
    parts = [f'"{name}"']
    if officer and officer.top_companies:
        parts.append(officer.top_companies[0])
    elif context.strip():
        parts.append(context.strip())
    return " ".join(parts)
