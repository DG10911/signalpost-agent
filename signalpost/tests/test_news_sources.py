"""Dated news from listing blocks, ordinary sitemaps and press-release links."""

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bs4 import BeautifulSoup  # noqa: E402

from norway_company_agent import news_sources as ns  # noqa: E402
from norway_company_agent.content_quality import classify_news  # noqa: E402

TODAY = dt.date(2026, 11, 9)


def soup(html):
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------- dates

def test_find_date_norwegian_formats():
    assert ns.find_date("Publisert 3. mars 2025", TODAY) == dt.date(2025, 3, 3)
    assert ns.find_date("03.03.2025", TODAY) == dt.date(2025, 3, 3)
    assert ns.find_date("2025-03-03T10:00:00+01:00", TODAY) == dt.date(2025, 3, 3)
    assert ns.find_date("March 3, 2025", TODAY) == dt.date(2025, 3, 3)


def test_find_date_rejects_partial_future_and_ancient():
    assert ns.find_date("mars 2025", TODAY) is None          # month only
    assert ns.find_date("2025", TODAY) is None               # year only
    assert ns.find_date("03.03.25", TODAY) is None           # two-digit year
    assert ns.find_date("1. januar 2027", TODAY) is None     # future
    assert ns.find_date("01.01.1990", TODAY) is None         # before 1995
    assert ns.find_date("31.02.2025", TODAY) is None         # not a date


def test_real_title():
    assert ns.is_real_title("Ny avtale med Statens vegvesen")
    for bad in ("Les mer", "Nyheter", "Kontakt oss", "3. mars 2025", "Se alle nyheter", "Hei"):
        assert not ns.is_real_title(bad), bad


# ---------------------------------------------------------------- listing blocks

LISTING = """
<html><body>
<nav><a href="/nyheter/meny-lenke">Meny lenke til nyheter</a> 01.01.2025</nav>
<main>
<h1>Nyheter</h1>
<div class="grid">
  <div class="card"><h3>Ny avtale med Statens vegvesen</h3><span>3. mars 2025</span>
     <a href="/nyheter/ny-avtale-med-statens-vegvesen">Les mer</a></div>
  <div class="card"><a href="https://acme.no/nyheter/apner-ny-avdeling-i-bergen">Åpner ny avdeling i Bergen</a>
     <p>14.02.2025</p></div>
  <div class="card"><a href="/nyheter/uten-dato">Saken uten noen dato</a></div>
  <div class="card"><a href="https://other.no/nyheter/fremmed-sak">Fremmed sak på annet nettsted</a> 01.02.2025</div>
  <div class="card"><a href="/nyheter">Alle nyheter samlet</a> 01.02.2025</div>
</div>
</main></body></html>
"""


def test_listing_blocks_pair_headline_date_and_own_post():
    items = ns.listing_items(soup(LISTING), "https://acme.no/nyheter", today=TODAY)
    got = {(i["headline"], i["date_published"], i["url"]) for i in items}
    assert got == {
        ("Ny avtale med Statens vegvesen", "2025-03-03", "https://acme.no/nyheter/ny-avtale-med-statens-vegvesen"),
        ("Åpner ny avdeling i Bergen", "2025-02-14", "https://acme.no/nyheter/apner-ny-avdeling-i-bergen"),
    }
    assert all(i["found_via"] == "listing_block" for i in items)
    for item in items:  # the published-news rules still accept them
        assert classify_news(item, "ACME AS", TODAY, site_url="https://acme.no/") is None


def test_wrapper_holding_several_posts_does_not_lend_its_date():
    html = """<ul><li><a href="/nyheter/forste-sak-her">Første sak her i dag</a></li>
              <li><a href="/nyheter/andre-sak-her">Andre sak her i dag</a></li>
              <li class="meta">Oppdatert 01.02.2025</li></ul>"""
    assert ns.listing_items(soup(html), "https://acme.no/nyheter", today=TODAY) == []


