"""Export agent output as a small, flat dataset for the JBox-built Signalpost site.

Input:  the agent's submission contract (one JSON object per company, with
        ``claims[]`` and ``evidence[]``), e.g. out/submission-contract.jsonl.
Output: <out>/companies.json  one object per company with its facts, each fact
                              carrying its own source (URL, type, date, quote, hash)
        <out>/companies.csv   one row per company   (for a Supabase table import)
        <out>/facts.csv       one row per fact      (for a Supabase table import)

Usage:
    uv run python scripts/export_jbox_dataset.py out/submission-contract.jsonl jbox-data/
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

# field -> (section, English label). Fields not listed are not shown on the site
# (registry codes, internal flags, and observation copies of the same fact).
FIELDS: dict[str, tuple[str, str]] = {
    "legal_name": ("Overview", "Registered name"),
    "legal_form": ("Overview", "Legal form"),
    "industry": ("Overview", "Industry"),
    "activity_description": ("Overview", "What the company does (registry)"),
    "statutory_purpose": ("Overview", "Statutory purpose"),
    "founding_date": ("Overview", "Founded"),
    "registered_at": ("Overview", "Registered"),
    "municipality": ("Overview", "Municipality"),
    "business_address": ("Overview", "Business address"),
    "postal_address": ("Overview", "Postal address"),
    "employees_registered": ("Overview", "Employees (registry)"),
    "share_capital": ("Overview", "Share capital (NOK)"),
    "vat_registered": ("Overview", "VAT registered"),
    "in_group": ("Overview", "Part of a group"),
    "financials.revenue": ("Finances", "Revenue (NOK)"),
    "financials.operating_result": ("Finances", "Operating result (NOK)"),
    "financials.annual_result": ("Finances", "Annual result (NOK)"),
    "financials.assets": ("Finances", "Total assets (NOK)"),
    "financials.equity": ("Finances", "Equity (NOK)"),
    "financials.debt": ("Finances", "Debt (NOK)"),
    "financial_history.years": ("Finances", "Years with filed accounts"),
    "roles": ("People", "Board and management"),
    "official_website": ("Website & social", "Official website"),
    "website_description": ("Website & social", "Website description"),
    "website.telephone": ("Website & social", "Phone (from website)"),
    "website.email": ("Website & social", "E-mail (from website)"),
    "social.facebook": ("Website & social", "Facebook"),
    "social.instagram": ("Website & social", "Instagram"),
    "social.x": ("Website & social", "X / Twitter"),
    "social.linkedin": ("Website & social", "LinkedIn"),
    "careers_page": ("Hiring", "Careers page"),
    "job_posting": ("Hiring", "Job ad"),
    "job_board.job_posting": ("Hiring", "Job ad (NAV job board)"),
    "news": ("News", "News item"),
}
SECTIONS = ["Overview", "Finances", "People", "Website & social", "Hiring", "News"]

SOURCE_NAMES = {
    "official_registry_bulk": "Brønnøysund Register Centre (registry)",
    "official_registry": "Brønnøysund Register Centre (registry)",
    "official_annual_accounts": "Brønnøysund annual accounts register",
    "official_roles": "Brønnøysund roles register",
}

AVAILABILITY_TEXT = {
    "available": "Found",
    "not_available": "Looked, nothing found",
    "blocked": "Source refused access",
    "not_applicable": "Does not apply",
    "ambiguous": "Could not confirm it is this company",
    "failed": "Lookup failed",
}


_DATES = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _period(p: Any) -> str:
    if isinstance(p, str) and "fraDato" in p:
        d = _DATES.findall(p)
        return f"{d[0]} – {d[1]}" if len(d) >= 2 else ""
    if isinstance(p, dict) and p.get("fraDato"):
        return f"{p['fraDato']} – {p.get('tilDato', '')}".strip(" –")
    if isinstance(p, str) and p[:4].isdigit() and "T" not in p:
        return p
    return ""


def _clean_period(p: str) -> str:
    """Registry placeholder dates (1970-01-01 etc.) are not real periods."""
    return "" if p and p[:4] < "1980" else p


METRICS = [("financials.revenue", "Revenue"), ("financials.operating_result", "Operating result"),
           ("financials.annual_result", "Annual result"), ("financials.equity", "Equity"),
           ("financials.assets", "Total assets"), ("financials.debt", "Debt")]


def _number(text: str) -> float | None:
    try:
        return float(text.replace(" ", "").replace("\u00a0", ""))
    except ValueError:
        return None


def _highlights(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Ready-made, derived summaries for the site (each points back to fact ids)."""
    series: dict[str, list[dict[str, Any]]] = {}
    for key, name in METRICS:
        pts = []
        for f in facts:
            if f["field"] == key and f["availability"] == "available" and f["period"]:
                n = _number(f["value"])
                if n is not None:
                    pts.append({"year": f["period"][:4], "value": n, "fact_id": f["id"]})
        pts.sort(key=lambda x: x["year"])
        if pts:
            series[name] = pts
    latest = {}
    for name, pts in series.items():
        last = pts[-1]
        prev = pts[-2] if len(pts) >= 2 else None
        change = None
        if prev and prev["value"] > 0 and last["value"] > 0:  # % only between two positive figures
            change = round((last["value"] - prev["value"]) / abs(prev["value"]) * 100, 1)
        latest[name] = {"year": last["year"], "value": last["value"], "fact_id": last["fact_id"],
                        "change_pct_vs_previous_year": change}
    coverage = {}
    for sec in SECTIONS:
        sec_facts = [f for f in facts if f["section"] == sec]
        coverage[sec] = {"found": sum(f["availability"] == "available" for f in sec_facts),
                         "not_found": sum(f["availability"] != "available" for f in sec_facts)}
    sources: dict[str, int] = {}
    for f in facts:
        if f["availability"] == "available" and f["source_name"]:
            sources[f["source_name"]] = sources.get(f["source_name"], 0) + 1
    return {"finance_series": series, "latest_finances": latest, "coverage": coverage,
            "sources": dict(sorted(sources.items(), key=lambda kv: -kv[1]))}


