"""Adversarial edge cases for the news / jobs / social second pass (2 Nov 2026)."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bs4 import BeautifulSoup  # noqa: E402

from norway_company_agent.content_quality import (  # noqa: E402
    apply_content_quality, careers_vacancy, classify_job, classify_news, is_generic_headline, parse_date)
from norway_company_agent.website import html_dated_items, html_job_ads, normalize_social_url  # noqa: E402

CO = "GASSNOVA SF"
TODAY = dt.date(2026, 11, 2)


def art(headline, url, date="2026-06-04"):
    return {"headline": headline, "url": url, "date_published": date}


# --- dates -------------------------------------------------------------------

def test_parse_norwegian_and_english_dates():
    assert parse_date("3. mars 2025") == dt.date(2025, 3, 3)
    assert parse_date("17. mai 2024") == dt.date(2024, 5, 17)
    assert parse_date("1. desember 2026") == dt.date(2026, 12, 1)
    assert parse_date("3 March 2025") == dt.date(2025, 3, 3)
    assert parse_date("March 3, 2025") == dt.date(2025, 3, 3)
    assert parse_date("05.10.2026") == dt.date(2026, 10, 5)  # day first in Norway
    assert parse_date("2026-06-04T10:00:00+02:00") == dt.date(2026, 6, 4)
    assert parse_date("Publisert 12.01.25") == dt.date(2025, 1, 12)


def test_partial_or_invalid_dates_are_not_dates():
    assert parse_date("2025") is None
    assert parse_date("mars 2025") is None
    assert parse_date("Invalid date") is None
    assert parse_date("31.02.2025") is None


def test_news_needs_a_real_full_date():
    assert "full calendar date" in classify_news(art("Gassnova nyhet", "https://gassnova.no/nyheter/a", "2025"), CO, TODAY)
    assert "full calendar date" in classify_news(art("Gassnova nyhet", "https://gassnova.no/nyheter/a", "Invalid Date"), CO, TODAY)
    assert classify_news(art("Gassnova nyhet", "https://gassnova.no/nyheter/a", "3. mars 2025"), CO, TODAY) is None


def test_future_and_placeholder_dates_are_rejected():
    assert "future" in classify_news(art("Gassnova nyhet", "https://gassnova.no/nyheter/a", "2027-05-01"), CO, TODAY)
    assert "placeholder" in classify_news(art("Gassnova nyhet", "https://gassnova.no/nyheter/a", "1970-01-01"), CO, TODAY)
    # a day of clock skew is fine
    assert classify_news(art("Gassnova nyhet", "https://gassnova.no/nyheter/a", "2026-11-03"), CO, TODAY) is None


def test_old_but_real_news_is_kept():
    assert classify_news(art("Gassnova etablert", "https://gassnova.no/nyheter/etablert", "2008-01-01"), CO, TODAY) is None


def test_written_date_is_not_a_headline():
    assert is_generic_headline("3. mars 2025")
    assert is_generic_headline("Mandag 3. mars 2025")
    assert is_generic_headline("March 3, 2025")
    assert not is_generic_headline("Gassnova åpner kontor 3. mars")


# --- site pages ----------------------------------------------------------------

def test_cookie_privacy_terms_pages_rejected_even_with_long_slugs():
    assert "privacy" in classify_news(art("Cookie policy", "https://gassnova.no/en/cookie-policy"), CO, TODAY)
    assert "privacy" in classify_news(art("Personvernerklæring for Gassnova", "https://gassnova.no/personvernerklaering"), CO, TODAY)
    assert "privacy" in classify_news(art("Gassnova salgsbetingelser", "https://gassnova.no/nyheter/salgsbetingelser"), CO, TODAY)
    assert "privacy" in classify_news(art("Privacy notice", "https://gassnova.no/news/privacy-notice"), CO, TODAY)


def test_english_press_release_kept_and_english_guide_rejected():
    assert classify_news(art("Gassnova signs agreement with Northern Lights",
                             "https://gassnova.no/en/news/agreement"), CO, TODAY) is None
    assert "guide" in classify_news(art("How to choose a heat pump", "https://x.no/en/blog/heat-pump"), CO, TODAY)
    assert "opinion" in classify_news(art("Opinion: CCS needs a price", "https://gassnova.no/en/news/opinion-ccs"), CO, TODAY)


# --- duplicates ------------------------------------------------------------------

def test_duplicates_under_different_urls_and_date_formats_are_dropped():
    site = {"value": {"news_articles": [
        art("Gassnova vinner pris", "https://gassnova.no/nyheter/pris", "2026-06-04"),
        art("Gassnova vinner pris", "http://www.gassnova.no/nyheter/pris/?utm_source=x", "2026-06-04"),
        art("Gassnova vinner pris", "https://gassnova.no/nyheter/pris-2", "2026-06-04T09:00:00+02:00"),
        art("Gassnova vinner pris", "https://gassnova.no/nyheter/pris-3", "04.06.2026"),
        art("Gassnova vinner ny pris", "https://gassnova.no/nyheter/pris-4", "2026-06-05"),
    ]}}
    out = apply_content_quality({"name": CO}, site, TODAY)["value"]
    assert [a["url"] for a in out["news_articles"]] == ["https://gassnova.no/nyheter/pris",
                                                         "https://gassnova.no/nyheter/pris-4"]
    assert all("duplicate" in a["excluded_reason"] for a in out["excluded_news_articles"])


def test_kept_written_dates_are_normalised_to_iso():
    site = {"value": {"news_articles": [art("Gassnova nyhet", "https://gassnova.no/nyheter/a", "3. mars 2025")]}}
    kept = apply_content_quality({"name": CO}, site, TODAY)["value"]["news_articles"][0]
    assert kept["date_published"] == "2025-03-03" and kept["date_published_raw"] == "3. mars 2025"


# --- jobs ------------------------------------------------------------------------

def test_expired_and_stale_job_postings_are_excluded():
    assert "deadline passed" in classify_job({"title": "Ingeniør", "valid_through": "2025-03-01"}, TODAY)
    assert classify_job({"title": "Ingeniør", "valid_through": "2026-12-01", "date_posted": "2026-01-01"}, TODAY) is None
    assert "older than" in classify_job({"title": "Ingeniør", "date_posted": "2024-01-01"}, TODAY)
    assert "future" in classify_job({"title": "Ingeniør", "date_posted": "2027-01-01"}, TODAY)
    assert classify_job({"title": "Ingeniør", "url": "https://x.no/stilling/ing"}, TODAY) is None  # undated link


def test_apply_moves_expired_jobs_to_excluded_with_reason():
    site = {"value": {"job_postings": [{"title": "Ingeniør", "valid_through": "2025-03-01"},
                                       {"title": "Prosjektleder", "valid_through": "2026-12-01"}]}}
    out = apply_content_quality({"name": CO}, site, TODAY)["value"]
    assert [j["title"] for j in out["job_postings"]] == ["Prosjektleder"]
    assert "deadline passed" in out["excluded_job_postings"][0]["excluded_reason"]


def test_careers_page_with_past_deadline_or_standing_invitation_has_no_vacancy():
    assert careers_vacancy("Prosjektleder. Søknadsfrist: 3. mars 2025", today=TODAY)[0] is False
    assert careers_vacancy("Prosjektleder. Søknadsfrist: 1. desember 2026", today=TODAY)[0] is True
    assert careers_vacancy("Ledige stillinger. Vi har ikke ledige stillinger nå, men vi søker alltid flinke folk.")[0] is False
    assert careers_vacancy("Vi søker alltid etter dyktige medarbeidere. Send en åpen søknad.")[0] is False
    assert careers_vacancy("There are currently no open positions.")[0] is False
    assert careers_vacancy("Vi søker en erfaren elektriker til fast stilling")[0] is True


def test_listing_page_and_open_application_links_are_not_job_ads():
    html = """<main><h1>Ledige stillinger</h1>
      <a href="/karriere/ledige-stillinger">Se våre stillinger her</a>
      <a href="/karriere/apen-soknad">Send oss en søknad</a>
      <a href="/karriere/hvorfor-jobbe-hos-oss">Hvorfor jobbe hos oss</a>
      <a href="https://acme.teamtailor.com/jobs">Alle våre jobber</a>
      <a href="https://acme.teamtailor.com/connect">Bli med i talentnettverket</a></main>"""
    assert html_job_ads(BeautifulSoup(html, "lxml"), "https://acme.no/karriere") == []


def test_single_job_ads_still_found():
    html = """<main><a href="/karriere/prosjektleder-bygg">Prosjektleder bygg</a>
      <a href="https://acme.teamtailor.com/jobs/12345-elektriker">Elektriker</a></main>"""
    titles = [a["title"] for a in html_job_ads(BeautifulSoup(html, "lxml"), "https://acme.no/karriere")]
    assert titles == ["Prosjektleder bygg", "Elektriker"]


# --- dated items in plain HTML -----------------------------------------------------

def test_list_wrapper_date_is_not_spread_to_every_link():
    html = """<div class="list"><span>12.01.2026</span>
      <a href="/nyheter/a">Første sak</a><a href="/nyheter/b">Andre sak</a><a href="/nyheter/c">Tredje sak</a></div>"""
    assert html_dated_items(BeautifulSoup(html, "lxml"), "https://acme.no/nyheter") == []


def test_written_norwegian_date_in_card_is_read():
    html = """<ul><li><a href="/nyheter/kontrakt">Acme vinner kontrakt</a><span>3. mars 2025</span></li>
      <li><a href="/nyheter/ny-leder">Acme ansetter ny leder</a><span>17. februar 2025</span></li></ul>"""
    items = html_dated_items(BeautifulSoup(html, "lxml"), "https://acme.no/nyheter")
    assert [(i["headline"], i["date_published"]) for i in items] == [
        ("Acme vinner kontrakt", "2025-03-03"), ("Acme ansetter ny leder", "2025-02-17")]


# --- social -------------------------------------------------------------------------

def test_share_buttons_tracking_pixels_and_platform_pages_are_not_profiles():
    for url in ("https://www.facebook.com/sharer/sharer.php?u=https://acme.no",
                "https://www.facebook.com/share/abc123",
                "https://www.facebook.com/tr?id=123&ev=PageView",
                "https://www.facebook.com/facebook",
                "https://www.facebook.com/hashtag/acme",
                "https://www.facebook.com/pages/category/Company",
                "https://www.instagram.com/accounts/login/",
                "https://www.instagram.com/instagram",
                "https://twitter.com/intent/tweet?text=x",
                "https://twitter.com/hashtag/acme",
                "https://x.com/twitter",
                "https://www.linkedin.com/shareArticle?mini=true&url=x",
                "https://www.linkedin.com/company/linkedin",
                "https://www.youtube.com/@YouTube"):
        assert normalize_social_url(url) is None, url


def test_real_company_profiles_still_normalise():
    assert normalize_social_url("https://www.facebook.com/acmebygg/")["url"] == "https://facebook.com/acmebygg"
    assert normalize_social_url("https://no.linkedin.com/company/acme-bygg-as/about/")["url"] == \
        "https://linkedin.com/company/acme-bygg-as"
    assert normalize_social_url("https://www.instagram.com/acmebygg")["url"] == "https://instagram.com/acmebygg"


def test_web_agency_footer_profile_fails_identity_gate():
    from norway_company_agent.identity import assess_social_identity
    profile = {"name": "ACME BYGG AS"}
    agency = assess_social_identity(profile, {"platform": "linkedin", "url": "https://linkedin.com/company/nettbyraet-webdesign"})
    own = assess_social_identity(profile, {"platform": "linkedin", "url": "https://linkedin.com/company/acme-bygg-as"})
    assert not agency["publishable"] and own["publishable"]
