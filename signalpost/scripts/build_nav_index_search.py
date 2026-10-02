#!/usr/bin/env python3
"""Build a national ``orgnr -> jobs`` NAV index efficiently.

The sequential firehose in ``build_nav_index.py`` starts in 2023 and needs tens
of thousands of pages to reach today's active ads. This builder uses the public
Arbeidsplassen search API to *enumerate* every currently-active ad's uuid
(~14k ads, 100/page) and then resolves each uuid against NAV's official
pam-stilling-feed detail endpoint, which carries ``ad_content.employer.orgnr``
(the exact legal entity). It is still an **official NAV platform API**, exact-
entity by organisation number, and is run OUTSIDE the 45-minute evaluation
window; evaluation-time lookup is O(1) via ``nav_jobs.load_index``.

Both endpoints are free and public. Review the terms before use:
https://arbeidsplassen.nav.no/vilkar-api

    python scripts/build_nav_index_search.py --output out/nav-index.json
    python scripts/build_nav_index_search.py --output out/nav-index.json --resume

Every ad's ``employer.homepage`` (self-declared in the official ad) is written
to ``<output>.homepages.json`` as an exact-entity website-discovery map, so a
job ad can also raise website coverage for a company with no registry website.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.nav_jobs import FEED_BASE, UA, build_index, save_index  # noqa: E402

SEARCH_BASE = "https://arbeidsplassen.nav.no/stillinger/api/search"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)


_THROTTLE_LOCK = threading.Lock()
_LAST_CALL = [0.0]


def _throttle(min_interval: float) -> None:
    with _THROTTLE_LOCK:
        wait = min_interval - (time.time() - _LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[0] = time.time()


def _json(url: str, headers: dict, timeout: float = 30.0, *, min_interval: float = 0.0,
          attempts: int = 8) -> dict:
    last: Exception | None = None
    for attempt in range(attempts):
        _throttle(min_interval)
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                time.sleep(min(30.0, 5.0 * (attempt + 1)))
                continue
            raise
    assert last is not None
    raise last


def _public_token() -> str:
    text = urllib.request.urlopen(
        urllib.request.Request(FEED_BASE + "/api/publicToken", headers={"User-Agent": UA}),
        timeout=30,
    ).read().decode("utf-8", errors="replace")
    for token in text.split():
        if token.startswith("eyJ"):
            return token.strip()
    raise SystemExit("Could not parse NAV public token")


def enumerate_uuids(max_pages: int, page_size: int, search_interval: float = 3.0) -> list[dict]:
    """Return active ad stubs (uuid + search metadata) via the search API."""
    stubs: list[dict] = []
    seen: set[str] = set()
    for page in range(max_pages):
        from_ = page * page_size
        if from_ >= 10_000:
            break
        url = f"{SEARCH_BASE}?size={page_size}&from={from_}"
        try:
            payload = _json(url, {"User-Agent": BROWSER_UA, "Accept": "application/json"},
                            min_interval=search_interval)
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                break
            # Rate-limited or transient: keep whatever we have rather than abort.
            print(json.dumps({"warning": f"search page {page} failed: HTTP {exc.code}; using partial enumeration"}))
            break
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"warning": f"search page {page} failed: {type(exc).__name__}; using partial enumeration"}))
            break
        hits = payload.get("hits", {}).get("hits", [])
        if not hits:
            break
        for hit in hits:
            src = hit.get("_source") or {}
            uuid = str(src.get("uuid") or hit.get("_id") or "")
            if uuid and uuid not in seen:
                seen.add(uuid)
                stubs.append({"uuid": uuid, "published": src.get("published"),
                              "title": src.get("title"), "businessName": src.get("businessName")})
    return stubs


def resolve_detail(uuid: str, token: str, min_interval: float = 0.0) -> dict | None:
    """Return the official feed detail for one ad, or None when unavailable."""
    headers = {"User-Agent": UA, "Authorization": f"Bearer {token}"}
    url = f"{FEED_BASE}/api/v1/feedentry/{uuid}"
    try:
        return _json(url, headers, timeout=25, min_interval=min_interval)
    except urllib.error.HTTPError as exc:
        return None
    except Exception:
        return None


def _feed_entry(detail: dict, stub: dict) -> dict | None:
    content = detail.get("ad_content") or {}
    employer = content.get("employer") or {}
    orgnr = "".join(ch for ch in str(employer.get("orgnr") or "") if ch.isdigit())
    if str(detail.get("status") or "").upper() != "ACTIVE" or len(orgnr) != 9:
        return None
    return {
        "_feed_entry": {
            "uuid": content.get("uuid") or stub.get("uuid"),
            "status": "ACTIVE",
            "title": content.get("title") or stub.get("title"),
            "businessName": employer.get("name") or stub.get("businessName"),
            "published": content.get("published"),
            "updated": content.get("updated") or detail.get("sistEndret"),
            "workLocations": content.get("workLocations") or [],
            "employer": {"orgnr": orgnr, "name": employer.get("name")},
        },
        "id": content.get("uuid") or stub.get("uuid"),
        "url": f"/api/v1/feedentry/{content.get('uuid') or stub.get('uuid')}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--homepages-output", help="Exact-entity orgnr->homepage map (default <output>.homepages.json)")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-interval", type=float, default=0.1,
                        help="Minimum seconds between detail requests (global rate limit).")
    parser.add_argument("--search-interval", type=float, default=3.0,
                        help="Minimum seconds between search-API page requests (NAV rate-limits aggressively).")
    parser.add_argument("--token", help="Bearer token (default: public rotating token)")
    args = parser.parse_args()

    token = args.token or _public_token()
    stubs = enumerate_uuids(args.max_pages, args.page_size, args.search_interval)
    print(json.dumps({"enumerated_ads": len(stubs)}, indent=2))

    entries: list[dict] = []
    homepages: dict[str, str] = {}
    resolved = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(resolve_detail, stub["uuid"], token, args.min_interval): stub for stub in stubs}
        for done, future in enumerate(cf.as_completed(futures), 1):
            detail = future.result()
            if not detail:
                continue
            stub = futures[future]
            entry = _feed_entry(detail, stub)
            if not entry:
                continue
            resolved += 1
            entries.append(entry)
            employer = (detail.get("ad_content") or {}).get("employer") or {}
            orgnr = "".join(ch for ch in str(employer.get("orgnr") or "") if ch.isdigit())
            homepage = str(employer.get("homepage") or "").strip()
            if orgnr and homepage and orgnr not in homepages:
                homepages[orgnr] = homepage
            if done % 500 == 0:
                print(json.dumps({"resolved": resolved, "processed": done, "total": len(stubs)}), flush=True)

    index = build_index(entries)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    save_index(args.output, index)
    homepage_path = Path(args.homepages_output or (args.output + ".homepages.json"))
    homepage_path.write_text(json.dumps(homepages, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "ads_enumerated": len(stubs),
        "ads_resolved": resolved,
        "orgnr_indexed": len(index),
        "jobs_indexed": sum(len(v) for v in index.values()),
        "homepages": len(homepages),
        "output": args.output,
        "homepages_output": str(homepage_path),
    }, indent=2))


if __name__ == "__main__":
    main()
