"""Wikidata P2333 connector: exact-entity by construction, publishable, and
absent-when-absent. A mocked SPARQL querier keeps the test offline."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.wikidata import query_p2333, observations_for  # noqa: E402
from norway_company_agent.external_footprint import publishable_observation  # noqa: E402
from norway_company_agent.external_observations import build_observations  # noqa: E402


def _fake_response(orgnr: str):
    return {"results": {"bindings": [{
        "orgnr": {"value": orgnr},
        "item": {"value": "http://www.wikidata.org/entity/Q1776022"},
        "itemLabel": {"value": "Equinor"},
        "website": {"value": "https://www.equinor.com"},
        "employees": {"value": "20000"},
        "inception": {"value": "1972-06-14T00:00:00Z"},
        "article": {"value": "https://no.wikipedia.org/wiki/Equinor"},
        "P4264": {"value": "equinor"},
    }]}}


def test_query_matches_only_exact_orgnr():
    calls = {}
    def querier(sparql):
        calls["sparql"] = sparql
        return _fake_response("923609016")
    idx = query_p2333(["923609016"], querier=querier)
    assert set(idx) == {"923609016"}
    rec = idx["923609016"]
    assert rec["item"].endswith("Q1776022")
    assert rec["employees"] == "20000"
    assert rec["social"]["linkedin"] == "https://linkedin.com/company/equinor"
    assert '"923609016"' in calls["sparql"] and "wdt:P2333" in calls["sparql"]


def test_observations_are_publishable_and_exact_entity():
    idx = query_p2333(["923609016"], querier=lambda s: _fake_response("923609016"))
    obs = observations_for(idx["923609016"], "923609016", retrieved_at="2026-09-26T00:00:00Z")
    platforms = {o["platform"] for o in obs}
    assert {"wikidata", "wikipedia", "linkedin"} <= platforms
    for o in obs:
        assert o["exact_entity"] is True
        assert o["identity_proof"][0]["type"] == "wikidata_p2333_organisation_number"
        assert publishable_observation(o), o


def test_no_match_yields_nothing():
    idx = query_p2333(["999999999"], querier=lambda s: {"results": {"bindings": []}})
    assert idx == {}
    assert observations_for({}, "999999999", retrieved_at="2026-09-26T00:00:00Z") == []


def test_build_observations_merges_wikidata_index():
    profile = {"organisation_number": "923609016", "evidence": {"website": {"status": "not_available"}}}
    idx = query_p2333(["923609016"], querier=lambda s: _fake_response("923609016"))
    obs = build_observations(profile, wikidata_index=idx, retrieved_at="2026-09-26T00:00:00Z")
    assert any(o["platform"] == "wikidata" for o in obs)
    # A company absent from the index gets no wikidata observations.
    other = build_observations({"organisation_number": "111111111", "evidence": {}}, wikidata_index=idx)
    assert not any(o["source_class"] == "wikidata" for o in other)
