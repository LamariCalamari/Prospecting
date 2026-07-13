"""Prospecting one-sheet app (Streamlit).

Flow:
  1. Search: enter a name + optional context.
  2. Disambiguate: pick the exact Companies House officer and/or Wikipedia
     person. Nothing is assembled until you explicitly confirm.
  3. One-sheet: link-rich, source-attributed research sheet + export.

The UI is deliberately thin. All data logic lives in core.assembly and the
source modules.
"""
from __future__ import annotations

import streamlit as st

import config
from core import assembly, export, prospecting
from core.models import OfficerCandidate

st.set_page_config(page_title="Prospecting one-sheet", page_icon="🔎", layout="wide")

# --- Session state defaults -------------------------------------------------
_DEFAULTS = {
    "stage": "search",        # search -> disambiguate -> sheet
    "name": "",
    "context": "",
    "context_fields": {},
    "candidates": None,
    "sheet": None,
}
for key, val in _DEFAULTS.items():
    st.session_state.setdefault(key, val)


def reset_to_search():
    for key, val in _DEFAULTS.items():
        st.session_state[key] = val


# --- Header -----------------------------------------------------------------
st.title("🔎 Prospecting one-sheet")
st.caption(
    "Verified, link-rich research from Companies House (directorships, PSC, "
    "filings, charges), Wikipedia, Wikidata, FCA register, Charity Commission, "
    "OpenSanctions, The Gazette and news. Every fact is attributed; blanks mean "
    "the source returned nothing. Nothing is inferred."
)

if not config.companies_house_configured():
    st.warning(
        "**No Companies House API key detected.** Wikipedia and news will still "
        "work, but directorships and PSC data need a free key. Add "
        "`COMPANIES_HOUSE_API_KEY` to your `.env` (see `.env.example`) and restart.",
        icon="⚠️",
    )


# ============================================================================
# STAGE 1 — SEARCH
# ============================================================================
if st.session_state.stage == "search":
    with st.form("search_form"):
        name = st.text_input(
            "Person's name",
            value=st.session_state.name,
            placeholder="e.g. Nikolay Storonsky",
        )
        st.caption("Optional details — help narrow the match (used to bias search, never treated as fact):")
        c1, c2 = st.columns(2)
        with c1:
            company = st.text_input("Company / employer", placeholder="e.g. Revolut")
            nationality = st.text_input("Nationality", placeholder="e.g. British")
        with c2:
            role = st.text_input("Role / title", placeholder="e.g. CEO, founder")
            location = st.text_input("City / location", placeholder="e.g. London")
        submitted = st.form_submit_button("Search", type="primary")

    if submitted and name.strip():
        st.session_state.name = name.strip()
        # Join the filled boxes into one context string the backend uses to bias
        # Wikipedia/news search and to display on the sheet.
        st.session_state.context_fields = {
            "Company": company.strip(),
            "Role": role.strip(),
            "Nationality": nationality.strip(),
            "Location": location.strip(),
        }
        st.session_state.context = ", ".join(
            v for v in st.session_state.context_fields.values() if v
        )
        with st.spinner("Searching Companies House and Wikipedia…"):
            st.session_state.candidates = assembly.find_candidates(
                st.session_state.name, st.session_state.context
            )
        st.session_state.stage = "disambiguate"
        st.rerun()
    elif submitted:
        st.error("Enter a name to search.")


