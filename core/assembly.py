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
from core import prospecting, valuation
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
# How many stake companies to fetch filed accounts (iXBRL) for. Each costs ~3
# requests; CH allows 600 per 5 minutes, so this stays well inside the limit.
_ACCOUNTS_FETCH_LIMIT = 20


@dataclass
class Candidates:
    """Everything the disambiguation screen needs."""
    officers: list[OfficerCandidate] = field(default_factory=list)
    wiki: list[WikiCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # per-source, non-fatal
    birth_year: Optional[str] = None  # from the top Wikipedia match, for CH linking


@dataclass
class PersonCandidate:
    """One *person* the user can pick — identity first, sources attached.

    Merges a Wikipedia identity (who they are) with their Companies House
    records (what they run), so the user picks a human, not a filing fragment.
    """
    display_name: str
    description: Optional[str] = None   # "British luxury property developer…"
    thumbnail: Optional[str] = None
    birth_year: Optional[str] = None
    wiki_title: Optional[str] = None
    officer: Optional[OfficerCandidate] = None  # representative CH record
    top_companies: list[str] = field(default_factory=list)
    n_appointments: int = 0

    @property
    def has_wiki(self) -> bool:
        return self.wiki_title is not None

    @property
    def has_ch(self) -> bool:
        return self.officer is not None


def find_candidates(name: str, context: str = "") -> Candidates:
    """Query Companies House and Wikipedia for possible matches.

    Errors from one source do not block the other — they are collected and
    surfaced to the user so they know a source was unavailable.
    """
    result = Candidates()
    query = name.strip()
    officers: list[OfficerCandidate] = []

    # Companies House officers
    try:
        officers = ch.search_officers(query)
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

    # Pull the birth year AND the registered full name from the best-matching
    # Wikipedia page. Birth year links the right Companies House record (avoiding
    # namesakes); the full name lets us re-search CH when the user typed a
    # nickname (e.g. "Nick" -> "Nicholas Anthony Christopher Candy").
    lead_name: Optional[str] = None
    bw = prospecting.best_wiki_index(result.wiki, query)
    if bw is not None:
        try:
            summary = wiki.get_summary(result.wiki[bw].title)
            if summary:
                result.birth_year = prospecting.extract_birth_year(
                    summary.description or ""
                ) or prospecting.extract_birth_year(summary.extract or "")
                lead_name = prospecting.extract_lead_name(summary.extract or "")
        except wiki.WikipediaError:
            pass

    # If the typed query didn't surface a confident person in Companies House but
    # Wikipedia gave us a fuller registered name, search CH again by that name and
    # merge in anything new (deduped by officer id).
    if (
        lead_name
        and lead_name.lower() != query.lower()
        and prospecting.best_officer_index(officers, query, result.birth_year) is None
    ):
        try:
            seen_ids = {o.officer_id for o in officers}
            for extra in ch.search_officers(lead_name):
                if extra.officer_id not in seen_ids:
                    officers.append(extra)
                    seen_ids.add(extra.officer_id)
        except ch.CompaniesHouseError:
            pass

    # Enrich the top officers with a couple of company names for at-a-glance
    # matching. Done once over the merged list, in parallel to stay fast.
    def _enrich(officer: OfficerCandidate) -> None:
        try:
            _, top, ident = ch.get_officer_appointments(officer.officer_id, max_items=6)
            officer.top_companies = top
            officer.occupation = ident.get("occupation")
            officer.nationality = ident.get("nationality")
            officer.residence = ident.get("country_of_residence")
        except ch.CompaniesHouseError:
            pass

    if officers:
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(_enrich, officers[:10]))
    result.officers = officers

    return result


