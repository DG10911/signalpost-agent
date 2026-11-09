"""Extra sources of dated company news from the company's own site.

website.py already reads RSS/Atom, JSON-LD, ``<time>`` items, the WordPress
posts API and Google-News sitemaps. This module adds three more, each held to
the same rule: an item is published only with a full calendar publication date
taken from the item itself, a real headline and the address of the post's own
page. Everything still goes through ``content_quality.classify_news`` and the
excerpt re-check afterwards; nothing here relaxes them.

1. Listing blocks (no extra requests). On pages already fetched (homepage and
   the news/press pages the crawl visits) a block that pairs one link to a post
   page on the same registered domain with a full date ("3. mars 2025",
   "03.03.2025", ``<time datetime>``) and a headline becomes an item.
2. Ordinary sitemaps. Sitemap URLs come from robots.txt ``Sitemap:`` lines,
   ``<link rel="sitemap">``, /sitemap.xml, /sitemap_index.xml or
   /wp-sitemap.xml. URLs whose path sits under a news section (nyheter,
   aktuelt, news, presse, blogg, artikler ...) are candidates. ``<lastmod>``
   only orders the candidates; it is never a publication date. At most
   ``MAX_ARTICLE_PAGES`` article pages are fetched and the date must come from
   the page itself (article:published_time, JSON-LD datePublished, a
   "Publisert" line, ``<time datetime>`` or a visible date next to the headline).
3. Press releases the company links to on NewsWeb, Cision, Mynewsdesk or
   GlobeNewswire. Dated in the linking block, or read from the release page.

Budget: at most ``EXTRA_REQUEST_BUDGET`` requests per company beyond what the
old Google-News sitemap fallback already spent.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from bs4 import BeautifulSoup

from . import website as _web
from .content_quality import (EARLIEST_PUBLICATION, MONTHS, NEWS_SECTIONS, _plain, classify_news,
                              is_generic_headline)

EXTRA_REQUEST_BUDGET = 3
MAX_ARTICLE_PAGES = 3
MAX_NEW_ITEMS = 12
MAX_ITEMS_PER_PAGE = 15
# Skip the article fetches once this many candidates would already pass the
# news rules without naming the company (they are enough for coverage).
ENOUGH_CANDIDATES = 3
NEW_SOURCES = ("listing_block", "press_link", "press_page", "sitemap_article")

# Path segments that mark a news section (task list plus the content_quality set).
NEWS_PATH_TERMS = {"nyheter", "nyhet", "aktuelt", "news", "presse", "press", "pressemelding",
                   "pressemeldinger", "presserom", "blogg", "blog", "artikler", "artikkel",
                   "siste-nytt", "newsroom", "nyhetsarkiv", "media", "medier"}
_LISTING_PAGE_TERMS = ("nyhet", "aktuelt", "news", "presse", "press", "blogg", "blog", "artikler",
                       "siste-nytt", "newsroom")

# Never a post page: legal, contact, careers, shop, taxonomy, pagination, search.
_NOT_A_POST = re.compile(
    r"/(?:kontakt|contact|personvern|privacy|cookie|vilkar|terms|career)[\w-]*(?:/|$|\.|\?)"
    r"|/(?:om-oss|about|about-us|karriere|jobb|jobs?|ledige-stillinger|stillinger|login|logg-inn|"
    r"min-side|produkt|produkter|products?|collections?|shop|nettbutikk|butikk|handlekurv|cart|checkout|"
    r"kategori|category|categories|tag|tags|emne|author|forfatter|search|sok|page|side|feed|"
    r"wp-content|wp-json|prosjekt|prosjekter|projects?|referanser|references?)(?:/|$|\.|\?)"
    r"|[?&](?:page|side|p|s|q|paged)=",
    re.IGNORECASE,
)
_FILE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".xml", ".mp4", ".doc", ".docx")

_SECTION_LABEL = re.compile(
    r"^(?:press\s*room|presserom|press\s+releases?|pressemeldinger?|news(?:room)?|nyheter|aktuelt|blogg?|"
    r"read\s+more|les\s+mer|se\s+alle(?:\s+\w+)?|view\s+all|all\s+news|alle\s+nyheter|latest\s+news|siste\s+nytt|"
    r"flere\s+nyheter|more\s+news|arkiv|archive|neste|forrige|next|previous)$", re.I)
_NAV_TITLE = re.compile(
    r"^(?:hjem|home|forside|kontakt(?:\s+oss)?|contact(?:\s+us)?|om\s+oss|about(?:\s+us)?|personvern\w*|"
    r"privacy(?:\s+policy)?|cookies?|vilkår|terms|faq|logg\s+inn|login|min\s+side|ledige\s+stillinger|"
    r"karriere|careers?|jobb(?:\s+hos\s+oss)?|produkter|products|tjenester|services|nettbutikk|shop)$", re.I)
# A job-ad or registration deadline is not a publication date.
_DEADLINE_MARKERS = ("soknadsfrist", "frist:", "frist ", "deadline", "pameldingsfrist", "pamelding", "apply by")

_ISO = re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)")
_DMY = re.compile(r"(?<![\d.])(\d{1,2})\.\s?(\d{1,2})\.\s?((?:19|20)\d{2})(?!\d)")
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_TEXT = re.compile(r"(?<!\d)(\d{1,2})\.?\s+(" + _MONTH_RE + r")\.?,?\s+((?:19|20)\d{2})(?!\d)")
_TEXT_US = re.compile(r"\b(" + _MONTH_RE + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})(?!\d)")
_PUBLISHED_LINE = re.compile(r"\b(?:publisert|published|postet|posted|dato)\b\s*:?\s*(?:den\s+)?(.{6,40})")

# Press-release hosts (company-issued releases) and the path of one release.
PRESS_HOSTS = {
    "newsweb.oslobors.no": re.compile(r"/message/\d+"),
    "newsweb.no": re.compile(r"/message/\d+"),
    "news.cision.com": re.compile(r"/r/[^/]+"),
    "cision.com": re.compile(r"/r/[^/]+"),
    "mynewsdesk.com": re.compile(r"/(?:pressreleases|news|blog_posts)/[^/]+-\d+"),
    "globenewswire.com": re.compile(r"/news-release/\d{4}/\d{2}/\d{2}/"),
}
# NewsWeb renders in the browser; its pages carry no date in the HTML.
_PRESS_FETCHABLE = ("cision.com", "mynewsdesk.com", "globenewswire.com")


def _today(today: _dt.date | None) -> _dt.date:
    return today or _dt.datetime.now(_dt.timezone.utc).date()


def _valid(year: int, month: int, day: int, today: _dt.date | None) -> _dt.date | None:
    try:
        value = _dt.date(year, month, day)
    except ValueError:
        return None
    if value < EARLIEST_PUBLICATION or value > _today(today) + _dt.timedelta(days=1):
        return None
    return value


def find_date(text: Any, today: _dt.date | None = None) -> _dt.date | None:
    """The first full calendar date in ``text`` (ISO, 03.03.2025, "3. mars 2025",
    "March 3, 2025"), not in the future and not before 1995. Two-digit years and
    year/month-only strings are not dates."""
    plain = _plain(text)
    if not plain:
        return None
    found: list[tuple[int, _dt.date | None]] = []
    for m in _ISO.finditer(plain):
        found.append((m.start(), _valid(int(m[1]), int(m[2]), int(m[3]), today)))
    for m in _DMY.finditer(plain):
        found.append((m.start(), _valid(int(m[3]), int(m[2]), int(m[1]), today)))
    for m in _TEXT.finditer(plain):
        found.append((m.start(), _valid(int(m[3]), MONTHS[m[2]], int(m[1]), today)))
    for m in _TEXT_US.finditer(plain):
        found.append((m.start(), _valid(int(m[3]), MONTHS[m[1]], int(m[2]), today)))
    for _, value in sorted(found, key=lambda item: item[0]):
        if value is not None:
            return value
    return None


def _strip_dates(text: str) -> str:
    plain = _plain(text)
    for rx in (_ISO, _DMY, _TEXT, _TEXT_US):
        plain = rx.sub(" ", plain)
    return " ".join(plain.split())


def is_real_title(text: Any) -> bool:
    """A headline that says something: not link text, a date, a section or
    navigation label, and at least two words with eight letters."""
    title = " ".join(str(text or "").split())
    if not 8 <= len(title) <= 220 or is_generic_headline(title):
        return False
    bare = title.strip(" .:-–|»>")
    if _SECTION_LABEL.match(bare) or _NAV_TITLE.match(bare):
        return False
    rest = _strip_dates(title)
    words = [w for w in rest.split() if re.search(r"[a-z]", w)]
    return len(words) >= 2 and len(re.sub(r"[^a-z]", "", rest)) >= 8


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")


def press_host(url: str) -> str | None:
    host = _host(url)
    for name in PRESS_HOSTS:
        if host == name or host.endswith("." + name):
            return name
    return None


def is_press_release_url(url: str) -> bool:
    name = press_host(url)
    return bool(name) and bool(PRESS_HOSTS[name].search(urllib.parse.urlparse(url).path))


def _segments(url: str) -> list[str]:
    return [s.casefold() for s in urllib.parse.urlparse(url).path.split("/") if s]


def _key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{(parsed.hostname or '').lower().removeprefix('www.')}{parsed.path.rstrip('/')}" + \
        (f"?{parsed.query}" if parsed.query else "")


def in_news_path(url: str) -> bool:
    return any(seg in NEWS_PATH_TERMS for seg in _segments(url))


def is_post_url(url: str, page_url: str) -> bool:
    """``url`` can be one post's own page on the same registered domain: not the
    site root, not the listing page itself, not a section index (/nyheter), not
    a legal/contact/careers/shop/taxonomy/pagination page or a file."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if _web._registered_domain(url) != _web._registered_domain(page_url):
        return False
    segs = _segments(url)
    if not segs or _key(url) == _key(page_url):
        return False
    if segs[-1] in NEWS_PATH_TERMS or segs[-1] in NEWS_SECTIONS:
        return False
    if parsed.path.lower().endswith(_FILE_EXT):
        return False
    return not _NOT_A_POST.search(parsed.path + ("?" + parsed.query if parsed.query else ""))


