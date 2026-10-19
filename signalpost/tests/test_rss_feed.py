"""RSS/Atom dated-activity extraction (the highest-yield coverage per request)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bs4 import BeautifulSoup  # noqa: E402

from norway_company_agent import website  # noqa: E402

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Ny rigg kontrahert</title><link>https://acme.no/nyheter/rigg</link>
<pubDate>Mon, 22 Sep 2025 20:00:00 +0200</pubDate></item>
<item><title>Arsresultat 2024</title><link>https://acme.no/nyheter/arsresultat</link>
<pubDate>Tue, 01 Apr 2025 08:00:00 +0200</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>New contract</title><link href="https://acme.no/news/contract"/>
<published>2025-09-22T20:00:00Z</published></entry>
</feed>"""


class TestFeedExtraction(unittest.TestCase):
    def test_rss_items(self):
        items = website._parse_feed(RSS, "https://acme.no/")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["headline"], "Ny rigg kontrahert")
        self.assertEqual(items[0]["url"], "https://acme.no/nyheter/rigg")
        self.assertIn("2025-09-22", items[0]["date_published"])

    def test_atom_items(self):
        items = website._parse_feed(ATOM, "https://acme.no/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["headline"], "New contract")
        self.assertEqual(items[0]["url"], "https://acme.no/news/contract")

    def test_malformed_feed_is_empty_not_crash(self):
        self.assertEqual(website._parse_feed(b"not xml", "https://acme.no/"), [])

    def test_feed_links_same_domain_only(self):
        html = ('<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
                '<link rel="alternate" type="application/rss+xml" href="https://evil.example/feed.xml">')
        links = website._feed_links(BeautifulSoup(html, "lxml"), "https://acme.no/")
        self.assertEqual(links, ["https://acme.no/feed.xml"])


if __name__ == "__main__":
    unittest.main()
