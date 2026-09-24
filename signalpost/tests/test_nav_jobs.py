"""NAV job index logic — orgnr matching, dedup, update/deletion detection,
freshness, cache determinism. Pure logic (no network), so fully reproducible."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent import nav_jobs  # noqa: E402
from norway_company_agent.external_footprint import publishable_observation  # noqa: E402


def _entry(uuid, orgnr, title="Role", status="ACTIVE", updated="2026-09-01T00:00:00+02:00",
           published="2026-09-01T00:00:00+02:00"):
    return {
        "id": uuid, "url": f"/api/v1/feedentry/{uuid}", "title": title,
        "date_modified": updated,
        "_feed_entry": {
            "uuid": uuid, "status": status, "title": title,
            "employer": {"name": "Example AS", "orgnr": orgnr},
            "published": published, "updated": updated,
            "workLocations": [{"municipal": "OSLO"}],
        },
    }


def test_orgnr_exact_match_only():
    idx = nav_jobs.build_index([_entry("j1", "923609016")])
    assert nav_jobs.jobs_for(idx, "923609016")            # exact match
    assert nav_jobs.jobs_for(idx, "999999999") == []      # different org -> nothing
    assert nav_jobs.jobs_for(idx, "923 609 016")          # normalises spaces


def test_inactive_ad_is_a_deletion_never_published():
    # An INACTIVE ad (orgnr stripped by NAV) must never become a job.
    idx = nav_jobs.build_index([_entry("j1", "923609016", status="INACTIVE")])
    assert idx == {}
    # ACTIVE then later INACTIVE -> removed.
    idx2 = nav_jobs.build_index([_entry("j1", "923609016"), _entry("j1", "923609016", status="INACTIVE")])
    assert nav_jobs.jobs_for(idx2, "923609016") == []


def test_update_detection_keeps_latest():
    idx = nav_jobs.build_index([
        _entry("j1", "923609016", title="Old", updated="2026-08-01T00:00:00+02:00"),
        _entry("j1", "923609016", title="New", updated="2026-09-15T00:00:00+02:00"),
    ])
    jobs = nav_jobs.jobs_for(idx, "923609016")
    assert len(jobs) == 1 and jobs[0]["title"] == "New"


def test_duplicate_jobs_collapsed():
    idx = nav_jobs.build_index([_entry("j1", "923609016"), _entry("j1", "923609016")])
    assert len(nav_jobs.jobs_for(idx, "923609016")) == 1


def test_cache_rebuild_is_deterministic(tmp_path):
    entries = [_entry("j2", "923609016"), _entry("j1", "923609016"), _entry("j3", "812345670")]
    a = nav_jobs.build_index(entries)
    b = nav_jobs.build_index(list(reversed(entries)))
    assert a == b  # order-independent
    p = tmp_path / "idx.json"
    nav_jobs.save_index(p, a)
    assert nav_jobs.load_index(p) == a


def test_observation_is_publishable_and_exact_entity():
    idx = nav_jobs.build_index([_entry("j1", "923609016")])
    obs = nav_jobs.observations_for(idx, "923609016", retrieved_at="2026-09-24T00:00:00Z")
    assert obs and publishable_observation(obs[0])
    o = obs[0]
    assert o["platform"] == "job_board" and o["signal_type"] == "job_posting"
    assert o["organisation_number"] == "923609016"
    assert o["acquisition_mode"] == "official_api"
    assert o["effective_at"] == "2026-09-01T00:00:00+02:00"   # publication date, not retrieval
    assert len(o["content_sha256"]) == 64


def test_freshness_uses_publication_date():
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    fresh_idx = nav_jobs.build_index([_entry("j1", "923609016", published="2026-09-10T00:00:00+02:00")])
    stale_idx = nav_jobs.build_index([_entry("j2", "812345670", published="2020-01-01T00:00:00+02:00")])
    assert nav_jobs.fresh_coverage(["923609016"], fresh_idx, as_of=now, freshness_days=45) == 1.0
    assert nav_jobs.fresh_coverage(["812345670"], stale_idx, as_of=now, freshness_days=45) == 0.0
