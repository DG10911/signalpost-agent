import gzip
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent import nav_jobs  # noqa: E402
from norway_company_agent.external_footprint import publishable_observation  # noqa: E402
from norway_company_agent.nav_live import NavLive, dedupe_jobs, is_single_vacancy, load_subunits, name_key  # noqa: E402

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def test_name_key_drops_legal_form_and_accents():
    assert name_key("Lillestrøm Tannhelse As") == name_key("LILLESTRØM TANNHELSE AS")
    assert name_key("Ærlig Bygg ASA") == "aerlig bygg"


def _detail(uuid, orgnr, name="Acme AS", status="ACTIVE", title="Snekker",
            expires="2026-12-01T00:00:00+01:00", published="2026-10-01T00:00:00+02:00", municipal="OSLO"):
    return {"uuid": uuid, "status": status, "sistEndret": "2026-10-01T00:00:00",
            "ad_content": {"title": title, "published": published, "expires": expires,
                           "link": f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}",
                           "workLocations": [{"municipal": municipal}],
                           "employer": {"name": name, "orgnr": orgnr}}}


class FakeNav(NavLive):
    def __init__(self, companies, details, parents=None, subunits=None, **kw):
        super().__init__(companies, subunits=subunits, now=NOW, **kw)
        self._details = details
        self._parents = parents or {}
        self.urls = []

    def _get(self, url, headers=None, timeout=None):
        with self._lock:
            if self.requests >= self.max_requests:
                return 0, b""
            self.requests += 1
        self.urls.append(url)
        if "/feedentry/" in url:
            uuid = url.rsplit("/", 1)[-1]
            return (200, json.dumps(self._details[uuid]).encode()) if uuid in self._details else (404, b"")
        if "underenheter" in url:
            sub = url.rsplit("/", 1)[-1]
            return (200, json.dumps({"overordnetEnhet": self._parents[sub]}).encode()) if sub in self._parents else (404, b"")
        return 404, b""


def _run(details, active, parents=None, subunits=None, companies=(("111111111", "ACME AS"),), **kw):
    nav = FakeNav(list(companies), details, parents, subunits, **kw)
    nav._match(active, {}, deadline=time.monotonic() + 60)
    return nav._index


# -- ownership: exact orgnr, or a registered sub-unit of the company ----------

def test_publishes_exact_orgnr():
    idx = _run({"u1": _detail("u1", "111111111")}, {"u1": "Acme AS"})
    assert list(idx) == ["111111111"] and idx["111111111"][0]["title"] == "Snekker"


def test_subunit_from_bulk_maps_to_parent_without_a_request():
    nav = FakeNav([("111111111", "ACME AS")], {"u1": _detail("u1", "222222222", name="ACME AVD BERGEN")},
                  subunits={"222222222": ("111111111", "ACME AVD BERGEN")})
    nav._match({"u1": "Acme avd Bergen"}, {}, deadline=time.monotonic() + 60)
    assert nav._index["111111111"][0]["employer_orgnr"] == "222222222"
    assert not any("underenheter" in u for u in nav.urls)


def test_subunit_name_selects_the_ad_for_opening():
    # The ad names only the workplace (sub-unit), not the legal entity.
    idx = _run({"u1": _detail("u1", "222222222", name="Kafe Sentrum")}, {"u1": "Kafe Sentrum"},
               subunits={"222222222": ("111111111", "KAFE SENTRUM")})
    assert idx["111111111"][0]["employer_orgnr"] == "222222222"


def test_live_parent_lookup_only_without_bulk():
    idx = _run({"u1": _detail("u1", "222222222")}, {"u1": "Acme AS"}, parents={"222222222": "111111111"})
    assert idx["111111111"][0]["employer_orgnr"] == "222222222"


def test_same_name_other_company_is_never_published():
    idx = _run({"u1": _detail("u1", "333333333")}, {"u1": "ACME AS"}, parents={"333333333": "999999999"})
    assert idx == {}
    # With the bulk loaded, an unknown orgnr is not ours (no live lookup either).
    idx = _run({"u1": _detail("u1", "333333333")}, {"u1": "ACME AS"},
               subunits={"444444444": ("111111111", "ACME LAGER")})
    assert idx == {}