def _is_listing_page(page_url: str, title: str = "") -> bool:
    path = urllib.parse.urlparse(page_url).path.casefold()
    head = _plain(title)
    return any(term in path for term in _LISTING_PAGE_TERMS) or any(
        w in head.split() for w in ("nyheter", "aktuelt", "news", "presse", "pressemeldinger", "blogg"))


def _clean_text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def _block_date(node: Any, headline: str, today: _dt.date | None) -> tuple[_dt.date | None, str]:
    for t in node.select("time[datetime]"):
        value = find_date(t.get("datetime"), today)
        if value:
            return value, "time_datetime"
    text = _clean_text(node)
    if headline:
        text = text.replace(headline, " ")
    value = find_date(text, today)
    return (value, "visible_date") if value else (None, "")


def _headline_for(node: Any, anchor: Any) -> str:
    for heading in node.find_all(["h1", "h2", "h3", "h4", "h5"]):
        text = _clean_text(heading)
        if is_real_title(text):
            return text
    for candidate in (_clean_text(anchor), str(anchor.get("title") or "").strip()):
        if is_real_title(candidate):
            return " ".join(candidate.split())
    return ""


def _in_site_chrome(anchor: Any) -> bool:
    """Menu links: inside <nav>, or a site <header> (not an article's own header)."""
    if anchor.find_parent("nav") is not None:
        return True
    header = anchor.find_parent("header")
    return header is not None and header.find_parent("article") is None


