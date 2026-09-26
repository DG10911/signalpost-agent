"""Registry-claim formalisation: emit official fields as evidence-backed claims,
never invent missing values, always carry provenance, deterministic order."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.registry_claims import registry_claims  # noqa: E402


def _profile(**value):
    return {
        "organisation_number": "923609016",
        "bankrupt": False, "liquidating": False,
        "evidence": {"registry": {
            "status": "available",
            "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
            "source_class": "official_registry_bulk",
            "retrieved_at": "2026-09-24T00:00:00Z",
            "content_sha256": "a" * 64,
            "value": value,
        }},
    }


def test_present_fields_become_claims_with_provenance():
    p = _profile(**{
        "navn": "Example AS", "organisasjonsform.beskrivelse": "Aksjeselskap",
        "naeringskode1.beskrivelse": "Programvare", "vedtektsfestetFormaal": "Lage programvare",
        "forretningsadresse.adresse": "Storgata 1", "forretningsadresse.postnummer": "0155",
        "forretningsadresse.poststed": "OSLO", "stiftelsesdato": "2004-01-01",
        "kapital.belop": "100000.00", "kapital.valuta": "NOK",
        "registrertIMvaRegisteret": "true", "epostadresse": "post@example.no",
        "telefon": "22000000", "antallAnsatte": "42",
    })
    claims = registry_claims(p)
    fields = {c["field"]: c for c in claims}
    assert {"legal_name", "statutory_purpose", "business_address", "founding_date",
            "share_capital", "vat_registered", "email", "phone",
            "employees_registered"} <= set(fields)
    # provenance on every claim
    for c in claims:
        assert c["source_url"] and c["retrieved_at"] and len(c["content_sha256"]) == 64
        assert c["organisation_number"] == "923609016"
        assert c["availability"] == "available"
    assert fields["business_address"]["value"] == "Storgata 1, 0155 OSLO"
    assert fields["share_capital"]["value"] == {"amount": "100000.00", "currency": "NOK"}
    assert fields["employees_registered"]["value"] == "42"


def test_missing_values_never_become_claims():
    p = _profile(**{"navn": "Example AS", "vedtektsfestetFormaal": "", "epostadresse": None,
                    "kapital.belop": "", "antallAnsatte": ""})
    fields = {c["field"] for c in registry_claims(p)}
    assert "legal_name" in fields
    assert "statutory_purpose" not in fields   # blank -> no claim
    assert "email" not in fields               # None -> no claim
    assert "share_capital" not in fields       # blank -> no claim
    assert "employees_registered" not in fields  # blank -> no claim


def test_non_digit_employees_not_emitted():
    p = _profile(**{"navn": "X AS", "antallAnsatte": "n/a"})
    assert "employees_registered" not in {c["field"] for c in registry_claims(p)}


def test_deterministic_and_no_registry_no_claims():
    p = _profile(**{"navn": "X AS", "telefon": "22", "stiftelsesdato": "2001-01-01"})
    a = registry_claims(p); b = registry_claims(p)
    assert a == b
    assert [c["field"] for c in a] == sorted(c["field"] for c in a)
    assert registry_claims({"organisation_number": "1", "evidence": {}}) == []
