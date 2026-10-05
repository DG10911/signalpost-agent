"""Brave Search website discovery (key-gated).

Activated only when ``BRAVE_SEARCH_API_KEY`` is set. This is the
participant-brief's recommended missing-website path: keep search output
transient, immediately crawl an accepted candidate, and publish nothing until the
crawled page passes the exact-entity gate (org number / registry contact).

We never store titles, snippets, ranks or response bodies — only the candidate
URLs returned here, which are then fetched and identity-gated by
``domain_discovery.discover_website``.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
UA = "builderr-signalpost-poc/0.5 (+https://builderr.ai)"

# Directory / social / aggregator hosts are never the company's own site.
_SKIP_HOSTS = (
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "wikipedia.org", "wikidata.org", "proff.no", "gulesider.no",
    "1881.no", "firmenabc", "glassdoor", "indeed", "bloomberg", "crunchbase",
    "dnb.com", "skatteetaten.no", "brreg.no", "arbeidsplassen.no", "google.com",
    "g2.com", "trustpilot", "yelp", "tripadvisor", "openstreetmap", "allabolag",
    "companieshouse", "rakuten", "amazon.",
)


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").casefold().removeprefix("www.")


def search_web(query: str, api_key: str, *, timeout: float = 15.0, count: int = 5) -> list[str]:
    url = ENDPOINT + "?" + urllib.parse.urlencode({"q": query, "count": count, "country": "NO"})
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "X-Subscription-Token": api_key, "User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except Exception:
        return []
    return [str(item.get("url") or "") for item in (payload.get("web", {}) or {}).get("results", []) or []]


def candidate_urls(profile: dict[str, Any], api_key: str, *, max_candidates: int = 3) -> list[str]:
    """Return up to ``max_candidates`` same-site URLs to try through the gate."""
    name = str(profile.get("name") or "").strip()
    if not name:
        return []
    municipality = str(profile.get("municipality") or "").strip()
    query = " ".join(part for part in (f'"{name}"', municipality) if part)
    out: list[str] = []
    seen: set[str] = set()
    for url in search_web(query, api_key):
        if "://" not in url:
            continue
        host = _host(url)
        if not host or any(skip in host for skip in _SKIP_HOSTS):
            continue
        if host in seen:
            continue
        seen.add(host)
        out.append(url)
        if len(out) >= max_candidates:
            break
    return out
