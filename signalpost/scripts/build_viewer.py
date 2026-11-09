#!/usr/bin/env python3
"""Build the hosted Signalpost viewer: a static, dependency-free site
(plain HTML/CSS/JS, no build step) for finding, comparing and verifying the
agent's company profiles on desktop and phone.

The data comes straight from the agent's submission contract and goes through
the same ``convert()`` that produces the JBox dataset, so the viewer always
shows exactly what the agent published (every fact keeps its source URL,
retrieval time, quoted text and SHA-256).

    # from the contract (the normal path after a run)
    uv run python scripts/build_viewer.py --contract out/submission-contract.jsonl --out viewer

    # from an already exported split dataset (export_jbox_dataset.py --split)
    uv run python scripts/build_viewer.py --dataset jbox-data-1000 --out viewer

Output (<out>/):
    index.html, assets/app.css, assets/app.js   the app (copied from scripts/viewer_template/)
    data/index.json                             light index for search, filters, cards
    data/c/<org>.json                           one file per company, loaded on demand
    data/stats.json                             totals for the header and About page
    .nojekyll                                   serve files as-is on GitHub Pages

Claims the JBox export does not show (``synthesis.*`` and the contract's
``changes[]``) are carried through generically when present, each with its
evidence, so the viewer can render a summary block and a changes list.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_jbox_dataset import SECTIONS, _source, convert  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "viewer_template"
FLAG_KEYS = [("has_website", "w"), ("has_news", "n"), ("has_hiring", "h"), ("has_social", "s"), ("has_finances", "a")]


def _extras(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """synthesis.* claims and changes[] per company, with their evidence resolved."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        evidence = {e.get("id"): e for e in row.get("evidence", [])}

        def sources(ids: Any) -> list[dict[str, str]]:
            return [_source(evidence[i]) for i in (ids or []) if i in evidence]

        synthesis = []
        for i, c in enumerate(row.get("claims", [])):
            field = str(c.get("field") or "")
            if not field.startswith("synthesis"):
                continue
            synthesis.append({"id": f"{row['organisation_number']}-s{i}", "field": field, "value": c.get("value"),
                              "availability": c.get("availability"), "confidence": c.get("confidence"),
                              "sources": sources(c.get("evidence_ids"))})
        changes = []
        for j, ch in enumerate(row.get("changes") or []):
            if not isinstance(ch, dict):
                ch = {"value": ch}
            ids = ch.get("evidence_ids") or ([ch["evidence_id"]] if ch.get("evidence_id") else [])
            changes.append({"id": f"{row['organisation_number']}-c{j}",
                            "change": {k: v for k, v in ch.items() if k not in ("evidence_ids", "evidence_id")},
                            "sources": sources(ids)})
        if synthesis or changes:
            out[row["organisation_number"]] = {"synthesis": synthesis, "changes": changes}
    return out


