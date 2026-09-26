"""Safe website discovery: accept ONLY on exact org-number presence; hard-reject
conflicting org numbers; never accept name-only (the wrong-company trap)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent import domain_discovery as dd  # noqa: E402

# A valid mod-11 Norwegian org number and a different valid one.
OURS = "923609016"
OTHER = "812345702"


def _valid_check():
    assert dd._valid_orgnr(OURS) and dd._valid_orgnr(OTHER) and not dd._valid_orgnr("123456789")


def test_candidate_generation_deterministic():
    a = dd.candidate_domains("Bergen Rør og Sanitær AS")
    b = dd.candidate_domains("Bergen Rør og Sanitær AS")
    assert a == b
    assert any(d.endswith(".no") for d in a)
    assert all(" " not in d and "ø" not in d for d in a)   # normalised


def _fetch(pages_text):
    def f(url):
        return ({"status": "available", "value": {"final_url": url, "main_text_excerpt": pages_text,
                                                   "title": "", "description": ""}},
                {"requests": 2})
    return f


def test_accept_only_on_exact_orgnr():
    _valid_check()
    prof = {"organisation_number": OURS, "name": "Example AS"}
    rec, diag = dd.discover_website(prof, _fetch(f"Kontakt oss. Org.nr {OURS}. Example AS"), max_candidates=1)
    assert rec is not None and diag["verdict"] == "verified_orgnr"
    assert rec["value"]["discovered"] is True


def test_reject_conflicting_orgnr():
    prof = {"organisation_number": OURS, "name": "Example AS"}
    rec, diag = dd.discover_website(prof, _fetch(f"Example AS. Org.nr {OTHER}."), max_candidates=1)
    assert rec is None
    assert diag["verdict"] == "rejected_conflicting_orgnr"


def test_reject_name_only():
    # Name matches but NO org number on the page -> must NOT bind (wrong-company trap).
    prof = {"organisation_number": OURS, "name": "Example AS"}
    rec, diag = dd.discover_website(prof, _fetch("Welcome to Example AS, the best example."), max_candidates=1)
    assert rec is None
    assert diag["verdict"] == "no_orgnr_match"


def test_orgnr_verdict_helpers():
    assert dd.orgnr_verdict(OURS, {"main_text_excerpt": f"org {OURS}"}) == "match"
    assert dd.orgnr_verdict(OURS, {"main_text_excerpt": f"org {OTHER}"}) == "conflict"
    assert dd.orgnr_verdict(OURS, {"main_text_excerpt": "no numbers here"}) == "none"
    # spaced/dotted org number still recognised
    spaced = f"{OURS[:3]} {OURS[3:6]} {OURS[6:]}"
    assert dd.orgnr_verdict(OURS, {"main_text_excerpt": f"Org.nr {spaced}"}) == "match"