def test_homepage_needs_news_path_and_careers_page_is_skipped():
    html = """<div><a href="/tjenester/ventilasjon-og-varme">Ventilasjon og varme hjemme</a> 01.02.2025</div>
              <div><a href="/aktuelt/ny-daglig-leder">Ny daglig leder ansatt</a> 01.02.2025</div>"""
    items = ns.listing_items(soup(html), "https://acme.no/", today=TODAY)
    assert [i["url"] for i in items] == ["https://acme.no/aktuelt/ny-daglig-leder"]
    jobs = """<div><a href="/karriere/prosjektleder-bygg">Prosjektleder bygg Oslo</a> Søknadsfrist 01.02.2025</div>"""
    assert ns.listing_items(soup(jobs), "https://acme.no/karriere", today=TODAY) == []


def test_deadline_is_not_a_publication_date():
    html = """<div class="card"><a href="/nyheter/kurs-i-sveising">Kurs i sveising for alle</a>
              <p>Påmeldingsfrist 01.02.2025</p></div>"""
    assert ns.listing_items(soup(html), "https://acme.no/nyheter", today=TODAY) == []


def test_article_header_inside_card_is_not_site_chrome():
    html = """<header class="site"><a href="/nyheter/menypunkt-lenke">Menypunkt med lenke</a> 01.02.2025</header>
              <article><header class="entry-header"><h2><a href="/nyheter/ny-kontrakt-signert">Ny kontrakt signert</a></h2>
              <time datetime="2025-05-06T08:00:00+02:00">6. mai</time></header></article>"""
    items = ns.listing_items(soup(html), "https://acme.no/nyheter", today=TODAY)
    assert [(i["headline"], i["date_published"]) for i in items] == [("Ny kontrakt signert", "2025-05-06")]


def test_press_release_links_dated_and_pending():
    html = """<ul><li><a href="https://news.cision.com/no/acme/r/acme-as-kvartalsrapport,c123">Acme AS kvartalsrapport tredje kvartal</a>
              <span>24.10.2025</span></li>
              <li><a href="https://www.mynewsdesk.com/no/acme/pressreleases/acme-kjoeper-firma-3311">Acme kjøper Firma AS</a></li>
              <li><a href="https://www.mynewsdesk.com/no/acme">Vårt presserom</a></li></ul>"""
    items = ns.listing_items(soup(html), "https://acme.no/investor", today=TODAY)
    dated = [i for i in items if not i.get("_pending")]
    pending = [i for i in items if i.get("_pending")]
    assert [(i["date_published"], i["found_via"]) for i in dated] == [("2025-10-24", "press_link")]
    assert [i["url"] for i in pending] == ["https://www.mynewsdesk.com/no/acme/pressreleases/acme-kjoeper-firma-3311"]


# ---------------------------------------------------------------- article pages

def test_article_date_from_meta_not_modified():
    html = """<html><head><meta property="article:modified_time" content="2026-01-01T00:00:00Z">
    <meta property="article:published_time" content="2025-04-02T09:00:00+02:00"></head>
    <body><article><h1>Vi bygger nytt lager på Gardermoen</h1><p>Tekst</p></article></body></html>"""
    item = ns.article_from_page(html, "https://acme.no/nyheter/lager", TODAY)
    assert item["headline"] == "Vi bygger nytt lager på Gardermoen"
    assert item["date_published"] == "2025-04-02"
    assert item["date_source"] == "meta:article:published_time"


def test_article_date_from_jsonld_and_visible_text():
    jsonld = """<script type="application/ld+json">{"@type":"NewsArticle","headline":"Rekordår for Acme",
    "datePublished":"2025-02-01","dateModified":"2026-02-01"}</script><h1>Rekordår for Acme i fjor</h1>"""
    assert ns.article_from_page(jsonld, "https://acme.no/nyheter/x", TODAY)["date_published"] == "2025-02-01"
    visible = "<main><h1>Ny daglig leder i Acme</h1><p class='meta'>Publisert 12. august 2025</p><p>...</p></main>"
    item = ns.article_from_page(visible, "https://acme.no/nyheter/y", TODAY)
    assert (item["date_published"], item["date_source"]) == ("2025-08-12", "text:publisert")


