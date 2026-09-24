"""External observations must (a) only be emitted for exact-entity-verified
sites, (b) always pass the publication validator, and (c) never appear for a
non-publishable (unverified) website. Plus the official_identity_complete gate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.external_observations import build_observations  # noqa: E402
from norway_company_agent.external_footprint import publishable_observation  # noqa: E402
from norway_company_agent.batch import official_identity_complete, exact_registry_identity  # noqa: E402


def _verified_profile(org="923609016", publishable=True):
    return {
        "organisation_number": org,
        "evidence": {
            "website": {
                "status": "available",
                "retrieved_at": "2026-09-24T00:00:00Z",
                "content_sha256": "a" * 64,
                "value": {
                    "final_url": "https://example.no/",
                    "registered_domain": "example.no",
                    "title": "Example AS",
                    "identity_assessment": {"publishable": publishable, "status": "exact", "score": 1.0},
                    "social_links": [{"platform": "linkedin", "url": "https://linkedin.com/company/example-as"}],
                    "job_postings": [{"title": "Engineer", "url": "https://example.no/jobs/1", "date_posted": "2026-09-01"}],
                },
            }
        },
    }


def test_observations_are_publishable_and_exact_entity():
    obs = build_observations(_verified_profile())
    assert obs, "expected observations from a verified site"
    platforms = {o["platform"] for o in obs}
    signals = {o["signal_type"] for o in obs}
    assert "company_site" in platforms and "linkedin" in platforms      # >=2 platforms => breadth
    assert {"company_profile", "profile_handle", "job_posting"} <= signals
    for o in obs:
        assert publishable_observation(o), o
        assert o["exact_entity"] is True
        assert len(o["content_sha256"]) == 64
        assert o["rights_status"] == "approved"
        assert o["organisation_number"] == "923609016"


def test_no_observations_when_identity_not_verified():
    # A fetched-but-unverified site must publish NOTHING (never name-only).
    assert build_observations(_verified_profile(publishable=False)) == []


def test_no_observations_when_website_missing():
    assert build_observations({"organisation_number": "1", "evidence": {}}) == []


def test_official_identity_complete_detects_one_failure():
    good = {"organisation_number": "1", "evidence": {"registry_live": {"value": {"organisation_number": "1"}}}}
    bad = {"organisation_number": "2", "evidence": {"registry_live": {"value": {"organisation_number": "999"}}}}
    ratio, failures = official_identity_complete([good, good, bad])
    assert failures == ["2"]
    assert ratio < 1.0
    assert exact_registry_identity(good) and not exact_registry_identity(bad)


def test_official_identity_complete_all_pass():
    good = {"organisation_number": "1", "evidence": {"registry_live": {"value": {"organisation_number": "1"}}}}
    ratio, failures = official_identity_complete([good, good])
    assert ratio == 1.0 and failures == []
