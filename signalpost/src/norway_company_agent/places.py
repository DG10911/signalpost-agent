"""Google Places connector (key-gated) — website discovery + ratings/reviews.

Activated only when ``GOOGLE_PLACES_API_KEY`` is set. Uses the Places API (New)
Text Search: one request per company, field-masked to the minimum we use, so a
100-company batch costs ~$3 at list price (inside the $10 budget).

Two things this unlocks:
  * ``websiteUri`` — an exact-entity website candidate for the ~90% of the
    universe with no registry website. It is only a *candidate*: it is fetched
    and must still pass the org-number / registry-contact identity gate before
    any company-site fact is published.
  * ``rating`` / ``userRatingCount`` — a ``place_summary`` + ``review_summary``
    observation (the ratings/reviews family) once the place is matched to the
    exact legal entity.

Identity gate (no name-only): a place is accepted ONLY when its display name
carries the legal-name tokens AND its locality matches the registered
municipality. A material mismatch returns nothing.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from typing import Any

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(
    f"places.{field}"
    for field in ("id", "displayName", "formattedAddress", "websiteUri",
                  "rating", "userRatingCount", "businessStatus", "primaryType")
)
UA = "builderr-signalpost-poc/0.5 (+https://builderr.ai)"

_GENERIC = {"as", "asa", "ans", "sa", "da", "enk", "iks", "nuf", "sti", "stiftelsen",
            "the", "og", "and", "norge", "norway"}


def _tokens(value: Any) -> set[str]:
    text = str(value or "").translate(str.maketrans({"ø": "o", "å": "a", "æ": "ae"}))
    return {tok for tok in re.findall(r"[a-z0-9]+", text.casefold()) if len(tok) > 1 and tok not in _GENERIC}


def place_identity(profile: dict[str, Any], place: dict[str, Any]) -> str | None:
    """Return 'exact' when the place is the same legal entity, else None.

    Requires (a) every distinctive legal-name token to appear in the place's
    display name (or vice-versa for a single distinctive token), and (b) the
    registered municipality to appear in the formatted address when we have one.
    """
    core = _tokens(profile.get("name"))
    display = _tokens((place.get("displayName") or {}).get("text") if isinstance(place.get("displayName"), dict) else place.get("displayName"))
    if not core or not display:
        return None
    if not core.issubset(display) and not (len(core) == 1 and core & display):
        return None
    muni = str(profile.get("municipality") or "").strip().casefold()
    if muni:
        address = str(place.get("formattedAddress") or "").casefold()
        if muni and muni not in address:
            return None
    return "exact"


def query_places(profile: dict[str, Any], api_key: str, *, timeout: float = 20.0) -> dict[str, Any] | None:
    """Network call: return the best exact-entity place for the company, or None."""
    name = str(profile.get("name") or "").strip()
    if not name:
        return None
    query = " ".join(part for part in (name, str(profile.get("municipality") or "").strip(), "Norge") if part)
    body = json.dumps({"textQuery": query, "languageCode": "no", "maxResultCount": 3}).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": api_key,
                 "X-Goog-FieldMask": FIELD_MASK, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except Exception:
        return None
    for place in payload.get("places", []) or []:
        if place_identity(profile, place) == "exact":
            return place
    return None


def website_candidate(place: dict[str, Any] | None) -> str | None:
    url = str((place or {}).get("websiteUri") or "").strip()
    return url or None


def observations_for(profile: dict[str, Any], place: dict[str, Any], *, retrieved_at: str) -> list[dict[str, Any]]:
    """Publishable place_summary + review_summary observations for an exact place."""
    org = str(profile.get("organisation_number") or "")
    place_id = str(place.get("id") or "")
    if not org.isdigit() or not place_id:
        return []
    display = place.get("displayName")
    label = display.get("text") if isinstance(display, dict) else str(display or "")
    proof = [{
        "type": "google_places_name_municipality_match",
        "organisation_number": org,
        "display_name": label,
        "formatted_address": place.get("formattedAddress"),
    }]
    source_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    import hashlib
    digest = hashlib.sha256(f"{org}|{place_id}".encode()).hexdigest()
    out = [{
        "id": f"places-{org}",
        "organisation_number": org,
        "platform": "google_places",
        "signal_type": "place_summary",
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "effective_at": retrieved_at,
        "content_sha256": digest,
        "exact_entity": True,
        "identity_proof": proof,
        "acquisition_mode": "licensed_api",
        "rights_status": "approved",
        "source_class": "google_places",
        "evidence_span": (place.get("formattedAddress") or label or "")[:300] or None,
        "metrics": {"primary_type": place.get("primaryType"), "business_status": place.get("businessStatus"),
                    "website": place.get("websiteUri")},
    }]
    rating = place.get("rating")
    count = place.get("userRatingCount")
    if rating and count:
        out.append({
            "id": f"places-rating-{org}",
            "organisation_number": org,
            "platform": "google_places",
            "signal_type": "review_summary",
            "source_url": source_url,
            "retrieved_at": retrieved_at,
            "effective_at": retrieved_at,
            "content_sha256": hashlib.sha256(f"{org}|{place_id}|rating".encode()).hexdigest(),
            "exact_entity": True,
            "identity_proof": proof,
            "acquisition_mode": "licensed_api",
            "rights_status": "approved",
            "source_class": "customer_review",
            "evidence_span": f"Google Places rating {rating}/5 from {count} reviews.",
            "metrics": {"rating": rating, "review_count": count, "rating_scale": 5},
        })
    return out


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
