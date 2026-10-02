"""Safe website discovery for companies with no registry-listed homepage.

Discovered domains are guesses, so — unlike registry-linked domains — a NAME
match is NOT enough (that is precisely the wrong-company trap: a guessed domain
can resolve to a namesake or a directory). A discovered site is accepted ONLY
when the requested organisation number itself appears on the page, and is HARD
REJECTED if a *different* valid Norwegian organisation number is the labelled
identity. This trades recall for ~1.0 precision, per the competition's
"a wrong fact is worse than a missing fact" rule.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

MOD11_WEIGHTS = [3, 2, 7, 6, 5, 4, 3, 2]
_ORGNR_RE = re.compile(r"\b(\d(?:[ .]?\d){8})\b")


def _valid_orgnr(digits: str) -> bool:
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], MOD11_WEIGHTS))
    remainder = total % 11
    check = 0 if remainder == 0 else 11 - remainder
    return check != 10 and check == int(digits[8])


def candidate_domains(name: str, *, max_candidates: int = 6) -> list[str]:
    """Deterministic domain guesses from a legal name (no network)."""
    text = (name or "").lower()
    for a, b in (("ø", "o"), ("å", "a"), ("æ", "ae"), ("é", "e")):
        text = text.replace(a, b)
    tokens = [t for t in re.findall(r"[a-z0-9]+", text)
              if t not in {"as", "asa", "ans", "da", "nuf", "sa", "ba", "ba", "og"}]
    if not tokens:
        return []
    compact = "".join(tokens)
    hyphen = "-".join(tokens)
    stems: list[str] = []
    for stem in (compact, hyphen):
        if 2 <= len(stem) <= 63 and stem not in stems:
            stems.append(stem)
    out: list[str] = []
    for stem in stems:
        for tld in (".no", ".com"):
            d = stem + tld
            if d not in out:
                out.append(d)
    return out[:max_candidates]


FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com",
    "live.com", "live.no", "yahoo.com", "yahoo.no", "icloud.com", "me.com",
    "online.no", "broadpark.no", "start.no", "getmail.no", "c2i.net",
}


def candidate_domains_from_email(email: str | None) -> list[str]:
    """Company website candidates derived from the OFFICIAL registry email.

    The address is the entity's self-declared contact in the Enhetsregisteret, so
    its domain is an official self-attribution — a strong candidate, but still
    passed through the same org-number / registry-contact gate (a shared
    franchise or group address can point at a parent). Free-mail providers are
    dropped (they carry no domain identity)."""
    value = str(email or "").strip().lower()
    if "@" not in value:
        return []
    domain = value.rsplit("@", 1)[1].strip().strip(".")
    if not domain or domain in FREEMAIL_DOMAINS or "." not in domain:
        return []
    return ["https://" + domain + "/"]


def _page_text(value: dict[str, Any]) -> str:
    parts = [value.get("title") or "", value.get("description") or "", value.get("main_text_excerpt") or ""]
    for org in value.get("structured_organisations") or []:
        parts.append(str(org))
    for page in value.get("pages") or []:
        parts.append(page.get("title") or "")
        parts.append(page.get("main_text_excerpt") or "")
    return " ".join(parts)


def orgnr_verdict(org: str, value: dict[str, Any]) -> str:
    """Return 'match' (our org number present), 'conflict' (a different valid
    org number present and ours absent), or 'none'."""
    text = _page_text(value)
    found = {re.sub(r"\D", "", m.group(1)) for m in _ORGNR_RE.finditer(text)}
    found = {d for d in found if _valid_orgnr(d)}
    if org in found:
        return "match"
    if found:                      # a valid, DIFFERENT org number is present
        return "conflict"
    return "none"


def _host(url: str) -> str:
    import urllib.parse
    return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")


def contact_verdict(fingerprint: dict[str, Any], value: dict[str, Any], final_url: str) -> bool:
    """Secondary exact-entity signal for a discovered site (used ONLY when there
    is no conflicting org number): the site carries the company's registry email
    domain, or the registry phone number. These are contact details the company
    itself published in the official register — a strong self-attribution."""
    text = _page_text(value)
    digits = re.sub(r"\D", "", text)
    reg_phone = re.sub(r"\D", "", str(fingerprint.get("phone") or ""))
    if len(reg_phone) >= 8 and reg_phone in digits:
        return True
    email = str(fingerprint.get("email") or "").lower()
    if "@" in email:
        dom = email.split("@", 1)[1].strip()
        if dom and (dom in text.lower() or dom == _host(final_url)):
            return True
    return False


def discover_website(
    profile: dict[str, Any],
    fetch_website: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
    *,
    max_candidates: int = 4,
    extra_candidates: list[str] | None = None,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Try discovered candidates; return a verified website evidence record only
    when the exact organisation number appears on the page. Returns
    (record_or_None, diagnostics). Registry-listed sites are handled elsewhere.

    ``extra_candidates`` are exact-entity URLs already keyed to this organisation
    number by an official source (e.g. the employer homepage self-declared in a
    NAV job ad whose ``employer.orgnr`` equals this org). They are tried first,
    but still go through the same org-number / registry-contact gate — an
    official self-declaration is a strong candidate, never a bypass."""
    org = str(profile.get("organisation_number") or "")
    diag = {"candidates": [], "requests": 0, "verdict": "no_candidate"}
    if not org.isdigit():
        return None, diag
    reg_value = (profile.get("evidence", {}) or {}).get("registry", {}).get("value") or {}
    fingerprint = {"email": reg_value.get("epostadresse"),
                   "phone": reg_value.get("telefon") or reg_value.get("mobil")}
    urls: list[str] = []
    for url in extra_candidates or []:
        url = str(url or "").strip()
        if url and url not in urls:
            urls.append(url if "://" in url else "https://" + url)
    for domain in candidate_domains(profile.get("name") or "", max_candidates=max_candidates):
        url = "https://" + domain
        if url not in urls:
            urls.append(url)
    for url in urls:
        diag["candidates"].append(url)
        record, metrics = fetch_website(url)
        diag["requests"] += int(metrics.get("requests", 0) or 0)
        if record.get("status") != "available":
            continue
        value = record.get("value") or {}
        verdict = orgnr_verdict(org, value)
        if verdict == "conflict":
            diag["verdict"] = "rejected_conflicting_orgnr"   # never bind; try other candidates
            continue
        method = None
        if verdict == "match":
            method = "exact_org_number_on_discovered_site"
        elif contact_verdict(fingerprint, value, value.get("final_url") or url):
            # No conflicting org number AND the site carries the registry email
            # domain or phone -> strong company self-attribution.
            method = "registry_contact_on_discovered_site"
        if method:
            value["discovered"] = True
            value["discovery_identity"] = {"method": method, "organisation_number": org}
            record["value"] = value
            diag["verdict"] = "verified_" + ("orgnr" if verdict == "match" else "contact")
            return record, diag
    if diag["verdict"] not in ("rejected_conflicting_orgnr",):
        diag["verdict"] = "no_match"
    return None, diag