def listing_items(soup: BeautifulSoup, page_url: str, *, page_title: str = "",
                  today: _dt.date | None = None) -> list[dict[str, Any]]:
    """Never raises: a parser failure must not cost the page's other facts."""
    try:
        return _listing_items(soup, page_url, page_title=page_title, today=today)
    except Exception:
        return []


def _listing_items(soup: BeautifulSoup, page_url: str, *, page_title: str = "",
                   today: _dt.date | None = None) -> list[dict[str, Any]]:
    """Dated items from listing blocks on an already-fetched page.

    A block is the smallest ancestor of a post link that holds exactly one
    distinct post (or press-release) address and a full date outside the
    headline. On a page that is not itself a news listing (the homepage), the
    post must sit under a news path. Careers pages are skipped (a deadline is
    not a publication date). Press-release links without a date in their block
    are returned as ``_pending`` for ``collect_extra_news`` to resolve.
    """
    if soup is None or _web.is_careers_page(page_url, page_title):
        return []
    listing = _is_listing_page(page_url, page_title)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def link_kind(href: str) -> tuple[str, str] | None:
        url = urllib.parse.urljoin(page_url, href.strip()).split("#")[0]
        if is_press_release_url(url):
            return url, "press"
        if is_post_url(url, page_url) and (listing or in_news_path(url)):
            return url, "post"
        return None

    for anchor in soup.select("a[href]"):
        if len(out) >= MAX_ITEMS_PER_PAGE:
            break
        kind = link_kind(str(anchor.get("href") or ""))
        if kind is None or _in_site_chrome(anchor):
            continue
        url, family = kind
        if _key(url) in seen:
            continue
        node, found = anchor, None
        for _ in range(6):
            node = node.parent
            if node is None or node.name in ("body", "html", "main", "nav", "footer", "form") \
                    or (node.name == "header" and node.find_parent("article") is None):
                break
            keys = set()
            for a in node.select("a[href]"):
                k = link_kind(str(a.get("href") or ""))
                if k is not None:
                    keys.add(_key(k[0]))
            if len(keys) > 1:
                break
            text = _clean_text(node)
            if len(text) > 1500:
                break
            headline = _headline_for(node, anchor)
            date, how = _block_date(node, headline, today)
            if date and headline:
                if any(m in " " + _plain(text) + " " for m in _DEADLINE_MARKERS):
                    break
                found = (headline, date, how)
                break
        if found:
            seen.add(_key(url))
            headline, date, how = found
            out.append({"headline": headline[:300], "date_published": date.isoformat(), "url": url,
                        "source_page": page_url, "date_source": f"listing:{how}",
                        "found_via": "press_link" if family == "press" else "listing_block"})
        elif family == "press":
            seen.add(_key(url))
            text = _clean_text(anchor)
            out.append({"headline": text[:300] if is_real_title(text) else "", "url": url,
                        "source_page": page_url, "found_via": "press_link", "_pending": True})
    return out


