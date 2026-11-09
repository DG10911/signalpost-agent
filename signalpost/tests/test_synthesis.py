"""Synthesis must be evidence-grounded: every sentence cites evidence ids that
exist in the envelope, every number in the summary is a claim value, nothing is
invented, and families that were not found are listed as unknowns (never 0)."""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.output_contract import to_contract  # noqa: E402
from norway_company_agent.synthesis import MAX_SUMMARY_WORDS, latest_two_periods, summarize_profile  # noqa: E402

T = "2026-11-01T00:00:00Z"


def _rec(source_url: str, value, status: str = "available") -> dict:
    return {"status": status, "source_url": source_url, "retrieved_at": T, "content_sha256": "x" * 64,
            "source_class": "official", "value": value}


def _fin(year: int, revenue, op, equity, account_type: str = "SELSKAP") -> dict:
    return {"record_id": year, "account_type": account_type, "currency": "NOK",
            "period": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"},
            "revenue": revenue, "operating_result": op, "annual_result": op, "assets": 1.0, "equity": equity, "debt": 1.0}


def rich_profile() -> dict:
    return {
        "organisation_number": "923609016", "name": "EXAMPLE AS", "legal_form": "AS", "municipality": "OSLO",
        "evidence": {
            "registry": _rec("https://data.brreg.no/bulk", {
                "navn": "EXAMPLE AS", "organisasjonsform.kode": "AS", "organisasjonsform.beskrivelse": "Aksjeselskap",
                "naeringskode1.beskrivelse": "Programvareutvikling", "aktivitet": "Utvikling av programvare.",
                "forretningsadresse.adresse": "Gate 1", "forretningsadresse.postnummer": "0150",
                "forretningsadresse.poststed": "OSLO", "forretningsadresse.kommune": "OSLO",
                "stiftelsesdato": "2010-05-01", "antallAnsatte": "12",
                "registreringsdatoAntallAnsatteEnhetsregisteret": "2026-09-14", "harRegistrertAntallAnsatte": "true"}),
            "financials": _rec("https://data.brreg.no/regnskap/923609016", {"records": [
                _fin(2023, 800000.0, 10000.0, 50000.0), _fin(2024, 1000000.0, 20000.0, 60000.0),
                _fin(2025, 1250000.0, -5000.0, 55000.0)]}),
            "roles": _rec("https://data.brreg.no/roller", {"roles": [
                {"name": "Jane Doe", "role_code": "DAGL", "role": "Daglig leder", "last_changed": "2019-03-01"},
                {"name": "John Roe", "role_code": "LEDE", "role": "Styrets leder", "last_changed": "2026-06-10"},
                {"name": "Kari Nordmann", "role_code": "MEDL", "role": "Styremedlem", "last_changed": "2026-06-10"}]}),
            "locations": _rec("https://data.brreg.no/underenheter", {"locations": [
                {"name": "EXAMPLE AS", "address": {"kommune": "OSLO"}},
                {"name": "EXAMPLE AS AVD BERGEN", "address": {"kommune": "BERGEN"}}]}),
            "website": _rec("https://example.no/", {
                "final_url": "https://example.no/", "title": "Example AS",
                "description": "Vi lager programvare for norske bedrifter.",
                "identity_assessment": {"publishable": True, "score": 0.95},
                "social_links": [{"platform": "linkedin", "url": "https://linkedin.com/company/example-as"}],
                "job_postings": [{"title": "Utvikler", "date_posted": "2026-10-20", "url": "https://example.no/jobb/1"}],
                "news_articles": [{"headline": "Ny avtale", "date_published": "2026-10-01", "url": "https://example.no/n/1"}]}),
        },
    }


def empty_profile() -> dict:
    return {
        "organisation_number": "999999999", "name": "GHOST AS", "legal_form": "AS",
        "evidence": {
            "registry": _rec("https://data.brreg.no/bulk", {"navn": "GHOST AS", "organisasjonsform.kode": "AS"}),
            "financials": _rec("https://data.brreg.no/regnskap/999999999", {"records": []}, status="not_found"),
            "roles": _rec("https://data.brreg.no/roller", {"roles": []}),
            "website": _rec("", {}, status="not_found"),
        },
    }


def _contract(profile: dict) -> dict:
    from norway_company_agent.registry_claims import registry_claims
    profile = copy.deepcopy(profile)
    profile["registry_claims"] = registry_claims(profile)
    return to_contract(profile, run_id="r1", started_at=T, completed_at=T, terminal_status="completed")


def _syn(env: dict, field: str = "synthesis.summary") -> dict:
    return next(c for c in env["claims"] if c["field"] == field)


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[float]:
    return {float(m.replace(",", "")) for m in _NUM.findall(text)}


def test_every_synthesis_citation_exists_in_the_envelope():
    for profile in (rich_profile(), empty_profile()):
        env = _contract(profile)
        ids = {e["id"] for e in env["evidence"]}
        syn = [c for c in env["claims"] if c["field"].startswith(("synthesis.", "financials.change."))]
        assert syn, "synthesis claims are emitted"
        for c in syn:
            assert c["evidence_ids"] and set(c["evidence_ids"]) <= ids, c["field"]
        for sentence in _syn(env)["sentences"]:
            assert sentence["evidence_ids"] and set(sentence["evidence_ids"]) <= ids, sentence


