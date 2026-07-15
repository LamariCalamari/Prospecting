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

# Common English nickname -> formal name, so "Nick Candy" links to "Nicholas
# Candy". Canonicalising both sides before comparison catches these.
_NICKNAMES = {
    "nick": "nicholas", "bill": "william", "will": "william", "bob": "robert",
    "rob": "robert", "tony": "anthony", "dave": "david", "mike": "michael",
    "jim": "james", "jimmy": "james", "tom": "thomas", "dan": "daniel",
    "chris": "christopher", "steve": "stephen", "andy": "andrew", "ed": "edward",
    "eddie": "edward", "alex": "alexander", "liz": "elizabeth", "beth": "elizabeth",
    "kate": "katherine", "katie": "katherine", "jack": "john", "johnny": "john",
    "dick": "richard", "rick": "richard", "harry": "henry", "fred": "frederick",
    "sam": "samuel", "ben": "benjamin", "joe": "joseph", "charlie": "charles",
    "greg": "gregory", "matt": "matthew", "pat": "patrick", "tim": "timothy",
    "ted": "edward", "nate": "nathaniel", "gabe": "gabriel", "vic": "victor",
}

_BORN_YEAR_RE = re.compile(r"born\s+(?:\w+\s+\d{1,2},?\s+)?(\d{4})", re.IGNORECASE)
_PARENS_RE = re.compile(r"\([^)]*\)")
_LEAD_NAME_RE = re.compile(
    r"^\s*([A-Z][\w.'’-]+(?:\s+[A-Z][\w.'’-]+){1,4})\s+(?:is|was)\b"
)


def _canon(token: str) -> str:
    return _NICKNAMES.get(token, token)


def extract_birth_year(text: str) -> Optional[str]:
    """Pull a 4-digit birth year from a Wikipedia description/extract, if any."""
    m = _BORN_YEAR_RE.search(text or "")
    return m.group(1) if m else None


def extract_lead_name(extract: str) -> Optional[str]:
    """Formal full name from a Wikipedia extract's first sentence.

    'Nicholas Anthony Christopher Candy (born 1973) is a British ...' ->
    'Nicholas Anthony Christopher Candy'. Lets us re-search Companies House by
    the registered name when the user typed a nickname.
    """
    if not extract:
        return None
    cleaned = _PARENS_RE.sub("", extract)
    m = _LEAD_NAME_RE.match(cleaned)
    return m.group(1).strip() if m else None


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
    active_directorships: int = 0      # live role at a live company
    dissolved_directorships: int = 0   # unresigned role, but company dissolved
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


def company_case(name: str) -> str:
    """'CANDY & CANDY HOLDINGS LIMITED' -> 'Candy & Candy Holdings Limited'.

    Words of 1–3 letters that arrive fully uppercase (NC, UBS, C&C) are kept
    as-is — they're usually initials or tickers, not words.
    """
    def fix_run(m: re.Match) -> str:
        run = m.group(0)
        if run.lower() in ("the", "of", "and", "for", "to"):
            return run.capitalize() if m.start() == 0 else run.lower()
        if run.isupper() and len(run) <= 3:
            return run  # initials/ticker: NC, UBS, C, AR
        return run[0].upper() + run[1:].lower()

    return re.sub(r"[A-Za-z]+", fix_run, name or "")


def person_name_case(name: str) -> str:
    """'HALL, Louis Tancred' (CH surname-first) -> 'Louis Tancred Hall'."""
    if "," in (name or ""):
        last, _, first = name.partition(",")
        name = f"{first.strip()} {last.strip()}"
    return company_case(name)


def role_case(role: Optional[str]) -> str:
    """'llp-designated-member' -> 'LLP Designated Member'."""
    text = (role or "officer").replace("-", " ").title()
    return re.sub(r"\bLlp\b", "LLP", text)


