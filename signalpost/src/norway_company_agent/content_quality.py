"""Second-pass checks on crawled news and careers pages.

Organiser feedback (1 Nov 2026) on a scored sample: about half the "news" links
were ordinary site pages (products, privacy, footer text) or opinion/guide/ESG
pieces on the company's own site, and two careers pages listed no opening.
Rule: keep a dated item only when it is plausibly news *about the company*;
keep a careers page only when it shows a current vacancy. Everything excluded
is kept with a plain-language reason, so an uncertain match is left blank with
the reason instead of being published.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse
from typing import Any

NEWS_SECTIONS = {"nyheter", "nyhet", "aktuelt", "news", "press", "presse", "pressemelding",
                 "pressemeldinger", "presserom", "media", "medier", "siste-nytt", "newsroom",
                 "press-releases", "pressreleases", "investor", "investors", "nyhetsarkiv", "artikler-nyheter",
                 # the company's own dated events (fairs, seminars, open days)
                 "arrangement", "arrangementer", "events", "event", "messer-og-seminarer", "seminarer",
                 "messer", "kurs-og-arrangementer"}
SITE_PAGE_SEGMENTS = {"produkt", "produkter", "product", "products", "shop", "butikk", "nettbutikk",
                      "tjenester", "tjeneste", "services", "service", "personvern", "privacy", "cookies",
                      "cookie", "kontakt", "contact", "om-oss", "about", "about-us", "diverse", "referanser",
                      "references", "prosjekter", "projects", "kategori", "category", "tag", "tags", "author",
                      "forfatter", "turer", "tours", "meny", "menu", "priser", "prices", "faq", "vilkar",
                      "terms", "ansatte", "team", "kurs", "courses",
                      "wp-content", "sok", "search"}
PLACEHOLDER_TITLES = {"hello world", "hello world!", "hei verden", "hei, verden!", "hei verden!", "sample page",
                      "eksempelside", "test", "untitled"}
OPINION_MARKERS = ("kronikk", "debatt", "leserinnlegg", "innlegg:", "mening:", "opinion", "kommentar:", "op-ed")
# Strong markers reject anywhere; weak ones ("hvordan" = how) only outside a
# news section, where an event title such as "Fra hvorfor til hvordan" is news.
GUIDE_MARKERS = (" tips", "tips ", "guide", "hva koster", "ting du", "things you", "dette bor du",
                 "derfor bor", "oppskrift", "slik reduserer", "slik velger", "slik unngar")
WEAK_GUIDE_MARKERS = ("hvordan ", "slik ", "hva er ", "how to", "what is")
ESG_MARKERS = ("esg", "baerekraft", "sustainability", "klimaregnskap", "apenhetsloven", "aktsomhetsvurdering",
               "samfunnsansvar", "csr ", "klimaavtrykk")
LEGAL_FORMS = {"as", "asa", "ans", "da", "sa", "ba", "nuf", "ks", "hf", "iks", "sf", "kf", "stiftelse", "og",
               "and", "the", "norge", "norway", "holding"}

VACANCY_MARKERS = ("soknadsfrist", "ledig stilling", "ledige stillinger:", "vi soker", "vi ser etter",
                   "stillingsprosent", "tiltredelse", "application deadline", "apply by", "we are hiring",
                   "we are looking for", "open position", "vacancy", "fast stilling", "vikariat",
                   "stillingstittel", "arbeidssted:")
NO_VACANCY_MARKERS = ("ingen ledige stillinger", "ingen ledige", "for tiden ingen", "har ingen ledige",
                      "no open positions", "no vacancies", "no current openings", "ikke noen ledige",
                      "ingen utlyste")


def _plain(text: Any) -> str:
    value = str(text or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return " ".join(value.split())


def _segments(url: str) -> list[str]:
    try:
        path = urllib.parse.urlparse(url or "").path
    except ValueError:
        return []
    return [_plain(seg) for seg in path.split("/") if seg]


def _name_tokens(name: Any) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _plain(name)) if t not in LEGAL_FORMS and len(t) >= 3]


GENERIC_LINK_TEXT = {"les mer", "les mer her", "read more", "mer", "se mer", "les hele saken", "les artikkelen",
                     "more", "learn more", "les", "her", "klikk her", "click here", "videre", "continue reading"}
_DATE_ONLY = re.compile(r"^[\d\s./\-:]+$")


def is_generic_headline(headline: Any) -> bool:
    plain = _plain(headline).rstrip(" .>»→")
    return plain in GENERIC_LINK_TEXT or bool(_DATE_ONLY.match(plain))


def _looks_like_slug(headline: str, url: str) -> bool:
    segs = _segments(url)
    last = segs[-1] if segs else ""
    last = re.sub(r"\.(html?|php|aspx?)$", "", last).replace("-", " ").replace("_", " ")
    head = _plain(headline)
    return bool(head) and headline == headline.lower() and head == " ".join(last.split())


def classify_news(article: dict[str, Any], company_name: Any) -> str | None:
    """None when the item is publishable company news; otherwise the reason."""
    headline = str(article.get("headline") or "").strip()
    url = str(article.get("url") or "")
    if not str(article.get("date_published") or "").strip():
        return "no publication date"
    if not headline:
        return "no headline"
    if not url.startswith(("http://", "https://")):
        return "no article address"
    if is_generic_headline(headline):
        return 'headline is link text ("read more") or a date, not an article title' 
    plain_head = _plain(headline)
    if plain_head.rstrip("!.") in {t.rstrip("!.") for t in PLACEHOLDER_TITLES}:
        return "placeholder post from the site template"
    if _looks_like_slug(headline, url):
        return "headline is only the page address, not an article title"
    segs = _segments(url)
    in_news_section = any(seg in NEWS_SECTIONS for seg in segs)
    if not in_news_section and any(seg in SITE_PAGE_SEGMENTS for seg in segs):
        return "page is an ordinary site page (product, service, policy or contact), not news"
    haystack = plain_head + " " + " ".join(segs)
    if any(marker in haystack for marker in OPINION_MARKERS):
        return "page is an opinion article"
    padded = " " + plain_head + " "
    if any(marker in padded for marker in GUIDE_MARKERS) or plain_head.endswith("?") \
            or (not in_news_section and any(marker in padded for marker in WEAK_GUIDE_MARKERS)):
        return "page is a guide or advice article, not news about the company"
    if any(marker in " " + haystack + " " for marker in ESG_MARKERS):
        return "page is ESG/sustainability content, not dated company news"
    tokens = _name_tokens(company_name)
    names_company = bool(tokens) and any(token in plain_head for token in tokens)
    if not in_news_section and not names_company:
        return "not in a news or press section and the headline does not name the company"
    return None


def careers_vacancy(text: Any, job_ads: int = 0, jsonld_jobs: int = 0) -> tuple[bool, str]:
    """Whether a careers page shows a current opening, with the evidence."""
    plain = " " + _plain(text) + " "
    if jsonld_jobs:
        return True, "structured JobPosting data on the page"
    if job_ads:
        return True, f"{job_ads} single job ad link(s) on the page"
    for marker in NO_VACANCY_MARKERS:
        if marker in plain:
            return False, f'page says "{marker}"'
    for marker in VACANCY_MARKERS:
        if marker in plain:
            return True, f'page text mentions "{marker}"'
    return False, "careers page lists no current opening"


def apply_content_quality(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    """Split the site's news and careers pages into published and excluded
    (with reasons). Runs after the identity gate, on publishable sites only."""
    value = website.get("value") if isinstance(website, dict) else None
    if not isinstance(value, dict):
        return website
    kept, excluded = [], []
    seen_urls: set[str] = set()
    for article in value.get("news_articles") or []:
        key = str(article.get("url") or "").split("#")[0].rstrip("/")
        reason = classify_news(article, profile.get("name"))
        if reason is None and key in seen_urls:
            reason = "duplicate of another item at the same address"
        if reason is None:
            kept.append(article)
            seen_urls.add(key)
        else:
            excluded.append({**article, "excluded_reason": reason})
    value["news_articles"] = kept
    value["excluded_news_articles"] = excluded
    careers_kept, careers_excluded = [], []
    for page in value.get("careers_pages") or []:
        if page.get("vacancy") is False:
            careers_excluded.append({**page, "excluded_reason": page.get("vacancy_evidence") or "careers page lists no current opening"})
        elif page.get("vacancy") is True:
            careers_kept.append(page)
        else:  # older capture without the check: leave blank rather than guess
            careers_excluded.append({**page, "excluded_reason": "vacancy not checked on this capture"})
    value["careers_pages"] = careers_kept
    value["excluded_careers_pages"] = careers_excluded
    website["value"] = value
    return website
