"""Key-gated connector safety: Google Places identity gate and Brave candidate
filtering. No network — the HTTP layer is monkeypatched."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent import brave_discovery, places  # noqa: E402
from norway_company_agent.external_footprint import publishable_observation  # noqa: E402


def _profile():
    return {"organisation_number": "811943622", "name": "G3 Gausdal Treindustrier SA", "municipality": "GAUSDAL"}


def test_place_identity_exact_and_rejections():
    p = _profile()
    good = {"id": "x", "displayName": {"text": "G3 Gausdal Treindustrier"},
            "formattedAddress": "Jorevegen 1, 2651 Gausdal, Norge", "rating": 4.2, "userRatingCount": 17}
    assert places.place_identity(p, good) == "exact"
    assert places.place_identity(p, {"displayName": {"text": "Gausdal Bil"}, "formattedAddress": "Gausdal"}) is None
    assert places.place_identity(p, {"displayName": {"text": "G3 Gausdal Treindustrier"}, "formattedAddress": "Oslo, Norge"}) is None


def test_places_observations_publishable():
    p = _profile()
    place = {"id": "abc", "displayName": {"text": "G3 Gausdal Treindustrier"},
             "formattedAddress": "Jorevegen 1, 2651 Gausdal, Norge", "rating": 4.2, "userRatingCount": 17,
             "websiteUri": "https://g3i.no/", "primaryType": "manufacturer"}
    obs = places.observations_for(p, place, retrieved_at="2026-10-05T00:00:00Z")
    assert {o["signal_type"] for o in obs} == {"place_summary", "review_summary"}
    assert all(publishable_observation(o) for o in obs)
    assert places.website_candidate(place) == "https://g3i.no/"


def test_brave_filters_social_and_directory_hosts(monkeypatch):
    monkeypatch.setattr(brave_discovery, "search_web",
                        lambda *a, **k: ["https://linkedin.com/company/x", "https://proff.no/x",
                                         "https://g3i.no/", "https://www.g3i.no/om-oss", "https://randomblog.no/x"])
    urls = brave_discovery.candidate_urls(_profile(), "key", max_candidates=3)
    hosts = [brave_discovery._host(u) for u in urls]
    assert "g3i.no" in hosts
    assert all("linkedin" not in h and "proff" not in h for h in hosts)
    assert len(urls) <= 3
