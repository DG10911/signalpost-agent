"""Request-efficiency invariants (the 10,000-request ceiling is a hard gate)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bs4 import BeautifulSoup  # noqa: E402

from norway_company_agent import website  # noqa: E402


class TestRequestEfficiency(unittest.TestCase):
    def test_secondary_page_cap_default_is_lean(self):
        # Was 14; the tighter cap is what keeps a 1,000-company run under budget.
        self.assertLessEqual(website.DEFAULT_MAX_SECONDARY_PAGES, 8)
        self.assertGreaterEqual(website.DEFAULT_MAX_SECONDARY_PAGES, 4)

    def test_canonical_probes_precede_discovered_links(self):
        html = '<a href="/om-oss">Om oss</a><a href="/kontakt">Kontakt</a>'
        soup = BeautifulSoup(html, "lxml")
        ordered = website._ordered_secondary_urls("https://acme.no/", soup)
        # canonical careers/news probes come first under a tight cap
        self.assertTrue(ordered[0].endswith("/careers"))
        self.assertIn("https://acme.no/karriere", ordered)
        # discovered links are still present, just later
        self.assertIn("https://acme.no/om-oss", ordered)
        idx_link = ordered.index("https://acme.no/om-oss")
        idx_probe = ordered.index("https://acme.no/careers")
        self.assertLess(idx_probe, idx_link)

    def test_no_duplicate_targets(self):
        soup = BeautifulSoup('<a href="/nyheter">Nyheter</a>', "lxml")
        ordered = website._ordered_secondary_urls("https://acme.no/", soup)
        self.assertEqual(len(ordered), len(set(u.rstrip("/") for u in ordered)))


if __name__ == "__main__":
    unittest.main()


class TestTimeBudget(unittest.TestCase):
    def test_budget_exhausted_is_terminal(self):
        from norway_company_agent.batch import evidence_terminal_state, TERMINAL_STATES
        state = evidence_terminal_state({"status": "source_error", "note": "budget_exhausted: wall-clock budget reached"})
        self.assertEqual(state, "budget_exhausted")
        self.assertIn(state, TERMINAL_STATES)