# ---------------------------------------------------------------- article page

_META_DATE_KEYS = ("article:published_time", "og:article:published_time", "og:published_time",
                   "datepublished", "publish-date", "publish_date", "publishdate", "pubdate",
                   "dc.date.issued", "dcterms.issued", "dcterms.created", "parsely-pub-date",
                   "sailthru.date", "date")
_ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "PressRelease", "Report", "AnalysisNewsArticle",
                  "ReportageNewsArticle"}


def _jsonld_nodes(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            out.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.string or script.get_text() or ""))
        except (ValueError, TypeError):
            continue
    return out


def _types(node: dict[str, Any]) -> set[str]:
    kind = node.get("@type")
    return set(kind if isinstance(kind, list) else [kind]) - {None}


def _headline_node(soup: BeautifulSoup) -> Any:
    for scope in (soup.find("article"), soup.find("main"), soup):
        if scope is None:
            continue
        h1 = scope.find("h1")
        if h1 is not None and is_real_title(_clean_text(h1)):
            return h1
    return None


def _near_text(node: Any, after: int = 300, before: int = 160) -> str:
    """Visible text right after (and just before) the headline."""
    if node is None:
        return ""
    forward: list[str] = []
    size = 0
    for s in node.find_all_next(string=True):
        if s.parent is not None and s.parent.name in ("script", "style", "noscript", "template"):
            continue
        if node in s.parents:
            continue
        piece = " ".join(str(s).split())
        if piece:
            forward.append(piece)
            size += len(piece)
            if size >= after:
                break
    backward: list[str] = []
    size = 0
    for s in node.find_all_previous(string=True):
        if s.parent is not None and s.parent.name in ("script", "style", "noscript", "template", "title"):
            continue
        piece = " ".join(str(s).split())
        if piece:
            backward.insert(0, piece)
            size += len(piece)
            if size >= before:
                break
    return " ".join(backward) + " | " + " ".join(forward)