def test_subunit_of_another_parent_is_never_published():
    subunits = {"222222222": ("111111111", "ACME")}  # only sub-units of batch parents are loaded
    nav = FakeNav([("111111111", "ACME AS")], {}, subunits=subunits)
    assert nav.owner_of("222222222") == "111111111"
    assert nav.owner_of("555555555") is None


# -- filters -------------------------------------------------------------------

def test_inactive_anonymous_expired_and_open_applications_are_skipped():
    details = {"u1": _detail("u1", "111111111", status="INACTIVE"),
               "u2": _detail("u2", "111111111", name="Anonym arbeidsgiver"),
               "u3": _detail("u3", "111111111", expires="2026-11-01T00:00:00+01:00"),
               "u4": _detail("u4", "111111111", title="Åpen søknad – Acme"),
               "u5": _detail("u5", "")}
    active = {u: "Acme AS" for u in details}
    assert _run(details, active) == {}


def test_single_vacancy_title_filter():
    assert is_single_vacancy("Snekker til prosjekt i Bergen")
    assert not is_single_vacancy("Åpen søknad")
    assert not is_single_vacancy("Registrer deg i vår talentbank")


def test_reposted_vacancy_is_deduplicated_keeping_latest():
    jobs = [{"job_id": "a", "orgnr": "1", "employer_orgnr": "2", "title": "Kokk", "municipal": "OSLO", "published": "2026-10-01"},
            {"job_id": "b", "orgnr": "1", "employer_orgnr": "2", "title": "KOKK", "municipal": "Oslo", "published": "2026-10-20"},
            {"job_id": "c", "orgnr": "1", "employer_orgnr": "2", "title": "Kokk", "municipal": "BERGEN", "published": "2026-10-05"}]
    kept = dedupe_jobs(jobs)
    assert [j["job_id"] for j in kept] == ["c", "b"]


# -- candidate selection + budget ---------------------------------------------

def test_prefix_candidates_only_for_distinctive_names():
    nav = FakeNav([("111111111", "FJORDTRANSPORT AS"), ("222222222", "BYGG AS")], {})
    active = {"u1": "Fjordtransport Vest AS", "u3": "Annet AS",
              **{f"b{i}": f"Bygg og Anlegg {i} AS" for i in range(30)}}
    cands = {uuid: (tier, orgs) for tier, uuid, orgs in nav.candidates_for(active)}
    assert cands["u1"] == (1, {"111111111"})
    # "bygg" is too short to prefix-match and too common a lead word to open.
    assert set(cands) == {"u1"}


def test_rare_shared_lead_words_select_a_sibling_workplace_name():
    nav = FakeNav([("111111111", "LÆRINGSVERKSTEDET HAUGERÅSEN BARNEHAGE AS")], {})
    active = {"u1": "Læringsverkstedet Haugeråsen Idrettsbarnehage AS",
              **{f"l{i}": f"Læringsverkstedet Sted{i} barnehage" for i in range(40)}}
    cands = {uuid: (tier, orgs) for tier, uuid, orgs in nav.candidates_for(active)}
    assert cands == {"u1": (3, {"111111111"})}  # the chain's other kindergartens are not opened


def test_company_name_inside_workplace_name_is_a_candidate():
    nav = FakeNav([("111111111", "BACKSTUBE AS")], {})
    active = {"u1": "Dely AS Backstube Stavanger Stasjon", "u2": "Backstuben Kafe"}
    cands = {uuid: (tier, orgs) for tier, uuid, orgs in nav.candidates_for(active)}
    assert cands == {"u1": (2, {"111111111"})}  # whole words only: "backstuben" is not "backstube"


def test_generic_prefix_key_is_not_opened():
    nav = FakeNav([("111111111", "NORDLANDSBUSS AS")], {})
    active = {f"u{i}": f"Nordlandsbuss rute {i} AS" for i in range(40)}
    assert nav.candidates_for(active) == []


