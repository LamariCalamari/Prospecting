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
from core import assembly, export, prospecting, valuation
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
    "Sourced research on UK-connected individuals: directorships, ownership, "
    "wealth signals, regulatory record and news — every fact linked to its source."
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

    # Companies House fragments one person across many officer records; group
    # them so the user picks a *person*, not a filing fragment. The birth year
    # from the matched Wikipedia page pre-selects the right namesake.
    groups = prospecting.group_officers(cands.officers)
    best_grp = prospecting.best_group_index(
        groups, st.session_state.name, cands.birth_year
    )
    best_wiki = prospecting.best_wiki_index(cands.wiki, st.session_state.name)
    group_default = (best_grp + 1) if best_grp is not None else 0
    wiki_default = (best_wiki + 1) if best_wiki is not None else 0

    col_ch, col_wiki = st.columns(2)

    # --- Companies House person choice (grouped records) ---
    with col_ch:
        st.markdown("### Companies House")
        group_labels = ["— none / not in Companies House —"]
        group_map: dict[str, OfficerCandidate] = {}
        for g in groups:
            bits = [prospecting.company_case(g.primary.name)]
            bits.append(f"b. {g.birth_year}" if g.birth_year else "birth year not filed")
            n_appts = max(g.total_appointments, g.primary.appointment_count or 0)
            if n_appts:
                bits.append(f"{n_appts} appointment{'s' if n_appts != 1 else ''}")
            label = " · ".join(bits)
            if g.top_companies:
                shown = ", ".join(
                    prospecting.company_case(c) for c in g.top_companies[:3]
                )
                label += f"  — {shown}"
            group_labels.append(label)
            group_map[label] = g.primary
        if not groups:
            st.caption("No Companies House matches.")
        elif group_default > 0 and cands.birth_year:
            st.caption(
                f"✅ Auto-matched by birth year **{cands.birth_year}** (from "
                "Wikipedia). Change it if this isn't the right person."
            )
        elif len(groups) > 1:
            st.caption("Several people share this name — pick by birth year / companies.")
        group_choice = st.radio(
            "Select the person",
            group_labels,
            index=group_default,
            label_visibility="collapsed",
        )
        chosen_officer = group_map.get(group_choice)
        if chosen_officer:
            st.markdown(
                f"[View on Companies House ↗]({chosen_officer.source_url})"
            )
            if chosen_officer.address:
                st.caption(f"Address on file: {chosen_officer.address}")

    # --- Wikipedia choice ---
    with col_wiki:
        st.markdown("### Wikipedia")
        # Name-matching pages first; unrelated full-text hits ("Candy cane")
        # are noise — hide them whenever a real match exists.
        matching = [w for w in cands.wiki
                    if prospecting.names_match(w.title, st.session_state.name)]
        others = [w for w in cands.wiki if w not in matching]
        shown_wiki = matching if matching else cands.wiki
        wiki_labels = ["— none / no confident match —"]
        wiki_map = {}
        for w in shown_wiki:
            wiki_labels.append(w.title)
            wiki_map[w.title] = w
        if matching:
            wiki_default = 1 if best_wiki is not None else 0
        if not cands.wiki:
            st.caption("No Wikipedia matches.")
        wiki_choice = st.radio(
            "Select the Wikipedia page",
            wiki_labels,
            index=wiki_default,
            label_visibility="collapsed",
        )
        if matching and others:
            st.caption(f"{len(others)} unrelated search results hidden.")
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
            with st.status("Assembling one-sheet…", expanded=True) as status:
                st.session_state.sheet = assembly.build_one_sheet(
                    confirmed_name=(
                        chosen_wiki.title if chosen_wiki else chosen_officer.name
                    ),
                    officer=chosen_officer,
                    wiki_title=chosen_wiki.title if chosen_wiki else None,
                    context=st.session_state.context,
                    include_news=True,
                    progress=st.write,
                )
                status.update(label="One-sheet ready", state="complete")
            st.session_state.stage = "sheet"
            st.rerun()


