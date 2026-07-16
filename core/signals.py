"""Why-they-matter synthesis.

Turns the sheet's sourced facts into the short judgments a prospect
researcher would write at the top of a profile — each one derived only from
data already on the sheet, each one linkable back to its source. This is
presentation-layer synthesis, not new claims: every bullet cites the record
it came from.
"""
from __future__ import annotations

from datetime import date

from core import prospecting, valuation
from core.models import OneSheet
from core.prospecting import PersonSignals
from core.valuation import WealthEstimate


def why_they_matter(
    sheet: OneSheet, sig: PersonSignals, est: WealthEstimate
) -> list[str]:
    """Markdown bullets, most compelling first."""
    bullets: list[str] = []

    # Listed-company officer — a wealth-class marker.
    for quote in sheet.listed:
        price = ""
        if quote.price is not None:
            unit = quote.currency or ""
            price = f", {quote.price:,.0f}{unit if unit != 'GBp' else 'p'}"
        bullets.append(
            f"Officer of **{prospecting.company_case(quote.company_name)}** — "
            f"publicly listed ({quote.exchange or 'listed'}: {quote.symbol}{price}) "
            f"· [market ↗]({quote.source_url})"
        )

    # Scale of the biggest company they run (filed accounts).
    with_turnover = [c for c in sheet.companies if c.turnover]
    if with_turnover:
        big = max(with_turnover, key=lambda c: c.turnover or 0)
        parts = [f"revenue {valuation.fmt_gbp(big.turnover)}"]
        if big.profit_before_tax is not None:
            parts.append(f"pre-tax profit {valuation.fmt_gbp(big.profit_before_tax)}")
        if big.employees:
            parts.append(f"~{big.employees:,.0f} staff")
        bullets.append(
            f"Runs **{prospecting.company_case(big.company_name)}** — "
            f"{', '.join(parts)} (filed accounts) · [CH ↗]({big.source_url})"
        )

    # Registered political giving — direct, dated evidence of capacity.
    valued = [d for d in sheet.donations if d.value]
    if valued:
        total = sum(d.value for d in valued)
        latest = max(valued, key=lambda d: d.date or "")
        recipients = {d.recipient for d in valued}
        bullets.append(
            f"Political donor — **{valuation.fmt_gbp(total)}** across "
            f"{len(valued)} registered donation{'s' if len(valued) != 1 else ''} "
            f"to {', '.join(sorted(recipients))} "
            f"(latest {latest.date or '—'}) · [Electoral Commission ↗]({latest.source_url})"
        )

    # Published net worth (already verbatim + sourced).
    if sig.net_worth and sheet.wikidata:
        bullets.append(
            f"Published net worth: **{sig.net_worth}** "
            f"· [Wikidata ↗]({sheet.wikidata.source_url})"
        )

    # Ownership: strongest disclosed stake.
    if sig.top_ownership and sig.stakes:
        first = sig.stakes[0]
        bullets.append(
            f"Disclosed owner — {sig.top_ownership.lower()} of "
            f"**{prospecting.company_case(first.company_name)}**"
            + (f" and {len(sig.stakes) - 1} more" if len(sig.stakes) > 1 else "")
            + f" · [CH PSC ↗]({first.source_url})"
        )

    # Age — helps pitch and peer context.
    officer = sheet.officer
    if officer and officer.date_of_birth:
        year = officer.date_of_birth[:4]
        if year.isdigit():
            age = date.today().year - int(year)
            bullets.append(f"Age ~{age} (born {officer.date_of_birth}) — Companies House")

    return bullets
