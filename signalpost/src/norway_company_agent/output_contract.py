"""Reshape our rich internal profile/envelope into the exact OUTPUT_CONTRACT.md
shape: a flat {organisation_number, run, claims[], evidence[], changes[], errors[],
operations} object with the 6-value availability vocabulary
(available / not_available / blocked / not_applicable / ambiguous / failed).

This is a pure offline transformation (no re-crawl). It exposes ALL the facts we
already collect — registry fields, financials, roles, locations, website layer,
external observations — as countable claims with per-claim evidence IDs, so the
evaluator reads them in the specified format instead of our internal module dump.
"""

from __future__ import annotations

from typing import Any

# internal evidence status -> contract availability
_AVAIL = {
    "available": "available",
    "not_found": "not_available",
    "not_applicable": "not_applicable",
    "blocked": "blocked",
    "blocked_robots": "blocked",
    "blocked_policy": "blocked",
    "source_error": "failed",
    "failed": "failed",
    "not_fetched": "failed",
}


def _avail(status: str | None) -> str:
    return _AVAIL.get(str(status or ""), "not_available")


def _ev_entry(ev_id: str, record: dict, span: str | None) -> dict:
    return {
        "id": ev_id,
        "source_url": record.get("source_url"),
        "source_class": record.get("source_class") or record.get("source_type"),
        "retrieved_at": record.get("retrieved_at"),
        "content_sha256": record.get("content_sha256"),
        "claim_span": span,
    }


def to_contract(profile: dict[str, Any], *, run_id: str, started_at: str, completed_at: str,
                terminal_status: str) -> dict[str, Any]:
    org = profile.get("organisation_number")
    ev = profile.get("evidence", {}) or {}
    claims: list[dict] = []
    evidence: list[dict] = []
    seen_ev: set[str] = set()

    def add_ev(ev_id: str, record: dict, span: str | None) -> str:
        if ev_id not in seen_ev:
            evidence.append(_ev_entry(ev_id, record, span))
            seen_ev.add(ev_id)
        return ev_id

    def claim(field: str, value: Any, availability: str, ev_ids: list[str], confidence: float,
              reporting_period: str | None = None) -> None:
        claims.append({"field": field, "value": value, "availability": availability,
                       "confidence": round(confidence, 3), "evidence_ids": ev_ids,
                       "reporting_period": reporting_period})

    # 1) Formalised official registry claims (V4) — one shared registry evidence.
    reg = ev.get("registry", {}) or {}
    if reg:
        add_ev("ev-registry", reg, "Official Brønnøysund registry record")
    for rc in profile.get("registry_claims", []) or []:
        claim(rc["field"], rc["value"], rc.get("availability", "available"), ["ev-registry"],
              0.97, rc.get("reporting_period"))

    # 2) Financials
    fin = ev.get("financials", {}) or {}
    recs = (fin.get("value") or {}).get("records") or []
    if fin:
        add_ev("ev-financials", fin, "Official annual accounts (Regnskapsregisteret)")
    if recs:
        r = recs[0]
        period = r.get("period")
        for f in ("revenue", "operating_result", "annual_result", "assets", "equity", "debt"):
            if r.get(f) is not None:
                claim(f"financials.{f}", r[f], "available", ["ev-financials"], 0.97, str(period))
    elif fin:
        claim("financials", None, _avail(fin.get("status")), ["ev-financials"], 0.5)

    # 3) Roles, locations, financial history
    for module, field, extract in (
        ("roles", "roles", lambda v: [{"name": p.get("name") or p.get("organisation_number"),
                                        "role": p.get("role")} for p in (v.get("roles") or []) if not p.get("inactive")][:20]),
        ("locations", "locations", lambda v: (v.get("locations") or [])[:20]),
    ):
        rec = ev.get(module, {}) or {}
        if not rec:
            continue
        add_ev(f"ev-{module}", rec, f"Official {module}")
        items = extract(rec.get("value") or {})
        if items:
            claim(module, items, "available", [f"ev-{module}"], 0.97)
        else:
            claim(module, None, _avail(rec.get("status")), [f"ev-{module}"], 0.5)
    fh = ev.get("financial_history", {}) or {}
    if fh:
        add_ev("ev-financial_history", fh, "Filed annual-account years")
        years = (fh.get("value") or {}).get("years") or []
        if years:
            claim("financial_history.years", years, "available", ["ev-financial_history"], 0.97)

    # 4) Website layer (only publish facts when identity is exact; else ambiguous)
    web = ev.get("website", {}) or {}
    wv = web.get("value") or {}
    pub = (wv.get("identity_assessment") or {}).get("publishable")
    if web:
        add_ev("ev-website", web, (wv.get("title") or "")[:300] or "Company website")
    if web.get("status") == "available" and pub:
        claim("official_website", wv.get("final_url"), "available", ["ev-website"], (wv.get("identity_assessment") or {}).get("score", 0.95))
        if wv.get("description"):
            claim("website_description", wv["description"], "available", ["ev-website"], 0.9)
        for s in wv.get("social_links") or []:
            claim(f"social.{s.get('platform')}", s.get("url"), "available", ["ev-website"], 0.9)
        for j in (wv.get("job_postings") or [])[:12]:
            claim("job_posting", {"title": j.get("title"), "date_posted": j.get("date_posted"), "url": j.get("url")}, "available", ["ev-website"], 0.9)
        for a in (wv.get("news_articles") or [])[:12]:
            claim("news", {"headline": a.get("headline"), "date_published": a.get("date_published")}, "available", ["ev-website"], 0.9)
        sf = wv.get("structured_facts") or {}
        for k, v in sf.items():
            claim(f"website.{k}", v, "available", ["ev-website"], 0.9)
    elif web.get("status") == "available" and not pub:
        claim("official_website", wv.get("final_url"), "ambiguous", ["ev-website"], 0.5)
    elif web:
        claim("official_website", None, _avail(web.get("status")), ["ev-website"], 0.5)

    # 5) Published external observations
    ef = ev.get("external_footprint", {}) or {}
    for i, obs in enumerate((ef.get("observations") if isinstance(ef, dict) else []) or []):
        eid = f"ev-obs-{i}"
        add_ev(eid, obs, (obs.get("evidence_span") or "")[:300] or None)
        claim(f"{obs.get('platform')}.{obs.get('signal_type')}", obs.get("metrics") or obs.get("source_url"),
              "available", [eid], 0.97, obs.get("effective_at"))

    rm = profile.get("run_metrics", {}) or {}
    return {
        "organisation_number": org,
        "run": {"run_id": run_id, "started_at": started_at, "completed_at": completed_at,
                "terminal_status": terminal_status},
        "claims": claims,
        "evidence": evidence,
        "changes": [],
        "errors": [],
        "operations": {"requests": rm.get("requests", 0), "runtime_ms": None, "third_party_cost_usd": 0},
    }