def short_band(humanized: Optional[str]) -> Optional[str]:
    """'Ownership of shares 75–100%' -> '75–100% shares' (fits a metric tile)."""
    if not humanized:
        return None
    m = re.search(r"(\d+–\d+%|\bmore than \d+%)", humanized)
    if not m:
        return humanized
    kind = "shares" if "share" in humanized.lower() else "voting"
    return f"{m.group(1)} {kind}"


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

    Order-agnostic (Companies House lists 'SURNAME Given'; Wikipedia lists
    'Given SURNAME') and nickname-aware ('Nick' == 'Nicholas'). Deliberately
    loose — matches are surfaced as *leads to verify*, never as a confirmed
    identification; birth-year is used to pin down the exact person.
    """
    ta = {_canon(t) for t in _name_tokens(a)}
    tb = {_canon(t) for t in _name_tokens(b)}
    if not ta or not tb:
        return False
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small.issubset(large)


# Officer-search results sometimes include corporate officers (a company acting
# as a director). These suffixes let us skip them when auto-picking a person.
_COMPANY_SUFFIXES = ("ltd", "limited", "llp", "plc", "inc", "sarl", "gmbh", "co")


def best_officer_index(
    officers, query: str, birth_year: Optional[str] = None
) -> Optional[int]:
    """Index of the Companies House officer to *pre-select*, or None if unsure.

    Only auto-selects when we can be confident, because picking the wrong
    namesake is worse than picking none:
      1. If a `birth_year` is known (from the matched Wikipedia page) and exactly
         one name-matching officer was born that year, pick that one.
      2. Otherwise, if there's exactly one name-matching person at all, pick it.
      3. If several people share the name and we can't tell them apart, return
         None — the user picks by birth year, which is shown on each card.
    Corporate officers (a company acting as director) are always skipped.
    """
    matches = []  # (index, officer)
    for i, off in enumerate(officers):
        low = (off.name or "").lower()
        if any(low == s or low.endswith(" " + s) for s in _COMPANY_SUFFIXES):
            continue
        if names_match(off.name or "", query):
            matches.append((i, off))
    if not matches:
        return None

    if birth_year:
        by_year = [
            (i, o) for i, o in matches
            if (o.date_of_birth or "").startswith(str(birth_year))
        ]
        if len(by_year) == 1:
            return by_year[0][0]
        if len(by_year) > 1:
            # Same name AND same birth year — pick the most-appointed record.
            return max(by_year, key=lambda t: t[1].appointment_count or 0)[0]

    if len(matches) == 1:
        return matches[0][0]
    return None  # ambiguous — let the user choose by birth year


@dataclass
class OfficerGroup:
    """Several CH officer records that are (very likely) the same human.

    Companies House fragments one person across many officer ids; showing the
    raw fragments makes the disambiguation screen unreadable. A group carries
    one representative record (the one with the most appointments) plus the
    merged at-a-glance info for its card.
    """
    primary: object                      # OfficerCandidate to confirm with
    records: list = field(default_factory=list)
    birth_year: Optional[str] = None     # "1973" or None when no record has one
    total_appointments: int = 0
    top_companies: list[str] = field(default_factory=list)
    occupation: Optional[str] = None     # from CH appointment filings
    nationality: Optional[str] = None
    residence: Optional[str] = None


def group_officers(officers) -> list[OfficerGroup]:
    """Collapse fragmented CH officer records into one group per person.

    Records join a group when the canonical name tokens are identical and the
    birth dates don't conflict (a record with no birth date joins the group of
    the same name that already has one). Groups are ordered by total
    appointment count, so the most substantial person comes first.
    """
    groups: list[OfficerGroup] = []
    keyed: dict[tuple, list[OfficerGroup]] = {}
    for off in officers:
        tokens = frozenset(_canon(t) for t in _name_tokens(off.name or ""))
        year = (off.date_of_birth or "")[:4] or None
        bucket = keyed.setdefault(tokens, [])
        target = None
        for g in bucket:
            if g.birth_year is None or year is None or g.birth_year == year:
                target = g
                break
        if target is None:
            target = OfficerGroup(primary=off, birth_year=year)
            bucket.append(target)
            groups.append(target)
        target.records.append(off)
        if target.birth_year is None:
            target.birth_year = year
        target.total_appointments += off.appointment_count or 0
        for name in off.top_companies:
            if name not in target.top_companies:
                target.top_companies.append(name)
        for attr in ("occupation", "nationality", "residence"):
            if not getattr(target, attr) and getattr(off, attr, None):
                setattr(target, attr, getattr(off, attr))
        # The fattest record becomes the group's representative.
        if (off.appointment_count or 0) > (target.primary.appointment_count or 0):
            target.primary = off
    groups.sort(key=lambda g: g.total_appointments, reverse=True)
    return groups


def best_group_index(
    groups: list[OfficerGroup], query: str, birth_year: Optional[str] = None
) -> Optional[int]:
    """Group to pre-select on the disambiguation screen, or None if unsure.

    With a known birth year (from Wikipedia), pick the name-matching group born
    that year. Without one, pick the only name-matching group — if several
    people share the name, return None and let the user decide.
    """
    matches = [
        (i, g) for i, g in enumerate(groups)
        if names_match(g.primary.name or "", query)
    ]
    if not matches:
        return None
    if birth_year:
        by_year = [(i, g) for i, g in matches if g.birth_year == str(birth_year)]
        if by_year:
            # groups are pre-sorted by substance; take the fattest match
            return by_year[0][0]
    if len(matches) == 1:
        return matches[0][0]
    return None


def best_wiki_index(candidates, query: str) -> Optional[int]:
    """Index of the first Wikipedia candidate whose title matches `query`."""
    for i, cand in enumerate(candidates):
        if names_match(cand.title, query):
            return i
    return None


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

    # "Active" must mean a live role at a live company. An unresigned role at a
    # dissolved company is history, not a current position — counting it makes
    # the headline number read wrong. Companies without a fetched profile get
    # the benefit of the doubt.
    status_by_number = {c.company_number: (c.status or "").lower() for c in sheet.companies}
    for a in sheet.appointments:
        comp_status = status_by_number.get(a.company_number, "")
        if a.status == "active" and comp_status in ("", "active", "open"):
            sig.active_directorships += 1
        elif a.status == "active":
            sig.dissolved_directorships += 1
        else:
            sig.resigned_directorships += 1

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
