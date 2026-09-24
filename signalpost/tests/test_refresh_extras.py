"""Refresh subsystem must (a) detect real changes to the added coverage fields
and (b) never fire a false change merely because list ordering differs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.refresh import diff_profile  # noqa: E402
from norway_company_agent import website  # noqa: E402


def _profile(org, *, jobs=(), news=(), social=(), facts=None):
    return {
        "organisation_number": org,
        "evidence": {
            "website": {
                "status": "available",
                "source_url": "https://example.no/",
                "retrieved_at": "2026-01-01T00:00:00Z",
                "content_sha256": "x",
                "value": {
                    "job_postings": list(jobs),
                    "news_articles": list(news),
                    "social_links": list(social),
                    "structured_facts": facts or {},
                },
            }
        },
    }


def test_reordered_lists_are_not_a_change():
    a = _profile("1",
                 jobs=[{"title": "A", "url": "u1", "date_posted": "2026-01"},
                       {"title": "B", "url": "u2", "date_posted": "2026-02"}])
    # Same jobs, opposite order -> after dedupe/sort both canonicalise identically.
    b = _profile("1",
                 jobs=[{"title": "B", "url": "u2", "date_posted": "2026-02"},
                       {"title": "A", "url": "u1", "date_posted": "2026-01"}])
    a["evidence"]["website"]["value"]["job_postings"] = website._dedupe_job_postings(a["evidence"]["website"]["value"]["job_postings"])
    b["evidence"]["website"]["value"]["job_postings"] = website._dedupe_job_postings(b["evidence"]["website"]["value"]["job_postings"])
    assert diff_profile(a, b) == []


def test_added_job_posting_is_detected():
    a = _profile("1", jobs=[{"title": "A", "url": "u1", "date_posted": "2026-01"}])
    b = _profile("1", jobs=[{"title": "A", "url": "u1", "date_posted": "2026-01"},
                            {"title": "B", "url": "u2", "date_posted": "2026-02"}])
    changes = diff_profile(a, b)
    fields = {c["field"] for c in changes}
    assert "website.job_postings" in fields
    assert changes[0]["source_url"] == "https://example.no/"
    assert changes[0]["retrieved_at"]


def test_added_news_and_social_detected():
    a = _profile("1")
    b = _profile("1",
                 news=[{"headline": "H", "date_published": "2026-03"}],
                 social=[{"platform": "linkedin", "url": "https://linkedin.com/company/x"}])
    fields = {c["field"] for c in diff_profile(a, b)}
    assert "website.news_articles" in fields
    assert "website.social_links" in fields


def test_idempotent_rerun_has_no_changes():
    a = _profile("1", facts={"telephone": "+47 1"}, social=[{"platform": "x", "url": "https://x.com/y"}])
    assert diff_profile(a, a) == []
