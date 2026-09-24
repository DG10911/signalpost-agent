"""Official NAV pam-stilling-feed job postings, exact-entity by organisation number.

Source: https://pam-stilling-feed.nav.no (NAV's official public job-vacancy feed,
permitted per the source policy as an official platform API). Each active ad
carries ``employer.orgnr``, so a job attaches to a company ONLY when
``requested_orgnr == employer.orgnr`` — never by name/fuzzy/parent matching.

The feed is a sequential firehose (no per-org query), so the durable
``orgnr -> [jobs]`` index is built OUTSIDE the 45-minute evaluation window by
``scripts/build_nav_index.py`` and looked up in O(1) at eval time. This module
holds the pure index/observation logic (no network) so it is fully testable and
deterministic; the network ingestion lives in the script.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UA = "builderr-signalpost-poc/0.3 (+https://builderr.ai)"
FEED_BASE = "https://pam-stilling-feed.nav.no"


def _norm_orgnr(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def parse_feed_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one NAV feed entry (detail form) into a compact job record.

    Returns None when the ad is not an active, org-bound posting (e.g. INACTIVE
    ads have their employer/orgnr stripped by NAV and must be treated as
    deletions, never published)."""
    detail = entry.get("_feed_entry") or entry
    status = str(detail.get("status") or "").upper()
    employer = detail.get("employer") or {}
    orgnr = _norm_orgnr(employer.get("orgnr") or detail.get("orgnr"))
    if status != "ACTIVE" or len(orgnr) != 9:
        return None
    locations = detail.get("workLocations") or detail.get("locationList") or []
    municipal = None
    if isinstance(locations, list) and locations:
        municipal = (locations[0] or {}).get("municipal") or (locations[0] or {}).get("city")
    municipal = municipal or detail.get("municipal")
    return {
        "job_id": str(entry.get("id") or detail.get("uuid")),
        "orgnr": orgnr,
        "employer_name": employer.get("name") or detail.get("businessName"),
        "title": detail.get("title") or entry.get("title"),
        "url": entry.get("url") or f"/api/v1/feedentry/{detail.get('uuid')}",
        "published": detail.get("published") or detail.get("publishedByAdmin"),
        "updated": detail.get("updated") or detail.get("sistEndret") or entry.get("date_modified"),
        "municipal": municipal,
        "content_sha256": hashlib.sha256(
            json.dumps(detail, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    }


def build_index(entries: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build a deterministic ``orgnr -> [job, ...]`` index.

    * INACTIVE / non-org entries are treated as deletions (their job_id is
      removed if present).
    * Re-seeing a job_id keeps the record with the latest ``updated`` timestamp
      (update detection), so a rebuilt index is idempotent.
    """
    by_org: dict[str, dict[str, dict[str, Any]]] = {}
    deletions: set[str] = set()
    for entry in entries:
        job = parse_feed_entry(entry)
        if job is None:
            detail = entry.get("_feed_entry") or entry
            jid = str(entry.get("id") or detail.get("uuid") or "")
            if jid:
                deletions.add(jid)
            continue
        jobs = by_org.setdefault(job["orgnr"], {})
        prev = jobs.get(job["job_id"])
        if prev is None or str(job.get("updated") or "") >= str(prev.get("updated") or ""):
            jobs[job["job_id"]] = job
        deletions.discard(job["job_id"])
    # Apply deletions (an INACTIVE seen after an ACTIVE removes the job).
    for org, jobs in by_org.items():
        for jid in list(jobs):
            if jid in deletions:
                del jobs[jid]
    return {
        org: sorted(jobs.values(), key=lambda j: (str(j.get("published") or ""), j["job_id"]))
        for org, jobs in by_org.items()
        if jobs
    }


def save_index(path: str | Path, index: dict[str, list[dict[str, Any]]]) -> None:
    payload = {"schema": "nav_jobs_index_v1", "orgnr_count": len(index), "index": index}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def load_index(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8")).get("index", {})


def jobs_for(index: dict[str, list[dict[str, Any]]], organisation_number: str) -> list[dict[str, Any]]:
    """Exact-orgnr lookup only. No name/fuzzy/parent matching."""
    return index.get(_norm_orgnr(organisation_number), [])


def _is_fresh(published: Any, as_of: datetime, freshness_days: int) -> bool:
    try:
        dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return 0 <= (as_of - dt).total_seconds() <= freshness_days * 86_400


def job_observation(organisation_number: str, job: dict[str, Any], *, retrieved_at: str) -> dict[str, Any]:
    """Build a publishable job_posting observation. Exact-entity by orgnr.

    ``effective_at`` is the ad's real publication date (never the retrieval
    time), so downstream freshness reflects the source, not the fetch.
    """
    org = _norm_orgnr(organisation_number)
    url = job["url"]
    if url.startswith("/"):
        url = FEED_BASE + url
    return {
        "id": f"navjob-{org}-{job['job_id']}",
        "organisation_number": org,
        "platform": "job_board",
        "signal_type": "job_posting",
        "source_url": url,
        "retrieved_at": retrieved_at,
        "effective_at": job.get("published") or job.get("updated"),
        "content_sha256": job["content_sha256"],
        "exact_entity": True,
        "identity_proof": [{"type": "nav_feed_employer_orgnr", "value": org, "employer_name": job.get("employer_name")}],
        "acquisition_mode": "official_api",
        "rights_status": "approved",
        "source_class": "official_job_board",
        "evidence_span": (job.get("title") or "")[:300] or None,
        "metrics": {"title": job.get("title"), "published": job.get("published"), "municipal": job.get("municipal")},
    }


def observations_for(index: dict[str, list[dict[str, Any]]], organisation_number: str, *, retrieved_at: str) -> list[dict[str, Any]]:
    return [job_observation(organisation_number, job, retrieved_at=retrieved_at)
            for job in jobs_for(index, organisation_number)]


def fresh_coverage(all_orgs: list[str], index: dict[str, list[dict[str, Any]]], *,
                   as_of: datetime | None = None, freshness_days: int = 45) -> float:
    """Fraction of orgs with at least one job published within the freshness
    window — the ``fresh_coverage`` ratio the competition scorer weights (×3)."""
    now = as_of or datetime.now(timezone.utc)
    if not all_orgs:
        return 0.0
    fresh = sum(
        any(_is_fresh(job.get("published"), now, freshness_days) for job in jobs_for(index, org))
        for org in all_orgs
    )
    return fresh / len(all_orgs)
