"""Companies House REST API client.

Docs: https://developer.company-information.service.gov.uk/

Auth is HTTP Basic: the API key is the username, password is empty.
Every function returns typed models from core.models and never raises on an
empty result — it returns an empty list / None so callers render blanks.
Network/HTTP errors raise CompaniesHouseError so the UI can show a clear
message instead of a stack trace.
"""
from __future__ import annotations

from typing import Optional

import requests

import config
from core.http import SESSION
from core.models import (
    Appointment,
    Charge,
    CompanyInfo,
    FilingItem,
    OfficerCandidate,
    PSC,
)

CH_WEB_BASE = "https://find-and-update.company-information.service.gov.uk"


class CompaniesHouseError(RuntimeError):
    """Raised for network/HTTP/auth failures talking to Companies House."""


def _get(path: str, params: Optional[dict] = None) -> dict:
    if not config.companies_house_configured():
        raise CompaniesHouseError(
            "No Companies House API key set. Add COMPANIES_HOUSE_API_KEY to your .env."
        )
    url = f"{config.COMPANIES_HOUSE_BASE_URL}{path}"
    try:
        resp = SESSION.get(
            url,
            params=params,
            auth=(config.COMPANIES_HOUSE_API_KEY, ""),
            timeout=config.REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:  # pragma: no cover - network
        raise CompaniesHouseError(f"Network error contacting Companies House: {exc}") from exc

    if resp.status_code == 401:
        raise CompaniesHouseError(
            "Companies House rejected the API key (401). Check COMPANIES_HOUSE_API_KEY."
        )
    if resp.status_code == 404:
        return {}
    if resp.status_code == 429:
        raise CompaniesHouseError(
            "Companies House rate limit hit (429). Wait a moment and try again."
        )
    if not resp.ok:
        raise CompaniesHouseError(
            f"Companies House returned HTTP {resp.status_code} for {path}."
        )
    try:
        return resp.json()
    except ValueError:
        return {}


def _paged_items(path: str, max_items: int, page_size: int = 50) -> list[dict]:
    """Fetch all `items` across pages up to `max_items`.

    Companies House paginates list endpoints via `start_index` / `items_per_page`
    and reports `total_results`. We stop at whichever comes first: total results,
    max_items, or a short (final) page. The cap prevents runaway calls for the
    rare officer with hundreds of appointments.
    """
    collected: list[dict] = []
    start_index = 0
    while len(collected) < max_items:
        want = min(page_size, max_items - len(collected))
        data = _get(path, params={"items_per_page": want, "start_index": start_index})
        items = data.get("items", []) or []
        if not items:
            break
        collected.extend(items)
        total = data.get("total_results")
        start_index += len(items)
        if len(items) < want:
            break
        if total is not None and start_index >= total:
            break
    return collected


def _officer_id_from_self_link(self_link: str) -> str:
    # self_link looks like: /officers/AbCd123.../appointments
    parts = [p for p in self_link.split("/") if p]
    if len(parts) >= 2 and parts[0] == "officers":
        return parts[1]
    return self_link.strip("/")


def search_officers(query: str, limit: int = 12) -> list[OfficerCandidate]:
    """Search people (officers) by name. Returns disambiguation candidates."""
    if not query.strip():
        return []
    data = _get("/search/officers", params={"q": query, "items_per_page": limit})
    candidates: list[OfficerCandidate] = []
    for item in data.get("items", []):
        self_link = (item.get("links") or {}).get("self", "")
        officer_id = _officer_id_from_self_link(self_link)
        if not officer_id:
            continue
        candidates.append(
            OfficerCandidate(
                officer_id=officer_id,
                name=item.get("title", "(no name)"),
                source_url=f"{CH_WEB_BASE}/officers/{officer_id}/appointments",
                appointment_count=item.get("appointment_count"),
                date_of_birth=_format_dob(item.get("date_of_birth")),
                address=item.get("address_snippet"),
                description=item.get("description"),
            )
        )
    return candidates


def _format_dob(dob: Optional[dict]) -> Optional[str]:
    if not dob:
        return None
    year = dob.get("year")
    month = dob.get("month")
    if year and month:
        return f"{year}-{int(month):02d}"
    if year:
        return str(year)
    return None


def get_officer_appointments(
    officer_id: str, max_items: int = 200
) -> tuple[list[Appointment], list[str], dict]:
    """Return (appointments, top_company_names, identity) for one officer.

    Pages through all appointments up to `max_items` so the sheet reflects the
    person's full directorship history, not just the first page. Callers that
    only need the disambiguation preview pass a small `max_items` to stay fast.
    top_company_names is a short list used to enrich the disambiguation card.
    identity carries the officer-level fields CH files with appointments —
    occupation, nationality, country of residence — which describe *who the
    person is* even when they have no Wikipedia page.
    """
    items = _paged_items(f"/officers/{officer_id}/appointments", max_items=max_items)
    identity: dict = {}
    for item in items:
        for key in ("occupation", "nationality", "country_of_residence"):
            if not identity.get(key) and item.get(key):
                identity[key] = item[key]
    appointments: list[Appointment] = []
    for item in items:
        appointed = item.get("appointed_to") or {}
        company_number = appointed.get("company_number") or item.get("company_number", "")
        company_name = appointed.get("company_name") or item.get("company_name", "")
        appointments.append(
            Appointment(
                company_name=company_name or "(unknown company)",
                company_number=company_number,
                officer_role=item.get("officer_role"),
                status="resigned" if item.get("resigned_on") else "active",
                appointed_on=item.get("appointed_on"),
                resigned_on=item.get("resigned_on"),
                address=_address_to_str(item.get("address")),
                source_url=(
                    f"{CH_WEB_BASE}/company/{company_number}" if company_number else ""
                ),
            )
        )
    # Sort active first, then most recent appointment date.
    appointments.sort(
        key=lambda a: (a.status == "resigned", a.appointed_on or ""),
        reverse=False,
    )
    top = [a.company_name for a in appointments[:4]]
    return appointments, top, identity


def get_company_profile(company_number: str) -> Optional[CompanyInfo]:
    if not company_number:
        return None
    data = _get(f"/company/{company_number}")
    if not data:
        return None
    accounts = data.get("accounts") or {}
    last_accounts = accounts.get("last_accounts") or {}
    confirmation = data.get("confirmation_statement") or {}
    return CompanyInfo(
        company_name=data.get("company_name", "(unknown)"),
        company_number=company_number,
        status=data.get("company_status"),
        company_type=data.get("type"),
        incorporation_date=data.get("date_of_creation"),
        registered_office=_address_to_str(data.get("registered_office_address")),
        sic_codes=data.get("sic_codes", []) or [],
        source_url=f"{CH_WEB_BASE}/company/{company_number}",
        accounts_last_made_up_to=last_accounts.get("made_up_to"),
        accounts_next_due=accounts.get("next_due"),
        accounts_overdue=accounts.get("overdue"),
        confirmation_next_due=confirmation.get("next_due"),
        has_insolvency_history=data.get("has_insolvency_history"),
        has_charges=data.get("has_charges"),
    )


def get_filing_history(company_number: str, max_items: int = 8) -> list[FilingItem]:
    """Recent filings (accounts, confirmation statements, appointments, etc.)."""
    if not company_number:
        return []
    items = _paged_items(
        f"/company/{company_number}/filing-history", max_items=max_items
    )
    filings: list[FilingItem] = []
    for item in items:
        tx_id = item.get("transaction_id")
        filings.append(
            FilingItem(
                category=item.get("category"),
                description=item.get("description"),
                date=item.get("date"),
                document_url=(
                    f"{CH_WEB_BASE}/company/{company_number}/filing-history/{tx_id}"
                    if tx_id
                    else f"{CH_WEB_BASE}/company/{company_number}/filing-history"
                ),
            )
        )
    return filings


def get_charges(company_number: str, max_items: int = 20) -> list[Charge]:
    """Registered charges (mortgages / debentures) against a company."""
    if not company_number:
        return []
    items = _paged_items(f"/company/{company_number}/charges", max_items=max_items)
    charges: list[Charge] = []
    for item in items:
        persons = [
            p.get("name")
            for p in (item.get("persons_entitled") or [])
            if p.get("name")
        ]
        charges.append(
            Charge(
                classification=(item.get("classification") or {}).get("description"),
                status=item.get("status"),
                created_on=item.get("created_on"),
                delivered_on=item.get("delivered_on"),
                persons_entitled=persons,
                source_url=f"{CH_WEB_BASE}/company/{company_number}/charges",
            )
        )
    return charges


def get_accounts_ixbrl(company_number: str) -> Optional[str]:
    """Fetch the latest filed accounts as iXBRL (XHTML), when available.

    Flow: filing history (category=accounts) -> document metadata -> document
    content with Accept: application/xhtml+xml. Older/paper filings only have a
    scanned PDF and return None. The document endpoint redirects to a signed
    AWS URL that must be followed WITHOUT the auth header (sending it breaks
    the AWS signature), hence the manual redirect handling.
    """
    if not company_number:
        return None
    data = _get(
        f"/company/{company_number}/filing-history",
        params={"category": "accounts", "items_per_page": 5},
    )
    for item in data.get("items", []) or []:
        meta_url = (item.get("links") or {}).get("document_metadata")
        if not meta_url:
            continue
        try:
            meta_resp = SESSION.get(
                meta_url,
                auth=(config.COMPANIES_HOUSE_API_KEY, ""),
                timeout=config.REQUEST_TIMEOUT,
            )
            if not meta_resp.ok:
                continue
            meta = meta_resp.json()
        except (requests.RequestException, ValueError):
            continue
        if "application/xhtml+xml" not in (meta.get("resources") or {}):
            continue  # scanned PDF only — not machine-readable
        doc_url = (meta.get("links") or {}).get("document")
        if not doc_url:
            continue
        try:
            first = SESSION.get(
                doc_url,
                auth=(config.COMPANIES_HOUSE_API_KEY, ""),
                headers={"Accept": "application/xhtml+xml"},
                timeout=config.REQUEST_TIMEOUT,
                allow_redirects=False,
            )
            if first.status_code in (301, 302, 303, 307, 308):
                signed = first.headers.get("Location")
                if not signed:
                    continue
                resp = SESSION.get(signed, timeout=config.REQUEST_TIMEOUT)
            else:
                resp = first
            if resp.ok and "html" in (resp.headers.get("Content-Type") or ""):
                return resp.text
        except requests.RequestException:
            continue
    return None


def get_psc(company_number: str, max_items: int = 100) -> list[PSC]:
    """Persons with significant control for a company (paged)."""
    if not company_number:
        return []
    items = _paged_items(
        f"/company/{company_number}/persons-with-significant-control",
        max_items=max_items,
    )
    filings: list[PSC] = []
    company_name = ""
    for item in items:
        filings.append(
            PSC(
                company_name=company_name or company_number,
                company_number=company_number,
                name=item.get("name"),
                kind=item.get("kind"),
                natures_of_control=item.get("natures_of_control", []) or [],
                notified_on=item.get("notified_on"),
                ceased_on=item.get("ceased_on"),
                source_url=(
                    f"{CH_WEB_BASE}/company/{company_number}"
                    "/persons-with-significant-control"
                ),
            )
        )
    return filings


def _address_to_str(address: Optional[dict]) -> Optional[str]:
    if not address:
        return None
    parts = [
        address.get("premises"),
        address.get("address_line_1"),
        address.get("address_line_2"),
        address.get("locality"),
        address.get("region"),
        address.get("postal_code"),
        address.get("country"),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None
