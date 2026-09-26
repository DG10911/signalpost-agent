"""OUTPUT_CONTRACT transform: flat claims/evidence, 6-state availability only,
per-claim evidence IDs resolve, provenance present, no null->available."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.output_contract import to_contract  # noqa: E402

SIX = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}


def _profile():
    return {
        "organisation_number": "923609016", "name": "Example AS",
        "registry_claims": [
            {"field": "legal_name", "value": "Example AS", "availability": "available", "reporting_period": None},
            {"field": "statutory_purpose", "value": "Lage programvare", "availability": "available", "reporting_period": None},
        ],
        "run_metrics": {"requests": 6},
        "evidence": {
            "registry": {"status": "available", "source_url": "https://data.brreg.no/x",
                         "source_class": "official_registry_bulk", "retrieved_at": "2026-01-01T00:00:00Z", "content_sha256": "a" * 64},
            "financials": {"status": "available", "source_url": "https://fin/x", "retrieved_at": "2026-01-01T00:00:00Z",
                           "content_sha256": "b" * 64, "value": {"records": [{"period": "2025", "revenue": 100, "annual_result": 5}]}},
            "roles": {"status": "available", "source_url": "https://roles/x", "value": {"roles": [{"name": "Jane", "role": "CEO"}]}},
            "website": {"status": "not_found", "source_url": "https://data.brreg.no/x", "retrieved_at": "2026-01-01T00:00:00Z"},
        },
    }


def test_contract_shape_and_states():
    c = to_contract(_profile(), run_id="r", started_at="s", completed_at="e", terminal_status="completed")
    assert set(c) >= {"organisation_number", "run", "claims", "evidence", "changes", "errors", "operations"}
    assert c["operations"]["third_party_cost_usd"] == 0
    # every claim availability is one of the official six; every claim resolves its evidence ids
    ev_ids = {e["id"] for e in c["evidence"]}
    for cl in c["claims"]:
        assert cl["availability"] in SIX
        for eid in cl["evidence_ids"]:
            assert eid in ev_ids
    # evidence carries provenance
    for e in c["evidence"]:
        assert e["source_url"] and "content_sha256" in e
    fields = {cl["field"] for cl in c["claims"]}
    assert "legal_name" in fields and "statutory_purpose" in fields
    assert "financials.revenue" in fields
    # website not_found -> availability not_available (never fabricated)
    web = next(cl for cl in c["claims"] if cl["field"] == "official_website")
    assert web["availability"] == "not_available" and web["value"] is None


def test_website_ambiguous_when_not_exact():
    p = _profile()
    p["evidence"]["website"] = {"status": "available", "source_url": "https://x", "retrieved_at": "t", "content_sha256": "c" * 64,
                                "value": {"final_url": "https://x.no", "identity_assessment": {"publishable": False, "score": 0.6}}}
    c = to_contract(p, run_id="r", started_at="s", completed_at="e", terminal_status="completed")
    web = next(cl for cl in c["claims"] if cl["field"] == "official_website")
    assert web["availability"] == "ambiguous"