def test_request_budget_is_never_exceeded():
    details = {f"u{i}": _detail(f"u{i}", "111111111", title=f"Stilling {i}") for i in range(30)}
    nav = FakeNav([("111111111", "ACME AS")], details, max_requests=5, max_details_per_company=50)
    nav._match({u: "Acme AS" for u in details}, {}, deadline=time.monotonic() + 60)
    assert nav.requests == 5 and len(nav._index["111111111"]) == 5


def test_per_company_detail_cap():
    details = {f"u{i}": _detail(f"u{i}", "111111111", title=f"Stilling {i}") for i in range(30)}
    idx = _run(details, {u: "Acme AS" for u in details}, max_details_per_company=10)
    assert len(idx["111111111"]) == 10


# -- feed walk ---------------------------------------------------------------

class FeedNav(NavLive):
    """Serves a synthetic feed: one item per hour across 10 days, 50 items/page."""

    def __init__(self, items, **kw):
        super().__init__([("111111111", "ACME AS")], now=NOW, **kw)
        self.items = items  # chronological list of (uuid, status, modified)

    def _get(self, url, headers=None, timeout=None):
        with self._lock:
            self.requests += 1
        headers = headers or {}
        if "If-Modified-Since" in headers:
            from email.utils import parsedate_to_datetime
            since = parsedate_to_datetime(headers["If-Modified-Since"])
            start = next((i for i, it in enumerate(self.items) if it[2] >= since), len(self.items))
        else:
            start = int(url.rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else 0
        page = self.items[start:start + 50]
        body = {"items": [{"id": u, "date_modified": m.isoformat(),
                           "_feed_entry": {"uuid": u, "status": s, "businessName": "Acme AS"}} for u, s, m in page],
                "next_url": f"/api/v1/feed/{start + 50}" if start + 50 < len(self.items) else None}
        return 200, json.dumps(body).encode()


def test_parallel_cursors_rebuild_the_active_set():
    items = []
    t0 = NOW - timedelta(days=10)
    for h in range(240):
        items.append((f"ad{h}", "ACTIVE", t0 + timedelta(hours=h)))
    # Ads 0-99 later become INACTIVE (expiry); the latest entry wins.
    for h in range(100):
        items.append((f"ad{h}", "INACTIVE", t0 + timedelta(hours=240, minutes=h // 10)))
    items.sort(key=lambda it: it[2])
    for cursors in (1, 4):
        nav = FeedNav(items, days=11, cursors=cursors, max_requests=1000)
        active = nav._active_set({}, deadline=time.monotonic() + 60)
        assert set(active) == {f"ad{h}" for h in range(100, 240)}, cursors


# -- sub-unit bulk ---------------------------------------------------------------

def test_load_subunits_filters_to_batch_parents(tmp_path):
    csv_text = ('"organisasjonsnummer","navn","overordnetEnhet"\n'
                '"222222222","ACME AVD BERGEN","111111111"\n'
                '"333333333","ANNET AS AVD","999999999"\n')
    path = tmp_path / "underenheter.csv.gz"
    path.write_bytes(gzip.compress(csv_text.encode("utf-8")))
    assert load_subunits(path, {"111111111"}) == {"222222222": ("111111111", "ACME AVD BERGEN")}
    assert load_subunits(csv_text.encode("utf-8"), {"111111111"}) == {"222222222": ("111111111", "ACME AVD BERGEN")}


# -- observation -----------------------------------------------------------------

def test_observation_cites_public_ad_page_and_retrieval_time():
    job, reason = NavLive.parse_detail("u1", _detail("u1", "222222222"), now=NOW)
    assert reason == "ok"
    job = {**job, "orgnr": "111111111", "employer_orgnr": "222222222", "retrieved_at": "2026-11-09T12:00:01Z"}
    obs = nav_jobs.observations_for({"111111111": [job]}, "111111111", retrieved_at="2026-11-09T11:00:00Z")[0]
    assert obs["source_url"] == "https://arbeidsplassen.nav.no/stillinger/stilling/u1"
    assert obs["retrieved_at"] == "2026-11-09T12:00:01Z"
    assert obs["platform"] == "job_board" and obs["signal_type"] == "job_posting"
    assert obs["identity_proof"][0]["value"] == "222222222"
    assert obs["identity_proof"][0]["registered_subunit_of"] == "111111111"
    assert publishable_observation(obs)
