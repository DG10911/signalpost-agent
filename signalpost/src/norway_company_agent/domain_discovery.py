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


def discover_website(
    profile: dict[str, Any],
    fetch_website: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
    *,
    max_candidates: int = 4,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Try discovered candidates; return a verified website evidence record only
    when the exact organisation number appears on the page. Returns
    (record_or_None, diagnostics). Registry-listed sites are handled elsewhere."""
    org = str(profile.get("organisation_number") or "")
    diag = {"candidates": [], "requests": 0, "verdict": "no_candidate"}
    if not org.isdigit():
        return None, diag
    for domain in candidate_domains(profile.get("name") or "", max_candidates=max_candidates):
        diag["candidates"].append(domain)
        record, metrics = fetch_website("https://" + domain)
        diag["requests"] += int(metrics.get("requests", 0) or 0)
        if record.get("status") != "available":
            continue
        value = record.get("value") or {}
        verdict = orgnr_verdict(org, value)
        if verdict == "match":
            value["discovered"] = True
            value["discovery_identity"] = {"method": "exact_org_number_on_discovered_site", "organisation_number": org}
            record["value"] = value
            diag["verdict"] = "verified_orgnr"
            return record, diag
        if verdict == "conflict":
            diag["verdict"] = "rejected_conflicting_orgnr"   # never bind; keep searching other candidates
    if diag["verdict"] not in ("rejected_conflicting_orgnr",):
        diag["verdict"] = "no_orgnr_match"
    return None, diag