def merge_people(wiki_persons: list[dict], groups, query: str) -> list[PersonCandidate]:
    """Merge Wikipedia identities with Companies House officer groups.

    `wiki_persons` items: {"title", "description", "thumbnail", "birth_year",
    "lead_name"}. Prominent (Wikipedia-known) people rank first — the targets
    of this tool are usually notable founders/wealthy individuals — with their
    CH group attached by name + birth year. Remaining CH-only groups follow,
    largest first. Pure function so ranking/matching is unit-testable.
    """
    people: list[PersonCandidate] = []
    remaining = list(groups)

    for wp in wiki_persons:
        match = None
        for g in remaining:
            name_ok = prospecting.names_match(
                g.primary.name or "", wp.get("lead_name") or wp["title"]
            ) or prospecting.names_match(g.primary.name or "", wp["title"])
            if not name_ok:
                continue
            if (
                wp.get("birth_year")
                and g.birth_year
                and g.birth_year != wp["birth_year"]
            ):
                continue  # a namesake, not this person
            match = g
            break
        if match is not None:
            remaining.remove(match)
        people.append(
            PersonCandidate(
                display_name=wp["title"],
                description=wp.get("description"),
                thumbnail=wp.get("thumbnail"),
                birth_year=wp.get("birth_year"),
                wiki_title=wp["title"],
                officer=match.primary if match else None,
                top_companies=(match.top_companies if match else [])[:4],
                n_appointments=match.total_appointments if match else 0,
            )
        )

    # People with a CH footprint but no Wikipedia page — still real candidates,
    # ranked below the notable ones, largest CH footprint first.
    for g in remaining[:4]:
        if not prospecting.names_match(g.primary.name or "", query):
            continue
        # No Wikipedia page — describe them from what CH itself files:
        # occupation, nationality, residence ("Property Developer · British").
        desc_bits = [b for b in (g.occupation, g.nationality, g.residence) if b]
        people.append(
            PersonCandidate(
                display_name=prospecting.company_case(g.primary.name or ""),
                description=(" · ".join(desc_bits) + " (per CH filings)"
                             if desc_bits else None),
                birth_year=g.birth_year,
                officer=g.primary,
                top_companies=g.top_companies[:4],
                n_appointments=max(
                    g.total_appointments, g.primary.appointment_count or 0
                ),
            )
        )
    return people[:6]


def find_people(name: str, context: str = "") -> tuple[list[PersonCandidate], Candidates]:
    """Identity-first search: a short ranked list of likely *people*.

    Returns (people, raw candidates) — the raw candidates feed the manual
    fallback UI for cases the automatic merge gets wrong.
    """
    cands = find_candidates(name, context)
    query = name.strip()

    # Enrich the name-matching Wikipedia hits into person profiles (parallel).
    matching = [w for w in cands.wiki
                if prospecting.names_match(w.title, query)][:5]

    def _profile(w) -> Optional[dict]:
        try:
            summary = wiki.get_summary(w.title)
        except wiki.WikipediaError:
            return None
        if not summary:
            return None
        birth = prospecting.extract_birth_year(summary.description or "") or \
            prospecting.extract_birth_year(summary.extract or "")
        return {
            "title": summary.title,
            "description": summary.description,
            "thumbnail": summary.thumbnail,
            "birth_year": birth,
            "lead_name": prospecting.extract_lead_name(summary.extract or ""),
        }

    wiki_persons: list[dict] = []
    if matching:
        with ThreadPoolExecutor(max_workers=5) as pool:
            for prof in pool.map(_profile, matching):
                if prof:
                    wiki_persons.append(prof)

    groups = prospecting.group_officers(cands.officers)
    return merge_people(wiki_persons, groups, query), cands


def _merged_appointments(officer: OfficerCandidate) -> list:
    """All appointments for a person, merged across their CH officer records.

    Companies House fragments one human across several officer ids (roughly one
    per filing style), so reading a single record can silently drop most of a
    person's directorships. We re-search by the officer's name, keep records
    with the same name whose birth date doesn't conflict with the chosen one,
    and merge their appointments (deduped by company + role + date).
    """
    records = [officer]
    try:
        for rec in ch.search_officers(officer.name, limit=20):
            if rec.officer_id == officer.officer_id:
                continue
            if not prospecting.names_match(rec.name, officer.name):
                continue
            # Same name but a *different* birth date = a namesake; skip. A record
            # with no birth date is kept — CH often omits it on older filings.
            if (
                officer.date_of_birth
                and rec.date_of_birth
                and rec.date_of_birth != officer.date_of_birth
            ):
                continue
            records.append(rec)
    except ch.CompaniesHouseError:
        pass  # merging is best-effort; the chosen record alone still works

    # Fetch the fattest records first: CH usually has one consolidated record
    # holding most of the person's appointments, and it must not fall past the
    # fetch cap because of a long tail of one-appointment fragments.
    records.sort(key=lambda r: r.appointment_count or 0, reverse=True)

    def _fetch(rec: OfficerCandidate):
        try:
            appts, _, _ = ch.get_officer_appointments(rec.officer_id)
            return appts
        except ch.CompaniesHouseError:
            return []

    merged = []
    seen: set[tuple] = set()
    with ThreadPoolExecutor(max_workers=6) as pool:
        for appts in pool.map(_fetch, records[:8]):
            for a in appts:
                key = (a.company_number, a.officer_role, a.appointed_on)
                if key not in seen:
                    seen.add(key)
                    merged.append(a)
    merged.sort(key=lambda a: (a.status == "resigned", a.appointed_on or ""))
    return merged


