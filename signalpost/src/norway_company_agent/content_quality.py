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

import datetime as _dt
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
                      "wp-content", "sok", "search", "login", "logg-inn", "handlekurv", "cart", "checkout"}
# Policy / legal pages are never news, wherever they sit on the site.
POLICY_MARKERS = ("personvern", "privacy", "cookie", "informasjonskapsl", "vilkar", "terms of", "terms-of",
                  "terms and conditions", "salgsbetingelser", "kjopsbetingelser", "angrerett",
                  "databehandler", "gdpr")
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
                      "ingen utlyste", "ikke ledige stillinger", "ingen apne stillinger", "ingen aktuelle stillinger",
                      "no open roles", "no openings", "there are currently no", "ingen stillinger ute",
                      "ingen ledig stilling")
# Standing invitations, not a vacancy: removed before the vacancy markers run.
STANDING_INVITATIONS = ("vi soker alltid", "vi ser alltid etter", "we are always looking", "apen soknad",
                        "open application", "spontaneous application")
_DEADLINE = re.compile(r"(?:soknadsfrist|application deadline|apply by|deadline)\s*:?\s*([^|;]{4,40})")


def _plain(text: Any) -> str:
    value = str(text or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return " ".join(value.split())


MONTHS = {"januar": 1, "january": 1, "jan": 1, "februar": 2, "february": 2, "feb": 2, "mars": 3, "march": 3,
          "mar": 3, "april": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "june": 6, "jun": 6, "juli": 7,
          "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10,
          "october": 10, "okt": 10, "oct": 10, "november": 11, "nov": 11, "desember": 12, "december": 12,
          "des": 12, "dec": 12}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_ISO_DATE = re.compile(r"\b((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})")
_NUM_DATE = re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-]((?:19|20)?\d{2})\b")
_TEXT_DATE = re.compile(r"\b(\d{1,2})\.?\s+(" + _MONTH_RE + r")\.?,?\s+((?:19|20)\d{2})\b")
_TEXT_DATE_US = re.compile(r"\b(" + _MONTH_RE + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})\b")
# Earliest plausible publication date: CMS placeholders default to 1970/0000.
EARLIEST_PUBLICATION = _dt.date(1995, 1, 1)
FUTURE_TOLERANCE_DAYS = 2
STALE_JOB_DAYS = 180


def _safe_date(year: int, month: int, day: int) -> _dt.date | None:
    try:
        return _dt.date(year, month, day)
    except ValueError:
        return None


def parse_date(value: Any) -> _dt.date | None:
    """A full calendar date from ISO, Norwegian numeric (dd.mm.yyyy, day first)
    or written dates ("3. mars 2025", "3 March 2025", "March 3, 2025").
    Year-only or month-only strings return None: not a publication date."""
    text = _plain(value)
    if not text:
        return None
    match = _ISO_DATE.search(text)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = _TEXT_DATE.search(text)
    if match:
        return _safe_date(int(match.group(3)), MONTHS[match.group(2)], int(match.group(1)))
    match = _TEXT_DATE_US.search(text)
    if match:
        return _safe_date(int(match.group(3)), MONTHS[match.group(1)], int(match.group(2)))
    match = _NUM_DATE.search(text)
    if match:
        year = int(match.group(3))
        if year < 100:
            year += 2000
        return _safe_date(year, int(match.group(2)), int(match.group(1)))
    return None


def _today(today: _dt.date | None) -> _dt.date:
    return today or _dt.datetime.now(_dt.timezone.utc).date()


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


_TEXT_DATE_ONLY = re.compile(r"^(?:(?:man|tirs|ons|tors|fre|lor|son)dag\s+|(?:mon|tue|wed|thu|fri|sat|sun)\w*,?\s+)?"
                             r"(?:\d{1,2}\.?\s+(?:" + _MONTH_RE + r")\.?,?(?:\s+\d{4})?|(?:" + _MONTH_RE
                             + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?,?(?:\s+\d{4})?|(?:" + _MONTH_RE + r")\s+\d{4})$")


def is_generic_headline(headline: Any) -> bool:
    plain = _plain(headline).rstrip(" .>»→")
    return plain in GENERIC_LINK_TEXT or bool(_DATE_ONLY.match(plain)) or bool(_TEXT_DATE_ONLY.match(plain))


def _looks_like_slug(headline: str, url: str) -> bool:
    segs = _segments(url)
    last = segs[-1] if segs else ""
    last = re.sub(r"\.(html?|php|aspx?)$", "", last).replace("-", " ").replace("_", " ")
    head = _plain(headline)
    return bool(head) and headline == headline.lower() and head == " ".join(last.split())


def classify_news(article: dict[str, Any], company_name: Any, today: _dt.date | None = None) -> str | None:
    """None when the item is publishable company news; otherwise the reason."""
    headline = str(article.get("headline") or "").strip()
    url = str(article.get("url") or "")
    if not str(article.get("date_published") or "").strip():
        return "no publication date"
    published = parse_date(article.get("date_published"))
    if published is None:
        return "publication date is not a full calendar date"
    if published > _today(today) + _dt.timedelta(days=FUTURE_TOLERANCE_DAYS):
        return "date is in the future, so it is not a publication date"
    if published < EARLIEST_PUBLICATION:
        return "date is a placeholder (before 1995), not a real publication date"
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
    policy_haystack = plain_head + " " + (segs[-1] if segs else "")
    if any(marker in policy_haystack for marker in POLICY_MARKERS):
        return "page is a privacy, cookie or terms page, not news"
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


