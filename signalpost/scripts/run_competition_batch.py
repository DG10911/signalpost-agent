#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import official_identity_complete, profile_complete_for_modules, profiles_from_bulk, read_organisation_inputs, terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.evidence import utc_now  # noqa: E402
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
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--modules", default="registry,accounting_obligation,registry_live,financials,financial_history,roles,group,locations,website")
    parser.add_argument("--external-workforce", action="store_true",
                        help="Attach publishable workforce coverage extracted from official annual-report PDFs (exact org-number bound)")
    parser.add_argument("--workforce-ocr-pages", type=int, default=0,
                        help="OCR the first N pages of scanned annual reports (0 = digital text only). Needs pdftoppm+tesseract.")
    parser.add_argument("--observations-output",
                        help="Write all published external-footprint observations to this JSONL (for external scoring).")
    parser.add_argument("--contract-output",
                        help="Write results in the exact OUTPUT_CONTRACT.md shape (flat claims/evidence/6-state availability) — the evaluator-facing artifact.")
    parser.add_argument("--require-exact-identity", action="store_true",
                        help="Fail the build unless EVERY profile has exact registry_live identity (official_identity_complete gate).")
    parser.add_argument("--nav-index",
                        help="Path to a pre-built NAV orgnr->jobs index (scripts/build_nav_index.py) for exact-entity job_posting observations.")
    parser.add_argument("--wikidata", action=argparse.BooleanOptionalAction, default=True,
                        help="Query Wikidata (P2333 exact org-number match) for exact-entity facts/social/wikipedia. Batched, official public API. Default: on.")
    parser.add_argument("--discover-websites", action=argparse.BooleanOptionalAction, default=False,
                        help="For companies with no registry-listed homepage, try deterministic domain guesses and accept ONLY on exact org-number / registry-contact presence. Default: off (measured ~0 yield on the holdco universe; opt in to protect the request/runtime budget).")
    parser.add_argument("--discover-max-candidates", type=int, default=3)
    parser.add_argument("--workforce-ocr-dpi", type=int, default=200)
    args = parser.parse_args()

    started_at = utc_now()
    organisation_inputs = read_organisation_inputs(args.organisations)
    orgs = [item["organisation_number"] for item in organisation_inputs]
    if len(orgs) != args.expected_count:
        raise SystemExit(f"Expected {args.expected_count} organisations, received {len(orgs)}")
    profiles, registry_metadata = profiles_from_bulk(args.bulk, orgs)
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
    # Wikidata exact-entity facts (P2333). Batched once for the whole cohort
    # (~2 SPARQL requests / 100 companies); every result is exact-entity by
    # construction, so there is no wrong-company surface.
    wikidata_index = {}
    if args.wikidata:
        from norway_company_agent.wikidata import query_p2333
        wikidata_index = query_p2333(orgs)

    def enrich(profile: dict) -> tuple[dict, dict]:
        records, metrics = fetch_official_modules(profile["organisation_number"], fetch_modules)
        profile["evidence"].update(records)
        website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
        discovery_requests = 0
        if "website" in requested_modules:
            website_record, website_metrics = fetch_website(profile.get("website"))
            # For a site-less company, try safe domain discovery (org-number-only gate).
            if args.discover_websites and website_record.get("status") != "available":
                from norway_company_agent.domain_discovery import discover_website
                discovered, ddiag = discover_website(
                    profile, lambda u: fetch_website(u), max_candidates=args.discover_max_candidates)
                discovery_requests = int(ddiag.get("requests", 0) or 0)
                if discovered is not None:
                    website_record = discovered
            profile["evidence"]["website"] = apply_website_identity_gate(profile, website_record)["website"]
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
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(enrich, profile): profile["organisation_number"] for profile in pending_profiles}
        for index, future in enumerate(as_completed(futures), 1):
            profile, metric = future.result()
            state[profile["organisation_number"]] = profile
            operations["requests"] += metric["requests"]
            operations["bytes"] += metric["bytes"]
            operations["latencies_ms"].extend(metric["latencies_ms"])
            if index % args.checkpoint_every == 0 or index == len(pending_profiles):
                checkpoint = [state[org] for org in orgs if org in state]
                write_jsonl(profiles_output, checkpoint)

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
    if args.require_exact_identity and identity_failures:
        raise SystemExit(
            f"official_identity_complete FAILED: {len(identity_failures)} profile(s) lack exact registry_live identity "
            f"(e.g. {identity_failures[:10]}). Refusing to emit a submission that would score zero awardable."
        )

    envelopes = [
        terminal_envelope(profile, run_id=args.run_id, modules=requested_modules, started_at=started_at, completed_at=completed_at)
        for profile in ordered_profiles
    ]
    validation = validate_envelopes(envelopes, args.expected_count)
    write_jsonl(profiles_output, ordered_profiles)
    write_jsonl(Path(args.output), envelopes)
    if args.contract_output:
        contract_rows = [
            to_contract(profile, run_id=args.run_id, started_at=started_at, completed_at=completed_at,
                        terminal_status="completed" if envelope["state"] == "complete" else envelope["state"])
            for profile, envelope in zip(ordered_profiles, envelopes)
        ]
        write_jsonl(Path(args.contract_output), contract_rows)
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
        "operations": operations,
        "validation": validation,
        "exact_registry": round(exact_registry_ratio, 4),
        "external_observations": sum(
            len(((p.get("evidence", {}) or {}).get("external_footprint", {}) or {}).get("observations", []))
            for p in ordered_profiles
        ),
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if validation["passed"] else 1)


if __name__ == "__main__":
    main()
