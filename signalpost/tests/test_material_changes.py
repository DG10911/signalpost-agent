"""Material changes: idempotent refresh, one change per changed field, prior
evidence preserved, and no false changes from sources that failed to load."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_company_agent.output_contract import to_contract  # noqa: E402
from norway_company_agent.registry_claims import registry_claims  # noqa: E402
from test_synthesis import _fin, _rec, rich_profile  # noqa: E402

RUN1 = ("run-1", "2026-11-01T00:00:00Z", "2026-11-01T00:30:00Z")
RUN2 = ("run-2", "2026-11-08T00:00:00Z", "2026-11-08T00:30:00Z")
RUN3 = ("run-3", "2026-11-15T00:00:00Z", "2026-11-15T00:30:00Z")


def env_for(profile: dict, run=RUN1, previous: dict | None = None) -> dict:
    profile = copy.deepcopy(profile)
    profile["registry_claims"] = registry_claims(profile)
    run_id, started, completed = run
    return to_contract(profile, run_id=run_id, started_at=started, completed_at=completed,
                       terminal_status="completed", previous=previous)


def test_first_run_exposes_changes_dated_by_sources():
    env = env_for(rich_profile())
    fields = {c["field"] for c in env["changes"]}
    assert {"financials.revenue", "financials.operating_result", "financials.equity"} <= fields
    assert "roles.board" in fields                            # board group entry dated 2026-06-10
    assert "roles.ceo" not in fields                          # dated 2019: not recent
    board = [c for c in env["changes"] if c["field"] == "roles.board"]
    assert len(board) == 1, "the register dates the group, so one event per group"
    assert board[0]["after"] == ["John Roe (chair)", "Kari Nordmann"]
    ids = {e["id"] for e in env["evidence"]}
    for c in env["changes"]:
        assert c["change_type"] == "dated_in_source"
        assert {"field", "before", "after", "detected_at", "evidence_ids"} <= set(c)
        assert c["evidence_ids"] and set(c["evidence_ids"]) <= ids
    rev = next(c for c in env["changes"] if c["field"] == "financials.revenue")
    assert rev["before"] == {"period": "2024", "value": 1000000.0}
    assert rev["after"]["value"] == 1250000.0 and rev["effective_at"] == "2025-12-31"
    assert env["refresh"]["baseline"] is None and env["refresh"]["dated_source_event_keys"]


def test_idempotent_rerun_of_same_snapshot_has_zero_changes():
    first = env_for(rich_profile(), RUN1)
    second = env_for(rich_profile(), RUN2, previous=first)
    assert second["changes"] == []
    assert second["refresh"]["baseline"]["run_id"] == "run-1"
    assert second["refresh"]["material_changes"] == 0
    third = env_for(rich_profile(), RUN3, previous=second)   # ledger carries forward
    assert third["changes"] == []


def test_one_changed_field_produces_exactly_one_change_with_prior_evidence():
    first = env_for(rich_profile(), RUN1)
    p = rich_profile()
    p["evidence"]["registry"]["value"]["antallAnsatte"] = "15"
    second = env_for(p, RUN2, previous=first)
    assert len(second["changes"]) == 1
    change = second["changes"][0]
    assert change["field"] == "registry.employees"
    assert (change["before"], change["after"]) == ("12", "15")
    assert change["change_type"] == "since_previous_run" and change["detected_at"] == RUN2[2]
    ids = {e["id"] for e in second["evidence"]}
    assert change["evidence_ids"] and set(change["evidence_ids"]) <= ids
    assert change["previous_evidence_ids"] and set(change["previous_evidence_ids"]) <= ids
    preserved = [e for e in second["evidence"] if e["id"] in change["previous_evidence_ids"]]
    assert all(e["preserved_from_run"] == "run-1" for e in preserved)
    assert second["refresh"]["prior_evidence_preserved"] == len(preserved)


def test_new_filed_accounts_are_one_change_not_one_per_metric():
    first = env_for(rich_profile(), RUN1)
    p = rich_profile()
    p["evidence"]["financials"]["value"]["records"].append(_fin(2026, 2000000.0, 1.0, 1.0))
    second = env_for(p, RUN2, previous=first)
    assert [c["field"] for c in second["changes"]] == ["financials.latest"]
    assert second["changes"][0]["after"]["period_end"] == "2026-12-31"


def test_ceo_change_is_one_change():
    first = env_for(rich_profile(), RUN1)
    p = rich_profile()
    p["evidence"]["roles"]["value"]["roles"][0] = {"name": "Ola Nordmann", "role_code": "DAGL",
                                                   "role": "Daglig leder", "last_changed": "2026-11-05"}
    second = env_for(p, RUN2, previous=first)
    assert [c["field"] for c in second["changes"]] == ["roles.ceo"]
    assert second["changes"][0]["before"] == ["Jane Doe"] and second["changes"][0]["after"] == ["Ola Nordmann"]


def test_failed_source_is_not_a_change():
    first = env_for(rich_profile(), RUN1)
    p = rich_profile()
    p["evidence"]["website"] = _rec("https://example.no/", {}, status="source_error")
    p["evidence"]["roles"] = _rec("https://data.brreg.no/roller", {"roles": []}, status="source_error")
    second = env_for(p, RUN2, previous=first)
    assert second["changes"] == []
    skipped = {s["field"] for s in second["refresh"]["skipped_fields"]}
    assert {"official_website", "job_postings", "roles.ceo"} <= skipped


def test_new_job_ad_and_newer_news_are_reported_once():
    first = env_for(rich_profile(), RUN1)
    p = rich_profile()
    v = p["evidence"]["website"]["value"]
    v["job_postings"].append({"title": "Tester", "date_posted": "2026-11-06", "url": "https://example.no/jobb/2"})
    v["news_articles"].append({"headline": "Gammel sak", "date_published": "2020-01-01", "url": "https://example.no/n/0"})
    v["news_articles"].append({"headline": "Ny kunde", "date_published": "2026-11-07", "url": "https://example.no/n/2"})
    second = env_for(p, RUN2, previous=first)
    by_field = {c["field"]: c for c in second["changes"]}
    assert set(by_field) == {"job_postings", "news"}
    assert by_field["job_postings"]["added"] == ["https://example.no/jobb/2"]
    assert by_field["news"]["after"] == [{"date_published": "2026-11-07", "headline": "Ny kunde"}]  # old item ignored
    third = env_for(p, RUN3, previous=second)
    assert third["changes"] == []


def test_unread_nav_feed_is_not_a_job_ad_removal():
    first = env_for(rich_profile(), RUN1)
    p = rich_profile()
    p["evidence"]["website"]["value"]["job_postings"] = []
    p["source_checks"] = {"nav_jobs": "not_read"}
    second = env_for(p, RUN2, previous=first)
    assert second["changes"] == []
    assert "job_postings" in {s["field"] for s in second["refresh"]["skipped_fields"]}


def test_baseline_for_another_company_is_rejected():
    first = env_for(rich_profile(), RUN1)
    first["organisation_number"] = "111111111"
    second = env_for(rich_profile(), RUN2, previous=first)
    assert second["refresh"]["baseline"] is None
    assert "baseline_rejected" in second["refresh"]


# ---- Brønnøysund update feed (registry_event) ----------------------------------

FEED = {"_embedded": {"oppdaterteEnheter": [
    {"dato": "2025-09-15T22:31:38.628Z", "endringstype": "Endring", "oppdateringsid": 22076856,
     "organisasjonsnummer": "923609016",
     "endringer": [{"op": "replace", "path": "/naeringskode1/kode", "value": "49.410"}]},
    {"dato": "2026-04-29T18:00:43.884Z", "endringstype": "Endring", "oppdateringsid": 24246922,
     "organisasjonsnummer": "923609016",
     "endringer": [{"op": "add", "path": "/sisteInnsendteAarsregnskap", "value": "2025"}]},
    {"dato": "2026-05-01T00:00:00.000Z", "endringstype": "Endring", "oppdateringsid": 1,
     "organisasjonsnummer": "111111111", "endringer": []},  # another entity: dropped
]}, "page": {"totalElements": 3}}


def with_feed(profile: dict, body: dict = FEED) -> dict:
    from norway_company_agent.registry_updates import normalize_updates
    profile = copy.deepcopy(profile)
    profile["evidence"]["registry_updates"] = _rec(
        "https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer=923609016",
        normalize_updates(body, "923609016"))
    return profile


def test_feed_is_normalised_exact_entity_and_newest_first():
    from norway_company_agent.registry_updates import normalize_updates
    events = normalize_updates(FEED, "923609016")["events"]
    assert [e["update_id"] for e in events] == [24246922, 22076856]
    assert events[0]["fields_changed"] == ["latest submitted annual accounts"]
    assert events[0]["description"] == "2026-04-29 change: latest submitted annual accounts 2025"


def test_feed_events_are_claims_changes_and_what_changed():
    env = env_for(with_feed(rich_profile()))
    events = [c for c in env["claims"] if c["field"] == "registry_event"]
    assert len(events) == 2 and all(c["reporting_period"] for c in events)
    feed_changes = [c for c in env["changes"] if c["field"] == "registry_event"]
    assert [c["effective_at"] for c in feed_changes] == ["2026-04-29"]  # 2025-09-15 is outside 365 days
    assert "registry.employees" not in {c["field"] for c in env["changes"]}  # feed supersedes registry date fields
    what_changed = next(c for c in env["claims"] if c["field"] == "synthesis.what_changed")["value"]
    assert "registry 2026-04-29 change: latest submitted annual accounts 2025" in what_changed
    assert env_for(with_feed(rich_profile()), RUN2, previous=env)["changes"] == []


def test_employee_change_explained_by_feed_is_still_one_change():
    first = env_for(with_feed(rich_profile()), RUN1)
    p = with_feed(rich_profile(), {"_embedded": {"oppdaterteEnheter": FEED["_embedded"]["oppdaterteEnheter"] + [
        {"dato": "2026-10-30T10:00:00.000Z", "endringstype": "Endring", "oppdateringsid": 25000000,
         "organisasjonsnummer": "923609016",
         "endringer": [{"op": "replace", "path": "/antallAnsatte", "value": 15}]}]}})
    p["evidence"]["registry"]["value"]["antallAnsatte"] = "15"
    second = env_for(p, RUN2, previous=first)
    assert [c["field"] for c in second["changes"]] == ["registry.employees"]
    assert second["changes"][0]["effective_at"] == "2026-10-30"


def test_unreadable_feed_is_an_unknown_not_a_change():
    p = rich_profile()
    p["evidence"]["registry_updates"] = _rec("https://data.brreg.no/x", None, status="source_error")
    env = env_for(p)
    row = next(c for c in env["claims"] if c["field"] == "registry_event")
    assert row["availability"] == "failed" and row["reason"]
    assert not [c for c in env["changes"] if c["field"] == "registry_event"]
