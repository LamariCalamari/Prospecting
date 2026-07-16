"""Data models shared across the app.

Design rule: these objects only ever hold data that a source actually
returned. Optional fields default to None / empty so the UI can render a
blank rather than inventing a value. Every model carries a `source_url`
(or per-item link) so the one-sheet can attribute every fact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --- Companies House --------------------------------------------------------
@dataclass
class OfficerCandidate:
    """A person returned by the Companies House officer search.

    Used in the disambiguation step. `officer_id` is the opaque id from the
    search result's `links.self` (e.g. /officers/<id>/appointments).
    """
    officer_id: str
    name: str
    source_url: str  # human-facing CH page for this officer
    appointment_count: Optional[int] = None
    date_of_birth: Optional[str] = None  # CH only exposes month/year, e.g. "1980-06"
    address: Optional[str] = None
    top_companies: list[str] = field(default_factory=list)  # for at-a-glance matching
    description: Optional[str] = None  # CH's own snippet, e.g. "Born 1980"
    # Officer-level identity fields from CH appointment filings — describe the
    # person ("Property Developer · British · London") without needing Wikipedia.
    occupation: Optional[str] = None
    nationality: Optional[str] = None
    residence: Optional[str] = None


@dataclass
class Appointment:
    """A single directorship / officer appointment."""
    company_name: str
    company_number: str
    officer_role: Optional[str] = None
    status: Optional[str] = None  # "active" or "resigned"
    appointed_on: Optional[str] = None
    resigned_on: Optional[str] = None
    address: Optional[str] = None
    source_url: str = ""  # CH company page


@dataclass
class PSC:
    """A person-with-significant-control filing for one company."""
    company_name: str
    company_number: str
    name: Optional[str] = None
    kind: Optional[str] = None  # e.g. individual-person-with-significant-control
    natures_of_control: list[str] = field(default_factory=list)  # verbatim CH strings
    notified_on: Optional[str] = None
    ceased_on: Optional[str] = None
    source_url: str = ""


@dataclass
class FilingItem:
    """One entry from a company's filing history."""
    category: Optional[str] = None  # e.g. accounts, confirmation-statement
    description: Optional[str] = None
    date: Optional[str] = None
    document_url: Optional[str] = None  # CH web page for the filing


@dataclass
class Charge:
    """A registered charge (e.g. mortgage / debenture) against a company."""
    classification: Optional[str] = None
    status: Optional[str] = None  # outstanding / satisfied / part-satisfied
    created_on: Optional[str] = None
    delivered_on: Optional[str] = None
    persons_entitled: list[str] = field(default_factory=list)
    source_url: str = ""


@dataclass
class CompanyInfo:
    """Profile details for a company from Companies House."""
    company_name: str
    company_number: str
    status: Optional[str] = None
    company_type: Optional[str] = None
    incorporation_date: Optional[str] = None
    registered_office: Optional[str] = None
    sic_codes: list[str] = field(default_factory=list)
    website: Optional[str] = None  # CH rarely has this; may stay blank
    source_url: str = ""
    # --- deep-dive fields (all optional; blank when not fetched/returned) ---
    accounts_last_made_up_to: Optional[str] = None
    accounts_next_due: Optional[str] = None
    accounts_overdue: Optional[bool] = None
    confirmation_next_due: Optional[str] = None
    has_insolvency_history: Optional[bool] = None
    has_charges: Optional[bool] = None
    recent_filings: list[FilingItem] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)
    # From the latest filed accounts (iXBRL), when parseable. GBP.
    net_assets: Optional[float] = None
    cash_at_bank: Optional[float] = None
    turnover: Optional[float] = None
    profit_before_tax: Optional[float] = None
    employees: Optional[float] = None


# --- Wikidata ---------------------------------------------------------------
@dataclass
class WikidataFacts:
    """Structured facts about the confirmed person from Wikidata."""
    qid: str
    source_url: str
    net_worth: Optional[str] = None  # verbatim value + unit + point-in-time date
    occupations: list[str] = field(default_factory=list)
    positions: list[str] = field(default_factory=list)  # position (with dates if any)
    educated_at: list[str] = field(default_factory=list)
    official_website: Optional[str] = None
    twitter: Optional[str] = None
    linkedin: Optional[str] = None


# --- FCA Financial Services Register ----------------------------------------
@dataclass
class FCARecord:
    name: str
    reference_number: str  # Individual Reference Number (IRN)
    status: Optional[str] = None
    source_url: str = ""
    roles: list[str] = field(default_factory=list)  # controlled functions / roles
    firms: list[str] = field(default_factory=list)


# --- OpenSanctions ----------------------------------------------------------
@dataclass
class SanctionsHit:
    name: str
    schema: Optional[str] = None  # Person / Company / etc.
    topics: list[str] = field(default_factory=list)  # e.g. role.pep, sanction
    datasets: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    source_url: str = ""
    score: Optional[float] = None


# --- The Gazette ------------------------------------------------------------
@dataclass
class GazetteNotice:
    title: str
    link: str
    published: Optional[str] = None
    notice_type: Optional[str] = None


# --- Charity Commission -----------------------------------------------------
@dataclass
class CharityRecord:
    name: str
    charity_number: Optional[str] = None
    status: Optional[str] = None  # registered / removed
    activities: Optional[str] = None
    source_url: str = ""


# --- Electoral Commission (political donations) ------------------------------
@dataclass
class Donation:
    """A registered political donation by this person (Electoral Commission)."""
    donor_name: str
    recipient: str
    value: Optional[float] = None  # GBP
    date: Optional[str] = None     # YYYY-MM-DD
    source_url: str = ""


# --- Listed-market quote ------------------------------------------------------
@dataclass
class ListedQuote:
    """A company from this person's record that trades on a public market."""
    company_name: str    # CH company name it matched
    symbol: str
    exchange: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    source_url: str = ""


# --- Wikipedia --------------------------------------------------------------
@dataclass
class WikiCandidate:
    """A Wikipedia search hit shown during disambiguation."""
    title: str
    pageid: int
    snippet: str  # plain-text, tags stripped
    url: str


@dataclass
class WikiSummary:
    """A confident Wikipedia page summary for the confirmed person."""
    title: str
    extract: str
    description: Optional[str]
    url: str
    thumbnail: Optional[str] = None


# --- News -------------------------------------------------------------------
@dataclass
class NewsItem:
    title: str
    link: str
    source: Optional[str] = None
    published: Optional[str] = None


# --- Assembled one-sheet ----------------------------------------------------
@dataclass
class OneSheet:
    """Everything needed to render the final one-sheet.

    `confirmed_name` is the label the user confirmed. Any section may be empty;
    the UI renders blanks rather than filling gaps.
    """
    confirmed_name: str
    context: str = ""
    officer: Optional[OfficerCandidate] = None
    appointments: list[Appointment] = field(default_factory=list)
    psc_filings: list[PSC] = field(default_factory=list)
    companies: list[CompanyInfo] = field(default_factory=list)
    wiki: Optional[WikiSummary] = None
    news: list[NewsItem] = field(default_factory=list)
    # --- deep-dive sources (blank/empty when unavailable or not configured) ---
    wikidata: Optional[WikidataFacts] = None
    fca_records: list[FCARecord] = field(default_factory=list)
    sanctions_hits: list[SanctionsHit] = field(default_factory=list)
    gazette_notices: list[GazetteNotice] = field(default_factory=list)
    charities: list[CharityRecord] = field(default_factory=list)
    donations: list[Donation] = field(default_factory=list)
    listed: list[ListedQuote] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)  # e.g. "FCA not configured"