def article_from_page(html: str, url: str, today: _dt.date | None = None) -> dict[str, Any] | None:
    """Headline and publication date of one article page, or None.

    The date comes only from the page: published-time meta, JSON-LD
    datePublished, a "Publisert" line, a ``<time datetime>`` next to the
    headline, or a visible date right by the headline. Modified dates are never
    used."""
    soup = BeautifulSoup(html, "lxml")
    h1 = _headline_node(soup)
    headline = _clean_text(h1) if h1 is not None else ""
    nodes = _jsonld_nodes(soup)
    if not headline:
        for node in nodes:
            if _types(node) & _ARTICLE_TYPES and is_real_title(node.get("headline")):
                headline = " ".join(str(node["headline"]).split())
                break
    if not headline:
        og = soup.select_one('meta[property="og:title"]')
        title = str(og.get("content") or "") if og else ""
        title = re.split(r"\s+[|–—-]\s+", title)[0].strip()
        if is_real_title(title):
            headline = title
    if not headline:
        return None
    date, how = None, ""
    # A source that states two different publication dates (e.g. a copied
    # template JSON-LD block next to the CMS's own) is not trusted at all.
    metas: dict[str, set] = {}
    for meta in soup.select("meta[content]"):
        key = str(meta.get("property") or meta.get("name") or meta.get("itemprop") or "").strip().casefold()
        if key:
            metas.setdefault(key, set()).add(find_date(meta.get("content"), today))
    for key in _META_DATE_KEYS:
        values = metas.get(key) or set()
        if len(values) == 1 and (date := next(iter(values))):
            how = f"meta:{key}"
            break
    if date is None:
        for wanted in (_ARTICLE_TYPES, {"WebPage", "ItemPage"}):
            values = {find_date(str(node.get("datePublished")), today)
                      for node in nodes if _types(node) & wanted and node.get("datePublished")}
            if len(values) == 1 and (date := next(iter(values))):
                how = "jsonld:datePublished"
            if values:
                break  # Article nodes decide (agreeing or not); WebPage dates are not consulted
    if date is None:
        item = soup.select_one('[itemprop="datePublished"]')
        if item is not None and (date := find_date(item.get("datetime") or item.get("content") or _clean_text(item), today)):
            how = "microdata:datePublished"
    near = _near_text(h1)
    if date is None:
        for m in _PUBLISHED_LINE.finditer(_plain(near)):
            if date := find_date(m.group(1), today):
                how = "text:publisert"
                break
    if date is None and h1 is not None:
        scope = h1.find_parent(["article", "main"]) or h1.parent
        for t in (scope.select("time[datetime]") if scope is not None else []):
            if t.find_parent("a") is None and (date := find_date(t.get("datetime"), today)):
                how = "time_datetime"
                break
    if date is None and h1 is not None:
        if date := find_date(near.replace(headline, " "), today):
            how = "visible_date_near_headline"
    if date is None:
        return None
    return {"headline": headline[:300], "date_published": date.isoformat(), "date_source": how}


