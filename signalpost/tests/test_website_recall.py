"""Website recall rules (9 Nov): more candidates, same exact-entity gates."""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bs4 import BeautifulSoup  # noqa: E402

from norway_company_agent import domain_discovery as dd  # noqa: E402
from norway_company_agent import website  # noqa: E402
from norway_company_agent.site_health import registry_site_problem, repair_homepage  # noqa: E402

OURS = "923609016"
OTHER = "812345702"


def _prof(name="Hansen & Andersen AS", street="Konnerudgata 7", postcode="3045", phone=None, email=None):
    return {"organisation_number": OURS, "name": name, "evidence": {"registry": {"value": {
        "epostadresse": email, "telefon": phone,
        "forretningsadresse.adresse": street, "forretningsadresse.postnummer": postcode}}}}


def _page(url, text, title="", links=None):
    return {"status": "available", "value": {"final_url": url, "title": title, "description": "",
                                             "main_text_excerpt": text, "identity_links": links or []}}


# --- candidates -------------------------------------------------------------------

def test_a_domain_written_in_the_name_is_the_first_guess():
    assert dd.candidate_domains("HOTELL.NO AS")[0] == "hotell.no"


def test_no_guesses_come_before_com_guesses():
    out = dd.candidate_domains("Bergen Rør AS")
    assert out[:4] == ["bergenror.no", "bergen-ror.no", "bergenror.com", "bergen-ror.com"]


def test_unresolvable_guesses_do_not_use_up_the_cap():
    calls = []

    def fetch(url):
        calls.append(url)
        if "bergenror.no" in url:      # does not resolve: no request spent
            return {"status": "blocked", "note": "Hostname did not resolve"}, {"requests": 0}
        return _page(url, f"Bergen Rør AS, org.nr {OURS}"), {"requests": 2}

    rec, diag = dd.discover_website(_prof(name="Bergen Rør AS"), fetch, max_candidates=1)
    assert rec is not None and calls[:2] == ["https://bergenror.no", "https://bergen-ror.no"]
    assert diag["requests"] == 2


def test_the_cap_still_bounds_fetched_guesses():
    def fetch(url):
        return _page(url, "Nothing about anyone"), {"requests": 2}

    rec, diag = dd.discover_website(_prof(name="Bergen Rør AS"), fetch, max_candidates=2)
    assert rec is None and diag["requests"] == 4


# --- registered address -----------------------------------------------------------

def test_name_plus_registered_street_and_postcode_binds():
    page = "Hansen & Andersen – sjømat. Konnerudgata 7, 3045 Drammen"
    rec, _ = dd.discover_website(_prof(), lambda u: (_page(u, page), {"requests": 2}), max_candidates=1)
    assert rec["value"]["discovery_identity"]["method"] == "registry_address_on_discovered_site"


def test_address_without_the_name_does_not_bind():
    page = "Fiskebutikken. Konnerudgata 7, 3045 Drammen"
    rec, _ = dd.discover_website(_prof(), lambda u: (_page(u, page), {"requests": 2}), max_candidates=1)
    assert rec is None


def test_street_without_postcode_or_a_post_box_does_not_bind():
    page = "Hansen & Andersen. Konnerudgata 7, Drammen"
    assert dd.discover_website(_prof(), lambda u: (_page(u, page), {"requests": 2}), max_candidates=1)[0] is None
    box = "Hansen & Andersen. Postboks 123, 3045 Drammen"
    prof = _prof(street="Postboks 123")
    assert dd.discover_website(prof, lambda u: (_page(u, box), {"requests": 2}), max_candidates=1)[0] is None


def test_address_never_overrides_another_org_number():
    page = f"Hansen & Andersen. Konnerudgata 7, 3045 Drammen. Org.nr {OTHER}"
    assert dd.discover_website(_prof(), lambda u: (_page(u, page), {"requests": 2}), max_candidates=1)[0] is None


# --- language start pages ---------------------------------------------------------