def test_article_without_page_date_is_dropped():
    html = "<main><h1>Ny daglig leder i Acme</h1><p>Ingen dato her.</p></main>"
    assert ns.article_from_page(html, "https://acme.no/nyheter/z", TODAY) is None
    modified_only = ('<meta property="article:modified_time" content="2025-01-01">'
                     "<main><h1>Ny daglig leder i Acme</h1></main>")
    assert ns.article_from_page(modified_only, "https://acme.no/nyheter/z", TODAY) is None


# ---------------------------------------------------------------- sitemaps

SITEMAP = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://acme.no/nyheter/eldre-sak</loc><lastmod>2024-01-01</lastmod></url>
<url><loc>https://acme.no/nyheter/ny-sak</loc><lastmod>2025-09-22T20:00:00+02:00</lastmod></url>
<url><loc>https://acme.no/blogg/tips-om-maling</loc><lastmod>2026-01-01</lastmod></url>
<url><loc>https://acme.no/nyheter</loc><lastmod>2026-01-01</lastmod></url>
<url><loc>https://acme.no/om-oss</loc><lastmod>2026-01-01</lastmod></url>
<url><loc>https://evil.no/nyheter/annen</loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://acme.no/page-sitemap.xml</loc></sitemap>
<sitemap><loc>https://acme.no/post-sitemap.xml</loc></sitemap>
<sitemap><loc>https://acme.no/category-sitemap.xml</loc></sitemap></sitemapindex>"""


def test_sitemap_candidates_news_paths_lastmod_only_orders():
    kind, entries = ns.parse_sitemap_xml(SITEMAP)
    assert kind == "urlset"
    got = ns.sitemap_article_candidates(entries, "https://acme.no/")
    # news sections first (newest lastmod first), blogg after; listing, other pages, other sites out
    assert got == ["https://acme.no/nyheter/ny-sak", "https://acme.no/nyheter/eldre-sak",
                   "https://acme.no/blogg/tips-om-maling"]
    assert ns.sitemap_article_candidates(entries, "https://acme.no/", known={"acme.no/nyheter/ny-sak"},
                                         limit=1) == ["https://acme.no/nyheter/eldre-sak"]


def test_sitemap_index_child_choice():
    kind, entries = ns.parse_sitemap_xml(INDEX)
    assert kind == "index"
    assert ns.choose_child_sitemap(entries) == "https://acme.no/post-sitemap.xml"
    assert ns.parse_sitemap_xml(b"not xml") == ("", [])


def test_post_url_rules():
    page = "https://acme.no/nyheter"
    assert ns.is_post_url("https://www.acme.no/nyheter/ny-sak", page)
    for bad in ("https://acme.no/", "https://acme.no/nyheter", "https://acme.no/nyheter/page/2",
                "https://acme.no/nyheter?page=2", "https://acme.no/kategori/nyheter-x",
                "https://acme.no/nyheter/rapport.pdf", "https://other.no/nyheter/x"):
        assert not ns.is_post_url(bad, page), bad


# ---------------------------------------------------------------- hook

def test_hook_strips_pending_and_spends_nothing_when_disabled():
    cands = [{"headline": "A", "url": "https://www.mynewsdesk.com/no/a/pressreleases/a-1", "_pending": True},
             {"headline": "Ny sak om Acme", "date_published": "2025-01-01", "url": "https://acme.no/nyheter/a"}]
    out, metrics = ns.collect_extra_news(soup=soup("<html></html>"), html="", final_url="https://acme.no/",
                                         candidates=cands, feed_found=True, texts={}, timeout=5,
                                         max_bytes=1000, enabled=False)
    assert out == [cands[1]]
    assert metrics["requests"] == 0


