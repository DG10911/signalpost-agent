"""Deterministic, evidence-grounded profile synthesis (the 10-point dimension).

Composes a concise summary from VERIFIED STRUCTURED FACTS ONLY. It is a template
over fields that already exist in the profile's evidence — it never infers,
estimates, or invents. Every statement carries the evidence field and source it
came from, and anything not found is listed explicitly under ``unknowns``.
"""

from __future__ import annotations

from typing import Any


def _ev(profile: dict, module: str) -> dict:
    return (profile.get("evidence", {}) or {}).get(module, {}) or {}


def _format_period(period: Any) -> str:
    if isinstance(period, dict):
        start, end = period.get("fraDato"), period.get("tilDato")
        if start and end:
            return f"{start} to {end}"
        return start or end or "the latest filed year"
    return str(period) if period else "the latest filed year"


def _stmt(statements: list, text: str, field: str, record: dict) -> None:
    statements.append({
        "text": text,
        "evidence_field": field,
        "source_url": record.get("source_url"),
        "retrieved_at": record.get("retrieved_at"),
    })


def summarize_profile(profile: dict) -> dict[str, Any]:
    name = profile.get("name")
    statements: list[dict[str, Any]] = []
    unknowns: list[str] = []

    reg = _ev(profile, "registry") if _ev(profile, "registry").get("status") == "available" else _ev(profile, "registry_live")

    # Identity sentence — only from present registry facts.
    ident_bits = []
    if profile.get("legal_form"):
        ident_bits.append(f"a {profile['legal_form']} company")
    if profile.get("industry_label") and profile["industry_label"] not in ("Uoppgitt",):
        ident_bits.append(f"operating in {profile['industry_label'].lower()}")
    if profile.get("municipality"):
        ident_bits.append(f"registered in {profile['municipality'].title()}")
    if name and ident_bits:
        _stmt(statements, f"{name} is " + ", ".join(ident_bits) + ".", "registry", reg)
    elif name:
        _stmt(statements, f"{name} is a registered Norwegian entity.", "registry", reg)

    if profile.get("bankrupt"):
        _stmt(statements, "The registry marks the entity as bankrupt.", "registry", reg)
    if profile.get("liquidating"):
        _stmt(statements, "The registry marks the entity as under liquidation.", "registry", reg)

    # Financials — latest filed record only.
    fin = _ev(profile, "financials")
    recs = (fin.get("value") or {}).get("records") or []
    if recs:
        r = recs[0]
        cur = r.get("currency") or "NOK"
        parts = []
        for label, key in (("revenue", "revenue"), ("operating result", "operating_result"), ("annual result", "annual_result")):
            if r.get(key) is not None:
                parts.append(f"{label} {r[key]} {cur}")
        if parts:
            period = _format_period(r.get("period"))
            _stmt(statements, f"Latest filed accounts ({period}): " + ", ".join(parts) + ".", "financials", fin)
    else:
        unknowns.append("filed financial figures")

    # Leadership
    roles = _ev(profile, "roles")
    people = [p for p in (roles.get("value") or {}).get("roles", []) if not p.get("inactive")]
    if people:
        lead = people[0]
        who = lead.get("name") or lead.get("organisation_number")
        extra = f" and {len(people) - 1} other registered role(s)" if len(people) > 1 else ""
        _stmt(statements, f"Registered leadership includes {who} ({lead.get('role') or 'role'}){extra}.", "roles", roles)
    else:
        unknowns.append("registered leadership/roles")

    # Locations
    locs = _ev(profile, "locations")
    items = (locs.get("value") or {}).get("locations", [])
    if items:
        _stmt(statements, f"{len(items)} registered subunit(s)/workplace(s).", "locations", locs)
    else:
        unknowns.append("registered subunits/locations")

    # Website + company-owned signals (only when identity is exact).
    web = _ev(profile, "website")
    wv = web.get("value") or {}
    if web.get("status") == "available" and (wv.get("identity_assessment") or {}).get("publishable"):
        bits = [f"verified official website {wv.get('final_url')}"]
        if wv.get("job_postings"):
            bits.append(f"{len(wv['job_postings'])} open job posting(s)")
        if wv.get("news_articles"):
            bits.append(f"{len(wv['news_articles'])} dated news item(s)")
        if wv.get("social_links"):
            bits.append(f"{len(wv['social_links'])} company-linked social profile(s)")
        _stmt(statements, "Company-owned sources: " + ", ".join(bits) + ".", "website", web)
    else:
        unknowns.append("verified company website")

    # Workforce (published external observation)
    ef = _ev(profile, "external_footprint")
    for obs in (ef.get("observations") if isinstance(ef, dict) else []) or []:
        if obs.get("signal_type") == "workforce_snapshot":
            m = obs.get("metrics") or {}
            _stmt(statements, f"Workforce reported in the {m.get('year')} annual report: {m.get('workforce_value')} ({m.get('measure')}).", "external_footprint", obs)

    headline = name or profile.get("organisation_number") or "Company"
    narrative = " ".join(s["text"] for s in statements) or "No verified public facts were found for this organisation number in this run."
    return {
        "headline": headline,
        "narrative": narrative,
        "statements": statements,
        "unknowns": unknowns,
        "policy": "Deterministic template over verified structured evidence only; no inference, estimation, or generated prose. Every statement cites its evidence field and source.",
    }
