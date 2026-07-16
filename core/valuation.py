"""Stake-value estimation from sourced inputs.

Methodology (standard wealth-screening practice, shown to the user verbatim):

    estimated holding = stake band (Companies House PSC, a RANGE)
                      x company value

Company value comes from one of two bases, always labelled:
  * BOOK VALUE — net assets / shareholders' funds parsed from the company's
    latest filed accounts (iXBRL via the Companies House document API). This is
    a conservative floor: profitable/growth companies are usually worth a
    multiple of book. It is still a real, filed, attributable number.
  * KNOWN MARKET VALUATION — a figure the user supplies (e.g. a funding-round
    valuation reported in the press). The app applies the stake band to it but
    never invents the number itself.

Every estimate is a range (from the PSC band), carries its inputs, and is
clearly marked as an estimate — distinct from the sourced-fact sections.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from core import prospecting
from core.models import OneSheet

# --- iXBRL parsing -----------------------------------------------------------
_NONFRACTION_RE = re.compile(
    r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S | re.I
)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_TAG_STRIP_RE = re.compile(r"<[^>]+>")

# Balance-sheet concepts we accept for each figure, in order of preference.
# Names are the local part of the iXBRL `name` attribute, lowercased.
_CONCEPTS: dict[str, tuple[str, ...]] = {
    "net_assets": (
        "netassetsliabilities",
        "equity",
        "shareholdersfunds",
        "totalassetslesscurrentliabilities",
    ),
    "cash": ("cashbankonhand", "cashbankinhand", "cashcashequivalents"),
    # P&L scale signals — turn "a company" into "a £43m-revenue company".
    "turnover": ("turnoverrevenue", "revenue", "turnover", "grossrevenue"),
    "profit": (
        "profitlossonordinaryactivitiesbeforetax",
        "profitlossbeforetax",
        "profitloss",
    ),
    "employees": (
        "averagenumberemployeesduringperiod",
        "averagenumberofemployeesduringperiod",
        "employeestotal",
    ),
}


def extract_ixbrl_figures(xhtml: str) -> dict[str, float]:
    """Best-effort extraction of key balance-sheet figures from iXBRL accounts.

    Takes the FIRST occurrence of each concept in document order — UK accounts
    present the current year before the comparative, so this is the latest
    figure. Handles the iXBRL sign and scale attributes.
    """
    out: dict[str, float] = {}
    if not xhtml:
        return out
    for m in _NONFRACTION_RE.finditer(xhtml):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        local = attrs.get("name", "").split(":")[-1].lower()
        concept = None
        for key, accepted in _CONCEPTS.items():
            if key not in out and local in accepted:
                concept = key
                break
        if concept is None:
            continue
        raw = _TAG_STRIP_RE.sub("", m.group(2))
        raw = raw.replace(",", "").replace(" ", "").replace("&nbsp;", "").strip()
        if raw in ("", "-", "—"):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        scale = attrs.get("scale")
        if scale:
            try:
                value *= 10 ** int(scale)
            except ValueError:
                pass
        if attrs.get("sign") == "-":
            value = -value
        out[concept] = value
        if len(out) == len(_CONCEPTS):
            break
    return out


# --- Stake bands --------------------------------------------------------------
_BAND_RE = re.compile(r"ownership-of-shares-(\d+)-to-(\d+)-percent")
_MORE_THAN_RE = re.compile(r"ownership-of-shares-more-than-(\d+)")


def band_to_range(natures: list[str]) -> Optional[tuple[float, float]]:
    """Strongest share-ownership band -> (low, high) fractions, else None."""
    best: Optional[tuple[float, float]] = None
    for nature in natures:
        m = _BAND_RE.search(nature)
        if m:
            rng = (int(m.group(1)) / 100, int(m.group(2)) / 100)
        else:
            m2 = _MORE_THAN_RE.search(nature)
            if not m2:
                continue
            rng = (int(m2.group(1)) / 100, 1.0)
        if best is None or rng[0] > best[0]:
            best = rng
    return best


# --- Estimates ----------------------------------------------------------------
@dataclass
class StakeEstimate:
    company_name: str
    company_number: str
    stake_lo: float                      # e.g. 0.75
    stake_hi: float                      # e.g. 1.0
    net_assets: Optional[float] = None   # GBP, from latest filed accounts
    accounts_date: Optional[str] = None
    value_lo: Optional[float] = None     # net_assets x stake, clamped at 0
    value_hi: Optional[float] = None
    source_url: str = ""


@dataclass
class WealthEstimate:
    stakes: list[StakeEstimate] = field(default_factory=list)
    total_lo: float = 0.0
    total_hi: float = 0.0
    counted: int = 0          # stakes with usable accounts figures
    missing: int = 0          # stakes lacking parseable accounts


def fmt_gbp(value: Optional[float]) -> str:
    if value is None:
        return "—"
    a = abs(value)
    if a >= 1e9:
        s = f"£{value / 1e9:,.1f}bn"
    elif a >= 1e6:
        s = f"£{value / 1e6:,.1f}m"
    elif a >= 1e3:
        s = f"£{value / 1e3:,.0f}k"
    else:
        s = f"£{value:,.0f}"
    return s


def build_estimates(sheet: OneSheet, person_name: str) -> WealthEstimate:
    """Estimate the value of each current disclosed stake on a book-value basis."""
    est = WealthEstimate()
    companies = {c.company_number: c for c in sheet.companies}
    for psc in sheet.psc_filings:
        if psc.ceased_on or not psc.name:
            continue
        if not prospecting.names_match(psc.name, person_name):
            continue
        rng = band_to_range(psc.natures_of_control)
        if rng is None:
            continue
        comp = companies.get(psc.company_number)
        item = StakeEstimate(
            company_name=psc.company_name,
            company_number=psc.company_number,
            stake_lo=rng[0],
            stake_hi=rng[1],
            source_url=(comp.source_url if comp else psc.source_url),
        )
        if comp is not None and comp.net_assets is not None:
            item.net_assets = comp.net_assets
            item.accounts_date = comp.accounts_last_made_up_to
            # A shareholder's downside is limited — clamp negative book value to 0.
            item.value_lo = max(comp.net_assets * rng[0], 0.0)
            item.value_hi = max(comp.net_assets * rng[1], 0.0)
            est.total_lo += item.value_lo
            est.total_hi += item.value_hi
            est.counted += 1
        else:
            est.missing += 1
        est.stakes.append(item)
    # Largest first so the material holdings lead.
    est.stakes.sort(key=lambda s: (s.value_hi or 0), reverse=True)
    return est


def apply_market_valuation(
    stake: StakeEstimate, market_value: float
) -> tuple[float, float]:
    """Stake range applied to a user-supplied market valuation (GBP)."""
    return (market_value * stake.stake_lo, market_value * stake.stake_hi)
