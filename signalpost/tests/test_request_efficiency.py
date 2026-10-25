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

    def test_real_links_precede_guessed_paths(self):
        html = '<a href="/om-oss">Om oss</a><a href="/kontakt">Kontakt</a><a href="/nyheter">Nyheter</a>'
        ordered = website._ordered_secondary_urls("https://acme.no/", BeautifulSoup(html, "lxml"))
        # a real news link outranks everything; real about/contact pages follow
        self.assertEqual(ordered[0], "https://acme.no/nyheter")
        self.assertLess(ordered.index("https://acme.no/om-oss"), ordered.index("https://acme.no/karriere"))
        # news is not guessed when the homepage links to it; hiring still is
        self.assertNotIn("https://acme.no/aktuelt", ordered)
        self.assertIn("https://acme.no/karriere", ordered)

    def test_guessed_news_and_hiring_alternate_without_real_links(self):
        ordered = website._ordered_secondary_urls("https://acme.no/", BeautifulSoup("<p>hei</p>", "lxml"))
        self.assertEqual(ordered[:2], ["https://acme.no/nyheter", "https://acme.no/karriere"])

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