def _text(field: str, v: Any) -> tuple[str, str]:
    """(display text, link) for a claim value."""
    if v is None:
        return "", ""
    if field == "roles" and isinstance(v, list):
        return "; ".join(f"{r.get('name')} ({r.get('role')})" for r in v if isinstance(r, dict)), ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v), ""
    if isinstance(v, dict) and "amount" in v:
        try:
            amount = round(float(v["amount"]))
            return f"{amount:,}".replace(",", " ") + f" {v.get('currency') or ''}".rstrip(), ""
        except (TypeError, ValueError):
            return str(v["amount"]), ""
    if isinstance(v, dict):
        title = v.get("headline") or v.get("title") or v.get("name") or ""
        date = v.get("date_published") or v.get("published") or v.get("date_posted") or ""
        date = str(date)[:10] if date else ""
        return (f"{title} ({date})" if date else str(title)), str(v.get("url") or "")
    if isinstance(v, bool):
        return ("Yes" if v else "No"), ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int) and field.startswith(("financials.", "share_capital")):
        return f"{v:,}".replace(",", " "), ""
    s = str(v)
    return s, (s if s.startswith("http") else "")


_DICT_PREFIX = re.compile(r"^\{[^}]*\}\s*")  # accounts spans start with the period dict


def _source(ev: dict[str, Any]) -> dict[str, str]:
    cls = ev.get("source_class") or ""
    url = ev.get("source_url") or ""
    name = SOURCE_NAMES.get(cls)
    host = url.split("/")[2] if url.count("/") >= 2 else url
    host = host[4:] if host.startswith("www.") else host
    if not name and "company_website" in cls:
        name = f"Company website ({host})"
    elif not name and "account" in cls:
        name = "Brønnøysund annual accounts register"
    elif not name and ("nav" in host or "job_board" in cls):
        name = "NAV job board (official)"
    elif not name and any(k in host for k in ("facebook", "instagram", "linkedin", "x.com", "twitter")):
        name = f"Social profile ({host})"
    if not name:
        host = url.split("/")[2] if url.count("/") >= 2 else url
        name = f"{cls.replace('_', ' ').capitalize() or 'Source'} – {host}"
    return {"source_url": url, "source_name": name, "source_type": cls,
            "retrieved_at": (ev.get("retrieved_at") or "")[:19].replace("T", " "),
            "quote": _DICT_PREFIX.sub("", ev.get("claim_span") or "")[:500],
            "content_sha256": ev.get("content_sha256") or ""}