# ---------------------------------------------------------------- sitemaps

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap_xml(raw: bytes) -> tuple[str, list[tuple[str, str]]]:
    """("index" | "urlset" | "", [(loc, lastmod)])."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return "", []
    kind = "index" if _local(root.tag) == "sitemapindex" else "urlset" if _local(root.tag) == "urlset" else ""
    entries: list[tuple[str, str]] = []
    for node in root:
        if _local(node.tag) not in ("url", "sitemap"):
            continue
        loc = lastmod = ""
        for child in node:
            name = _local(child.tag)
            if name == "loc":
                loc = (child.text or "").strip()
            elif name == "lastmod":
                lastmod = (child.text or "").strip()
        if loc:
            entries.append((loc, lastmod))
    return kind, entries


_CHILD_PRIORITY = ("news", "nyhet", "aktuelt", "presse", "press", "artik", "posts-post", "post-sitemap",
                   "blog-posts", "post", "blog")
_CHILD_SKIP = ("category", "kategori", "tag", "author", "user", "taxonom", "product", "produkt",
               "attachment", "media", "image", "video", "booking", "store", "shop")


def choose_child_sitemap(entries: list[tuple[str, str]]) -> str | None:
    """The child sitemap most likely to list news posts (posts before pages;
    taxonomy, product and media sitemaps never); None when unclear."""
    names = [(loc, urllib.parse.urlparse(loc).path.casefold().rsplit("/", 1)[-1]) for loc, _ in entries]
    names = [(loc, name) for loc, name in names if not any(skip in name for skip in _CHILD_SKIP)]
    for term in _CHILD_PRIORITY:
        for loc, name in names:
            if term in name and "page" not in name:
                return loc
    pages = [loc for loc, name in names if "page" in name]
    if len(pages) == 1:
        return pages[0]  # a CMS that keeps /nyheter/... as ordinary pages
    return names[0][0] if len(names) == 1 else None


def sitemap_article_candidates(entries: list[tuple[str, str]], site_url: str,
                               known: set[str] | None = None, limit: int = MAX_ARTICLE_PAGES) -> list[str]:
    """Post URLs under a news section, best first: sections that the news rules
    accept (nyheter, aktuelt, presse ...) before blogg/artikler, then newest
    ``<lastmod>`` (a candidate signal only, never the publication date)."""
    known = known or set()
    ranked: list[tuple[int, str, str]] = []
    for loc, lastmod in entries:
        if _key(loc) in known or not in_news_path(loc) or not is_post_url(loc, site_url):
            continue
        accepted = any(seg in NEWS_SECTIONS for seg in _segments(loc))
        ranked.append((0 if accepted else 1, lastmod, loc))
    ranked.sort(key=lambda item: (item[0], _neg(item[1]), item[2]))
    return [loc for _, _, loc in ranked[:limit]]


def _neg(lastmod: str) -> str:
    # Newest first; missing lastmod last.
    return "".join(chr(0x10FFFF - ord(c)) for c in lastmod) if lastmod else "\U0010ffff"


def _sitemap_urls(soup: BeautifulSoup, site_url: str, html: str) -> list[str]:
    parsed = urllib.parse.urlparse(site_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    out: list[str] = []
    with _web._ROBOTS_LOCK:
        cached = _web._ROBOTS_CACHE.get(root)
    if cached is not None and hasattr(cached, "site_maps"):
        out.extend(cached.site_maps() or [])
    for link in soup.select('link[rel="sitemap"]') if soup is not None else []:
        if link.get("href"):
            out.append(urllib.parse.urljoin(site_url, str(link.get("href"))))
    out += [root + "/sitemap.xml", root + "/sitemap_index.xml"]
    if _web._is_wordpress(html or ""):
        out.append(root + "/wp-sitemap.xml")
    domain = _web._registered_domain(site_url)
    unique: list[str] = []
    for url in out:
        if url not in unique and _web._registered_domain(url) == domain and not url.endswith(".gz"):
            unique.append(url)
    return unique


# ---------------------------------------------------------------- network

class _Budget:
    def __init__(self, allowance: int) -> None:
        self.allowance = allowance
        self.requests = 0
        self.bytes = 0
        self.latencies: list[int] = []
        self.throttled = False

    def left(self) -> int:
        return 0 if self.throttled else self.allowance - self.requests


def _robots_cached(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    with _web._ROBOTS_LOCK:
        return f"{parsed.scheme}://{parsed.netloc}" in _web._ROBOTS_CACHE


def _get(url: str, budget: _Budget, *, timeout: float, max_bytes: int,
         accept: str) -> tuple[bytes | None, str]:
    """One polite GET within the budget (robots.txt counted when not cached)."""
    need = 1 if _robots_cached(url) else 2
    if budget.left() < need:
        return None, url
    try:
        _web.assert_public_url(url)
        budget.requests += need - 1
        if not _web._robots_allowed(url, timeout):
            return None, url
    except Exception:
        return None, url
    budget.requests += 1
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": _web.USER_AGENT, "Accept": accept})
    try:
        with _web.SAFE_OPENER.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            final_url = response.geturl()
            budget.latencies.append(int((time.monotonic() - started) * 1000))
            budget.bytes += len(raw)
            if len(raw) > max_bytes:
                return None, final_url
            return raw, final_url
    except urllib.error.HTTPError as exc:
        budget.latencies.append(int((time.monotonic() - started) * 1000))
        if exc.code in (429, 503):
            budget.throttled = True  # the site asked us to back off: stop here
        return None, url
    except Exception:
        budget.latencies.append(int((time.monotonic() - started) * 1000))
        return None, url


def _fetch_article(url: str, budget: _Budget, *, timeout: float, max_bytes: int,
                   texts: dict[str, str] | None, via: str, today: _dt.date | None,
                   site_url: str | None) -> dict[str, Any] | None:
    raw, final_url = _get(url, budget, timeout=timeout, max_bytes=max_bytes,
                          accept="text/html,application/xhtml+xml")
    if raw is None:
        return None
    # A redirect to another page (the listing, the homepage, another site) is
    # not this post.
    if _key(final_url) != _key(url) and not (site_url and is_post_url(final_url, site_url)) \
            and not is_press_release_url(final_url):
        return None
    html = raw.decode("utf-8", errors="replace")
    if "<" not in html[:2000]:
        return None
    item = article_from_page(html, final_url, today)
    if item is None:
        return None
    if texts is not None:
        texts[final_url] = _web.source_text(html)
    return {**item, "url": final_url, "source_page": final_url, "found_via": via}


def _would_pass(article: dict[str, Any], site_url: str, today: _dt.date | None) -> bool:
    if article.get("_pending"):
        return False
    url = str(article.get("url") or "")
    if _key(url) == _key(str(article.get("source_page") or "")):
        return False
    return classify_news(article, None, today, site_url=site_url) is None


def collect_extra_news(**kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Never raises: on an unexpected error keep the candidates found so far."""
    started = time.monotonic()
    try:
        out, metrics = _collect_extra_news(**kwargs)
        metrics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return out, metrics
    except Exception as exc:
        kept = [a for a in kwargs.get("candidates") or [] if not a.get("_pending")]
        return kept, {"requests": 0, "bytes": 0, "latencies_ms": [], "legacy_requests": 0,
                      "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def _collect_extra_news(*, soup: BeautifulSoup, html: str, final_url: str,
                        candidates: list[dict[str, Any]], feed_found: bool,
                        texts: dict[str, str] | None, timeout: float, max_bytes: int,
                        enabled: bool = True, today: _dt.date | None = None
                        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve pending press links, read sitemaps and fetch a few article pages.

    Returns (all candidate articles, metrics). Replaces the old Google-News
    sitemap fallback: the same first sitemap fetch now also yields ordinary
    news URLs. ``metrics["legacy_requests"]`` is what the old fallback would
    have spent, so ``requests - legacy_requests`` is the extra cost."""
    timeout = min(timeout, 10.0)
    max_bytes = min(max_bytes, 2_000_000)
    pending = [a for a in candidates if a.get("_pending")]
    articles = [a for a in candidates if not a.get("_pending")]
    legacy = 0 if feed_found else 1
    budget = _Budget((EXTRA_REQUEST_BUDGET if enabled else 0) + legacy)
    diag: dict[str, Any] = {"enabled": enabled, "pending_press_links": len(pending)}
    known = {_key(str(a.get("url") or "")) for a in articles}
    new: list[dict[str, Any]] = []

    # 1. Press releases linked without a date in their block.
    for item in pending[:2] if enabled else []:
        url = str(item.get("url") or "")
        if not (press_host(url) or "").endswith(_PRESS_FETCHABLE):
            continue
        if budget.left() - legacy < 2 or _key(url) in known:
            continue
        got = _fetch_article(url, budget, timeout=timeout, max_bytes=max_bytes, texts=texts,
                             via="press_page", today=today, site_url=None)
        if got:
            new.append(got)
            known.add(_key(got["url"]))

    # 2. Sitemap: needed for the old Google-News fallback (no feed) or when the
    #    cheap sources left too few candidates.
    good = sum(_would_pass(a, final_url, today) for a in articles + new)
    need_more = enabled and good < ENOUGH_CANDIDATES
    diag["candidates_before"] = good
    entries: list[tuple[str, str]] = []
    if not feed_found or need_more:
        for sm_url in _sitemap_urls(soup, final_url, html)[:3]:
            raw, sm_final = _get(sm_url, budget, timeout=timeout, max_bytes=max_bytes,
                                 accept="application/xml, text/xml, */*")
            if raw is not None and _web._registered_domain(sm_final) != _web._registered_domain(final_url):
                raw = None  # redirected to another site's sitemap: not this company's
            if raw is None:
                if not enabled:
                    break  # old behaviour: one sitemap attempt only
                continue
            news_items = _web._parse_sitemap(raw, sm_final)
            if news_items:
                _web._remember_source(texts, sm_final, raw, news_items)
                for a in news_items:
                    a["found_via"] = "news_sitemap"
                new.extend(news_items)
            kind, entries = parse_sitemap_xml(raw)
            if not entries and not news_items and enabled:
                continue  # empty sitemap: try the next declared/conventional one
            diag["sitemap"] = sm_final
            if kind == "index" and need_more:
                child = choose_child_sitemap(entries)
                entries = []
                if child and _web._registered_domain(child) == _web._registered_domain(final_url):
                    raw, child_final = _get(child, budget, timeout=timeout, max_bytes=max_bytes,
                                            accept="application/xml, text/xml, */*")
                    if raw is not None:
                        _, entries = parse_sitemap_xml(raw)
                        diag["child_sitemap"] = child_final
            break

    # 3. Up to MAX_ARTICLE_PAGES news posts from the sitemap, dated from the page.
    if need_more and entries:
        targets = sitemap_article_candidates(entries, final_url, known)
        diag["sitemap_candidates"] = len(targets)
        fetched = 0
        for url in targets:
            if fetched >= MAX_ARTICLE_PAGES or budget.left() <= 0:
                break
            fetched += 1
            got = _fetch_article(url, budget, timeout=timeout, max_bytes=max_bytes, texts=texts,
                                 via="sitemap_article", today=today, site_url=final_url)
            if got and _key(got["url"]) not in known:
                new.append(got)
                known.add(_key(got["url"]))
        diag["article_pages_fetched"] = fetched

    capped, count = [], 0
    for a in new:
        if a.get("found_via") in NEW_SOURCES:
            if count >= MAX_NEW_ITEMS:
                continue
            count += 1
        capped.append(a)
    # Listing-block items already sit in ``articles``; cap them too.
    out, listed = [], 0
    for a in articles:
        if a.get("found_via") in NEW_SOURCES:
            if listed >= MAX_NEW_ITEMS:
                continue
            listed += 1
        out.append(a)
    metrics = {"requests": budget.requests, "bytes": budget.bytes, "latencies_ms": budget.latencies,
               "legacy_requests": min(legacy, budget.requests), "throttled": budget.throttled, **diag}
    return out + capped, metrics
