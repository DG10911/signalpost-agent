"""Tests for the added company-site extractors (JSON-LD facts, job postings,
structured social) and the identity-gate quarantine of those new fields."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent import website  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402


def test_jsonld_facts_promotes_company_reported_fields():
    orgs = [{
        "@type": "Organization",
        "name": "Example AS",
        "telephone": "+47 22 00 00 00",
        "email": "mailto:post@example.no",
        "foundingDate": "2004-01-01",
        "numberOfEmployees": {"@type": "QuantitativeValue", "value": "42"},
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Storgata 1",
            "postalCode": "0155",
            "addressLocality": "Oslo",
            "addressCountry": "NO",
        },
    }]
    facts = website._jsonld_facts(orgs)
    assert facts["telephone"] == "+47 22 00 00 00"
    assert facts["email"] == "post@example.no"          # mailto: stripped
    assert facts["founding_date"] == "2004-01-01"
    assert facts["employees_reported"] == "42"           # unwrapped QuantitativeValue
    assert facts["address"]["locality"] == "Oslo"
    assert facts["address"]["postal_code"] == "0155"


def test_jsonld_jobpostings_collected_and_titled():
    metadata = {"json-ld": [
        {"@type": "JobPosting", "title": "Senior Engineer", "datePosted": "2026-09-01",
         "employmentType": "FULL_TIME", "hiringOrganization": {"@type": "Organization", "name": "Example AS"},
         "url": "https://example.no/jobs/1"},
        {"@type": "JobPosting", "datePosted": "2026-09-02"},  # no title -> dropped
    ]}
    postings = website._jsonld_jobpostings(metadata)
    assert len(postings) == 1
    assert postings[0]["title"] == "Senior Engineer"
    assert postings[0]["hiring_organization"] == "Example AS"


def test_jsonld_articles_collected_with_dates():
    metadata = {"json-ld": [
        {"@type": "NewsArticle", "headline": "We opened an office", "datePublished": "2026-06-01",
         "url": "https://example.no/news/1"},
        {"@type": "BlogPosting", "headline": "No date here"},  # dropped: no date
    ]}
    articles = website._jsonld_articles(metadata)
    assert len(articles) == 1
    assert articles[0]["date_published"] == "2026-06-01"


def test_dedupe_articles():
    arts = [
        {"headline": "A", "date_published": "2026-01-01"},
        {"headline": "a", "date_published": "2026-01-01"},  # dup
        {"headline": "A", "date_published": "2026-02-01"},  # different date
    ]
    assert len(website._dedupe_articles(arts)) == 2


def test_dedupe_job_postings():
    postings = [
        {"title": "Engineer", "url": "https://x/1"},
        {"title": "engineer", "url": "https://x/1"},  # dup (case-insensitive)
        {"title": "Designer", "url": "https://x/2"},
    ]
    assert len(website._dedupe_job_postings(postings)) == 2


def test_structured_social_links_from_sameas():
    links = website.structured_social_links([
        {"@type": "Organization", "sameAs": [
            "https://www.linkedin.com/company/example-as",
            "https://twitter.com/exampleas",
            "https://example.no/not-social",
        ]}
    ])
    platforms = {item["platform"] for item in links}
    assert "linkedin" in platforms
    assert "x" in platforms  # twitter.com canonicalised to x


def test_gate_quarantines_new_fields_when_not_exact():
    # A profile whose website does not resolve to the entity must not publish
    # its structured facts or job postings.
    profile = {"organisation_number": "999999999", "name": "TOTALLY DIFFERENT NAME AS",
               "evidence": {}}
    website_record = {
        "status": "available",
        "value": {
            "title": "Some Unrelated Site",
            "description": "unrelated",
            "main_text_excerpt": "nothing matching here",
            "social_links": [{"platform": "linkedin", "url": "https://linkedin.com/company/unrelated"}],
            "structured_facts": {"telephone": "+47 00 00 00 00"},
            "job_postings": [{"title": "Role", "url": "https://x/1"}],
            "news_articles": [{"headline": "News", "date_published": "2026-01-01"}],
        },
    }
    result = apply_website_identity_gate(profile, website_record)
    assert result["assessment"]["publishable"] is False
    value = result["website"]["value"]
    assert value["structured_facts"] == {}
    assert value["job_postings"] == []
    assert value["news_articles"] == []
    assert result["quarantined_job_postings"] == 1
    assert result["quarantined_news_articles"] == 1
    assert value["quarantined_structured_facts"]["telephone"] == "+47 00 00 00 00"


def test_html_dated_items_from_time_tags():
    from bs4 import BeautifulSoup
    html = """
    <ul><li><time datetime="2025-09-22">22.09.2025</time>
      <a href="/nyheter/bedre-sosial-funksjon">Bedre sosial funksjon etter hjerneskade</a></li></ul>
    """
    items = website.html_dated_items(BeautifulSoup(html, "lxml"), "https://example.no/aktuelt")
    assert items and items[0]["headline"] == "Bedre sosial funksjon etter hjerneskade"
    assert items[0]["date_published"] == "2025-09-22"
    assert items[0]["url"] == "https://example.no/nyheter/bedre-sosial-funksjon"


def test_html_dated_items_fallback_on_article_links():
    from bs4 import BeautifulSoup
    html = '<article><a href="/aktuelt/ny-kunde">Ny kunde</a><span>12.03.2025</span></article>'
    items = website.html_dated_items(BeautifulSoup(html, "lxml"), "https://example.no/")
    assert any(i["headline"] == "Ny kunde" for i in items)


def test_is_careers_page_detects_norwegian_and_english():
    assert website.is_careers_page("https://x.no/careers")
    assert website.is_careers_page("https://x.no/karriere")
    assert website.is_careers_page("https://x.no/om-oss/ledige-stillinger")
    assert not website.is_careers_page("https://x.no/kontakt")


def _verified_profile(value):
    value = dict(value)
    value["identity_assessment"] = {"publishable": True, "status": "exact", "score": 1.0}
    return {"organisation_number": "923609016",
            "evidence": {"website": {"status": "available", "value": value,
                                     "source_url": "https://example.no", "retrieved_at": "2026-10-01T00:00:00Z",
                                     "content_sha256": "a" * 64}}}


def test_careers_page_becomes_a_job_posting_observation():
    from norway_company_agent.external_observations import build_observations
    from norway_company_agent.external_footprint import publishable_observation
    prof = _verified_profile({"registered_domain": "example.no", "careers_pages": [{"url": "https://example.no/careers", "title": "Ledige stillinger"}]})
    obs = [o for o in build_observations(prof) if o["signal_type"] == "job_posting" and o.get("metrics", {}).get("kind") == "careers_page"]
    assert obs and publishable_observation(obs[0])
    assert obs[0]["organisation_number"] == "923609016"


def test_gate_quarantines_careers_pages_when_not_exact():
    web = {"status": "available", "value": {"careers_pages": [{"url": "https://x.no/careers"}]}}
    out = apply_website_identity_gate({"organisation_number": "923609016", "evidence": {}}, web)
    assert out["website"]["value"]["careers_pages"] == []
    assert out["website"]["value"]["quarantined_careers_pages"]


def test_canonical_probe_urls_stay_on_homepage_domain():
    urls = website._canonical_probe_urls("https://www.equinor.com/")
    assert "https://www.equinor.com/careers" in urls
    assert "https://www.equinor.com/karriere" in urls
    assert all(u.startswith("https://www.equinor.com/") for u in urls)


def test_html_dated_items_ignores_time_only_values():
    from bs4 import BeautifulSoup
    html = '<ul><li><time datetime="08:00">08:00</time><a href="/nyheter/x">Åpningstider</a></li></ul>'
    assert website.html_dated_items(BeautifulSoup(html, "lxml"), "https://x.no/") == []