def convert(contract_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    companies = []
    for row in contract_rows:
        org = row["organisation_number"]
        evidence = {e["id"]: e for e in row.get("evidence", [])}
        facts, head = [], {}
        for i, c in enumerate(row.get("claims", [])):
            field = c.get("field")
            if field in ("legal_name", "legal_form", "municipality", "industry", "official_website",
                         "activity_description", "employees_registered") and c.get("availability") == "available":
                head[field] = c.get("value")
            if field not in FIELDS:
                continue
            section, label = FIELDS[field]
            text, link = _text(field, c.get("value"))
            if field == "statutory_purpose" and text and text == str(head.get("activity_description") or ""):
                continue  # same text as "What the company does"; show it once
            ev = next((evidence[e] for e in c.get("evidence_ids") or [] if e in evidence), {})
            facts.append({"id": f"{org}-{i}", "section": section, "label": label, "field": field,
                          "value": text, "link": link,
                          "availability": c.get("availability"),
                          "availability_text": AVAILABILITY_TEXT.get(c.get("availability"), c.get("availability")),
                          "period": _clean_period(_period(c.get("reporting_period"))),
                          **_source(ev)})
        facts.sort(key=lambda f: (SECTIONS.index(f["section"]), f["label"]))
        found = [f for f in facts if f["availability"] == "available"]
        companies.append({
            "organisation_number": org,
            "name": head.get("legal_name") or org,
            "legal_form": head.get("legal_form") or "",
            "municipality": head.get("municipality") or "",
            "industry": head.get("industry") or "",
            "summary": head.get("activity_description") or "",
            "employees": head.get("employees_registered") or "",
            "website": head.get("official_website") or "",
            "has_finances": any(f["section"] == "Finances" for f in found),
            "has_website": any(f["field"] == "official_website" for f in found),
            "has_social": any(f["field"].startswith("social.") for f in found),
            "has_news": any(f["section"] == "News" for f in found),
            "has_hiring": any(f["section"] == "Hiring" for f in found),
            "fact_count": len(found),
            "source_count": len({f["source_url"] for f in found if f["source_url"]}),
            "highlights": _highlights(facts),
            "facts": facts,
        })
    companies.sort(key=lambda c: c["name"])
    return companies


def main(src: str, out_dir: str) -> None:
    rows = [json.loads(l) for l in Path(src).read_text(encoding="utf-8").splitlines() if l.strip()]
    companies = convert(rows)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "companies.json").write_text(json.dumps(companies, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    cols = [k for k in companies[0] if k not in ("facts", "highlights")]
    with open(out / "companies.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for c in companies:
            w.writerow({k: c[k] for k in cols})
    fact_cols = ["organisation_number"] + list(companies[0]["facts"][0])
    with open(out / "facts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fact_cols)
        w.writeheader()
        for c in companies:
            for fact in c["facts"]:
                w.writerow({"organisation_number": c["organisation_number"], **fact})
    n_facts = sum(len(c["facts"]) for c in companies)
    print(f"{len(companies)} companies, {n_facts} facts -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