def test_a_language_start_page_on_the_same_host_is_still_the_homepage():
    def fetch(url):
        return _page(url.rstrip("/") + "/en", "Welcome. © Example AS"), {"requests": 2}

    rec, _ = dd.discover_website(_prof(name="Example AS"), fetch, max_candidates=1)
    assert rec is not None


def test_a_sub_page_redirect_still_needs_the_org_number():
    def fetch(url):
        return _page(url.rstrip("/") + "/portfolio/example", "Example AS is one of our companies"), {"requests": 2}

    rec, diag = dd.discover_website(_prof(name="Example AS"), fetch, max_candidates=1)
    assert rec is None and diag["verdict"] == "rejected_redirect_without_orgnr"


# --- contact / about / privacy pages ------------------------------------------------

def _site(home_text, pages, links):
    def fetch(url):
        if url in pages:
            return _page(url, pages[url]), {"requests": 2}
        return _page(url, home_text, links=links), {"requests": 2}
    return fetch


def test_the_legal_name_on_the_privacy_page_binds_when_the_homepage_names_the_company():
    fetch = _site("My Knitting Journal – your knitting notebook",
                  {"https://myknittingjournal.no/privacy": "This service is run by My Knitting Journal AS."},
                  ["https://myknittingjournal.no/privacy"])
    rec, diag = dd.discover_website(_prof(name="My Knitting Journal AS"), fetch, max_candidates=1)
    assert rec["value"]["discovery_identity"]["method"] == "full_legal_name_on_discovered_site"
    assert rec["value"]["discovery_identity"]["identity_page"] == "https://myknittingjournal.no/privacy"
    assert diag["requests"] == 4


def test_another_org_number_on_the_contact_page_rejects_the_site():
    fetch = _site("Example – welcome", {"https://example.no/kontakt": f"Example Group AS org.nr {OTHER}"},
                  ["https://example.no/kontakt"])
    rec, diag = dd.discover_website(_prof(name="Example AS"), fetch, max_candidates=1)
    assert rec is None and diag["verdict"] == "rejected_conflicting_orgnr_on_identity_page"


def test_identity_pages_are_not_read_when_the_homepage_does_not_name_the_company():
    fetch = _site("Something else entirely", {"https://example.no/kontakt": f"Example AS org.nr {OURS}"},
                  ["https://example.no/kontakt"])
    rec, diag = dd.discover_website(_prof(name="Example AS"), fetch, max_candidates=1)
    assert rec is None and diag["requests"] == 2


def test_identity_links_are_same_host_contact_about_and_privacy_pages():
    soup = BeautifulSoup('<a href="/kontakt">Kontakt</a><a href="https://other.no/om-oss">x</a>'
                         '<a href="/produkter">Produkter</a><a href="/personvern">Personvern</a>'
                         '<a href="/side">Om oss</a>', "lxml")
    assert website.identity_links("https://www.firma.no/", soup) == [
        "https://www.firma.no/kontakt", "https://www.firma.no/personvern", "https://www.firma.no/side"]


# --- registry homepage typos and www/apex twins -------------------------------------

def test_obvious_registry_homepage_typos_are_repaired():
    assert repair_homepage("wwwrekdaltransport") == "www.rekdaltransport.no"
    assert repair_homepage("wwwexample.no") == "www.example.no"
    assert repair_homepage("ww.zbartravel.com") == "www.zbartravel.com"
    assert repair_homepage("www,example,no") == "www.example.no"
    assert repair_homepage("www.example.no") is None
    assert repair_homepage("example.no/kontakt") is None
    assert repair_homepage("") is None


