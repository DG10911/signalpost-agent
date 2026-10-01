"""V9 recall expansion — official-field claims, atomic claims, and
exact-entity official-registry observations. All zero-network and identity-safe.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.registry_claims import registry_claims  # noqa: E402
from norway_company_agent.output_contract import to_contract  # noqa: E402
from norway_company_agent.external_observations import build_observations  # noqa: E402
from norway_company_agent.external_footprint import publishable_observation  # noqa: E402


def _profile(**value):
    return {
        "organisation_number": "923609016",
        "bankrupt": False,
        "liquidating": False,
        "evidence": {"registry": {
            "status": "available",
            "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
            "source_class": "official_registry_bulk",
            "retrieved_at": "2026-09-24T00:00:00Z",
            "content_sha256": "a" * 64,
            "source_row_key": "923609016",
            "value": value,
        }},
    }


def test_extra_official_fields_emitted_and_blanks_skipped():
    p = _profile(**{
        "navn": "Example AS",
        "naeringskode1.kode": "62.010",
        "forretningsadresse.kommune": "BERGEN",
        "forretningsadresse.kommunenummer": "4601",
        "kapital.antallAksjer": "500",
        "maalform": "Bokmål",
        "aktivitet": "Programvare",
        "naeringskode2.kode": "",           # blank -> skipped
    })
    fields = {c["field"]: c for c in registry_claims(p)}
    assert fields["industry_code"]["value"] == "62.010"
    assert fields["municipality"]["value"] == "BERGEN"
    assert fields["municipality_number"]["value"] == "4601"
    assert fields["share_count"]["value"] == "500"
    assert fields["language_form"]["value"] == "Bokmål"
    assert fields["activity_description"]["value"] == "Programvare"
    assert "industry_code_2" not in fields
    for c in registry_claims(p):
        assert c["source_url"] and c["retrieved_at"] and len(c["content_sha256"]) == 64
        assert c["organisation_number"] == "923609016"


def test_atomic_role_location_and_year_claims():
    p = _profile(**{"navn": "Example AS"})
    p["registry_claims"] = registry_claims(p)
    p["evidence"].update({
        "roles": {"status": "available", "source_url": "https://roles/x", "retrieved_at": "t",
                  "content_sha256": "b" * 64,
                  "value": {"roles": [{"name": "Jane Doe", "role": "Styrets leder", "role_code": "LEDE",
                                       "last_changed": "2020-01-01", "inactive": False},
                                      {"name": "Old Person", "role": "Daglig leder", "role_code": "DAGL",
                                       "last_changed": "1999-01-01", "inactive": True}]}},
        "locations": {"status": "available", "source_url": "https://loc/x", "retrieved_at": "t",
                      "content_sha256": "c" * 64,
                      "value": {"locations": [{"organisation_number": "999", "name": "Branch"}]}},
        "financial_history": {"status": "available", "source_url": "https://fh/x", "retrieved_at": "t",
                              "content_sha256": "d" * 64,
                              "value": {"years": ["2023", "2024"], "pdfs": []}},
    })
    c = to_contract(p, run_id="r", started_at="s", completed_at="e", terminal_status="completed")
    by_field = {}
    for cl in c["claims"]:
        by_field.setdefault(cl["field"], []).append(cl)
    assert "role.lede" in by_field and by_field["role.lede"][0]["value"] == "Jane Doe"
    assert "role.dagl" not in by_field                 # inactive role excluded
    assert "roles" in by_field                          # aggregate retained
    assert by_field["location"][0]["value"]["name"] == "Branch"
    years = {cl["value"] for cl in by_field["financial_history.year"]}
    assert years == {"2023", "2024"}


def test_registry_observations_are_publishable_and_exact_entity():
    p = _profile(**{"navn": "Example AS", "naeringskode1.kode": "62.010", "antallAnsatte": "42"})
    obs = build_observations(p, retrieved_at="2026-09-26T00:00:00Z")
    signals = {o["signal_type"] for o in obs}
    assert {"company_profile", "workforce_snapshot"} <= signals
    for o in obs:
        assert o["platform"] == "brreg"
        assert o["organisation_number"] == "923609016"
        assert o["exact_entity"] is True
        assert o["acquisition_mode"] == "official_api"
        assert publishable_observation(o), o


def test_no_registry_means_no_registry_observations():
    p = {"organisation_number": "923609016", "evidence": {"registry": {"status": "not_found"}}}
    assert build_observations(p) == []