def build_one_sheet(
    confirmed_name: str,
    officer: Optional[OfficerCandidate],
    wiki_title: Optional[str],
    context: str = "",
    include_news: bool = True,
    progress=None,
) -> OneSheet:
    """Assemble the full one-sheet for a *confirmed* person.

    Either an officer, a wiki_title, or both may be provided depending on what
    the user confirmed. Missing sources leave their sections blank.
    `progress`, when given, is called with a short human label per stage so the
    UI can show what the (30s+) build is doing.
    """
    def _note(msg: str) -> None:
        if progress is not None:
            progress(msg)

    sheet = OneSheet(confirmed_name=confirmed_name, context=context)

    # --- Companies House: appointments, companies, PSC ---
    if officer is not None:
        sheet.officer = officer
        _note("Merging Companies House officer records…")
        sheet.appointments = _merged_appointments(officer)

        # Deduplicate company numbers (preserving order), then fetch profile +
        # PSC for each in parallel — a prolific director can have many companies
        # and doing this sequentially is the main source of latency.
        seen: set[str] = set()
        company_numbers: list[str] = []
        for appt in sheet.appointments:
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
            # The PSC endpoint doesn't return the company name; backfill it from
            # the profile so ownership stakes read "Revolut Ltd", not "08804411".
            if profile and psc:
                for entry in psc:
                    entry.company_name = profile.company_name
            return profile, psc

        if company_numbers:
            _note(f"Fetching {len(company_numbers)} company profiles, PSC and charges…")
            with ThreadPoolExecutor(max_workers=8) as pool:
                for profile, psc in pool.map(_fetch_company, company_numbers):
                    if profile:
                        sheet.companies.append(profile)
                    if psc:
                        sheet.psc_filings.extend(psc)

        # --- Filed-accounts figures for stake valuation ---
        # For companies where this person holds a *current* disclosed stake,
        # pull the latest accounts (iXBRL) and extract net assets, so the
        # estimates layer can price the stake on a book-value basis. Bounded
        # and parallel; companies with paper/PDF-only accounts just stay blank.
        stake_numbers: list[str] = []
        for psc_item in sheet.psc_filings:
            if psc_item.ceased_on or not psc_item.name:
                continue
            if not prospecting.names_match(psc_item.name, confirmed_name):
                continue
            if valuation.band_to_range(psc_item.natures_of_control) is None:
                continue
            if psc_item.company_number not in stake_numbers:
                stake_numbers.append(psc_item.company_number)
        stake_numbers = stake_numbers[:_ACCOUNTS_FETCH_LIMIT]
        if stake_numbers:
            _note(f"Reading filed accounts for {len(stake_numbers)} stake companies…")
            by_number = {c.company_number: c for c in sheet.companies}

            def _fetch_accounts(num: str) -> None:
                comp = by_number.get(num)
                if comp is None:
                    return
                try:
                    xhtml = ch.get_accounts_ixbrl(num)
                except ch.CompaniesHouseError:
                    return
                if not xhtml:
                    return
                figures = valuation.extract_ixbrl_figures(xhtml)
                comp.net_assets = figures.get("net_assets")
                comp.cash_at_bank = figures.get("cash")

            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(_fetch_accounts, stake_numbers))

    # --- Wikipedia: confident summary only ---
    if wiki_title:
        _note("Fetching Wikipedia summary…")
        try:
            summary = wiki.get_summary(wiki_title)
            if summary:
                sheet.wiki = summary
        except wiki.WikipediaError:
            pass

    # --- Person-level deep-dive sources (run concurrently) ---
    _note("Wikidata, news, Gazette and screening sources…")
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
            # A company-biased query is precise but often too narrow (the
            # person's best-known company may not be their CH one). Fall back
            # to the bare quoted name rather than showing an empty section.
            items = news_source.search_news(
                _news_query(confirmed_name, officer, context)
            )
            if not items:
                items = news_source.search_news(f'"{confirmed_name}"')
            sheet.news = items
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