def test_summary_numbers_all_appear_in_claims():
    env = _contract(rich_profile())
    summary = _syn(env)["value"]
    grounded = [c for c in env["claims"]
                if not c["field"].startswith("synthesis.") or c["field"] == "synthesis.counts"]
    allowed = _numbers(json.dumps([[c["value"], c.get("reporting_period")] for c in grounded], ensure_ascii=False))
    missing = _numbers(summary) - allowed
    assert not missing, (missing, summary)


def test_rich_summary_content_uses_latest_period_and_consecutive_change():
    env = _contract(rich_profile())
    summary = _syn(env)["value"]
    for label in ("What it does:", "Size:", "Leadership:", "Online:", "What changed:"):
        assert label in summary
    assert "filed accounts for 2025 show revenue NOK 1,250,000" in summary.lower().replace("nok", "NOK")
    assert "Revenue went from NOK 1,000,000 in 2024 to NOK 1,250,000 in 2025 (+25.0%)" in summary
    assert "12 registered employees (registry entry dated 2026-09-14)" in summary
    assert "Jane Doe" in summary and "John Roe" in summary
    assert "(company-described)" in _syn(env, "synthesis.what_it_does")["value"]
    assert "board entry in the roles register changed 2026-06-10 (now chair John Roe)" in summary
    assert "LinkedIn" in summary and "1 job ad" in summary and "latest 2026-10-01" in summary
    footprint = _syn(env, "synthesis.footprint")["value"]
    assert "2 registered sub-units in Bergen, Oslo" in footprint
    for sentence in _syn(env)["sentences"]:
        assert sentence["sources"] and all(s["source_url"] and s["retrieved_at"] for s in sentence["sources"])
    assert len(summary.split()) <= MAX_SUMMARY_WORDS
    change = _syn(env, "financials.change.revenue")["value"]
    assert change["before"] == 1000000.0 and change["after"] == 1250000.0 and change["change_pct"] == 25.0
    # No praise or speculation words.
    assert not re.search(r"\b(strong|weak|impressive|healthy|growing|leading|likely|probably)\b", summary, re.I)


def test_no_change_without_two_consecutive_filed_periods():
    p = rich_profile()
    p["evidence"]["financials"]["value"]["records"] = [_fin(2022, 900.0, 1.0, 1.0), _fin(2025, 1000.0, 2.0, 2.0)]
    env = _contract(p)
    assert not [c for c in env["claims"] if c["field"].startswith("financials.change.")]
    assert "%" not in _syn(env)["value"]
    latest, prior = latest_two_periods(p["evidence"]["financials"]["value"]["records"])
    assert latest["record_id"] == 2025 and prior is None


def test_group_accounts_do_not_mix_with_company_accounts():
    recs = [_fin(2025, 10.0, 1.0, 1.0), _fin(2024, 999.0, 1.0, 1.0, account_type="KONSERN")]
    latest, prior = latest_two_periods(recs)
    assert latest["record_id"] == 2025 and prior is None


def test_missing_data_is_unknown_never_zero():
    env = _contract(empty_profile())
    summary = _syn(env)["value"]
    unknowns = {u["family"]: u["reason"] for u in _syn(env, "synthesis.unknowns_detail")["value"]}
    assert set(unknowns) >= {"employees", "financials", "leadership", "website", "job_ads", "dated_news", "social_profiles"}
    assert unknowns["website"] == "no website is listed in the registry and none could be verified"
    assert "no annual accounts" in unknowns["financials"]
    assert not re.search(r"\b0\b", summary), summary
    assert "revenue" not in summary.lower() and "employees" not in summary.lower().replace("employee count", "")
    assert "Unknowns:" in summary
    states = {u["family"]: u["state"] for u in _syn(env, "synthesis.unknowns_detail")["value"]}
    assert states["website"] == "not_available" and states["financials"] == "not_available"
    # each external family with nothing gets a not_available claim with a reason
    for field in ("official_website", "job_posting", "news", "social"):
        row = next(c for c in env["claims"] if c["field"] == field)
        assert row["availability"] == "not_available" and row["reason"], field


def test_dead_registry_homepage_reason_is_carried_into_unknowns():
    p = empty_profile()
    p["evidence"]["registry"]["value"]["hjemmeside"] = "www.gone-example.no"
    p["evidence"]["website"] = _rec("http://www.gone-example.no", {}, status="source_error")
    p["evidence"]["website"]["note"] = "Hostname did not resolve"
    env = _contract(p)
    unknowns = {u["family"]: u["reason"] for u in _syn(env, "synthesis.unknowns_detail")["value"]}
    site = next(c for c in env["claims"] if c["field"] == "official_website")
    assert site["availability"] != "available"
    assert unknowns["website"] == site["reason"] and "does not exist" in site["reason"]


def test_unverified_site_description_is_not_used():
    p = rich_profile()
    p["evidence"]["website"]["value"]["identity_assessment"] = {"publishable": False}
    summary = _syn(_contract(p))["value"]
    assert "company-described" not in summary and "programvare for norske" not in summary


def test_legacy_profile_summary_shape():
    s = summarize_profile(rich_profile())
    assert "EXAMPLE AS" in s["narrative"] and "NOK 1,250,000" in s["narrative"]
    assert all(st["evidence_ids"] for st in s["statements"])
    g = summarize_profile(empty_profile())
    assert any(u.startswith("financials:") for u in g["unknowns"])
    assert "revenue" not in g["narrative"].lower()
