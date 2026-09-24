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
