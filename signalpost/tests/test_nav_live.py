import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.nav_live import NavLive, name_key  # noqa: E402


def test_name_key_drops_legal_form_and_accents():
    assert name_key("Lillestrøm Tannhelse As") == name_key("LILLESTRØM TANNHELSE AS")
    assert name_key("Ærlig Bygg ASA") == "aerlig bygg"


def _detail(uuid, orgnr, name="Acme AS", status="ACTIVE"):
    return {"uuid": uuid, "status": status, "sistEndret": "2026-10-01T00:00:00",
            "ad_content": {"title": "Snekker", "published": "2026-10-01T00:00:00+02:00",
                           "employer": {"name": name, "orgnr": orgnr}}}


class FakeNav(NavLive):
    def __init__(self, companies, details, parents):
        super().__init__(companies)
        self._details = details
        self._parents = parents

    def _get(self, url, headers=None):
        self.requests += 1
        if "/feedentry/" in url:
            uuid = url.rsplit("/", 1)[-1]
            return 200, json.dumps(self._details[uuid]).encode()
        if "underenheter" in url:
            sub = url.rsplit("/", 1)[-1]
            return (200, json.dumps({"overordnetEnhet": self._parents[sub]}).encode()) if sub in self._parents else (404, b"")
        return 404, b""


def _run(details, parents, active):
    nav = FakeNav([("111111111", "ACME AS")], details, parents)
    nav._match(active, {}, started=__import__("time").monotonic())
    return nav._index


def test_publishes_exact_orgnr():
    idx = _run({"u1": _detail("u1", "111111111")}, {}, {"u1": "Acme AS"})
    assert list(idx) == ["111111111"] and idx["111111111"][0]["title"] == "Snekker"


def test_publishes_registered_subunit_under_parent():
    idx = _run({"u1": _detail("u1", "222222222")}, {"222222222": "111111111"}, {"u1": "Acme AS"})
    assert idx["111111111"][0]["employer_orgnr"] == "222222222"


def test_same_name_other_company_is_never_published():
    # Name matches, but the ad's employer is an unrelated organisation number.
    idx = _run({"u1": _detail("u1", "333333333")}, {"333333333": "999999999"}, {"u1": "ACME AS"})
    assert idx == {}


def test_inactive_and_anonymous_ads_are_skipped():
    details = {"u1": _detail("u1", "111111111", status="INACTIVE"),
               "u2": _detail("u2", "111111111", name="Anonym arbeidsgiver")}
    assert _run(details, {}, {"u1": "Acme AS", "u2": "Acme AS"}) == {}