def _fake_once(live):
    def once(url, **_):
        host = (urllib.parse.urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
        if host in live:
            return {"status": "available", "value": {"final_url": url}}, {"requests": 2, "bytes": 10, "latencies_ms": [1]}
        return {"status": "blocked", "note": "Hostname did not resolve"}, \
            {"requests": 2, "bytes": 0, "latencies_ms": [1]}
    return once


def test_a_dead_www_host_falls_back_to_the_apex_twin(monkeypatch):
    monkeypatch.setattr(website, "_fetch_website_once", _fake_once({"skottneset.no"}))
    record, metrics = website.fetch_website("www.Skottneset.no")
    assert record["status"] == "available"
    assert record["value"]["final_url"] == "https://skottneset.no/"
    assert record["value"]["host_fallback_from"] == "www.Skottneset.no"
    assert metrics["requests"] == 4


def test_a_typo_is_repaired_only_after_the_address_as_written_fails(monkeypatch):
    monkeypatch.setattr(website, "_fetch_website_once", _fake_once({"www.rekdaltransport.no"}))
    record, _ = website.fetch_website("wwwrekdaltransport")
    assert record["value"]["registry_url_repaired"] == "wwwrekdaltransport"


def test_server_errors_and_sub_pages_are_not_retried_on_the_twin(monkeypatch):
    calls = []

    def once(url, **_):
        calls.append(url)
        note = "HTTP 500" if "kontakt" not in url else "URLError: reset"
        return {"status": "source_error", "note": note}, {"requests": 2, "bytes": 0, "latencies_ms": []}

    monkeypatch.setattr(website, "_fetch_website_once", once)
    record, _ = website.fetch_website("www.example.no")
    assert record["status"] == "source_error" and len(calls) == 1
    website.fetch_website("https://www.example.no/kontakt")
    assert len(calls) == 2


def test_a_missing_homepage_root_is_tried_on_the_twin(monkeypatch):
    def once(url, **_):
        if url.startswith("https://skottneset.no"):
            return {"status": "available", "value": {"final_url": url}}, {"requests": 2, "bytes": 1, "latencies_ms": []}
        return {"status": "not_found", "note": "HTTP 404"}, {"requests": 2, "bytes": 0, "latencies_ms": []}

    monkeypatch.setattr(website, "_fetch_website_once", once)
    record, _ = website.fetch_website("www.Skottneset.no")
    assert record["value"]["host_fallback_from"] == "www.Skottneset.no"


def test_a_fallback_registry_site_is_published_only_when_identity_confirms_it():
    web = {"status": "available", "value": {"final_url": "https://www.rekdaltransport.no/",
                                            "registry_url_repaired": "wwwrekdaltransport",
                                            "identity_assessment": {"publishable": False}}}
    assert registry_site_problem("wwwrekdaltransport", web, "Rekdal Transport AS")
    web["value"]["identity_assessment"]["publishable"] = True
    # Publishable: output_contract publishes the crawled final_url before ever asking.
    assert "does not load as written" not in (registry_site_problem("wwwrekdaltransport", web, "Rekdal Transport AS") or "")


# --- shared registry domain whose homepage title is this company -------------------

def _shared_profile(title, monkeypatch):
    from norway_company_agent import identity
    monkeypatch.setattr(identity, "DOMAIN_OWNERS", {"brenden.no": ["913168240", "976015347", OURS]})
    return {"organisation_number": OURS, "name": "BRENDEN & CO STILLASUTLEIE AS", "evidence": {
        "registry": {"value": {"hjemmeside": "www.brenden.no"}},
        "website": {"status": "available", "source_url": "https://www.brenden.no/", "value": {
            "requested_url": "https://www.brenden.no/", "final_url": "https://www.brenden.no/", "title": title,
            "description": "", "main_text_excerpt": "Stillas til bolig og næring i hele Østlandet. " * 5,
            "pages": []}}}}


def test_a_shared_domain_whose_title_is_our_full_legal_name_is_our_site(monkeypatch):
    from norway_company_agent.identity import assess_website_identity
    result = assess_website_identity(_shared_profile("Ledende på stillas | Brenden & CO Stillasutleie AS", monkeypatch))
    assert result["publishable"] and not result["group_site"]


def test_a_shared_domain_without_our_name_in_the_title_stays_a_group_site(monkeypatch):
    from norway_company_agent.identity import assess_website_identity
    result = assess_website_identity(_shared_profile("Brenden – stillas, miljø og rådgivning", monkeypatch))
    assert not result["publishable"] and result["group_site"]
