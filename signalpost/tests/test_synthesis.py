"""Synthesis must be evidence-grounded: every statement traces to a present
fact, nothing is invented, and absent categories are listed as unknowns."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.synthesis import summarize_profile  # noqa: E402


def _rich():
    return {
        "organisation_number": "923609016", "name": "Example AS", "legal_form": "AS",
        "municipality": "OSLO", "industry_label": "Programvareutvikling",
        "evidence": {
            "registry": {"status": "available", "source_url": "https://data.brreg.no/x", "retrieved_at": "2026-01-01T00:00:00Z"},
            "financials": {"status": "available", "source_url": "https://fin/x", "retrieved_at": "2026-01-01T00:00:00Z",
                           "value": {"records": [{"period": "2025", "currency": "NOK", "revenue": 1000000, "annual_result": 50000}]}},
            "roles": {"status": "available", "source_url": "https://roles/x",
                      "value": {"roles": [{"name": "Jane Doe", "role": "CEO"}]}},
            "locations": {"status": "available", "value": {"locations": [{"name": "HQ"}]}},
        },
    }


def test_rich_profile_summary_traces_to_evidence():
    s = summarize_profile(_rich())
    assert "Example AS" in s["narrative"]
    assert "2025" in s["narrative"] and "1000000" in s["narrative"]
    assert "Jane Doe" in s["narrative"]
    # every statement cites an evidence field that exists on the profile
    for st in s["statements"]:
        assert st["evidence_field"] in {"registry", "registry_live", "financials", "roles", "locations", "website", "external_footprint"}
    assert "verified company website" in s["unknowns"]  # none provided


def test_empty_profile_invents_nothing():
    s = summarize_profile({"organisation_number": "999", "name": "Ghost AS", "evidence": {}})
    # No fabricated numbers/roles; unknowns list the absent categories.
    assert "filed financial figures" in s["unknowns"]
    assert "registered leadership/roles" in s["unknowns"]
    # Narrative contains only the name-level identity or the no-facts fallback.
    assert "revenue" not in s["narrative"].lower()
    assert s["statements"] == [] or all(st["evidence_field"] == "registry" for st in s["statements"])


def test_no_financial_claim_without_records():
    p = _rich()
    p["evidence"]["financials"] = {"status": "not_found"}
    s = summarize_profile(p)
    assert "filed financial figures" in s["unknowns"]
    assert "revenue" not in s["narrative"].lower()
