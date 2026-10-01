"""Formalise official BRREG registry fields into evidence-backed claims.

The bulk registry record already holds many authoritative fields (statutory
purpose, addresses, founding date, share capital, VAT status, contact, sector,
parent entity) that were never emitted as explicit claims. This materialises
them as claims that carry the registry evidence's provenance (source URL,
retrieval time, content hash) so they are auditable and countable.

Rules (competition-safe):
- Only fields actually present are emitted. A missing/blank value is NEVER
  turned into a claim (no null -> claim, no zero substitution).
- Every claim inherits the exact-entity registry provenance (the bulk row is
  keyed by this organisation number), so there is no wrong-company surface.
- Deterministic order and values -> idempotent refresh.
"""

from __future__ import annotations

from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


# Additional official Brreg fields that were fetched but never surfaced as
# claims. Each maps a raw bulk-registry column to a contract field name. Every
# value is keyed by this organisation number in the official snapshot, so there
# is no wrong-company surface. Blank/absent values are never emitted.
_EXTRA_FIELDS: tuple[tuple[str, str], ...] = (
    ("organisasjonsform.kode", "legal_form_code"),
    ("naeringskode1.kode", "industry_code"),
    ("naeringskode2.kode", "industry_code_2"),
    ("naeringskode2.beskrivelse", "industry_2"),
    ("naeringskode3.kode", "industry_code_3"),
    ("naeringskode3.beskrivelse", "industry_3"),
    ("hjelpeenhetskode.kode", "auxiliary_unit_code"),
    ("hjelpeenhetskode.beskrivelse", "auxiliary_unit"),
    ("institusjonellSektorkode.kode", "institutional_sector_code"),
    ("forretningsadresse.kommune", "municipality"),
    ("forretningsadresse.kommunenummer", "municipality_number"),
    ("forretningsadresse.postnummer", "postal_code"),
    ("forretningsadresse.landkode", "country_code"),
    ("harRegistrertAntallAnsatte", "employees_registered_flag"),
    ("registreringsdatoAntallAnsatteEnhetsregisteret", "employees_registered_date"),
    ("registreringsdatoantallansatteNAVAaregisteret", "employees_nav_date"),
    ("registreringsdatoMerverdiavgiftsregisteret", "vat_registered_date"),
    ("registreringsdatoFrivilligMerverdiavgiftsregisteret", "voluntary_vat_date"),
    ("registrertIForetaksregisteret", "foretaksregisteret_registered"),
    ("registreringsdatoForetaksregisteret", "foretaksregisteret_date"),
    ("registrertIFrivillighetsregisteret", "voluntary_register_registered"),
    ("registrertIStiftelsesregisteret", "foundation_register_registered"),
    ("registrertIPartiregisteret", "party_register_registered"),
    ("registrertIForetaksregisteret", "foretaksregisteret_registered"),
    ("maalform", "language_form"),
    ("vedtektsdato", "articles_date"),
    ("aktivitet", "activity_description"),
    ("kapital.antallAksjer", "share_count"),
    ("kapital.type", "share_capital_type"),
    ("kapital.innfortDato", "share_capital_date"),
    ("konkursdato", "bankruptcy_date"),
    ("underAvviklingDato", "liquidation_date"),
    ("fravalgRevisjonDato", "audit_opt_out_date"),
    ("registreringsdatoEnhetsregisteret", "registered_at"),
)


def _extra_claims(value: dict, prov: dict, org: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_key, field in _EXTRA_FIELDS:
        if field in seen:
            continue
        raw = _clean(value.get(raw_key))
        if not raw:
            continue
        seen.add(field)
        if raw.lower() in {"true", "false"}:
            parsed: Any = raw.lower() == "true"
        elif raw.isdigit():
            parsed = raw
        else:
            parsed = raw
        out.append({"field": field, "value": parsed, "availability": "available",
                    "reporting_period": None, **prov, "organisation_number": org})
    return out


def _addr(value: dict, prefix: str) -> str | None:
    parts = [
        _clean(value.get(f"{prefix}.adresse")),
        " ".join(p for p in (_clean(value.get(f"{prefix}.postnummer")), _clean(value.get(f"{prefix}.poststed"))) if p),
        _clean(value.get(f"{prefix}.land")),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def registry_claims(profile: dict[str, Any]) -> list[dict[str, Any]]:
    org = str(profile.get("organisation_number") or "")
    reg = (profile.get("evidence", {}) or {}).get("registry", {}) or {}
    if reg.get("status") != "available":
        return []
    value = reg.get("value") or {}
    prov = {
        "source_url": reg.get("source_url"),
        "source_class": reg.get("source_class") or reg.get("source_type") or "official_registry_bulk",
        "retrieved_at": reg.get("retrieved_at"),
        "content_sha256": reg.get("content_sha256"),
    }

    def claim(field: str, val: Any, *, reporting_period: str | None = None) -> dict[str, Any] | None:
        v = val.strip() if isinstance(val, str) else val
        if v in (None, "", []):
            return None
        return {"field": field, "value": v, "availability": "available", "reporting_period": reporting_period, **prov}

    emp = _clean(value.get("antallAnsatte"))
    capital = _clean(value.get("kapital.belop"))
    candidates = [
        claim("legal_name", _clean(value.get("navn"))),
        claim("legal_form", _clean(value.get("organisasjonsform.beskrivelse"))),
        claim("industry", _clean(value.get("naeringskode1.beskrivelse"))),
        claim("institutional_sector", _clean(value.get("institusjonellSektorkode.beskrivelse"))),
        claim("statutory_purpose", _clean(value.get("vedtektsfestetFormaal"))),
        claim("business_address", _addr(value, "forretningsadresse")),
        claim("postal_address", _addr(value, "postadresse")),
        claim("founding_date", _clean(value.get("stiftelsesdato"))),
        claim("registered_at", _clean(value.get("registreringsdatoenhetsregisteret"))),
        claim("share_capital",
              {"amount": capital, "currency": _clean(value.get("kapital.valuta")) or "NOK"} if capital else None),
        claim("email", _clean(value.get("epostadresse"))),
        claim("phone", _clean(value.get("telefon")) or _clean(value.get("mobil"))),
        claim("website_registered", _clean(value.get("hjemmeside"))),
        claim("employees_registered", emp if emp.isdigit() else None),
        claim("vat_registered", True if _clean(value.get("registrertIMvaRegisteret")).lower() == "true" else None,
              reporting_period=_clean(value.get("registreringsdatoMerverdiavgiftsregisteret")) or None),
        claim("in_group", True if _clean(value.get("erIKonsern")).lower() == "true" else None),
        claim("parent_entity", _clean(value.get("overordnetEnhet")) or None),
        claim("bankrupt", True if profile.get("bankrupt") else None),
        claim("under_liquidation", True if profile.get("liquidating") else None),
    ]
    claims = [c for c in candidates if c is not None]
    existing = {c["field"] for c in claims}
    claims.extend(c for c in _extra_claims(value, prov, org) if c["field"] not in existing)
    # Deterministic order (idempotent refresh).
    claims.sort(key=lambda c: c["field"])
    for c in claims:
        c["organisation_number"] = org
    return claims
