"""Dated registry change events from Brønnøysund's public update feed.

One keyless request per company:
``/enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer=<org>&dato=<since>&size=100&includeChanges=true``

Each update carries its own date (``dato``), a change type (``endringstype``:
Ny / Endring / Sletting / Ukjent ...) and, with ``includeChanges``, the JSON
patch of the registry fields that changed. These are official, exact-entity
(keyed by organisation number) and dated, so they feed "what changed" and
``changes[]`` as ``registry_event`` claims. They are registry events, not news.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .evidence import evidence
from .http import FetchResult, fetch_json

UPDATES_URL = ("https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter"
               "?organisasjonsnummer={org}&dato={since}&size=100&includeChanges=true")
LOOKBACK_DAYS = 730
MAX_EVENTS = 12

# Registry JSON paths -> plain-English field names (others keep their path).
FIELD_NAMES = {
    "navn": "name",
    "organisasjonsform": "legal form",
    "naeringskode1": "industry code",
    "naeringskode2": "second industry code",
    "hjelpeenhetskode": "auxiliary unit code",
    "forretningsadresse": "business address",
    "postadresse": "postal address",
    "antallAnsatte": "employee count",
    "harRegistrertAntallAnsatte": "employees registered",
    "registreringsdatoAntallAnsatteEnhetsregisteret": "employee count date",
    "registreringsdatoAntallAnsatteNAVAaregisteret": "employee count date (NAV)",
    "hjemmeside": "website",
    "epostadresse": "e-mail",
    "telefon": "phone",
    "mobil": "mobile phone",
    "vedtektsdato": "articles of association date",
    "vedtektsfestetFormaal": "statutory purpose",
    "aktivitet": "stated activity",
    "kapital": "share capital",
    "sisteInnsendteAarsregnskap": "latest submitted annual accounts",
    "konkurs": "bankruptcy",
    "konkursdato": "bankruptcy date",
    "underAvvikling": "liquidation",
    "underAvviklingDato": "liquidation date",
    "registrertIMvaregisteret": "VAT registration",
    "registrertIForetaksregisteret": "Register of Business Enterprises",
    "overordnetEnhet": "parent entity",
    "institusjonellSektorkode": "institutional sector",
    "slettedato": "deletion date",
    "stiftelsesdato": "founding date",
    "maalform": "language form",
}

CHANGE_TYPES = {"Ny": "new registration", "Endring": "change", "Sletting": "deletion",
                "Fjernet": "removed", "Ukjent": "update (type not stated)"}


def updates_url(org: str, now: datetime | None = None) -> str:
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00.000Z")
    return UPDATES_URL.format(org=org, since=since)


def _field(path: str) -> str:
    top = str(path or "").strip("/").split("/")[0]
    return FIELD_NAMES.get(top, top or "record")


def normalize_updates(body: Any, org: str) -> dict[str, Any]:
    items = ((body or {}).get("_embedded") or {}).get("oppdaterteEnheter") or [] if isinstance(body, dict) else []
    events = []
    for item in items:
        if str(item.get("organisasjonsnummer")) != str(org):
            continue  # exact entity only
        changed = []
        for patch in item.get("endringer") or []:
            name = _field(patch.get("path"))
            if name not in changed:
                changed.append(name)
        events.append({
            "update_id": item.get("oppdateringsid"),
            "date": str(item.get("dato") or "")[:10] or None,
            "datetime": item.get("dato"),
            "change_type": item.get("endringstype"),
            "change_type_label": CHANGE_TYPES.get(item.get("endringstype"), item.get("endringstype")),
            "fields_changed": changed,
            "patch": [{"op": p.get("op"), "path": p.get("path"), "value": p.get("value")}
                      for p in (item.get("endringer") or [])][:20],
        })
    for e in events:
        e["description"] = describe(e)
    events.sort(key=lambda e: (e["datetime"] or "", e["update_id"] or 0), reverse=True)
    return {"events": events[:MAX_EVENTS], "total": len(events), "lookback_days": LOOKBACK_DAYS}


def fetch_registry_updates(org: str, fetcher: Callable[[str], FetchResult] = fetch_json,
                           now: datetime | None = None) -> tuple[dict[str, Any], FetchResult]:
    url = updates_url(org, now)
    result = fetcher(url)
    if result.status == 200:
        record = evidence("registry_updates", "available", "official_registry_updates", url,
                          value=normalize_updates(result.body, org), content_sha256=result.content_sha256,
                          retrieved_at=result.retrieved_at)
    elif result.status in (404, 410):
        record = evidence("registry_updates", "not_found", "official_registry_updates", url, note=result.error,
                          retrieved_at=result.retrieved_at)
    else:
        record = evidence("registry_updates", "source_error", "official_registry_updates", url, note=result.error,
                          retrieved_at=result.retrieved_at)
    return record, result


def describe(event: dict[str, Any]) -> str:
    """'2026-04-29 change: latest submitted annual accounts 2025' (plain English).
    Short scalar new values are shown; addresses and long texts are not."""
    shown: dict[str, str] = {}
    for p in event.get("patch") or []:
        name = _field(p.get("path"))
        value = p.get("value")
        if p.get("op") in ("add", "replace") and isinstance(value, (str, int)) and not isinstance(value, bool) \
                and len(str(value)) <= 24 and name not in ("e-mail", "phone", "mobile phone") \
                and str(p.get("path") or "").count("/") <= 1:
            shown[name] = str(value)
    names = list(event.get("fields_changed") or [])
    if "employee count" in names:  # the count itself says it; its re-dating is noise
        names = [n for n in names if n not in ("employee count date", "employee count date (NAV)")]
    fields = ", ".join(f"{f} {shown[f]}" if f in shown else f for f in names)
    label = event.get("change_type_label") or "update"
    return f"{event.get('date')} {label}" + (f": {fields}" if fields else "")