def careers_vacancy(text: Any, job_ads: int = 0, jsonld_jobs: int = 0,
                    today: _dt.date | None = None) -> tuple[bool, str]:
    """Whether a careers page shows a current opening, with the evidence."""
    plain = " " + _plain(text) + " "
    if jsonld_jobs:
        return True, "structured JobPosting data on the page"
    if job_ads:
        return True, f"{job_ads} single job ad link(s) on the page"
    for marker in NO_VACANCY_MARKERS:
        if marker in plain:
            return False, f'page says "{marker}"'
    deadlines = [d for d in (parse_date(m.group(1)) for m in _DEADLINE.finditer(plain)) if d is not None]
    if deadlines and max(deadlines) < _today(today):
        return False, f"application deadline passed ({max(deadlines).isoformat()})"
    for phrase in STANDING_INVITATIONS:
        plain = plain.replace(phrase, " ")
    for marker in VACANCY_MARKERS:
        if marker in plain:
            return True, f'page text mentions "{marker}"'
    return False, "careers page lists no current opening"


def _url_key(url: Any) -> str:
    """Same page under http/https, www, tracking parameters or index.html."""
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except ValueError:
        return str(url or "")
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = re.sub(r"/(?:index\.(?:html?|php|aspx?))?$", "", parsed.path or "").rstrip("/")
    query = urllib.parse.urlencode(sorted(
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
        if not k.casefold().startswith(("utm_", "fbclid", "gclid", "mc_"))))
    return f"{host}{path}?{query}" if query else f"{host}{path}"


# A recruiting landing page, not one vacancy: "Jobb i Torghatten", "Karriere hos X",
# "Work for us", "Søk plass som lærling og kadett". Only applied to undated ads.
_GENERIC_JOB_TITLE = re.compile(
    r"^(?:jobb(?:e)?|karriere|careers?|jobs?|work|ledige stillinger|stillinger|join us|bli med|bli en av oss)"
    r"(?:\s+(?:i|hos|for|at|in|med|with|us|oss)\b.*)?$"
    r"|^(?:sok|soke|apply)\s+(?:plass|jobb|stilling)?\s*(?:som\s+)?(?:laerling|larling|kadett|apprentice)")


def classify_job(posting: dict[str, Any], today: _dt.date | None = None) -> str | None:
    """None when a job ad is plausibly current; otherwise the reason."""
    now = _today(today)
    title = _plain(posting.get("title")).strip()
    if (title and _GENERIC_JOB_TITLE.match(title)
            and not posting.get("date_posted") and not posting.get("valid_through")):
        return "recruiting landing page, not a single job ad"
    valid_through = parse_date(posting.get("valid_through"))
    if valid_through is not None and valid_through < now:
        return f"application deadline passed ({valid_through.isoformat()})"
    posted = parse_date(posting.get("date_posted"))
    if posted is not None and posted > now + _dt.timedelta(days=FUTURE_TOLERANCE_DAYS):
        return "posting date is in the future"
    if valid_through is None and posted is not None and (now - posted).days > STALE_JOB_DAYS:
        return f"posted {posted.isoformat()} with no deadline: older than {STALE_JOB_DAYS} days, likely filled"
    return None


def apply_content_quality(profile: dict[str, Any], website: dict[str, Any],
                          today: _dt.date | None = None) -> dict[str, Any]:
    """Split the site's news and careers pages into published and excluded
    (with reasons). Runs after the identity gate, on publishable sites only."""
    value = website.get("value") if isinstance(website, dict) else None
    if not isinstance(value, dict):
        return website
    kept, excluded = [], []
    seen_urls: set[str] = set()
    seen_items: set[tuple[str, str]] = set()
    for article in value.get("news_articles") or []:
        key = _url_key(str(article.get("url") or "").split("#")[0])
        reason = classify_news(article, profile.get("name"), today)
        published = parse_date(article.get("date_published"))
        item_key = (_plain(article.get("headline")), published.isoformat() if published else "")
        if reason is None and key in seen_urls:
            reason = "duplicate of another item at the same address"
        if reason is None and item_key in seen_items:
            reason = "duplicate of another item with the same headline and date"
        if reason is None:
            raw_date = str(article.get("date_published") or "")
            if published is not None and not _ISO_DATE.match(raw_date.strip()):
                # "3. mars 2025" / "05.10.2026" -> ISO, so readers never guess day vs month.
                article = {**article, "date_published": published.isoformat(), "date_published_raw": raw_date}
            kept.append(article)
            seen_urls.add(key)
            seen_items.add(item_key)
        else:
            excluded.append({**article, "excluded_reason": reason})
    value["news_articles"] = kept
    value["excluded_news_articles"] = excluded
    jobs_kept, jobs_excluded = [], []
    for posting in value.get("job_postings") or []:
        reason = classify_job(posting, today)
        if reason is None:
            jobs_kept.append(posting)
        else:
            jobs_excluded.append({**posting, "excluded_reason": reason})
    value["job_postings"] = jobs_kept
    value["excluded_job_postings"] = jobs_excluded
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