# ============================================================================
# STAGE 2 — DISAMBIGUATE (mandatory confirmation gate)
# ============================================================================
elif st.session_state.stage == "disambiguate":
    cands = st.session_state.candidates
    st.subheader(f"Confirm who you mean: “{st.session_state.name}”")
    st.info(
        "We've **pre-selected the most likely match** in each column — check "
        "they're the same person, adjust if not, then confirm. The one-sheet is "
        "built **only** for what you select here.",
        icon="✅",
    )

    for err in cands.errors:
        st.warning(err, icon="⚠️")

    # Smart defaults: pre-select the best-matching officer + Wikipedia page so
    # the two columns line up on the same person automatically. Birth year (from
    # the matched Wikipedia page) is used to pick the right Companies House record
    # among namesakes. Radio index 0 is the "none" option, so add 1.
    best_off = prospecting.best_officer_index(
        cands.officers, st.session_state.name, cands.birth_year
    )
    best_wiki = prospecting.best_wiki_index(cands.wiki, st.session_state.name)
    officer_default = (best_off + 1) if best_off is not None else 0
    wiki_default = (best_wiki + 1) if best_wiki is not None else 0
    if cands.birth_year:
        st.caption(f"↪ Wikipedia gives a birth year of **{cands.birth_year}** — "
                   "used to match the right Companies House record.")

    col_ch, col_wiki = st.columns(2)

    # --- Companies House officer choice ---
    with col_ch:
        st.markdown("### Companies House")
        officer_labels = ["— none / not in Companies House —"]
        officer_map: dict[str, OfficerCandidate] = {}
        for o in cands.officers:
            bits = [o.name]
            if o.date_of_birth:
                bits.append(f"b. {o.date_of_birth}")
            if o.appointment_count is not None:
                bits.append(f"{o.appointment_count} appts")
            label = " · ".join(bits)
            if o.top_companies:
                label += f"  ↳ {', '.join(o.top_companies[:3])}"
            officer_labels.append(label)
            officer_map[label] = o
        if not cands.officers:
            st.caption("No Companies House officer matches.")
        elif officer_default > 0:
            st.caption("✅ auto-matched to this person — verify the birth year, change if wrong.")
        else:
            st.caption("⚠️ Several possible matches — pick the one with the correct birth year.")
        officer_choice = st.radio(
            "Select the officer",
            officer_labels,
            index=officer_default,
            label_visibility="collapsed",
        )
        chosen_officer = officer_map.get(officer_choice)
        if chosen_officer:
            st.markdown(
                f"[View on Companies House ↗]({chosen_officer.source_url})"
            )
            if chosen_officer.address:
                st.caption(f"Address on file: {chosen_officer.address}")

    # --- Wikipedia choice ---
    with col_wiki:
        st.markdown("### Wikipedia")
        wiki_labels = ["— none / no confident match —"]
        wiki_map = {}
        for w in cands.wiki:
            label = w.title
            wiki_labels.append(label)
            wiki_map[label] = w
        if not cands.wiki:
            st.caption("No Wikipedia matches.")
        wiki_choice = st.radio(
            "Select the Wikipedia page",
            wiki_labels,
            index=wiki_default,
            label_visibility="collapsed",
        )
        chosen_wiki = wiki_map.get(wiki_choice)
        if chosen_wiki:
            st.markdown(f"[Open Wikipedia page ↗]({chosen_wiki.url})")
            if chosen_wiki.snippet:
                st.caption(chosen_wiki.snippet + "…")

    st.divider()
    confirm_col, back_col = st.columns([1, 1])
    with confirm_col:
        confirm = st.button("Confirm & build one-sheet", type="primary")
    with back_col:
        if st.button("Start over"):
            reset_to_search()
            st.rerun()

    if confirm:
        if not chosen_officer and not chosen_wiki:
            st.error(
                "Select at least one match (Companies House or Wikipedia) before "
                "building the sheet — this prevents building a sheet for the wrong "
                "person."
            )
        else:
            with st.spinner("Assembling one-sheet…"):
                st.session_state.sheet = assembly.build_one_sheet(
                    confirmed_name=(
                        chosen_wiki.title if chosen_wiki else chosen_officer.name
                    ),
                    officer=chosen_officer,
                    wiki_title=chosen_wiki.title if chosen_wiki else None,
                    context=st.session_state.context,
                    include_news=True,
                )
            st.session_state.stage = "sheet"
            st.rerun()


