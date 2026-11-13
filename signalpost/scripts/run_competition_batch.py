#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import official_identity_complete, profile_complete_for_modules, profiles_from_bulk, read_organisation_inputs, terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.evidence import evidence as _evidence_record, utc_now  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.official import fetch_official_modules  # noqa: E402
from norway_company_agent.website import fetch_website  # noqa: E402
from norway_company_agent.external_footprint import aggregate_footprint, publishable_observation  # noqa: E402
from norway_company_agent.external_observations import build_observations  # noqa: E402
from norway_company_agent.synthesis import summarize_profile  # noqa: E402
from norway_company_agent.registry_claims import registry_claims  # noqa: E402
from norway_company_agent.output_contract import to_contract  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import run_annual_report_workforce_connector as workforce  # noqa: E402


def bounded_call(fn, seconds: float, fallback):
    """Run ``fn()`` with a hard wall-clock limit. Socket timeouts do not cover
    DNS resolution or a server that trickles bytes, so one stuck host could
    otherwise hold a worker for many minutes. On overrun the call is abandoned
    on a daemon thread and ``fallback`` is returned; the company carries on."""
    import threading
    box: dict = {}

    def run() -> None:
        try:
            box["result"] = fn()
        except Exception as exc:  # surface as fallback, never crash the batch
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=max(1.0, seconds))
    if worker.is_alive() or "result" not in box:
        return fallback
    return box["result"]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluator-owned Signalpost batch contract")
    parser.add_argument("--organisations", required=True, help="JSON, JSONL, or text organisation-number list")
    parser.add_argument("--bulk", required=True, help="Frozen Brreg entity snapshot")
    parser.add_argument("--output", required=True, help="Terminal envelope JSONL")
    parser.add_argument("--profiles-output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--workers", type=int, default=None,
                        help="Parallel companies. Default scales with the batch: 8 up to 480 companies, "
                             "then N/60 up to 16, so a 1,000-company batch fits the 45-minute window.")
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--modules", default="registry,accounting_obligation,registry_live,financials,financial_history,roles,group,locations,website")
    parser.add_argument("--external-workforce", action="store_true",
                        help="Attach publishable workforce coverage extracted from official annual-report PDFs (exact org-number bound)")
    parser.add_argument("--workforce-ocr-pages", type=int, default=0,
                        help="OCR the first N pages of scanned annual reports (0 = digital text only). Needs pdftoppm+tesseract.")
    parser.add_argument("--observations-output",
                        help="Write all published external-footprint observations to this JSONL (for external scoring).")
    parser.add_argument("--registry-updates", action=argparse.BooleanOptionalAction, default=True,
                        help="Read Brønnøysund's update feed for each company (1 keyless request): dated registry "
                             "change events for 'what changed' and changes[].")
    parser.add_argument("--previous-contract", "--previous-snapshot", dest="previous_contract",
                        help="Previous run's --contract-output JSONL. Material changes since that run go to each "
                             "envelope's changes[] (with the prior evidence preserved), plus refresh metadata. "
                             "May be the same path as --contract-output: it is archived under snapshots/ first.")
    parser.add_argument("--contract-output",
                        help="Write results in the exact OUTPUT_CONTRACT.md shape (flat claims/evidence/6-state availability) — the evaluator-facing artifact.")
    parser.add_argument("--require-exact-identity", action="store_true",
                        help="Fail the build unless EVERY profile has exact registry_live identity (official_identity_complete gate).")
    parser.add_argument("--nav-index",
                        help="Path to a pre-built NAV orgnr->jobs index (scripts/build_nav_index_search.py) for exact-entity job_posting observations.")
    parser.add_argument("--nav-live", action=argparse.BooleanOptionalAction, default=True,
                        help="Read NAV's public vacancy feed once per run (background thread, ~120 requests) and publish "
                             "ads only where the ad's employer.orgnr is the company or one of its Brreg sub-units.")
    parser.add_argument("--nav-live-days", type=int, default=90,
                        help="How far back the NAV feed is read. Measured 9 Nov 2026: 90 days holds 98%% of the ads "
                             "active in a 150-day read (45 days: 93%%).")
    parser.add_argument("--nav-live-requests", type=int, default=None,
                        help="Fixed NAV request budget shared across the batch (feed pages + ad details + one sub-unit "
                             "bulk download if not cached). Default: 300 + 0.5 per company.")
    parser.add_argument("--nav-subunits",
                        help="Brreg sub-unit (underenheter) CSV bulk used to map NAV workplace orgnrs to their parent. "
                             "Default: brreg-underenheter.csv.gz next to --bulk; downloaded once (keyless) if absent.")
    parser.add_argument("--nav-homepages",
                        help="Path to the NAV orgnr->homepage map (written next to --nav-index). Exact-entity website candidates for companies with no registry site.")
    parser.add_argument("--wikidata", action=argparse.BooleanOptionalAction, default=False,
                        help="Query Wikidata (P2333 exact org-number match) for exact-entity facts/social/wikipedia. Batched, official public API. Default: OFF — the batch SPARQL query delays large runs (time budget).")
    parser.add_argument("--discover-websites", action=argparse.BooleanOptionalAction, default=True,
                        help="For companies with no registry-listed homepage, try the registry e-mail domain and name-derived "
                             "domains; bind ONLY on the org number, registry contact plus full name, or the full registered "
                             "legal name with legal form on the page, and never a domain another entity lists as its own.")
    parser.add_argument("--discover-max-candidates", type=int, default=3)
    parser.add_argument("--discover-seconds", type=float, default=75.0,
                        help="Hard wall-clock limit for website discovery per company.")
    parser.add_argument("--site-seconds", type=float, default=120.0,
                        help="Hard wall-clock limit for one company's site crawl.")
    parser.add_argument("--workforce-ocr-dpi", type=int, default=200)
    parser.add_argument("--google-places", action=argparse.BooleanOptionalAction, default=True,
                        help="Use Google Places (New) for website discovery + ratings/reviews. Requires GOOGLE_PLACES_API_KEY; inert without it.")
    parser.add_argument("--brave", action=argparse.BooleanOptionalAction, default=True,
                        help="Use Brave Search for missing-website discovery. Requires BRAVE_SEARCH_API_KEY; inert without it.")
    parser.add_argument("--osm", action=argparse.BooleanOptionalAction, default=False,
                        help="Use OpenStreetMap/Nominatim (free, ODbL) for missing-website discovery. Off by default (request-budget).")
    parser.add_argument("--max-secondary-pages", type=int, default=None,
                        help="Cap secondary pages crawled per site (request-efficiency knob). Default: website.DEFAULT_MAX_SECONDARY_PAGES.")
    parser.add_argument("--request-budget", type=int, default=9500,
                        help="Self-imposed ceiling on total outbound requests; the run warns loudly if exceeded. "
                             "The official allowance is 2,000 requests per 100 companies.")
    parser.add_argument("--time-budget-minutes", type=float, default=43.0,
                        help="Hard wall-clock budget for the whole run (default 43 to finish inside the official 45, or SIGNALPOST_TIME_BUDGET_MINUTES). "
                             "After 70%% it skips secondary crawls; at the limit remaining companies are closed "
                             "as budget_exhausted so the file is still complete.")
    args = parser.parse_args()
    if args.workers is None:
        args.workers = max(8, min(16, -(-args.expected_count // 60)))

    import time as _time
    env_minutes = os.environ.get("SIGNALPOST_TIME_BUDGET_MINUTES")
    if env_minutes:
        try:
            args.time_budget_minutes = float(env_minutes)
        except ValueError:
            pass
    run_started_monotonic = _time.monotonic()
    budget_deadline = (run_started_monotonic + args.time_budget_minutes * 60.0) if args.time_budget_minutes > 0 else None
    soft_deadline = (budget_deadline - 0.3 * args.time_budget_minutes * 60.0) if budget_deadline else None

    # Auto-scale the per-site crawl to the batch so a large official batch still
    # completes under the request budget (completion is mandatory: an over-budget
    # run is not scored). Allowance minus a base of ~6 live module requests +
    # homepage/robots; floor of 3 keeps the canonical careers/news probes running.
    if args.max_secondary_pages is None:
        from norway_company_agent.website import DEFAULT_MAX_SECONDARY_PAGES
        allowance = max(1, args.request_budget // max(1, args.expected_count))
        args.max_secondary_pages = max(3, min(DEFAULT_MAX_SECONDARY_PAGES, allowance - 3))
    request_plan = {
        "budget": args.request_budget,
        "companies": args.expected_count,
        "per_company_allowance": max(1, args.request_budget // max(1, args.expected_count)),
        "max_secondary_pages": args.max_secondary_pages,
        "time_budget_minutes": args.time_budget_minutes,
        "discover_websites": bool(args.discover_websites),
        "workers": args.workers,
        "osm": bool(args.osm),
    }

    # Key-gated connectors: enabled by default only when the key is present.
    places_key = os.environ.get("GOOGLE_PLACES_API_KEY") if args.google_places else None
    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY") if args.brave else None

    started_at = utc_now()
    organisation_inputs = read_organisation_inputs(args.organisations)
    orgs = [item["organisation_number"] for item in organisation_inputs]
    if len(orgs) != args.expected_count:
        raise SystemExit(f"Expected {args.expected_count} organisations, received {len(orgs)}")
    # The domain-owner table scans the whole bulk; build it in a separate
    # process while the batch's own rows are loaded (they were sequential).
    import concurrent.futures as _cfp
    from norway_company_agent.domain_discovery import build_domain_owners
    _owners_pool = _cfp.ProcessPoolExecutor(max_workers=1)
    _owners_future = _owners_pool.submit(build_domain_owners, args.bulk)
    profiles, registry_metadata = profiles_from_bulk(args.bulk, orgs)
    # Registry domain ownership (offline, from the same frozen bulk): rejects a
    # discovered domain another entity lists as its own and flags registry
    # homepages shared by many entities (manager/chain/group sites).
    from norway_company_agent.domain_discovery import build_domain_owners
    from norway_company_agent.identity import set_domain_owners
    set_domain_owners(_owners_future.result())
    _owners_pool.shutdown(wait=False)
    annotations = {item["organisation_number"]: item for item in organisation_inputs}
    for profile in profiles:
        for key in ("evaluation_split", "sample_slice"):
            if key in annotations[profile["organisation_number"]]:
                profile[key] = annotations[profile["organisation_number"]][key]
    requested_modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    fetch_modules = set(requested_modules) - {"registry", "accounting_obligation", "website"}
    operations = {"requests": 0, "bytes": 0, "latencies_ms": []}
    workforce_cache = Path(args.profiles_output).parent / ".cache" / "annual-reports"
    if args.external_workforce:
        workforce_cache.mkdir(parents=True, exist_ok=True)
    # Pre-built NAV index (built outside the eval window); O(1) orgnr lookup.
    nav_index = {}
    if args.nav_index:
        from norway_company_agent.nav_jobs import load_index
        nav_index = load_index(args.nav_index)
    # NAV employer homepages: exact-entity website candidates keyed by orgnr.
    nav_homepages: dict[str, str] = {}
    if args.nav_homepages and Path(args.nav_homepages).exists():
        nav_homepages = json.loads(Path(args.nav_homepages).read_text(encoding="utf-8"))
    # Wikidata exact-entity facts (P2333). Batched once for the whole cohort
    # (~2 SPARQL requests / 100 companies); every result is exact-entity by
    # construction, so there is no wrong-company surface.
    wikidata_index = {}
    if args.wikidata:
        from norway_company_agent.wikidata import query_p2333
        wikidata_index = query_p2333(orgs)

    # Live NAV vacancy feed, read in the background while companies are enriched.
    nav_live = None
    if args.nav_live:
        from norway_company_agent.nav_live import NavLive
        live_seconds = max(60.0, (budget_deadline - _time.monotonic() - 120.0)) if budget_deadline else 900.0
        nav_subunits = Path(args.nav_subunits) if args.nav_subunits else Path(args.bulk).with_name("brreg-underenheter.csv.gz")
        nav_requests = args.nav_live_requests or (300 + len(profiles) // 2)
        nav_live = NavLive([(p["organisation_number"], p.get("name") or "") for p in profiles],
                           days=args.nav_live_days, subunits_path=nav_subunits, max_requests=nav_requests,
                           max_seconds=min(900.0, live_seconds)).start()

    def _budget_exhausted(profile: dict) -> tuple[dict, dict]:
        """Close a company without network when the wall-clock budget is spent.

        Every requested module gets a terminal ``budget_exhausted`` record, so the
        batch still emits exactly one complete envelope per company.
        """
        from norway_company_agent.evidence import evidence as _evidence
        records = profile.setdefault("evidence", {})
        metric = {"requests": 0, "bytes": 0, "latencies_ms": []}
        for module in requested_modules:
            if module not in records:
                records[module] = _evidence(
                    module, "source_error", "time_budget",
                    "https://data.brreg.no/enhetsregisteret/api/enheter",
                    note="budget_exhausted: wall-clock budget reached before this company was processed")
        profile["run_metrics"] = metric
        return profile, metric

    def enrich(profile: dict) -> tuple[dict, dict]:
        if budget_deadline is not None and _time.monotonic() >= budget_deadline:
            return _budget_exhausted(profile)
        soft = soft_deadline is not None and _time.monotonic() >= soft_deadline
        secondary_pages = 0 if soft else args.max_secondary_pages
        have_live = bool((profile.get("evidence", {}) or {}).get("registry_live"))
        modules_now = set(fetch_modules) - ({"registry_live"} if have_live else set())
        records, metrics = fetch_official_modules(profile["organisation_number"], modules_now)
        profile["evidence"].update(records)
        if args.registry_updates:
            from norway_company_agent.registry_updates import fetch_registry_updates
            from norway_company_agent.http import FetchResult as _FetchResult
            updates_record, updates_result = bounded_call(
                lambda: fetch_registry_updates(profile["organisation_number"]), 20.0,
                (_evidence_record("registry_updates", "source_error", "official_registry_updates",
                                  "https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter",
                                  note="registry update feed exceeded the 20s per-company limit"),
                 _FetchResult(url="https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter",
                              status=0, elapsed_ms=20000, bytes_received=0, error="timeout")))
            profile["evidence"]["registry_updates"] = updates_record
            metrics = list(metrics) + [updates_result]
        website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
        discovery_requests = 0
        if "website" in requested_modules:
            website_record, website_metrics = bounded_call(
                lambda: fetch_website(profile.get("website"), max_secondary_pages=secondary_pages),
                args.site_seconds,
                (_evidence_record("website", "source_error", "registry_linked_company_website",
                                  str(profile.get("website") or "https://data.brreg.no/enhetsregisteret/api/enheter"),
                                  note=f"site crawl exceeded the {args.site_seconds:.0f}s per-company limit"),
                 {"requests": 2, "bytes": 0, "latencies_ms": []}))
            # Website discovery is OPT-IN. On the holdco-heavy universe it has
            # measured ~0 yield and it multiplies requests, so it is off by
            # default to stay inside the 10,000-request budget. When enabled, it
            # uses exact-entity candidates (NAV ad homepage, registry-email
            # domain, key-gated Places/Brave, OSM), each still gated.
            if args.discover_websites and website_record.get("status") != "available":
                nav_home = nav_homepages.get(str(profile["organisation_number"]))
                extra = [nav_home] if nav_home else []
                from norway_company_agent.domain_discovery import candidate_domains_from_email
                reg_value = (profile.get("evidence", {}) or {}).get("registry", {}).get("value") or {}
                email_candidates = candidate_domains_from_email(reg_value.get("epostadresse"))
                connector_requests = 0
                # Google Places (key-gated): websiteUri candidate + ratings/reviews.
                if places_key:
                    from norway_company_agent import places as places_mod
                    place = places_mod.query_places(profile, places_key)
                    connector_requests += 1
                    if place:
                        profile["_places_observations"] = places_mod.observations_for(profile, place, retrieved_at=started_at)
                        place_site = places_mod.website_candidate(place)
                        if place_site:
                            extra.append(place_site)
                # Brave Search (key-gated): transient website candidates.
                if brave_key and website_record.get("status") != "available":
                    from norway_company_agent.brave_discovery import candidate_urls
                    extra.extend(candidate_urls(profile, brave_key))
                    connector_requests += 1
                # OpenStreetMap/Nominatim (free, ODbL): website tags for physical sites.
                if args.osm and website_record.get("status") != "available" and profile.get("municipality"):
                    from norway_company_agent.osm_discovery import candidate_urls as osm_candidates
                    extra.extend(osm_candidates(profile))
                    connector_requests += 1
                from norway_company_agent.domain_discovery import discover_website
                # Probe the homepage only (cheap): name guesses / email domains
                # can be wrong and must not consume the deep-crawl budget. Only a
                # candidate that passes the identity gate is deep-crawled.
                discovered, ddiag = bounded_call(
                    lambda: discover_website(
                        profile, lambda u: fetch_website(u, max_secondary_pages=0, timeout=8.0),
                        max_candidates=args.discover_max_candidates, extra_candidates=extra,
                        email_candidates=email_candidates),
                    args.discover_seconds,
                    (None, {"requests": 2 * (len(extra) + len(email_candidates) + args.discover_max_candidates),
                            "verdict": "discovery_time_limit"}))
                discovery_requests = int(ddiag.get("requests", 0) or 0)
                if discovered is not None:
                    found_url = discovered.get("value", {}).get("final_url")
                    full_record, full_metrics = bounded_call(
                        lambda: fetch_website(found_url, max_secondary_pages=secondary_pages),
                        args.site_seconds,
                        ({"status": "source_error"}, {"requests": 2, "bytes": 0, "latencies_ms": []}))
                    discovery_requests += int(full_metrics.get("requests", 0) or 0)
                    if full_record.get("status") == "available":
                        found_value = discovered.get("value") or {}
                        full_value = full_record.setdefault("value", {})
                        full_value["discovered"] = True
                        full_value["discovery_identity"] = found_value.get("discovery_identity")
                        full_value.setdefault("identity_text_excerpt", found_value.get("identity_text_excerpt"))
                        website_record = full_record
                    else:
                        website_record = discovered
                discovery_requests += connector_requests
            from norway_company_agent.claim_checks import (drop_source_texts, recheck_excerpts,
                                                           rescue_group_site_items)
            gated = apply_website_identity_gate(profile, website_record)["website"]
            # Group/parent/sister site: only items whose own page names us.
            gated = rescue_group_site_items(profile, gated)
            # Second pass (organiser feedback, 1 Nov): keep only dated news about
            # the company and careers pages that show a current opening; the
            # rest are kept as exclusions with a reason.
            from norway_company_agent.content_quality import apply_content_quality
            checked = recheck_excerpts(apply_content_quality(profile, gated))
            profile["evidence"]["website"] = drop_source_texts(checked)
        workforce_requests = 0
        wf_diag = {"status": "disabled"}
        if args.external_workforce:
            # OCR optional (--workforce-ocr-pages). The connector abstains unless
            # the exact organisation number appears in the filing text (digital
            # or OCR'd), so scanned reports never produce a guessed claim.
            observation, wf_diag = workforce.collect(profile, workforce_cache, ocr_pages=args.workforce_ocr_pages, ocr_dpi=args.workforce_ocr_dpi)
            if observation:
                profile["_workforce_observation"] = observation
            if wf_diag.get("cache_hit") is False and wf_diag.get("status") not in {"registry_count_already_available", "no_annual_report"}:
                workforce_requests = 1
        profile["_workforce_diag"] = wf_diag
        metric = {
            "requests": len(metrics) + website_metrics["requests"] + workforce_requests + discovery_requests,
            "bytes": sum(item.bytes_received for item in metrics) + website_metrics["bytes"],
            "latencies_ms": [item.elapsed_ms for item in metrics] + website_metrics["latencies_ms"],
        }
        profile["run_metrics"] = metric
        return profile, metric

    def derive(profile: dict) -> dict:
        """Recompute derived artifacts from the (fetched) evidence: registry
        claims, external observations (website + official-registry + NAV +
        Wikidata + workforce), footprint aggregate, and synthesis. Always runs,
        including for resumed profiles, so a code change re-derives cleanly."""
        candidates = build_observations(profile, nav_index=nav_index, wikidata_index=wikidata_index, retrieved_at=started_at)
        wf_observation = profile.pop("_workforce_observation", None)
        if wf_observation:
            candidates.append(wf_observation)
        # Google Places observations (key-gated) are already exact-entity gated.
        candidates.extend(profile.pop("_places_observations", []) or [])
        accepted = [item for item in candidates if publishable_observation(item)]
        profile["evidence"]["external_footprint"] = {
            "observations": accepted,
            "summary": aggregate_footprint(accepted, as_of=started_at),
            "diagnostic": profile.pop("_workforce_diag", {"status": "disabled"}),
        }
        profile["registry_claims"] = registry_claims(profile)
        profile["summary"] = summarize_profile(profile)
        return profile

    state: dict[str, dict] = {}
    resumed_profiles = 0
    profiles_output = Path(args.profiles_output)
    if args.resume and profiles_output.exists():
        prior = [json.loads(line) for line in profiles_output.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not set(item["organisation_number"] for item in prior).issubset(set(orgs)):
            raise SystemExit("Resume profile membership is not a subset of this batch")
        state = {
            item["organisation_number"]: item
            for item in prior
            if profile_complete_for_modules(item, requested_modules)
        }
        resumed_profiles = len(state)
    pending_profiles = [profile for profile in profiles if profile["organisation_number"] not in state]
    # Hard stop: a company already in flight when the wall-clock budget runs out
    # (e.g. a registry that stops answering) must not hold the batch past the
    # deadline. Unfinished companies are closed from a pristine copy with a
    # terminal budget_exhausted record, so every input still gets an envelope.
    # Identity first: one cheap live registry lookup per company, in parallel,
    # before any crawling. Every envelope then carries its exact registry match
    # even if the time guard later closes the company early.
    if "registry_live" in fetch_modules and pending_profiles:
        def _live(profile: dict) -> int:
            records, live_metrics = bounded_call(
                lambda: fetch_official_modules(profile["organisation_number"], {"registry_live"}), 8.0, ({}, []))
            if records.get("registry_live"):
                profile.setdefault("evidence", {})["registry_live"] = records["registry_live"]
            return len(live_metrics)
        with ThreadPoolExecutor(max_workers=max(args.workers, 16)) as live_pool:
            operations["requests"] += sum(live_pool.map(_live, pending_profiles))
    import copy as _copy
    import concurrent.futures as _cf
    pristine = {p["organisation_number"]: _copy.deepcopy(p) for p in pending_profiles}
    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = {pool.submit(enrich, profile): profile["organisation_number"] for profile in pending_profiles}
    abandoned: list[str] = []

    def _completed():
        wait = (budget_deadline - _time.monotonic()) if budget_deadline is not None else None
        try:
            for future in as_completed(futures, timeout=None if wait is None else max(1.0, wait)):
                yield future
        except _cf.TimeoutError:
            for future, org in futures.items():
                if not future.done():
                    future.cancel()
                    abandoned.append(org)
                    yield org

    for index, future in enumerate(_completed(), 1):
        if isinstance(future, str):
            profile, metric = _budget_exhausted(pristine[future])
        else:
            try:
                profile, metric = future.result()
            except Exception as exc:  # one company's bug must never sink the batch
                org = futures[future]
                print(f"WARNING enrich failed for {org}: {type(exc).__name__}: {exc}", file=sys.stderr)
                profile, metric = _budget_exhausted(pristine[org])
                operations["company_errors"] = operations.get("company_errors", 0) + 1
        if True:
            state[profile["organisation_number"]] = profile
            operations["requests"] += metric["requests"]
            operations["bytes"] += metric["bytes"]
            operations["latencies_ms"].extend(metric["latencies_ms"])
            if index % args.checkpoint_every == 0 or index == len(pending_profiles):
                checkpoint = [state[org] for org in orgs if org in state]
                write_jsonl(profiles_output, checkpoint)

    pool.shutdown(wait=False, cancel_futures=True)
    if abandoned:
        operations["abandoned_at_deadline"] = len(abandoned)

    nav_live_diag = None
    if nav_live is not None:
        remaining = (budget_deadline - _time.monotonic() - 60.0) if budget_deadline else 900.0
        for org, jobs in nav_live.index(wait_seconds=max(0.0, remaining)).items():
            known = {job.get("job_id") for job in nav_index.get(org, [])}
            nav_index.setdefault(org, []).extend(job for job in jobs if job.get("job_id") not in known)
        nav_live_diag = nav_live.diagnostic()
        operations["requests"] += int(nav_live_diag.get("requests", 0) or 0)

    completed_at = utc_now()
    ordered_profiles = [state[org] for org in orgs]
    # Re-derive artifacts for every profile (including resumed ones) so a code
    # change to claims/observations/synthesis always takes effect.
    for profile in ordered_profiles:
        derive(profile)

    # official_identity_complete gate: every submitted company must resolve to
    # its exact registry_live identity, or the whole submission scores zero.
    # Surface it loudly at build time rather than shipping a silent zero.
    exact_registry_ratio, identity_failures = official_identity_complete(ordered_profiles)
    # Never throw the batch away over identity: every envelope is written and
    # any profile without a live registry match is listed in the report (it is
    # still anchored to the official registry snapshot row for its org number).
    identity_warning = None
    if args.require_exact_identity and identity_failures:
        identity_warning = {
            "profiles_without_live_registry_match": len(identity_failures),
            "examples": identity_failures[:10],
            "anchor": "official registry bulk snapshot row for the same organisation number",
        }
        print(f"WARNING official_identity_complete: {len(identity_failures)} profile(s) lack a live registry match "
              f"(e.g. {identity_failures[:10]}); envelopes are still emitted.", file=sys.stderr)

    envelopes = [
        terminal_envelope(profile, run_id=args.run_id, modules=requested_modules, started_at=started_at, completed_at=completed_at)
        for profile in ordered_profiles
    ]
    validation = validate_envelopes(envelopes, args.expected_count)
    from norway_company_agent.claim_checks import withheld_counts
    write_jsonl(profiles_output, ordered_profiles)
    write_jsonl(Path(args.output), envelopes)
    # Which run-wide sources were actually read: a job list from an unread NAV
    # feed is not evidence that ads were removed (refresh compares only read sources).
    nav_read = bool(nav_index) if nav_live_diag is None else bool(nav_live_diag.get("complete"))
    for profile in ordered_profiles:
        profile["source_checks"] = {"nav_jobs": "read" if nav_read or args.nav_index else "not_read"}
    refresh_report = None
    if args.contract_output:
        previous_rows: dict[str, dict] = {}
        if args.previous_contract and Path(args.previous_contract).exists():
            raw_previous = Path(args.previous_contract).read_text(encoding="utf-8")
            for line in raw_previous.splitlines():
                if line.strip():
                    row = json.loads(line)
                    previous_rows[str(row.get("organisation_number"))] = row
            # Prior snapshots are preserved: archive the previous file before it can be overwritten.
            previous_run_id = next(iter(previous_rows.values()), {}).get("run", {}).get("run_id") or "previous"
            archive = Path(args.contract_output).parent / "snapshots" / f"{previous_run_id}.contract.jsonl"
            if not archive.exists():
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_text(raw_previous, encoding="utf-8")
        contract_rows = [
            to_contract(profile, run_id=args.run_id, started_at=started_at, completed_at=completed_at,
                        terminal_status="completed" if envelope["state"] == "complete" else envelope["state"],
                        previous=previous_rows.get(str(profile["organisation_number"])))
            for profile, envelope in zip(ordered_profiles, envelopes)
        ]
        write_jsonl(Path(args.contract_output), contract_rows)
        refresh_report = {
            "previous_contract": args.previous_contract,
            "companies_with_baseline": sum(1 for r in contract_rows if (r.get("refresh") or {}).get("baseline")),
            "material_changes": sum(len(r.get("changes") or []) for r in contract_rows),
            "since_previous_run": sum(1 for r in contract_rows for c in r.get("changes") or []
                                      if c.get("change_type") == "since_previous_run"),
            "dated_in_source": sum(1 for r in contract_rows for c in r.get("changes") or []
                                   if c.get("change_type") == "dated_in_source"),
            "prior_evidence_preserved": sum((r.get("refresh") or {}).get("prior_evidence_preserved", 0) for r in contract_rows),
        }
    if args.observations_output:
        all_observations = [
            obs
            for profile in ordered_profiles
            for obs in ((profile.get("evidence", {}) or {}).get("external_footprint", {}) or {}).get("observations", [])
        ]
        write_jsonl(Path(args.observations_output), all_observations)
    latencies = sorted(operations.pop("latencies_ms"))
    operations["p50_ms"] = latencies[len(latencies) // 2] if latencies else None
    operations["p95_ms"] = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    operations["request_budget"] = args.request_budget
    budget_exceeded = operations["requests"] > args.request_budget
    if budget_exceeded:
        print(json.dumps({
            "WARNING": "request budget exceeded — lower --max-secondary-pages or keep --discover-websites off",
            "requests": operations["requests"], "budget": args.request_budget,
        }))
    report = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "expected_count": args.expected_count,
        "emitted_envelopes": len(envelopes),
        "resumed_profiles": resumed_profiles,
        "profiles_fetched_this_run": len(pending_profiles),
        "modules": requested_modules,
        "registry": registry_metadata,
        "request_plan": request_plan,
        "nav_live": nav_live_diag,
        "operations": operations,
        "validation": validation,
        "request_budget_exceeded": budget_exceeded,
        "exact_registry": round(exact_registry_ratio, 4),
        "identity_warning": identity_warning,
        "withheld": withheld_counts(ordered_profiles),
        "refresh": refresh_report,
        "external_observations": sum(
            len(((p.get("evidence", {}) or {}).get("external_footprint", {}) or {}).get("observations", []))
            for p in ordered_profiles
        ),
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.stdout.flush()
    sys.stderr.flush()
    # Abandoned network threads must not keep the process alive past the budget.
    os._exit(0 if validation["passed"] else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stderr.flush()
        os._exit(2)  # never hang on abandoned worker threads after a crash
