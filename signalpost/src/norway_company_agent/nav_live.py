"""Live NAV job ads for the evaluation batch, exact-entity by organisation number.

NAV's official public vacancy feed (pam-stilling-feed.nav.no, NAV's openly
published public token, no account) is read once per run in a background
thread. The run has a fixed NAV request budget shared across the batch:

1. **Active set.** The feed is walked from ``days`` back to now with several
   parallel cursors (each cursor starts at its own ``If-Modified-Since`` and
   stops where the next one begins), so the whole currently-active ad set is
   read in a few minutes. For every ad the latest feed entry wins: ACTIVE keeps
   it, INACTIVE removes it.
2. **Owner map.** NAV ads name the *workplace* — usually a Brreg sub-unit
   (underenhet / virksomhet) — not the legal entity. The Brreg sub-unit bulk
   (keyless, one download, cached on disk next to the entity bulk) maps every
   sub-unit of a batch company to its parent (``overordnetEnhet``).
3. **Candidates.** The feed's list view carries only the employer *name*, so
   names (the company's and its sub-units') are used solely to choose which
   ads to open; an exact name match is opened first, then a token-prefix match
   on distinctive names.
4. **Proof.** An ad is published for a company only when the ad's own
   ``employer.orgnr`` is that company's organisation number, or one of its
   registered sub-units. A matching name alone never publishes. Expired,
   inactive, anonymous-employer and open-application ads are dropped, and
   repeats of the same vacancy are collapsed.

The result has the ``orgnr -> [job]`` shape of ``nav_jobs.load_index`` so the
existing observation code (``nav_jobs.observations_for``) publishes it.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Iterable

from .nav_jobs import FEED_BASE, UA, public_ad_url

BRREG_SUBUNIT = "https://data.brreg.no/enhetsregisteret/api/underenheter/"
BRREG_SUBUNIT_BULK = "https://data.brreg.no/enhetsregisteret/api/underenheter/lastned/csv"

_LEGAL_FORMS = {"as", "asa", "ans", "da", "sa", "ba", "nuf", "ks", "enk", "hf", "iks", "sf", "kf",
                "fkf", "stiftelse", "sti", "bbl", "brl", "ab"}

# "Send an open application" / talent-pool ads are not a single current vacancy.
_NOT_A_VACANCY = re.compile(
    r"\b(?:åpen\s+søknad|apen\s+soknad|generell\s+søknad|open\s+application|talentbank|talent\s*pool"
    r"|ønsker\s+du\s+å\s+jobbe\s+hos\s+oss\??\s*$)", re.I)

# Name-prefix candidates: the shorter key must be at least this distinctive.
_FEED_PAGES_PER_DAY = 2.5

_MIN_PREFIX_CHARS = 5
# A one-word key that prefixes more active ads than this is generic ("fjord");
# a multi-word name ("prima assistanse") stays distinctive across a chain's ads.
_MAX_PREFIX_ADS = {1: 25, 2: 200}


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


def _parse_dt(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_subunits(source: Any, parents: set[str]) -> dict[str, tuple[str, str]]:
    """``{subunit_orgnr: (parent_orgnr, subunit_name)}`` for sub-units of ``parents``.

    ``source`` is a path to (or bytes of) the Brreg ``underenheter`` CSV bulk,
    gzipped or plain."""
    binary = io.BytesIO(bytes(source)) if isinstance(source, (bytes, bytearray)) else open(source, "rb")
    with binary:
        magic = binary.read(2)
        binary.seek(0)
        stream = gzip.GzipFile(fileobj=binary) if magic == b"\x1f\x8b" else binary
        text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
        out: dict[str, tuple[str, str]] = {}
        for rec in csv.DictReader(text):
            parent = _digits(rec.get("overordnetEnhet"))
            if parent in parents:
                sub = _digits(rec.get("organisasjonsnummer"))
                if len(sub) == 9:
                    out[sub] = (parent, rec.get("navn") or "")
        return out


def is_single_vacancy(title: Any) -> bool:
    return not _NOT_A_VACANCY.search(str(title or ""))


class NavLive:
    """Reads the feed in the background; ``index()`` returns the exact-entity jobs."""

    def __init__(self, companies: Iterable[tuple[str, str]], *, days: int = 90,
                 subunits: dict[str, tuple[str, str]] | None = None,
                 subunits_path: str | Path | None = None, download_subunits: bool = True,
                 max_requests: int = 600, max_seconds: float = 600.0,
                 max_details_per_company: int = 15, cursors: int = 6, detail_workers: int = 4,
                 timeout: float = 30.0, now: datetime | None = None) -> None:
        self.companies: dict[str, str] = {}
        for org, name in companies:
            org = _digits(org)
            if len(org) == 9:
                self.companies[org] = name or ""
        self.subunits: dict[str, tuple[str, str]] = dict(subunits or {})
        self.subunits_path = Path(subunits_path) if subunits_path else None
        self.download_subunits = download_subunits
        self.subunit_source: str = "given" if subunits else "none"
        self.days = days
        self.max_requests = max_requests
        self.max_seconds = max_seconds
        self.max_details = max_details_per_company
        self.cursors = max(1, cursors)
        self.detail_workers = max(1, detail_workers)
        self.timeout = timeout
        self.now = now
        self.requests = 0
        self.pages = 0
        self.details_opened = 0
        self.active_ads = 0
        self.candidates = 0
        self.dropped: dict[str, int] = {}
        self.seconds: float | None = None
        self.error: str | None = None
        self._lock = threading.Lock()
        self._index: dict[str, list[dict[str, Any]]] = {}
        self._parent_cache: dict[str, str | None] = {}
        self.by_key: dict[str, set[str]] = {}
        self.done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nav-live", daemon=True)

    def start(self) -> "NavLive":
        if not self.companies:
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
                    "requests": self.requests, "request_budget": self.max_requests,
                    "active_ads": self.active_ads, "candidates": self.candidates,
                    "details_opened": self.details_opened,
                    "subunits_mapped": len(self.subunits), "subunit_source": self.subunit_source,
                    "companies_with_jobs": len(self._index),
                    "jobs": sum(len(v) for v in self._index.values()), "dropped": dict(self.dropped),
                    "seconds": self.seconds,
                    "complete": self.done.is_set() and self.error is None, "error": self.error}

    # -- network ------------------------------------------------------------ #

    def _budget_left(self) -> int:
        with self._lock:
            return self.max_requests - self.requests

    def _get(self, url: str, headers: dict[str, str] | None = None, timeout: float | None = None) -> tuple[int, bytes]:
        hdrs = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
        for attempt in range(3):
            with self._lock:
                if self.requests >= self.max_requests:
                    return 0, b""
                self.requests += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs),
                                            timeout=timeout or self.timeout) as resp:
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
        """Live Brreg lookup — only used when the sub-unit bulk is unavailable."""
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

    def _load_subunits(self) -> None:
        if self.subunits:
            return
        parents = set(self.companies)
        try:
            if self.subunits_path and self.subunits_path.exists():
                self.subunits = load_subunits(self.subunits_path, parents)
                self.subunit_source = f"cache:{self.subunits_path.name}"
                return
            if not self.download_subunits:
                return
            status, body = self._get(BRREG_SUBUNIT_BULK, {"Accept": "application/vnd.brreg.enhetsregisteret.underenhet.v2+gzip"},
                                     timeout=300.0)
            if status != 200 or not body:
                self.subunit_source = f"download failed (HTTP {status})"
                return
            if self.subunits_path:
                try:
                    self.subunits_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self.subunits_path.with_suffix(self.subunits_path.suffix + ".part")
                    tmp.write_bytes(body)
                    tmp.replace(self.subunits_path)
                except OSError:
                    pass
            self.subunits = load_subunits(body, parents)
            self.subunit_source = "download"
        except (OSError, ValueError, EOFError, csv.Error) as exc:
            self.subunit_source = f"unavailable ({type(exc).__name__})"

    # -- feed --------------------------------------------------------------- #

    def _walk(self, start: datetime, stop: datetime | None, auth: dict[str, str],
              deadline: float, max_pages: int) -> dict[str, tuple[datetime, str, str]]:
        """Walk one cursor from ``start``; keep entries modified in [start, stop)."""
        seen: dict[str, tuple[datetime, str, str]] = {}
        url: str | None = FEED_BASE + "/api/v1/feed"
        first = True
        pages = 0
        while url and pages < max_pages:
            if time.monotonic() > deadline:
                self.error = self.error or "feed read stopped at the NAV time limit"
                break
            status, body = self._get(url, {**auth, **({"If-Modified-Since": format_datetime(start, usegmt=True)} if first else {})})
            first = False
            if status != 200:
                self.error = self.error or f"feed page HTTP {status}"
                break
            try:
                page = json.loads(body)
            except ValueError:
                self.error = self.error or "feed page was not JSON"
                break
            pages += 1
            with self._lock:
                self.pages += 1
            items = page.get("items") or []
            past_stop = False
            for item in items:
                entry = item.get("_feed_entry") or {}
                uuid = str(entry.get("uuid") or item.get("id") or "")
                modified = _parse_dt(item.get("date_modified") or entry.get("sistEndret"))
                if not uuid or modified is None or modified < start:
                    continue
                if stop is not None and modified >= stop:
                    past_stop = True
                    continue
                prev = seen.get(uuid)
                if prev is None or modified >= prev[0]:
                    seen[uuid] = (modified, str(entry.get("status") or ""), str(entry.get("businessName") or ""))
            if past_stop:
                break
            nxt = page.get("next_url")
            url = (FEED_BASE + nxt if nxt and nxt.startswith("/") else nxt) if nxt and items else None
        return seen

    def _active_set(self, auth: dict[str, str], deadline: float) -> dict[str, str]:
        now = self.now or datetime.now(timezone.utc)
        begin = now - timedelta(days=self.days)
        step = (now - begin) / self.cursors
        bounds = [begin + step * i for i in range(self.cursors)] + [None]
        # The feed carries ~1.5-1.8 pages of 1,000 entries per day; allow 2.5 per
        # day of window, and never more than ~2/3 of the NAV budget (details
        # need the rest).
        feed_pages = min(int(self.days * _FEED_PAGES_PER_DAY) + 2 * self.cursors, (2 * self.max_requests) // 3)
        per_cursor = max(1, -(-feed_pages // self.cursors))
        with ThreadPoolExecutor(max_workers=self.cursors) as pool:
            parts = list(pool.map(lambda i: self._walk(bounds[i], bounds[i + 1], auth, deadline, per_cursor),
                                  range(self.cursors)))
        latest: dict[str, tuple[datetime, str, str]] = {}
        for part in parts:
            for uuid, rec in part.items():
                if uuid not in latest or rec[0] >= latest[uuid][0]:
                    latest[uuid] = rec
        return {uuid: business for uuid, (_, status, business) in latest.items() if status == "ACTIVE"}

    # -- matching ----------------------------------------------------------- #

    def _build_keys(self) -> None:
        self.by_key = {}
        for org, name in self.companies.items():
            key = name_key(name)
            if len(key.replace(" ", "")) >= 3:
                self.by_key.setdefault(key, set()).add(org)
        for _sub, (parent, name) in self.subunits.items():
            key = name_key(name)
            if parent in self.companies and len(key.replace(" ", "")) >= 3:
                self.by_key.setdefault(key, set()).add(parent)

    def owner_of(self, employer_orgnr: str) -> str | None:
        """The batch company an employer orgnr belongs to (itself or its parent)."""
        if employer_orgnr in self.companies:
            return employer_orgnr
        sub = self.subunits.get(employer_orgnr)
        if sub and sub[0] in self.companies:
            return sub[0]
        return None

    def candidates_for(self, active: dict[str, str]) -> list[tuple[int, str, set[str]]]:
        """``[(tier, uuid, candidate_orgs)]`` of ads worth opening.

        Tier 0: the ad's employer name equals the company's or a sub-unit's
        name. Tier 1: one name is a whole-word prefix of the other. Tier 2: the
        company name appears inside a longer workplace name. Tier 3: the ad
        starts with the same rare leading word(s). Tiers 1-3 only use names
        that are not generic across the active ads. Selection only — the
        ad's own orgnr decides ownership."""
        if not self.by_key:
            self._build_keys()
        ad_keys = {uuid: name_key(business) for uuid, business in active.items()}
        out: list[tuple[int, str, set[str]]] = []
        prefixable: dict[str, set[str]] = {k: v for k, v in self.by_key.items()
                                           if len(k.replace(" ", "")) >= _MIN_PREFIX_CHARS}
        # How many active ads each company key prefixes (generic keys are skipped).
        prefix_hits: dict[str, list[str]] = {}
        first_token: dict[str, list[str]] = {}
        for key in prefixable:
            first_token.setdefault(key.split(" ", 1)[0], []).append(key)
        contain_hits: dict[str, list[str]] = {}
        for uuid, akey in ad_keys.items():
            if not akey:
                continue
            orgs = self.by_key.get(akey)
            if orgs:
                out.append((0, uuid, set(orgs)))
                continue
            for key in first_token.get(akey.split(" ", 1)[0], ()):
                if akey.startswith(key + " ") or (key.startswith(akey + " ") and len(akey.replace(" ", "")) >= _MIN_PREFIX_CHARS):
                    prefix_hits.setdefault(key, []).append(uuid)
            # The company name inside a longer workplace name
            # ("Dely AS Backstube Stavanger" -> "backstube").
            tokens = akey.split(" ")
            for i in range(1, len(tokens)):
                for key in first_token.get(tokens[i], ()):
                    if f" {' '.join(tokens[i:])} ".startswith(f" {key} "):
                        contain_hits.setdefault(key, []).append(uuid)
        # Tier 3: same leading word(s) as the company or one of its sub-units
        # ("Laeringsverkstedet Haugerasen Idrettsbarnehage" for "Laeringsverkstedet
        # Haugerasen Barnehage"), when that lead is rare among active ads.
        by_lead: dict[str, list[str]] = {}
        for uuid, akey in ad_keys.items():
            tokens = akey.split(" ") if akey else []
            if tokens:
                by_lead.setdefault(tokens[0], []).append(uuid)
                if len(tokens) > 1:
                    by_lead.setdefault(" ".join(tokens[:2]), []).append(uuid)
        lead_hits: dict[str, list[str]] = {}
        for key in self.by_key:
            tokens = key.split(" ")
            if len(tokens[0]) >= 3 and len(by_lead.get(tokens[0], ())) <= _MAX_PREFIX_ADS[1]:
                lead = tokens[0]
            elif len(tokens) > 1 and len(by_lead.get(" ".join(tokens[:2]), ())) <= _MAX_PREFIX_ADS[2]:
                lead = " ".join(tokens[:2])
            else:
                continue
            if by_lead.get(lead):
                lead_hits[key] = by_lead[lead]
        tiers: dict[str, tuple[int, set[str]]] = {}
        for tier, hits in ((1, prefix_hits), (2, contain_hits)):
            for key, uuids in hits.items():
                if len(set(uuids)) > _MAX_PREFIX_ADS[min(2, key.count(" ") + 1)]:
                    continue
                for uuid in uuids:
                    prev = tiers.get(uuid)
                    if prev is None:
                        tiers[uuid] = (tier, set(prefixable[key]))
                    elif prev[0] == tier:
                        prev[1].update(prefixable[key])
        for key, uuids in lead_hits.items():
            for uuid in uuids:
                prev = tiers.get(uuid)
                if prev is None:
                    tiers[uuid] = (3, set(self.by_key[key]))
                elif prev[0] == 3:
                    prev[1].update(self.by_key[key])
        out.extend((tier, uuid, orgs) for uuid, (tier, orgs) in tiers.items())
        out.sort(key=lambda c: (c[0], c[1]))
        return out

    def _run(self) -> None:
        started = time.monotonic()
        deadline = started + self.max_seconds
        try:
            loader = threading.Thread(target=self._load_subunits, name="nav-subunits", daemon=True)
            loader.start()
            token = self._token()
            if not token:
                self.error = "NAV public token unavailable"
                return
            auth = {"Authorization": f"Bearer {token}"}
            active = self._active_set(auth, deadline)
            self.active_ads = len(active)
            loader.join(timeout=max(0.0, deadline - time.monotonic()))
            self._build_keys()
            self._match(active, auth, deadline)
        finally:
            self.seconds = round(time.monotonic() - started, 1)
            self.done.set()

    def _drop(self, reason: str) -> None:
        with self._lock:
            self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def _match(self, active: dict[str, str], auth: dict[str, str], deadline: float) -> None:
        cands = self.candidates_for(active)
        self.candidates = len(cands)
        opened: dict[str, int] = {}
        plan: list[tuple[str, set[str]]] = []
        for _tier, uuid, orgs in cands:
            if all(opened.get(org, 0) >= self.max_details for org in orgs):
                continue
            for org in orgs:
                opened[org] = opened.get(org, 0) + 1
            plan.append((uuid, orgs))
        found: dict[str, dict[str, dict[str, Any]]] = {}

        def fetch(item: tuple[str, set[str]]) -> None:
            uuid, orgs = item
            if time.monotonic() > deadline or self._budget_left() <= 0:
                self._drop("budget")
                return
            status, body = self._get(f"{FEED_BASE}/api/v1/feedentry/{uuid}", auth)
            retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if status != 200:
                self._drop(f"http_{status}")
                return
            with self._lock:
                self.details_opened += 1
            try:
                detail = json.loads(body)
            except ValueError:
                self._drop("not_json")
                return
            job, reason = self.parse_detail(uuid, detail, now=self.now)
            if job is None:
                self._drop(reason)
                return
            employer_org = job["orgnr"]
            owner = self.owner_of(employer_org)
            if owner is None and not self.subunits and employer_org not in self.companies:
                parent = self._parent_of(employer_org)
                owner = parent if parent in self.companies else None
            if owner is None:
                self._drop("employer_not_in_batch")
                return
            with self._lock:
                found.setdefault(owner, {})[uuid] = {**job, "orgnr": owner, "employer_orgnr": employer_org,
                                                     "retrieved_at": retrieved_at}

        with ThreadPoolExecutor(max_workers=self.detail_workers) as pool:
            list(pool.map(fetch, plan))
        index = {org: dedupe_jobs(list(jobs.values())) for org, jobs in found.items()}
        with self._lock:
            self._index = {org: jobs for org, jobs in index.items() if jobs}

    @staticmethod
    def parse_detail(uuid: str, detail: dict[str, Any], *, now: datetime | None = None) -> tuple[dict[str, Any] | None, str]:
        """A job record for one feed detail, or ``(None, reason)`` when it is not
        an active, current, named, single vacancy with an employer orgnr."""
        if str(detail.get("status") or "").upper() != "ACTIVE":
            return None, "inactive"
        ad = detail.get("ad_content") or {}
        employer = ad.get("employer") or {}
        orgnr = _digits(employer.get("orgnr"))
        if len(orgnr) != 9:
            return None, "no_employer_orgnr"
        if re.fullmatch(r"\s*anonym(?:\s+arbeidsgiver)?\s*", str(employer.get("name") or ""), re.I):
            return None, "anonymous_employer"
        expires = _parse_dt(ad.get("expires")) if ad.get("expires") else None
        if expires is not None and expires <= (now or datetime.now(timezone.utc)):
            return None, "expired"
        if not is_single_vacancy(ad.get("title")):
            return None, "open_application"
        locations = ad.get("workLocations") or []
        municipal = (locations[0] or {}).get("municipal") if isinstance(locations, list) and locations else None
        link = str(ad.get("link") or "")
        return {
            "job_id": uuid,
            "orgnr": orgnr,
            "employer_name": employer.get("name"),
            "title": ad.get("title"),
            "url": link if link.startswith("https://arbeidsplassen.nav.no/") else public_ad_url(uuid),
            "published": ad.get("published"),
            "updated": ad.get("updated") or detail.get("sistEndret"),
            "expires": ad.get("expires"),
            "municipal": municipal,
            "content_sha256": hashlib.sha256(
                json.dumps(detail, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
        }, "ok"


def dedupe_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per ad, and one per re-posted vacancy (same employer unit,
    title and place) — the most recently published copy is kept."""
    by_vacancy: dict[tuple[str, str, str], dict[str, Any]] = {}
    for job in sorted(jobs, key=lambda j: (str(j.get("published") or ""), str(j.get("job_id")))):
        key = (str(job.get("employer_orgnr") or job.get("orgnr")),
               name_key(str(job.get("title") or "")) or str(job.get("job_id")),
               str(job.get("municipal") or "").casefold())
        by_vacancy[key] = job
    return sorted(by_vacancy.values(), key=lambda j: (str(j.get("published") or ""), str(j.get("job_id"))))
