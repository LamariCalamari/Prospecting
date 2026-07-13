"""Export a OneSheet to Markdown or PDF.

Markdown is the source of truth; the PDF is a plain rendering of the same
content using fpdf2 (pure-Python, no system dependencies). Both attribute
every fact with a source link, matching the on-screen sheet.
"""
from __future__ import annotations

from core.models import OneSheet

# fpdf2's core fonts are Latin-1 only. Map the common "smart" punctuation that
# shows up in Wikipedia/news text to plain equivalents so the PDF renders them
# instead of "?" placeholders.
_UNICODE_FIXES = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
    "•": "-", "′": "'", "″": '"',
}


def _pdf_safe(text: str) -> str:
    for bad, good in _UNICODE_FIXES.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def _fmt_date(value) -> str:
    return value or ""


def to_markdown(sheet: OneSheet) -> str:
    lines: list[str] = []
    lines.append(f"# Prospecting one-sheet — {sheet.confirmed_name}")
    if sheet.context:
        lines.append(f"*Research context:* {sheet.context}")
    lines.append("")
    lines.append(
        "> Every fact below is attributed to its source. Blank fields mean the "
        "source returned nothing — nothing here is inferred or estimated."
    )
    lines.append("")

    # Position & company
    lines.append("## Position & company")
    active = [a for a in sheet.appointments if a.status == "active"]
    if active:
        for a in active[:5]:
            role = a.officer_role or "officer"
            lines.append(
                f"- **{role.title()}**, {a.company_name} "
                f"(appointed {_fmt_date(a.appointed_on)}) "
                f"— [Companies House]({a.source_url})"
            )
    if sheet.wiki and sheet.wiki.description:
        lines.append(f"- {sheet.wiki.description} — [Wikipedia]({sheet.wiki.url})")
    if not active and not (sheet.wiki and sheet.wiki.description):
        lines.append("_No current position data returned._")
    lines.append("")

    # Directorships & control
    lines.append("## Directorships & control")
    if sheet.appointments:
        lines.append("### Appointments")
        for a in sheet.appointments:
            status = a.status or ""
            dates = _fmt_date(a.appointed_on)
            if a.resigned_on:
                dates += f" → {a.resigned_on}"
            role = a.officer_role or "officer"
            lines.append(
                f"- {a.company_name} — {role} ({status}); {dates} "
                f"— [Companies House]({a.source_url})"
            )
    else:
        lines.append("_No directorships returned._")

    if sheet.psc_filings:
        lines.append("")
        lines.append("### Persons with significant control (PSC)")
        for p in sheet.psc_filings:
            controls = "; ".join(p.natures_of_control) if p.natures_of_control else "—"
            ceased = f", ceased {p.ceased_on}" if p.ceased_on else ""
            lines.append(
                f"- {p.name or '(unnamed)'} @ {p.company_name}: {controls} "
                f"(notified {_fmt_date(p.notified_on)}{ceased}) "
                f"— [Companies House]({p.source_url})"
            )
    lines.append("")

    # Background
    lines.append("## Background")
    if sheet.wiki:
        lines.append(sheet.wiki.extract)
        lines.append("")
        lines.append(f"— [Wikipedia: {sheet.wiki.title}]({sheet.wiki.url})")
    else:
        lines.append("_No confident Wikipedia match._")
    lines.append("")

    # Key facts (Wikidata)
    wd = sheet.wikidata
    if wd:
        lines.append("### Key facts — source: Wikidata")
        if wd.net_worth:
            lines.append(f"- Net worth: {wd.net_worth}")
        if wd.occupations:
            lines.append(f"- Occupation: {', '.join(wd.occupations)}")
        if wd.positions:
            lines.append(f"- Positions held: {', '.join(wd.positions)}")
        if wd.educated_at:
            lines.append(f"- Educated at: {', '.join(wd.educated_at)}")
        if wd.official_website:
            lines.append(f"- Official website: {wd.official_website}")
        if wd.linkedin:
            lines.append(f"- LinkedIn: {wd.linkedin}")
        if wd.twitter:
            lines.append(f"- X/Twitter: {wd.twitter}")
        lines.append(f"— [Wikidata {wd.qid}]({wd.source_url})")
        lines.append("")

    # Funding / company info
    lines.append("## Funding / company info")
    if wd and wd.official_website:
        lines.append(f"- Official website: {wd.official_website} (source: Wikidata)")
    if sheet.companies:
        for c in sheet.companies:
            bits = [f"**{c.company_name}** ({c.company_number})"]
            if c.status:
                bits.append(f"status: {c.status}")
            if c.incorporation_date:
                bits.append(f"incorporated: {c.incorporation_date}")
            lines.append("- " + ", ".join(bits) + f" — [Companies House]({c.source_url})")
            sig = []
            if c.accounts_last_made_up_to:
                sig.append(f"accounts to {c.accounts_last_made_up_to}")
            if c.accounts_next_due:
                sig.append(f"next due {c.accounts_next_due}" + (" (overdue)" if c.accounts_overdue else ""))
            if c.has_charges:
                sig.append(f"{len(c.charges) or 'has'} charge(s)")
            if c.has_insolvency_history:
                sig.append("insolvency history")
            if sig:
                lines.append(f"  - signals: {' · '.join(sig)}")
    else:
        lines.append("_No company profiles returned._")
    lines.append("")

    # Regulatory record (FCA)
    lines.append("## Regulatory record (FCA)")
    if sheet.fca_records:
        for r in sheet.fca_records:
            status = f" — {r.status}" if r.status else ""
            lines.append(f"- {r.name} (IRN {r.reference_number or '—'}){status} — [FCA]({r.source_url})")
            if r.roles:
                lines.append(f"  - roles: {', '.join(r.roles)}")
            if r.firms:
                lines.append(f"  - firms: {', '.join(r.firms)}")
    else:
        lines.append("_No FCA-approved individual matched (or not configured)._")
    lines.append("")

    # Philanthropy (Charity Commission)
    lines.append("## Philanthropy (Charity Commission)")
    if sheet.charities:
        for ch_rec in sheet.charities:
            num = f" (no. {ch_rec.charity_number})" if ch_rec.charity_number else ""
            lines.append(f"- {ch_rec.name}{num} — [Charity register]({ch_rec.source_url})")
    else:
        lines.append("_No matching charities (or not configured)._")
    lines.append("")

    # Risk & due diligence
    lines.append("## Risk & due diligence")
    lines.append("_Screening leads to verify — a name match is not a confirmed identification._")
    if sheet.sanctions_hits:
        lines.append("### Sanctions / PEP — source: OpenSanctions")
        for h in sheet.sanctions_hits:
            topics = f" — {', '.join(h.topics)}" if h.topics else ""
            lines.append(f"- {h.name} ({h.schema or '—'}){topics} — [OpenSanctions]({h.source_url})")
    if sheet.gazette_notices:
        lines.append("### Official notices — source: The Gazette")
        for g in sheet.gazette_notices:
            pub = f" — {g.published[:10]}" if g.published else ""
            lines.append(f"- [{g.title}]({g.link}){pub}")
    if not sheet.sanctions_hits and not sheet.gazette_notices:
        lines.append("_No screening matches._")
    lines.append("")

    # News & controversies
    lines.append("## News & controversies")
    lines.append(
        "_Headlines surfaced from a news search for review — not verified claims._"
    )
    if sheet.news:
        for n in sheet.news:
            src = f" — {n.source}" if n.source else ""
            lines.append(f"- [{n.title}]({n.link}){src}")
    else:
        lines.append("_No news results._")
    lines.append("")

    return "\n".join(lines)


def to_pdf(sheet: OneSheet) -> bytes:
    """Render the markdown content to a simple, clean PDF via fpdf2."""
    from fpdf import FPDF

    md = to_markdown(sheet)

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(15, 15, 15)
    effective_width = pdf.w - 30

    def write_line(text: str, size: int, style: str = "", gap: float = 2):
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(effective_width, size * 0.5 + 2, _pdf_safe(text))
        pdf.ln(gap)

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            pdf.ln(2)
        elif line.startswith("# "):
            write_line(line[2:], 18, "B", gap=3)
        elif line.startswith("## "):
            write_line(line[3:], 14, "B", gap=2)
        elif line.startswith("### "):
            write_line(line[4:], 12, "B", gap=1)
        elif line.startswith(("> ", "_")):
            write_line(line.lstrip("> "), 9, "I")
        else:
            write_line(line, 10)

    out = pdf.output()  # fpdf2 returns a bytearray
    return bytes(out)
