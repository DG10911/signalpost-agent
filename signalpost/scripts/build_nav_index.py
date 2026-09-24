#!/usr/bin/env python3
"""Ingest NAV's official pam-stilling-feed into a durable orgnr -> jobs index.

Run this OUTSIDE the 45-minute evaluation window (the feed is a sequential
firehose with no per-org query). Evaluation-time lookup is then O(1) via
``nav_jobs.load_index`` / ``jobs_for``.

    # first build (traverse from the start, or resume from a saved cursor)
    python scripts/build_nav_index.py --output out/nav-index.json --max-pages 500
    # incremental refresh later (resumes from the saved next cursor)
    python scripts/build_nav_index.py --output out/nav-index.json --resume --max-pages 100

Reproducibility: the public token rotates, so the index is a dated snapshot.
Record the run date and the final cursor (written alongside the index) so a
reviewer can reproduce an equivalent snapshot. Terms of use:
https://arbeidsplassen.nav.no/vilkar-api (free; remove inactive ads — this
script does, via build_index deletion handling).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.nav_jobs import FEED_BASE, UA, build_index  # noqa: E402


def _get(url: str, token: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _public_token() -> str:
    req = urllib.request.Request(FEED_BASE + "/api/publicToken", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    for tok in text.split():
        if tok.startswith("eyJ"):
            return tok.strip()
    raise SystemExit("Could not parse NAV public token")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True)
    ap.add_argument("--cursor-file", help="Where to persist the feed cursor (default: <output>.cursor)")
    ap.add_argument("--max-pages", type=int, default=500)
    ap.add_argument("--resume", action="store_true", help="Resume from the saved cursor")
    ap.add_argument("--token", help="Bearer token (default: public rotating token)")
    ap.add_argument("--fetch-details", action="store_true",
                    help="Fetch each entry detail for employer.orgnr (required; list view omits orgnr)")
    args = ap.parse_args()

    token = args.token or _public_token()
    cursor_file = Path(args.cursor_file or (args.output + ".cursor"))
    url = FEED_BASE + "/api/v1/feed"
    if args.resume and cursor_file.exists():
        url = FEED_BASE + cursor_file.read_text(encoding="utf-8").strip()

    entries: list[dict] = []
    pages = 0
    while url and pages < args.max_pages:
        page = _get(url, token)
        items = page.get("items", [])
        for item in items:
            if args.fetch_details:
                detail_url = item.get("url")
                if detail_url:
                    try:
                        item = {**item, "_feed_entry": _get(FEED_BASE + detail_url if detail_url.startswith("/") else detail_url, token)}
                    except Exception:
                        pass
            entries.append(item)
        pages += 1
        nxt = page.get("next_url")
        if not nxt or not items:
            break
        url = FEED_BASE + nxt if nxt.startswith("/") else nxt

    index = build_index(entries)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    from norway_company_agent.nav_jobs import save_index
    save_index(args.output, index)
    if url:
        cursor_file.write_text(url.replace(FEED_BASE, ""), encoding="utf-8")
    print(json.dumps({"pages": pages, "entries": len(entries), "orgnr_indexed": len(index),
                      "output": args.output, "cursor": str(cursor_file)}, indent=2))


if __name__ == "__main__":
    main()
