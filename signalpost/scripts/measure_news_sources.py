"""Live before/after measurement of dated-news recall (sp-news).

One crawl per company with the new sources on; every new item carries
``found_via`` so "before" = kept items from the old sources only, "after" =
all kept items. Website layer only (no registry/NAV modules).

  uv run python scripts/measure_news_sources.py --out /tmp/sp_news/run.jsonl
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.claim_checks import recheck_excerpts, rescue_group_site_items  # noqa: E402
from norway_company_agent.content_quality import apply_content_quality  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.news_sources import NEW_SOURCES  # noqa: E402
from norway_company_agent.website import fetch_website  # noqa: E402

FIELDS = ("headline", "date_published", "url", "source_page", "found_via", "date_source", "excluded_reason")


def companies(limit: int) -> list[dict]:
    """Every entry company with a registry website, topped up with the sites
    the 1,000-company run verified by discovery (the entry list has 109)."""
    rows = [json.loads(line) for line in (ROOT / "entry-companies.jsonl").open() if line.strip()]
    out = [dict(r, discovered=False) for r in rows if r.get("website")]
    index = ROOT / "jbox-data-1000" / "index.json"
    if index.exists() and len(out) < limit:
        by_org = {r["organisation_number"]: r for r in rows}
        for r in json.loads(index.read_text()):
            base = by_org.get(r["organisation_number"]) or {}
            if r.get("website") and not base.get("website"):
                out.append(dict(base, website=r["website"], discovered=True))
    return out[:limit]


def _strip(items: list[dict]) -> list[dict]:
    return [{k: i.get(k) for k in FIELDS} for i in items]


def _publish(profile: dict, record: dict) -> dict:
    gated = apply_website_identity_gate(profile, record)["website"]
    gated = rescue_group_site_items(profile, gated)
    return recheck_excerpts(apply_content_quality(profile, gated)).get("value") or {}


def run_one(c: dict) -> dict:
    started = time.monotonic()
    record, metrics = fetch_website(c["website"], max_secondary_pages=5)
    elapsed = time.monotonic() - started
    profile = {"organisation_number": c["organisation_number"], "name": c["name"], "evidence": {}}
    value = record.get("value") or {}
    if c["discovered"] and record.get("status") == "available":
        # These sites passed the discovery identity check in the 1,000-company run.
        value["discovered"] = True
        value["discovery_identity"] = {"method": "full_legal_name_on_discovered_site"}
    # "Before": the same crawl without the new sources' items.
    old = copy.deepcopy(record)
    if old.get("value"):
        old["value"]["news_articles"] = [a for a in old["value"].get("news_articles") or []
                                         if a.get("found_via") not in NEW_SOURCES]
    before = _publish(profile, old)
    v = _publish(profile, record)
    return {
        "news_before": _strip(before.get("news_articles") or []),
        "organisation_number": c["organisation_number"], "name": c["name"], "website": c["website"],
        "discovered": c["discovered"], "status": record.get("status"),
        "publishable": bool((v.get("identity_assessment") or {}).get("publishable")),
        "news": _strip(v.get("news_articles") or []),
        "excluded_new": _strip([i for i in v.get("excluded_news_articles") or [] if i.get("found_via") in NEW_SOURCES]),
        "news_sources": v.get("news_sources"), "requests": metrics.get("requests"), "elapsed_s": round(elapsed, 2),
    }


def summary(rows: list[dict]) -> dict:
    sites = [r for r in rows if r["status"] == "available"]
    old = lambda r: r["news_before"]
    ns = [(r.get("news_sources") or {}) for r in sites]
    extra = [m.get("requests", 0) - m.get("legacy_requests", 0) for m in ns]
    return {
        "companies": len(rows), "sites_available": len(sites),
        "publishable_sites": sum(r["publishable"] for r in sites),
        "companies_with_news_before": sum(bool(old(r)) for r in rows),
        "companies_with_news_after": sum(bool(r["news"]) for r in rows),
        "items_before": sum(len(old(r)) for r in rows),
        "items_after": sum(len(r["news"]) for r in rows),
        "new_items_by_source": {s: sum(i.get("found_via") == s for r in rows for i in r["news"]) for s in NEW_SOURCES},
        "extra_requests_total": sum(extra), "extra_requests_mean": round(sum(extra) / max(1, len(extra)), 2),
        "extra_requests_max": max(extra or [0]),
        "hook_seconds_total": round(sum(m.get("elapsed_ms", 0) for m in ns) / 1000, 1),
        "hook_seconds_mean": round(sum(m.get("elapsed_ms", 0) for m in ns) / 1000 / max(1, len(ns)), 2),
        "site_seconds_mean": round(sum(r["elapsed_s"] for r in sites) / max(1, len(sites)), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--count", type=int, default=None, help="companies from --offset (default: all)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()
    if not args.summary_only:
        todo = companies(args.limit)[args.offset:][:args.count]
        started = time.monotonic()
        with ThreadPoolExecutor(args.workers) as pool, open(args.out, "w") as fh:
            for i, row in enumerate(pool.map(run_one, todo), 1):
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                if i % 25 == 0:
                    print(f"{i}/{len(todo)} {time.monotonic() - started:.0f}s", flush=True)
        print(f"wall {time.monotonic() - started:.0f}s")
    rows = [json.loads(line) for path in args.out.split(",") for line in open(path)]
    print(json.dumps(summary(rows), indent=1))


if __name__ == "__main__":
    main()