def _load_contract(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    companies = convert(rows)
    extras = _extras(rows)
    for c in companies:
        c.update(extras.get(c["organisation_number"], {}))
    return companies


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    files = sorted((path / "companies").glob("*.json"))
    if not files:
        raise SystemExit(f"no companies/*.json under {path} (expected export_jbox_dataset.py --split output)")
    companies = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    companies.sort(key=lambda c: c["name"])
    return companies


SOURCE_KEYS = ["source_url", "source_name", "source_type", "retrieved_at", "content_sha256"]


class _SourceTable:
    """Deduplicate per-fact source records (most facts share the same registry file)."""

    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self.ix: dict[tuple[str, ...], int] = {}

    def add(self, rec: dict[str, Any]) -> int | None:
        key = tuple(str(rec.get(k) or "") for k in SOURCE_KEYS)
        if not any(key):
            return None
        if key not in self.ix:
            self.ix[key] = len(self.rows)
            self.rows.append(list(key))
        return self.ix[key]


def _company_file(c: dict[str, Any]) -> dict[str, Any]:
    st = _SourceTable()
    facts = []
    for f in c["facts"]:
        facts.append({"id": f["id"], "sec": f["section"], "label": f["label"], "field": f["field"],
                      "v": f["value"], "link": f.get("link") or "", "a": f["availability"],
                      "at": f.get("availability_text") or "", "p": f.get("period") or "",
                      "q": f.get("quote") or "", "s": st.add(f)})

    def pack(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        packed = []
        for it in items or []:
            srcs = it.get("sources") or []
            packed.append({**{k: v for k, v in it.items() if k != "sources"},
                           "q": [s.get("quote") or "" for s in srcs], "s": [st.add(s) for s in srcs]})
        return packed

    h = c.get("highlights") or {}
    return {
        "org": c["organisation_number"], "name": c["name"], "legal_form": c.get("legal_form") or "",
        "municipality": c.get("municipality") or "", "industry": c.get("industry") or "",
        "summary": c.get("summary") or "", "employees": c.get("employees") or "", "website": c.get("website") or "",
        "fact_count": c.get("fact_count", 0), "source_count": c.get("source_count", 0),
        "latest": h.get("latest_finances") or {}, "coverage": h.get("coverage") or {},
        "synthesis": pack(c.get("synthesis") or [], "synthesis"),
        "changes": pack(c.get("changes") or [], "changes"),
        "sources": st.rows, "facts": facts,
    }


def _index_row(c: dict[str, Any]) -> dict[str, Any]:
    h = c.get("highlights") or {}
    rev = (h.get("latest_finances") or {}).get("Revenue")
    cov = h.get("coverage") or {}
    return {"o": c["organisation_number"], "n": c["name"], "m": c.get("municipality") or "",
            "f": c.get("legal_form") or "", "i": c.get("industry") or "", "e": c.get("employees") or "",
            "w": c.get("website") or "", "fl": "".join(code for key, code in FLAG_KEYS if c.get(key)),
            "fc": c.get("fact_count", 0), "sc": c.get("source_count", 0),
            "r": [rev["value"], rev["year"]] if rev else None,
            "cv": [(cov.get(s) or {}).get("found", 0) if isinstance(cov.get(s), dict) else (cov.get(s) or 0)
                   for s in SECTIONS],
            "sy": 1 if c.get("synthesis") else 0, "ch": len(c.get("changes") or [])}


def _stats(companies: list[dict[str, Any]], origin: str) -> dict[str, Any]:
    facts = [f for c in companies for f in c["facts"]]
    dates = sorted({f["retrieved_at"][:10] for f in facts if f.get("retrieved_at")})
    return {
        "companies": len(companies), "facts": len(facts),
        "cited_facts_found": sum(c.get("fact_count", 0) for c in companies),
        "sources": len({f["source_url"] for f in facts if f.get("source_url")}),
        "retrieved_from": dates[0] if dates else "", "retrieved_to": dates[-1] if dates else "",
        "with_website": sum(bool(c.get("has_website")) for c in companies),
        "with_news": sum(bool(c.get("has_news")) for c in companies),
        "with_hiring": sum(bool(c.get("has_hiring")) for c in companies),
        "with_social": sum(bool(c.get("has_social")) for c in companies),
        "with_accounts": sum(bool(c.get("has_finances")) for c in companies),
        "with_synthesis": sum(bool(c.get("synthesis")) for c in companies),
        "with_changes": sum(bool(c.get("changes")) for c in companies),
        "municipalities": len({c.get("municipality") for c in companies if c.get("municipality")}),
        "sections": SECTIONS, "origin": origin,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def build(companies: list[dict[str, Any]], out: Path, origin: str) -> dict[str, Any]:
    data = out / "data"
    if data.exists():
        shutil.rmtree(data)
    (data / "c").mkdir(parents=True)
    for name in ("index.html", "assets"):
        src, dst = TEMPLATE / name, out / name
        if src.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    dump = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    for c in companies:
        (data / "c" / f"{c['organisation_number']}.json").write_text(dump(_company_file(c)), encoding="utf-8")
    (data / "index.json").write_text(dump([_index_row(c) for c in companies]), encoding="utf-8")
    stats = _stats(companies, origin)
    (data / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--contract", type=Path, help="agent output, e.g. out/submission-contract.jsonl")
    src.add_argument("--dataset", type=Path, help="export_jbox_dataset.py --split output dir, e.g. jbox-data-1000")
    ap.add_argument("--out", type=Path, default=Path("viewer"))
    args = ap.parse_args(argv)
    if args.contract:
        companies, origin = _load_contract(args.contract), f"contract:{args.contract.name}"
    else:
        companies, origin = _load_dataset(args.dataset), f"dataset:{args.dataset.name}"
    stats = build(companies, args.out, origin)
    size = sum(p.stat().st_size for p in (args.out / "data").rglob("*") if p.is_file())
    print(f"viewer: {stats['companies']} companies, {stats['facts']} facts -> {args.out}/ "
          f"(index {(args.out / 'data' / 'index.json').stat().st_size / 1024:.0f} KB, data {size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
