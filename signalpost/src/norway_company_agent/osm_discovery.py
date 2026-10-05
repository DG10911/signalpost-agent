"""OpenStreetMap / Nominatim website discovery (free, ODbL, no key).

For companies with a physical presence (shops, workshops, clinics, restaurants)
OpenStreetMap nodes often carry the company's own website/contact tags even when
the registry has no website. This queries the public Nominatim geocoder once per
company (rate-limited to 1 request/second per the usage policy) and returns the
OSM-tagged website ONLY when the OSM name carries the legal-name tokens and the
locality matches the registered municipality.

The returned URL is still just a candidate: it is fetched and must pass the
org-number / registry-contact identity gate before any fact is published.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

ENDPOINT = "https://nominatim.openstreetmap.org/search"
UA = "builderr-signalpost-poc/0.5 (+https://builderr.ai; contact submit@builderr.ai)"

_GENERIC = {"as", "asa", "ans", "sa", "da", "enk", "iks", "nuf", "sti", "stiftelsen",
            "the", "og", "and", "norge", "norway", "holding", "group", "gruppen"}
_LOCK = threading.Lock()
_LAST = [0.0]


def _tokens(value: Any) -> set[str]:
    text = str(value or "").translate(str.maketrans({"ø": "o", "å": "a", "æ": "ae"}))
    return {t for t in re.findall(r"[a-z0-9]+", text.casefold()) if len(t) > 1 and t not in _GENERIC}


def _throttle(min_interval: float = 1.1) -> None:
    with _LOCK:
        wait = min_interval - (time.time() - _LAST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()


def _search(query: str, *, timeout: float = 15.0) -> list[dict[str, Any]]:
    url = ENDPOINT + "?" + urllib.parse.urlencode(
        {"q": query, "format": "jsonv2", "addressdetails": "1", "extratags": "1", "limit": "3"})
    _throttle()
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return []


def _website(entry: dict[str, Any]) -> str | None:
    tags = entry.get("extratags") or {}
    for key in ("website", "contact:website", "url", "contact:url"):
        value = str(tags.get(key) or "").strip()
        if value and "." in value:
            return value if "://" in value else "https://" + value
    return None


def osm_match(profile: dict[str, Any], entry: dict[str, Any]) -> bool:
    core = _tokens(profile.get("name"))
    osm_name = _tokens(entry.get("name") or (entry.get("display_name") or "").split(",")[0])
    if not core or not osm_name:
        return False
    if not core.issubset(osm_name) and not (len(core) == 1 and core & osm_name):
        return False
    muni = str(profile.get("municipality") or "").strip().casefold()
    if muni:
        address = entry.get("address") or {}
        locality = " ".join(str(address.get(k) or "") for k in ("municipality", "city", "town", "village", "county")).casefold()
        if muni and muni not in locality and muni not in str(entry.get("display_name") or "").casefold():
            return False
    return True


def candidate_urls(profile: dict[str, Any], *, max_candidates: int = 2) -> list[str]:
    name = str(profile.get("name") or "").strip()
    if not name:
        return []
    municipality = str(profile.get("municipality") or "").strip()
    query = " ".join(part for part in (name, municipality, "Norge") if part)
    out: list[str] = []
    for entry in _search(query):
        if not osm_match(profile, entry):
            continue
        site = _website(entry)
        if site and site not in out:
            out.append(site)
        if len(out) >= max_candidates:
            break
    return out
