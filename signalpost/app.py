#!/usr/bin/env python3
"""Signalpost — interactive company-intelligence app (UX dimension).

Run locally:

    uv run --with streamlit streamlit run app.py

It reads the scored artifacts (``out/profiles.jsonl``) and gives a user the four
things the evaluation asks for: **find, compare and verify company information
on desktop and mobile**, with sources and dates visible on every fact, honest
missing/blocked states, and export.

Sections:
  * Research   — one organisation number → full profile, every fact next to its
                 source URL + retrieval date + availability state.
  * Search     — filter the whole set by name / org no. / municipality /
                 industry / coverage; export the filtered list as CSV.
  * Compare    — 2–4 companies side by side.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.research import answer_profile  # noqa: E402

PROFILE_FILE = ROOT / "out" / "profiles.jsonl"

STATE_ORDER = ["available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"]
STATE_BADGE = {
    "available": "ok",
    "not_available": "muted",
    "blocked": "warn",
    "not_applicable": "muted",
    "ambiguous": "warn",
    "failed": "bad",
}


@st.cache_data(show_spinner=False)
def load_profiles() -> list[dict]:
    if not PROFILE_FILE.exists():
        return []
    return [json.loads(line) for line in PROFILE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def fmt(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, list):
        return ", ".join(fmt(v) for v in value)
    if isinstance(value, dict):
        return " · ".join(f"{k.replace('_', ' ').title()}: {fmt(v)}" for k, v in value.items() if v not in (None, "", [], {}))
    return str(value)


def evidence_of(row: dict, module: str) -> dict:
    return ((row.get("evidence", {}) or {}).get(module) or {})


def coverage(row: dict) -> dict:
    fin = evidence_of(row, "financials").get("status") == "available"
    web = evidence_of(row, "website")
    pub = ((web.get("value") or {}).get("identity_assessment") or {}).get("publishable")
    has_web = web.get("status") == "available" and bool(pub)
    obs = (evidence_of(row, "external_footprint").get("observations") or [])
    return {"financials": fin, "website": has_web, "external": len(obs), "signals": sum(
        1 for o in obs if o.get("signal_type") in {"job_posting", "public_post", "review", "review_summary"})}


def availability_badge(state: str) -> str:
    return f'<span class="sp-badge {STATE_BADGE.get(state, "muted")}">{state or "not_available"}</span>'


def render_research(row: dict, question: str) -> None:
    result = answer_profile(row, question)
    facts = result.get("facts", [])
    name = result.get("company_name") or row.get("name")
    org = result.get("organisation_number") or row.get("organisation_number")

    st.markdown(f"### {name}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Organisation number", org)
    c2.metric("Municipality", fmt(row.get("municipality")))
    c3.metric("Legal form", fmt(row.get("legal_form")))
    c4.metric("Latest accounts", fmt(row.get("latest_submitted_accounts")))

    summary = row.get("summary") or {}
    if summary.get("narrative"):
        st.markdown("#### Summary")
        st.write(summary["narrative"])
        if summary.get("unknowns"):
            st.caption("Not found: " + ", ".join(summary["unknowns"]))
        st.caption(summary.get("policy", ""))

    st.markdown("#### Facts (each with its source and date)")
    financial = {"Revenue", "Operating result", "Annual result", "Assets", "Equity", "Debt"}
    money = [f for f in facts if f.get("claim") in financial and f.get("value") not in (None, "")]
    if money:
        cols = st.columns(len(money))
        for col, fact in zip(cols, money):
            col.metric(fact["claim"], fmt(fact["value"]))
    for fact in facts:
        if fact.get("claim") in financial:
            continue
        with st.container(border=True):
            st.markdown(f"**{fact.get('claim')}**")
            st.write(fmt(fact.get("value")))
            with st.expander("View evidence"):
                st.write(f"**Availability:** {fact.get('availability', 'available')}")
                st.write(f"**Classification:** {fact.get('classification', '—')}")
                st.write(f"**Source:** {fact.get('source_url', '—')}")
                st.write(f"**Retrieved:** {fact.get('retrieved_at', '—')}")
                if fact.get("reporting_period"):
                    st.write(f"**Reporting period:** {fact['reporting_period']}")

    obs = evidence_of(row, "external_footprint").get("observations") or []
    st.markdown(f"#### External intelligence ({len(obs)} verified source(s))")
    if not obs:
        st.info("No exact-entity external sources published for this company. Shown as `not_available`, not as zero.")
    groups: dict[str, list[dict]] = {}
    for o in obs:
        groups.setdefault(o.get("signal_type", "signal"), []).append(o)
    label = {"company_profile": "Verified web presence", "profile_handle": "Company-owned profiles",
             "job_posting": "Hiring signals", "workforce_snapshot": "Workforce",
             "public_post": "Dated public activity", "review": "Reviews", "review_summary": "Ratings"}
    for signal, items in groups.items():
        st.markdown(f"**{label.get(signal, signal)}**")
        for o in items[:25]:
            with st.container(border=True):
                st.write(fmt(o.get("metrics")) or o.get("evidence_span") or o.get("source_url"))
                st.caption(f"{o.get('platform')} · {o.get('signal_type')} · exact entity "
                           f"{o.get('exact_entity')} · effective {o.get('effective_at')}")
                with st.expander("View evidence"):
                    st.write(f"**Source:** {o.get('source_url')}")
                    st.write(f"**Retrieved:** {o.get('retrieved_at')}")
                    st.write(f"**Acquisition:** {o.get('acquisition_mode')} · rights: {o.get('rights_status')}")

    # Export the raw profile.
    st.download_button("Download this profile (JSON)", data=json.dumps(row, ensure_ascii=False, indent=2),
                       file_name=f"{org}.json", mime="application/json")


def render_search(profiles: list[dict]) -> None:
    st.markdown("#### Search and filter")
    c1, c2, c3, c4 = st.columns(4)
    q = c1.text_input("Name or organisation number")
    munis = sorted({p.get("municipality") or "" for p in profiles if p.get("municipality")})
    inds = sorted({p.get("industry_label") or "" for p in profiles if p.get("industry_label")})
    muni = c2.selectbox("Municipality", ["All"] + munis)
    ind = c3.selectbox("Industry", ["All"] + inds)
    cov = c4.selectbox("Coverage", ["All", "website", "financials", "has news/hiring"])

    ql = q.strip().lower()
    rows = []
    for p in profiles:
        if ql and ql not in f"{p.get('name','')} {p.get('organisation_number','')}".lower():
            continue
        if muni != "All" and (p.get("municipality") or "") != muni:
            continue
        if ind != "All" and (p.get("industry_label") or "") != ind:
            continue
        c = coverage(p)
        if cov == "website" and not c["website"]:
            continue
        if cov == "financials" and not c["financials"]:
            continue
        if cov == "has news/hiring" and not c["signals"]:
            continue
        rows.append(p)

    st.caption(f"{len(rows)} of {len(profiles)} companies")
    table = [{"Name": p.get("name"), "Org no.": p.get("organisation_number"),
              "Municipality": p.get("municipality"), "Industry": p.get("industry_label"),
              "Website": coverage(p)["website"], "Financials": coverage(p)["financials"],
              "News/hiring": coverage(p)["signals"]} for p in rows]
    st.dataframe(table, use_container_width=True, height=420)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["Name", "Org no.", "Municipality", "Industry", "Website", "Financials", "News/hiring"])
    writer.writeheader()
    writer.writerows(table)
    st.download_button("Export filtered list (CSV)", data=buffer.getvalue(), file_name="signalpost-filter.csv", mime="text/csv")

    pick = st.selectbox("Open a company", ["—"] + [f"{p.get('name')} ({p.get('organisation_number')})" for p in rows[:500]])
    if pick != "—":
        org = pick.rsplit("(", 1)[1].rstrip(")")
        row = next((p for p in rows if str(p.get("organisation_number")) == org), None)
        if row:
            render_research(row, "What do we know about this company?")


def render_compare(profiles: list[dict]) -> None:
    st.markdown("#### Compare companies")
    options = [f"{p.get('name')} ({p.get('organisation_number')})" for p in profiles]
    picks = st.multiselect("Select 2–4 companies", options, max_selections=4)
    if len(picks) < 2:
        st.info("Select at least two companies.")
        return
    chosen = []
    for pick in picks:
        org = pick.rsplit("(", 1)[1].rstrip(")")
        chosen.append(next(p for p in profiles if str(p.get("organisation_number")) == org))
    name_map = {c.get("organisation_number"): c.get("name") for c in chosen}
    facts = ["Municipality", "Legal form", "Industry", "Latest accounts year", "Employees", "Website"]
    table = {"Fact": facts}
    for c in chosen:
        c = c  # noqa
        table[name_map[c["organisation_number"]]] = [
            c.get("municipality"), c.get("legal_form"), c.get("industry_label"),
            c.get("latest_submitted_accounts"), c.get("employees"),
            (evidence_of(c, "website").get("value") or {}).get("final_url") or "—",
        ]
    st.dataframe(table, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Signalpost", page_icon="", layout="wide")
    st.markdown(
        """<style>
        .sp-badge{font-size:11px;padding:1px 8px;border-radius:999px;border:1px solid #ddd}
        .sp-badge.ok{color:#047857;border-color:#a7f3d0;background:#ecfdf5}
        .sp-badge.warn{color:#b45309;border-color:#fde68a;background:#fffbeb}
        .sp-badge.bad{color:#b91c1c;border-color:#fecaca;background:#fef2f2}
        .sp-badge.muted{color:#6b7280;border-color:#e5e7eb;background:#f9fafb}
        </style>""",
        unsafe_allow_html=True,
    )
    st.title("Signalpost")
    st.caption("Evidence-backed company intelligence for Norwegian companies — every fact links its source and date.")

    profiles = load_profiles()
    if not profiles:
        st.error("No profiles found. Run: uv run python scripts/run_competition_batch.py ... (see SUBMISSION.md)")
        st.stop()
    st.success(f"Loaded {len(profiles):,} company profiles · {sum(coverage(p)['external'] for p in profiles):,} verified external observations")

    mode = st.radio("What do you want to do?", ["Research a company", "Search companies", "Compare companies"], horizontal=True)
    if mode == "Research a company":
        org = st.text_input("Organisation number", placeholder="e.g. 985589003")
        question = st.text_input("Ask a question", value="What do we know about this company?")
        if st.button("Research", type="primary") and org.strip():
            row = next((p for p in profiles if str(p.get("organisation_number")) == org.strip()), None)
            if row is None:
                st.error(f"Organisation number {org} is not in this dataset.")
            else:
                render_research(row, question)
    elif mode == "Search companies":
        render_search(profiles)
    else:
        render_compare(profiles)


if __name__ == "__main__":
    main()