def test_hook_budget_and_page_dates(monkeypatch):
    pages = {
        "https://acme.no/sitemap.xml": SITEMAP,
        "https://acme.no/nyheter/ny-sak": b'<meta property="article:published_time" content="2025-09-20">'
                                          b"<h1>Acme vinner stor kontrakt</h1>",
        "https://acme.no/nyheter/eldre-sak": b"<h1>Acme flytter til nye lokaler</h1><p>Ingen dato</p>",
        "https://acme.no/blogg/tips-om-maling": b"<h1>Tips om maling av hus</h1><time datetime='2025-01-01'>x</time>",
    }
    calls = []

    def fake_get(url, budget, *, timeout, max_bytes, accept):
        if budget.left() < 1:
            return None, url
        budget.requests += 1
        calls.append(url)
        return pages.get(url), url

    monkeypatch.setattr(ns, "_get", fake_get)
    monkeypatch.setattr(ns, "_sitemap_urls", lambda s, u, h: ["https://acme.no/sitemap.xml"])
    texts = {}
    out, metrics = ns.collect_extra_news(soup=soup("<html></html>"), html="", final_url="https://acme.no/",
                                         candidates=[], feed_found=False, texts=texts, timeout=5,
                                         max_bytes=10_000, enabled=True, today=TODAY)
    # the undated page is dropped; the blog guide is a candidate the news rules then reject
    assert [(a["headline"], a["date_published"]) for a in out] == [("Acme vinner stor kontrakt", "2025-09-20"),
                                                                   ("Tips om maling av hus", "2025-01-01")]
    assert classify_news(out[0], "ACME AS", TODAY, site_url="https://acme.no/") is None
    assert classify_news(out[1], "ACME AS", TODAY, site_url="https://acme.no/") is not None
    assert out[0]["url"] in texts  # excerpt re-check has the page text
    # 1 sitemap (the old fallback's request) + 3 article pages = legacy + 3 extra
    assert metrics["requests"] == 4 and metrics["legacy_requests"] == 1
    assert len(calls) == 4


def test_conflicting_jsonld_dates_are_not_trusted():
    # sensio.com: a copied template block says 2024-10-01, the CMS block the real date.
    html = """<script type="application/ld+json">{"@type":"BlogPosting","headline":"Sensio acquires market leader",
    "datePublished":"2024-10-01"}</script><script type="application/ld+json">{"@type":"BlogPosting",
    "headline":"Sensio acquires market leader","datePublished":"2026-03-03T04:39:39.000Z"}</script>
    <h1>Sensio acquires market leader in Spain</h1><p>No visible date.</p>"""
    assert ns.article_from_page(html, "https://www.sensio.com/news/x", TODAY) is None
    agreeing = html.replace("2024-10-01", "2026-03-03")
    assert ns.article_from_page(agreeing, "https://www.sensio.com/news/x", TODAY)["date_published"] == "2026-03-03"


def test_child_sitemap_prefers_posts_over_pages_and_skips_taxonomy():
    wp = [("https://a.no/wp-sitemap-posts-page-1.xml", ""), ("https://a.no/wp-sitemap-posts-post-1.xml", ""),
          ("https://a.no/wp-sitemap-taxonomies-category-1.xml", "")]
    assert ns.choose_child_sitemap(wp) == "https://a.no/wp-sitemap-posts-post-1.xml"
    wix = [("https://a.no/booking-services-sitemap.xml", ""), ("https://a.no/pages-sitemap.xml", "")]
    assert ns.choose_child_sitemap(wix) == "https://a.no/pages-sitemap.xml"
    assert ns.choose_child_sitemap([("https://a.no/product-sitemap.xml", ""), ("https://a.no/x.xml", ""),
                                    ("https://a.no/y.xml", "")]) is None
