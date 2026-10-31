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


SITEMAP = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://acme.no/nyheter/ny-rigg</loc><lastmod>2025-09-22T20:00:00+02:00</lastmod></url>
<url><loc>https://acme.no/om-oss</loc><lastmod>2024-01-01</lastmod></url>
</urlset>"""


class TestSitemapExtraction(unittest.TestCase):
    def test_plain_sitemap_lastmod_is_not_news(self):
        # <lastmod> is a modification date and a slug is not a headline.
        self.assertEqual(website._parse_sitemap(SITEMAP, "https://acme.no/"), [])

    def test_news_sitemap_items(self):
        news = (b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                b'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"><url>'
                b'<loc>https://acme.no/nyheter/ny-rigg</loc><lastmod>2025-10-01</lastmod>'
                b'<news:news><news:publication><news:name>Acme</news:name></news:publication>'
                b'<news:publication_date>2025-09-22</news:publication_date>'
                b'<news:title>Acme leverer ny rigg</news:title></news:news></url></urlset>')
        items = website._parse_sitemap(news, "https://acme.no/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["headline"], "Acme leverer ny rigg")
        self.assertIn("2025-09-22", items[0]["date_published"])

    def test_sitemap_candidate_convention(self):
        links = website._sitemap_candidates(BeautifulSoup("<html></html>", "lxml"), "https://acme.no/")
        self.assertEqual(links, ["https://acme.no/sitemap.xml"])

    def test_malformed_sitemap_is_empty(self):
        self.assertEqual(website._parse_sitemap(b"nope", "https://acme.no/"), [])
