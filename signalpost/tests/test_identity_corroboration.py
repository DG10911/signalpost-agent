"""Post-audit safety test: a review-tier (0.85) name match is NOT promoted to
exact even when a registry signal appears on the page. The corroboration
escalation was rejected by the §7 audit (email-domain==hostname is not an
independent signal; it promoted a parent/brand match). The 0.9 exact threshold
must hold — never trade exact-entity identity for coverage."""

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


def test_review_tier_not_promoted_even_with_registry_signal():
    for reg, txt in (
        ({"telefon": "22 50 07 37"}, "Gamma. Ring 22500737. " + "x" * 120),
        ({"epostadresse": "post@alphabeta.no"}, "Gamma. post@alphabeta.no. " + "x" * 120),
        ({"forretningsadresse.postnummer": "0155", "forretningsadresse.poststed": "OSLO"}, "Gamma. 0155 Oslo. " + "x" * 120),
    ):
        a = assess_website_identity(_profile(main_text=txt, registry=reg))
        assert a["score"] == 0.85 and a["status"] == "review" and not a["publishable"]
        assert "corroboration" not in a  # feature removed


def test_exact_org_number_still_publishes():
    p = _profile(main_text="Org.nr 923 609 016. " + "x" * 120, registry={})
    a = assess_website_identity(p)
    assert a["score"] == 1.0 and a["publishable"]

