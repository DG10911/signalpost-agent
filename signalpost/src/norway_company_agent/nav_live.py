"""Live NAV job ads for the evaluation batch, exact-entity by organisation number.

NAV's official public vacancy feed (pam-stilling-feed.nav.no, public token, no
account) is read once per run in a background thread, from ``days`` back to
now. The feed's list view carries only the employer *name*, so names are used
solely to choose which ads to open. An ad is published for a company only when
the ad's own ``employer.orgnr`` is that company's organisation number, or a
sub-unit whose Brreg record names the company as ``overordnetEnhet``. Ads with
an anonymous employer are skipped.

The result has the ``orgnr -> [job]`` shape of ``nav_jobs.load_index`` so the
existing observation code (``nav_jobs.observations_for``) publishes it.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, Iterable

from .nav_jobs import FEED_BASE, UA

BRREG_SUBUNIT = "https://data.brreg.no/enhetsregisteret/api/underenheter/"

_LEGAL_FORMS = {"as", "asa", "ans", "da", "sa", "ba", "nuf", "ks", "enk", "hf", "iks", "sf", "kf",
                "fkf", "stiftelse", "sti", "bbl", "brl", "ab"}


def name_key(name: str) -> str:
    """Casefolded, accent-free name without punctuation or a trailing legal form."""
    text = (name or "").casefold().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    tokens = re.findall(r"[a-z0-9]+", text)
    while tokens and tokens[-1] in _LEGAL_FORMS:
        tokens.pop()
    return " ".join(tokens)


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


class NavLive:
    """Reads the feed in the background; ``index()`` returns the exact-entity jobs."""

    def __init__(self, companies: Iterable[tuple[str, str]], *, days: int = 45,
                 max_pages: int = 200, max_seconds: float = 600.0,
                 max_details_per_company: int = 5, timeout: float = 30.0) -> None:
        self.by_key: dict[str, set[str]] = {}
        for org, name in companies:
            key = name_key(name)
            if len(key.replace(" ", "")) >= 3:
                self.by_key.setdefault(key, set()).add(_digits(org))
        self.days = days
        self.max_pages = max_pages
        self.max_seconds = max_seconds
        self.max_details = max_details_per_company
        self.timeout = timeout
        self.requests = 0
        self.pages = 0
        self.error: str | None = None
        self._lock = threading.Lock()
        self._index: dict[str, list[dict[str, Any]]] = {}
        self._parent_cache: dict[str, str | None] = {}
        self.done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nav-live", daemon=True)

    def start(self) -> "NavLive":
        if not self.by_key:
            self.done.set()
        else:
            self._thread.start()
        return self

    def index(self, wait_seconds: float = 0.0) -> dict[str, list[dict[str, Any]]]:
        self.done.wait(timeout=max(0.0, wait_seconds))
        with self._lock:
            return {org: list(jobs) for org, jobs in self._index.items()}

    def diagnostic(self) -> dict[str, Any]:
        with self._lock:
            return {"source": "pam-stilling-feed.nav.no", "days": self.days, "pages": self.pages,
                    "requests": self.requests, "companies_with_jobs": len(self._index),
                    "jobs": sum(len(v) for v in self._index.values()),
                    "complete": self.done.is_set() and self.error is None, "error": self.error}

    # -- network ------------------------------------------------------------ #

    def _get(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        hdrs = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
        for attempt in range(3):
            with self._lock:
                self.requests += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=self.timeout) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < 2:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        wait = min(10.0, float(retry_after)) if retry_after else 2.0 * (attempt + 1)
                    except ValueError:
                        wait = 2.0 * (attempt + 1)
                    time.sleep(wait)
                    continue
                return exc.code, b""
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                return 0, b""
        return 0, b""

    def _token(self) -> str | None:
        status, body = self._get(FEED_BASE + "/api/publicToken", {"Accept": "text/plain"})
        if status != 200:
            return None
        for tok in body.decode("utf-8", errors="replace").split():
            if tok.startswith("eyJ"):
                return tok.strip()
        return None

    def _parent_of(self, subunit: str) -> str | None:
        if subunit in self._parent_cache:
            return self._parent_cache[subunit]
        status, body = self._get(BRREG_SUBUNIT + subunit)
        parent = None
        if status == 200:
            try:
                parent = _digits(json.loads(body).get("overordnetEnhet")) or None
            except ValueError:
                parent = None
        self._parent_cache[subunit] = parent
        return parent

    def _run(self) -> None:
        started = time.monotonic()
        try:
            token = self._token()
            if not token:
                self.error = "NAV public token unavailable"
                return
            auth = {"Authorization": f"Bearer {token}"}
            since = format_datetime(datetime.now(timezone.utc) - timedelta(days=self.days), usegmt=True)
            active: dict[str, str] = {}  # uuid -> businessName
            url: str | None = FEED_BASE + "/api/v1/feed"
            first = True
            while url and self.pages < self.max_pages:
                if time.monotonic() - started > self.max_seconds:
                    self.error = f"feed read stopped after {self.max_seconds:.0f}s ({self.pages} pages)"
                    break
                status, body = self._get(url, {**auth, **({"If-Modified-Since": since} if first else {})})
                first = False
                if status != 200:
                    self.error = f"feed page HTTP {status} after {self.pages} pages"
                    break
                page = json.loads(body)
                with self._lock:
                    self.pages += 1
                items = page.get("items") or []
                for item in items:
                    entry = item.get("_feed_entry") or {}
                    uuid = str(entry.get("uuid") or item.get("id") or "")
                    if not uuid:
                        continue
                    if entry.get("status") == "ACTIVE":
                        active[uuid] = str(entry.get("businessName") or "")
                    else:
                        active.pop(uuid, None)
                nxt = page.get("next_url")
                url = (FEED_BASE + nxt if nxt and nxt.startswith("/") else nxt) if nxt and items else None
            self._match(active, auth, started)
        finally:
            self.done.set()

    def _match(self, active: dict[str, str], auth: dict[str, str], started: float) -> None:
        opened: dict[str, int] = {}
        for uuid, business in active.items():
            orgs = self.by_key.get(name_key(business))
            if not orgs:
                continue
            if all(opened.get(org, 0) >= self.max_details for org in orgs):
                continue
            if time.monotonic() - started > self.max_seconds:
                self.error = self.error or "stopped before all matching ads were opened"
                return
            for org in orgs:
                opened[org] = opened.get(org, 0) + 1
            status, body = self._get(f"{FEED_BASE}/api/v1/feedentry/{uuid}", auth)
            if status != 200:
                continue
            try:
                detail = json.loads(body)
            except ValueError:
                continue
            job = self._job(uuid, detail)
            if job is None:
                continue
            employer_org = job["orgnr"]
            owner = employer_org if employer_org in orgs else None
            if owner is None:
                parent = self._parent_of(employer_org)
                owner = parent if parent in orgs else None
            if owner is None:
                continue
            with self._lock:
                self._index.setdefault(owner, []).append({**job, "orgnr": owner,
                                                          "employer_orgnr": employer_org})

    @staticmethod
    def _job(uuid: str, detail: dict[str, Any]) -> dict[str, Any] | None:
        if str(detail.get("status") or "").upper() != "ACTIVE":
            return None
        ad = detail.get("ad_content") or {}
        employer = ad.get("employer") or {}
        orgnr = _digits(employer.get("orgnr"))
        if len(orgnr) != 9:
            return None
        if re.fullmatch(r"\s*anonym(?:\s+arbeidsgiver)?\s*", str(employer.get("name") or ""), re.I):
            return None
        locations = ad.get("workLocations") or []
        municipal = (locations[0] or {}).get("municipal") if isinstance(locations, list) and locations else None
        return {
            "job_id": uuid,
            "orgnr": orgnr,
            "employer_name": employer.get("name"),
            "title": ad.get("title"),
            "url": f"/api/v1/feedentry/{uuid}",
            "published": ad.get("published"),
            "updated": ad.get("updated") or detail.get("sistEndret"),
            "expires": ad.get("expires"),
            "municipal": municipal,
            "content_sha256": hashlib.sha256(
                json.dumps(detail, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
        }