# ============================================================================
# STAGE 3 — ONE-SHEET
# ============================================================================
elif st.session_state.stage == "sheet":
    sheet = st.session_state.sheet
    sig = prospecting.derive_signals(sheet, sheet.confirmed_name)
    est = valuation.build_estimates(sheet, sheet.confirmed_name)
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

    # --- Prospecting snapshot: only tiles that actually have data ---
    tiles: list[tuple[str, str, str]] = []  # (label, value, help)
    if sig.net_worth:
        tiles.append(("Net worth (published)", sig.net_worth,
                      "As published on Wikidata, verbatim."))
    band_short = prospecting.short_band(sig.top_ownership) or (
        f"{prospecting.short_band(sig.top_former_ownership)} (former)"
        if sig.top_former_ownership else None
    )
    if band_short:
        tiles.append(("Largest disclosed stake", band_short,
                      "Companies House PSC band — a filed range, not computed."))
    if est.counted:
        tiles.append((
            "UK filed equity (floor)",
            f"{valuation.fmt_gbp(est.total_lo)}–{valuation.fmt_gbp(est.total_hi)}",
            "Stake band × net assets from latest UK filed accounts, summed. A "
            "conservative floor covering UK filings only — NOT total wealth. "
            "See the 💰 Stake value tab for the full working.",
        ))
    tiles.append(("Active directorships", str(sig.active_directorships),
                  "Live roles at live companies (dissolved companies excluded)."))
    if sig.stakes:
        tiles.append(("Companies controlled", str(len(sig.stakes)),
                      "Current PSC filings naming this person."))
    cols = st.columns(len(tiles))
    for col, (label, value, help_text) in zip(cols, tiles):
        col.metric(label, value, help=help_text)

    flags = []
    if sig.companies_with_charges:
        flags.append(f"{sig.companies_with_charges} companies with registered charges")
    if sig.insolvency_companies:
        flags.append(f"{sig.insolvency_companies} with insolvency history")
    if sig.dissolved_directorships:
        flags.append(f"{sig.dissolved_directorships} roles at dissolved companies")
    if sig.resigned_directorships:
        flags.append(f"{sig.resigned_directorships} resigned roles")
    if flags:
        st.caption("Also on file: " + " · ".join(flags) + ".")

    # --- Quick lookups ---
    link_bits = []
    if sig.linkedin_url:
        link_bits.append(f"[LinkedIn (verified) ↗]({sig.linkedin_url})")
    else:
        link_bits.append(f"[Find on LinkedIn ↗]({sig.google_linkedin_url})")
    if wd and wd.official_website:
        link_bits.append(f"[Website ↗]({wd.official_website})")
    if wd and wd.twitter:
        link_bits.append(f"[X/Twitter ↗]({wd.twitter})")
    if sheet.wiki:
        link_bits.append(f"[Wikipedia ↗]({sheet.wiki.url})")
    st.markdown(" · ".join(link_bits))

    st.divider()

    tab_overview, tab_control, tab_value, tab_companies, tab_risk, tab_news = st.tabs(
        ["👤 Overview", "🏢 Directorships & control", "💰 Stake value",
         "📊 Companies", "⚖️ Regulatory & risk", "📰 News"]
    )

    # ========================= OVERVIEW =========================
    with tab_overview:
        st.markdown("### Position")
        if sheet.wiki and sheet.wiki.description:
            st.markdown(
                f"**{sheet.wiki.description.capitalize()}** "
                f"· [Wikipedia ↗]({sheet.wiki.url})"
            )
        # Current roles: live company only, most recent first.
        status_by_num = {c.company_number: (c.status or "").lower()
                         for c in sheet.companies}
        live_roles = [
            a for a in sheet.appointments
            if a.status == "active"
            and status_by_num.get(a.company_number, "active") in ("", "active", "open")
        ]
        live_roles.sort(key=lambda a: a.appointed_on or "", reverse=True)
        for a in live_roles[:5]:
            st.markdown(
                f"- **{prospecting.role_case(a.officer_role)}**, "
                f"{prospecting.company_case(a.company_name)} "
                f"(since {a.appointed_on or '—'}) "
                f"· [Companies House ↗]({a.source_url})"
            )
        if len(live_roles) > 5:
            st.caption(
                f"+ {len(live_roles) - 5} more current roles — see the "
                "🏢 Directorships tab."
            )
        if not live_roles and not (sheet.wiki and sheet.wiki.description):
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
                    f"- 🟢 **{prospecting.company_case(s.company_name)}** "
                    f"({s.company_number}) — {controls} · [CH PSC ↗]({s.source_url})"
                )
            for s in sig.former_stakes:
                controls = "; ".join(s.controls) if s.controls else "control not specified"
                st.markdown(
                    f"- ⚪ **{prospecting.company_case(s.company_name)}** "
                    f"({s.company_number}) — {controls} "
                    f"— _former, ceased {s.ceased_on}_ · [CH PSC ↗]({s.source_url})"
                )
            st.divider()

        st.markdown("### Appointments")
        if sheet.appointments:
            st.caption("Source: Companies House. Newest first.")
            comp_status = {c.company_number: (c.status or "").lower()
                           for c in sheet.companies}
            ordered = sorted(
                sheet.appointments,
                key=lambda a: (a.status == "resigned", a.appointed_on or ""),
            )
            ordered = (
                sorted([a for a in ordered if a.status == "active"],
                       key=lambda a: a.appointed_on or "", reverse=True)
                + sorted([a for a in ordered if a.status != "active"],
                         key=lambda a: a.appointed_on or "", reverse=True)
            )
            for a in ordered:
                dates = a.appointed_on or "—"
                if a.resigned_on:
                    dates += f" → {a.resigned_on}"
                role = prospecting.role_case(a.officer_role)
                cstat = comp_status.get(a.company_number, "")
                if a.status == "active" and cstat in ("", "active", "open"):
                    badge = "🟢"
                elif a.status == "active":
                    badge = f"🏚 company {cstat or 'closed'}"
                else:
                    badge = "⚪ resigned"
                st.markdown(
                    f"- {badge} · **{prospecting.company_case(a.company_name)}** "
                    f"— {role}; {dates} · [CH ↗]({a.source_url})"
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

    # ========================= STAKE VALUE =========================
    with tab_value:
        st.markdown("### Estimated stake value")
        st.caption(
            "Method: disclosed stake band (Companies House PSC) × company value. "
            "Estimates, not facts — every input is shown and linked."
        )
        if est.counted:
            st.metric(
                "UK filed equity (book-value floor)",
                f"{valuation.fmt_gbp(est.total_lo)} – {valuation.fmt_gbp(est.total_hi)}",
                help="Net assets from latest UK filed accounts × stake band, "
                     "summed. A floor, not total wealth: book value understates "
                     "growth companies, and offshore/unfiled wealth is invisible "
                     "to UK accounts.",
            )
        if est.stakes:
            # Material holdings up front; £2-share-capital shells and other
            # nominal entries collapse into one line so they don't read as
            # findings.
            _MATERIAL = 10_000  # GBP
            material = [
                s for s in est.stakes
                if (s.value_hi or 0) >= _MATERIAL
                or (s.net_assets is not None and abs(s.net_assets) >= _MATERIAL)
                or s.net_assets is None
            ]
            nominal = [s for s in est.stakes if s not in material]

            st.markdown("#### Holdings")
            for s in material:
                band = f"{s.stake_lo:.0%}–{s.stake_hi:.0%}"
                cname = prospecting.company_case(s.company_name)
                if s.net_assets is not None:
                    date = f" (accounts to {s.accounts_date})" if s.accounts_date else ""
                    st.markdown(
                        f"- **{cname}** — {band} of net assets "
                        f"{valuation.fmt_gbp(s.net_assets)}{date} → "
                        f"**{valuation.fmt_gbp(s.value_lo)}–{valuation.fmt_gbp(s.value_hi)}** "
                        f"· [CH ↗]({s.source_url})"
                    )
                else:
                    st.markdown(
                        f"- **{cname}** — {band} held · paper-filed accounts — "
                        f"[open filings ↗]({s.source_url})"
                    )
            if nominal:
                names = ", ".join(
                    prospecting.company_case(s.company_name) for s in nominal
                )
                st.caption(
                    f"Plus {len(nominal)} nominal holding(s) (≈£0 book value — "
                    f"dormant/shell entities): {names}."
                )

            st.markdown("#### Market-basis calculator")
            st.caption(
                "Know a market valuation (funding round, press)? Apply the "
                "disclosed stake band to it — your figure, their filed stake."
            )
            calc_options = {
                f"{prospecting.company_case(s.company_name)} "
                f"({s.stake_lo:.0%}–{s.stake_hi:.0%})": s
                for s in est.stakes
            }
            pick = st.selectbox("Company", list(calc_options.keys()))
            mv = st.number_input(
                "Known market valuation (£ millions)", min_value=0.0, step=10.0,
                value=0.0,
            )
            if mv > 0:
                s = calc_options[pick]
                lo, hi = valuation.apply_market_valuation(s, mv * 1e6)
                st.success(
                    f"Estimated holding in "
                    f"{prospecting.company_case(s.company_name)}: "
                    f"**{valuation.fmt_gbp(lo)} – {valuation.fmt_gbp(hi)}** "
                    f"({s.stake_lo:.0%}–{s.stake_hi:.0%} of £{mv:,.0f}m — your figure)"
                )
        else:
            st.caption(
                "No disclosed share-ownership stakes to value (select a Companies "
                "House person, or none are filed)."
            )

    # ========================= COMPANIES =========================
    with tab_companies:
        if sheet.companies:
            import pandas as pd

            ordered_companies = sorted(
                sheet.companies,
                key=lambda c: ((c.status or "") != "active", c.company_name),
            )
            df = pd.DataFrame([
                {
                    "Company": prospecting.company_case(c.company_name),
                    "Status": c.status or "—",
                    "Incorporated": c.incorporation_date or "—",
                    "Accounts to": c.accounts_last_made_up_to or "—",
                    "Charges": len(c.charges) if c.charges else
                               ("yes" if c.has_charges else ""),
                    "Insolvency": "⚠️" if c.has_insolvency_history else "",
                    "Companies House": c.source_url,
                }
                for c in ordered_companies
            ])
            st.caption(
                f"{len(ordered_companies)} companies from this person's Companies "
                "House record. Click a column header to sort."
            )
            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Companies House": st.column_config.LinkColumn(
                        "Companies House", display_text="open ↗"
                    ),
                },
            )

            # Deep-dive panel for one company at a time.
            st.markdown("#### Inspect a company")
            by_label = {
                f"{prospecting.company_case(c.company_name)} ({c.company_number})": c
                for c in ordered_companies
            }
            chosen = st.selectbox("Company", list(by_label.keys()),
                                  label_visibility="collapsed")
            c = by_label[chosen]
            facts = [f"status: {c.status or '—'}",
                     f"incorporated: {c.incorporation_date or '—'}"]
            if c.accounts_next_due:
                facts.append(
                    f"next accounts due {c.accounts_next_due}"
                    + (" ⚠️ overdue" if c.accounts_overdue else "")
                )
            if c.net_assets is not None:
                facts.append(f"net assets {valuation.fmt_gbp(c.net_assets)}")
            st.markdown(" · ".join(facts) + f" · [Companies House ↗]({c.source_url})")
            for ch_item in c.charges[:8]:
                cls = ch_item.classification or "charge"
                pe = (f" to {', '.join(ch_item.persons_entitled)}"
                      if ch_item.persons_entitled else "")
                st.markdown(
                    f"- {cls} — {ch_item.status or ''} "
                    f"(created {ch_item.created_on or '—'}){pe} "
                    f"· [charges ↗]({ch_item.source_url})"
                )
            if c.recent_filings:
                st.markdown("**Recent filings**")
                for f in c.recent_filings:
                    st.markdown(
                        f"- {f.date or '—'} · {f.description or f.category or 'filing'} "
                        f"· [document ↗]({f.document_url})"
                    )
            if not c.charges and not c.recent_filings:
                st.caption(
                    "No deep-dive data fetched for this company (only the first "
                    "few get filings/charges pulled) — use the Companies House "
                    "link above."
                )
        else:
            st.caption("No company profiles returned.")

    # ==================== REGULATORY & RISK ====================
    with tab_risk:
        # Only render sections that can say something. Unconfigured optional
        # sources collapse into one line instead of three dead headers.
        unconfigured = []
        if not config.fca_configured():
            unconfigured.append("FCA register")
        if not config.charity_commission_configured():
            unconfigured.append("Charity Commission")
        if not config.opensanctions_configured():
            unconfigured.append("OpenSanctions")

        st.caption(
            "Screening leads for you to verify — a name match is NOT a "
            "confirmed identification."
        )

        if sheet.fca_records or config.fca_configured():
            st.markdown("### Regulatory record (FCA)")
            if sheet.fca_records:
                for r in sheet.fca_records:
                    status = f" · {r.status}" if r.status else ""
                    st.markdown(
                        f"- **{r.name}** (IRN {r.reference_number or '—'}){status} "
                        f"· [FCA register ↗]({r.source_url})"
                    )
                    if r.roles:
                        st.caption(f"roles: {', '.join(r.roles)}")
                    if r.firms:
                        st.caption(f"firms: {', '.join(r.firms)}")
            else:
                st.caption("No FCA-approved individual matched this name.")

        if sheet.charities or config.charity_commission_configured():
            st.markdown("### Philanthropy (Charity Commission)")
            if sheet.charities:
                st.caption("Name-matching charities — often eponymous foundations. Verify the link.")
                for ch_rec in sheet.charities:
                    num = f" (no. {ch_rec.charity_number})" if ch_rec.charity_number else ""
                    status = f" · {ch_rec.status}" if ch_rec.status else ""
                    st.markdown(
                        f"- **{ch_rec.name}**{num}{status} "
                        f"· [Charity register ↗]({ch_rec.source_url})"
                    )
            else:
                st.caption("No charities match this name.")

        if sheet.sanctions_hits or config.opensanctions_configured():
            st.markdown("### Sanctions / PEP screening")
            if sheet.sanctions_hits:
                for h in sheet.sanctions_hits:
                    topics = f" · {', '.join(h.topics)}" if h.topics else ""
                    ctry = f" · {', '.join(h.countries)}" if h.countries else ""
                    score = f" · score {h.score:.2f}" if isinstance(h.score, (int, float)) else ""
                    st.markdown(
                        f"- **{h.name}** ({h.schema or '—'}){topics}{ctry}{score} "
                        f"· [OpenSanctions ↗]({h.source_url})"
                    )
            else:
                st.caption("No sanctions/PEP/watchlist matches.")

        st.markdown("### Official notices (The Gazette)")
        if sheet.gazette_notices:
            for g in sheet.gazette_notices:
                pub = f" · {g.published[:10]}" if g.published else ""
                st.markdown(f"- [{g.title}]({g.link}){pub}")
        else:
            st.caption("No Gazette notices (insolvency, strike-off, legal) matched.")

        if unconfigured:
            st.info(
                "Not yet enabled (free keys, add to secrets to switch on): "
                + ", ".join(unconfigured) + ".",
                icon="🔧",
            )

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
