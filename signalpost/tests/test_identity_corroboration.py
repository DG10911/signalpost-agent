"""§10 corroboration escalation: a review-tier (0.85) name match is promoted to
exact ONLY when an independent registry signal (phone/email-domain/postcode+town)
also appears on the page. Without corroboration it stays review (never promoted)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.identity import assess_website_identity  # noqa: E402


def _profile(*, main_text, registry):
    return {
        "organisation_number": "923609016",
        "name": "Alpha Beta Gamma AS",
        "evidence": {
            "registry": {"status": "available", "value": registry},
            "website": {"status": "available", "source_url": "https://alphabeta.no",
                        "value": {"final_url": "https://alphabeta.no", "title": "Alpha Beta Corp",
                                  "main_text_excerpt": main_text}},
        },
    }


def test_baseline_is_review_without_corroboration():
    # name mostly matches (0.85) but no registry signal on page -> stays review
    p = _profile(main_text="Welcome. We are Gamma division. " + "x" * 120, registry={})
    a = assess_website_identity(p)
    assert a["score"] == 0.85 and a["status"] == "review" and not a["publishable"]


def test_promoted_by_registry_phone():
    p = _profile(main_text="Gamma. Ring oss 22500737 for kontakt. " + "x" * 120,
                 registry={"telefon": "22 50 07 37"})
    a = assess_website_identity(p)
    assert a["score"] == 0.95 and a["status"] == "exact" and a["publishable"]
    assert "registry_phone" in a["corroboration"]


def test_promoted_by_registry_email_domain():
    p = _profile(main_text="Gamma. Kontakt: post@alphabeta.no. " + "x" * 120,
                 registry={"epostadresse": "post@alphabeta.no"})
    a = assess_website_identity(p)
    assert a["publishable"] and "registry_email_domain" in a["corroboration"]


def test_promoted_by_postcode_and_town():
    p = _profile(main_text="Gamma. Besøk oss i 0155 Oslo. " + "x" * 120,
                 registry={"forretningsadresse.postnummer": "0155", "forretningsadresse.poststed": "OSLO"})
    a = assess_website_identity(p)
    assert a["publishable"] and "registry_postcode_town" in a["corroboration"]


def test_weak_match_not_promoted_even_with_signal():
    # Only ONE name token overlaps (weak, <0.75 ratio) -> corroboration must NOT fire.
    p = {
        "organisation_number": "923609016", "name": "Zeta Delta Epsilon Omega AS",
        "evidence": {
            "registry": {"status": "available", "value": {"telefon": "22500737"}},
            "website": {"status": "available", "source_url": "https://x.no",
                        "value": {"final_url": "https://x.no", "title": "Zeta Widgets",
                                  "main_text_excerpt": "Ring 22500737. " + "x" * 120}},
        },
    }
    a = assess_website_identity(p)
    assert a["status"] != "exact" and not a["publishable"]