# ============================================================================
# STAGE 3 — ONE-SHEET
# ============================================================================
elif st.session_state.stage == "sheet":
    sheet = st.session_state.sheet
    sig = prospecting.derive_signals(sheet, sheet.confirmed_name)
    wd = sheet.wikidata

    # --- Header ---
    top = st.columns([3, 1])
    with top[0]:
        st.subheader(sheet.confirmed_name)
        if wd and wd.occupations:
            st.caption(", ".join(wd.occupations[:3]))
        if sheet.context:
            st.caption(f"Research context: {sheet.context}")
    with top[1]:
        if st.button("← New search"):
            reset_to_search()
            st.rerun()

    # --- Prospecting snapshot (metric tiles) ---
    st.markdown("#### 🎯 Prospecting snapshot")
    m = st.columns(4)
    m[0].metric("Net worth", sig.net_worth or "—")
    stake_val = sig.top_ownership or (
        f"{sig.top_former_ownership} (former)" if sig.top_former_ownership else "—"
    )
    m[1].metric("Largest disclosed stake", stake_val)
    m[2].metric("Active directorships", sig.active_directorships or "—")
    m[3].metric("Companies controlled", len(sig.stakes) or "—")
    flags = []
    if sig.companies_with_charges:
        flags.append(f"💷 {sig.companies_with_charges} co. with registered charges (debt)")
    if sig.insolvency_companies:
        flags.append(f"⚠️ {sig.insolvency_companies} co. with insolvency history")
    if sig.resigned_directorships:
        flags.append(f"⚪ {sig.resigned_directorships} past directorships")
    if flags:
        st.caption(" · ".join(flags))
    st.caption(
        "Net worth shows only when published (Wikidata). Stake = Companies House "
        "PSC band, verbatim. Free sources don't provide company valuations — the "
        "signals here are the sourced facts to base your own judgement on."
    )

    # --- Quick lookups: LinkedIn + verified links ---
    link_bits = []
    if sig.linkedin_url:
        link_bits.append(f"[✓ LinkedIn (verified) ↗]({sig.linkedin_url})")
    link_bits.append(f"[🔎 Search LinkedIn ↗]({sig.linkedin_search_url})")
    link_bits.append(f"[🔎 Google → LinkedIn ↗]({sig.google_linkedin_url})")
    if wd and wd.official_website:
        link_bits.append(f"[🌐 Official website ↗]({wd.official_website})")
    if wd and wd.twitter:
        link_bits.append(f"[X/Twitter ↗]({wd.twitter})")
    st.markdown(" · ".join(link_bits))

    st.divider()

    tab_overview, tab_control, tab_companies, tab_risk, tab_news = st.tabs(
        ["👤 Overview", "🏢 Directorships & control", "📊 Companies",
         "⚖️ Regulatory & risk", "📰 News"]
    )

    # ========================= OVERVIEW =========================
    with tab_overview:
        st.markdown("### Position")
        active = [a for a in sheet.appointments if a.status == "active"]
        if active:
            for a in active[:5]:
                role = (a.officer_role or "officer").title()
                st.markdown(
                    f"- **{role}**, {a.company_name} "
                    f"(appointed {a.appointed_on or '—'}) "
                    f"· [Companies House ↗]({a.source_url})"
                )
        if sheet.wiki and sheet.wiki.description:
            st.markdown(f"- {sheet.wiki.description} · [Wikipedia ↗]({sheet.wiki.url})")
        if not active and not (sheet.wiki and sheet.wiki.description):
            st.caption("No current position data returned.")

        st.markdown("### Background")
        if sheet.wiki:
            if sheet.wiki.thumbnail:
                img_col, text_col = st.columns([1, 4])
                with img_col:
                    st.image(sheet.wiki.thumbnail, width=110)
                with text_col:
                    st.write(sheet.wiki.extract)
                    st.markdown(f"— [Wikipedia: {sheet.wiki.title} ↗]({sheet.wiki.url})")
            else:
                st.write(sheet.wiki.extract)
                st.markdown(f"— [Wikipedia: {sheet.wiki.title} ↗]({sheet.wiki.url})")
        else:
            st.caption("No confident Wikipedia match — background left blank.")

        if wd:
            st.markdown("**Key facts** — source: Wikidata")
            rows = []
            if wd.net_worth:
                rows.append(f"- **Net worth:** {wd.net_worth}")
            if wd.occupations:
                rows.append(f"- **Occupation:** {', '.join(wd.occupations)}")
            if wd.positions:
                rows.append(f"- **Positions held:** {', '.join(wd.positions)}")
            if wd.educated_at:
                rows.append(f"- **Educated at:** {', '.join(wd.educated_at)}")
            for r in rows:
                st.markdown(r)
            st.markdown(
                f"<small>— [Wikidata: {wd.qid} ↗]({wd.source_url})</small>",
                unsafe_allow_html=True,
            )

    # ==================== DIRECTORSHIPS & CONTROL ====================
    with tab_control:
        if sig.stakes or sig.former_stakes:
            st.markdown("### Disclosed ownership stakes")
            st.caption(
                "Companies House PSC filings matched to this person by name — "
                "verify the identity. Percentages are CH's own bands, verbatim."
            )
            for s in sig.stakes:
                controls = "; ".join(s.controls) if s.controls else "control not specified"
                st.markdown(
                    f"- 🟢 **{s.company_name}** ({s.company_number}) — {controls} "
                    f"· [CH PSC ↗]({s.source_url})"
                )
            for s in sig.former_stakes:
                controls = "; ".join(s.controls) if s.controls else "control not specified"
                st.markdown(
                    f"- ⚪ **{s.company_name}** ({s.company_number}) — {controls} "
                    f"— _former, ceased {s.ceased_on}_ · [CH PSC ↗]({s.source_url})"
                )
            st.divider()

        st.markdown("### Appointments")
        if sheet.appointments:
            st.caption("source: Companies House")
            for a in sheet.appointments:
                dates = a.appointed_on or "—"
                if a.resigned_on:
                    dates += f" → {a.resigned_on}"
                role = a.officer_role or "officer"
                badge = "🟢 active" if a.status == "active" else "⚪ resigned"
                st.markdown(
                    f"- {badge} · **{a.company_name}** — {role}; {dates} "
                    f"· [CH ↗]({a.source_url})"
                )
        else:
            st.caption("No directorships returned.")

        if sheet.psc_filings:
            with st.expander("All persons with significant control (PSC), incl. others"):
                for p in sheet.psc_filings:
                    controls = "; ".join(
                        prospecting.humanize_control(n) for n in p.natures_of_control
                    ) if p.natures_of_control else "—"
                    ceased = f" · ceased {p.ceased_on}" if p.ceased_on else ""
                    st.markdown(
                        f"- {p.name or '(unnamed)'} @ {p.company_name}: {controls} "
                        f"(notified {p.notified_on or '—'}{ceased}) "
                        f"· [CH ↗]({p.source_url})"
                    )

    # ========================= COMPANIES =========================
    with tab_companies:
        if sheet.companies:
            for c in sheet.companies:
                bits = [f"**{c.company_name}** ({c.company_number})"]
                if c.status:
                    bits.append(f"status: {c.status}")
                if c.incorporation_date:
                    bits.append(f"incorporated: {c.incorporation_date}")
                st.markdown("- " + ", ".join(bits) + f" · [Companies House ↗]({c.source_url})")

                signals = []
                if c.accounts_last_made_up_to:
                    signals.append(f"accounts to {c.accounts_last_made_up_to}")
                if c.accounts_next_due:
                    overdue = " ⚠️ overdue" if c.accounts_overdue else ""
                    signals.append(f"next accounts due {c.accounts_next_due}{overdue}")
                if c.has_charges:
                    signals.append(f"{len(c.charges) or 'has'} charge(s)")
                if c.has_insolvency_history:
                    signals.append("⚠️ insolvency history")
                if signals:
                    st.markdown("&nbsp;&nbsp;&nbsp;↳ " + " · ".join(signals), unsafe_allow_html=True)
                for ch_item in c.charges[:5]:
                    cls = ch_item.classification or "charge"
                    pe = f" to {', '.join(ch_item.persons_entitled)}" if ch_item.persons_entitled else ""
                    st.markdown(
                        f"&nbsp;&nbsp;&nbsp;&nbsp;• {cls} — {ch_item.status or ''}"
                        f" (created {ch_item.created_on or '—'}){pe} · [charges ↗]({ch_item.source_url})",
                        unsafe_allow_html=True,
                    )
                if c.recent_filings:
                    with st.expander(f"Recent filings — {c.company_name}"):
                        for f in c.recent_filings:
                            st.markdown(
                                f"- {f.date or '—'} · {f.description or f.category or 'filing'} "
                                f"· [document ↗]({f.document_url})"
                            )
        else:
            st.caption("No company profiles returned.")

    # ==================== REGULATORY & RISK ====================
    with tab_risk:
        st.markdown("### Regulatory record (FCA)")
        if sheet.fca_records:
            st.caption("FCA Financial Services Register — verify identity before relying on a match.")
            for r in sheet.fca_records:
                status = f" · {r.status}" if r.status else ""
                st.markdown(f"- **{r.name}** (IRN {r.reference_number or '—'}){status} · [FCA register ↗]({r.source_url})")
                if r.roles:
                    st.markdown(f"&nbsp;&nbsp;&nbsp;↳ roles: {', '.join(r.roles)}", unsafe_allow_html=True)
                if r.firms:
                    st.markdown(f"&nbsp;&nbsp;&nbsp;↳ firms: {', '.join(r.firms)}", unsafe_allow_html=True)
        else:
            st.caption("No FCA-approved individual matched (or FCA not configured — see notes below).")

        st.markdown("### Philanthropy (Charity Commission)")
        if sheet.charities:
            st.caption("Charities whose name matches — often catches eponymous foundations. Verify the link.")
            for ch_rec in sheet.charities:
                num = f" (no. {ch_rec.charity_number})" if ch_rec.charity_number else ""
                status = f" · {ch_rec.status}" if ch_rec.status else ""
                st.markdown(f"- **{ch_rec.name}**{num}{status} · [Charity register ↗]({ch_rec.source_url})")
        else:
            st.caption("No matching charities (or Charity Commission not configured — see notes below).")

        st.markdown("### Sanctions / PEP / official notices")
        st.caption("Screening leads for you to verify — a name match is NOT a confirmed identification.")
        if sheet.sanctions_hits:
            st.markdown("**Sanctions / PEP / watchlist matches** — source: OpenSanctions")
            for h in sheet.sanctions_hits:
                topics = f" · {', '.join(h.topics)}" if h.topics else ""
                ctry = f" · {', '.join(h.countries)}" if h.countries else ""
                score = f" · score {h.score:.2f}" if isinstance(h.score, (int, float)) else ""
                st.markdown(f"- **{h.name}** ({h.schema or '—'}){topics}{ctry}{score} · [OpenSanctions ↗]({h.source_url})")
        else:
            st.caption("No sanctions/PEP matches (or OpenSanctions not configured — see notes below).")

        if sheet.gazette_notices:
            st.markdown("**Official notices** — source: The Gazette (insolvency, strike-off, legal)")
            for g in sheet.gazette_notices:
                pub = f" · {g.published[:10]}" if g.published else ""
                st.markdown(f"- [{g.title}]({g.link}){pub}")
        else:
            st.caption("No Gazette notices matched.")

    # ========================= NEWS =========================
    with tab_news:
        st.caption("Headlines from a news search, for you to review — not verified claims.")
        if sheet.news:
            for n in sheet.news:
                src = f" · {n.source}" if n.source else ""
                pub = f" · {n.published}" if n.published else ""
                st.markdown(f"- [{n.title}]({n.link}){src}{pub}")
        else:
            st.caption("No news results.")

    # Source notes (which sources ran / were skipped)
    if sheet.source_notes:
        with st.expander("Source notes (skipped / unavailable sources)"):
            for note in sheet.source_notes:
                st.markdown(f"- {note}")

    # Export
    st.divider()
    st.markdown("### Export")
    md = export.to_markdown(sheet)
    safe_name = sheet.confirmed_name.replace(" ", "_")
    exp_cols = st.columns(3)
    with exp_cols[0]:
        try:
            xlsx_bytes = export.to_excel(sheet)
            st.download_button(
                "⬇️ Download Excel",
                data=xlsx_bytes,
                file_name=f"onesheet_{safe_name}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Excel export unavailable: {exc}")
    with exp_cols[1]:
        try:
            pdf_bytes = export.to_pdf(sheet)
            st.download_button(
                "⬇️ Download PDF",
                data=pdf_bytes,
                file_name=f"onesheet_{safe_name}.pdf",
                mime="application/pdf",
            )
        except Exception as exc:  # noqa: BLE001
            st.caption(f"PDF export unavailable: {exc}")
    with exp_cols[2]:
        st.download_button(
            "⬇️ Download Markdown",
            data=md,
            file_name=f"onesheet_{safe_name}.md",
            mime="text/markdown",
        )

    with st.expander("Preview Markdown"):
        st.code(md, language="markdown")
