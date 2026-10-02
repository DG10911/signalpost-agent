"""Exact-entity NAV homepage candidates: tried first, but still pass the same
org-number / registry-contact gate as any other discovered site — an official
self-declaration is a strong candidate, never a precision bypass."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent import domain_discovery as dd  # noqa: E402

OURS = "923609016"
OTHER = "812345702"
NAV_URL = "https://nav-declared-company.no"


def _fetch(text_by_host):
    calls = []

    def f(url):
        calls.append(url)
        text = ""
        for host, value in text_by_host.items():
            if host in url:
                text = value
                break
        return ({"status": "available", "value": {"final_url": url, "main_text_excerpt": text,
                                                    "title": "", "description": ""}},
                {"requests": 2})

    return f, calls


def test_nav_homepage_tried_first_and_accepted_on_orgnr():
    fetch, calls = _fetch({NAV_URL: f"Kontakt. Org.nr {OURS}. Example AS"})
    prof = {"organisation_number": OURS, "name": "Example AS"}
    rec, diag = dd.discover_website(prof, fetch, max_candidates=3, extra_candidates=[NAV_URL])
    assert rec is not None and diag["verdict"] == "verified_orgnr"
    assert calls[0] == NAV_URL                      # exact-entity candidate first
    assert diag["candidates"][0] == NAV_URL


def test_nav_homepage_still_rejected_on_conflicting_orgnr():
    fetch, calls = _fetch({NAV_URL: f"Example AS. Org.nr {OTHER}."})
    prof = {"organisation_number": OURS, "name": "Example AS"}
    rec, diag = dd.discover_website(prof, fetch, max_candidates=1, extra_candidates=[NAV_URL])
    assert rec is None and diag["verdict"] == "rejected_conflicting_orgnr"


def test_extra_candidate_without_scheme_gets_https():
    fetch, calls = _fetch({NAV_URL: f"Org.nr {OURS}"})
    prof = {"organisation_number": OURS, "name": "Example AS"}
    rec, diag = dd.discover_website(prof, fetch, max_candidates=0, extra_candidates=["nav-declared-company.no"])
    assert rec is not None
    assert calls[0] == "https://nav-declared-company.no"
