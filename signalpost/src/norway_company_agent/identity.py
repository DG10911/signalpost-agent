from __future__ import annotations

import re
import unicodedata
import urllib.parse
from typing import Any


LEGAL_AND_GENERIC = {
    "as", "asa", "ans", "da", "enk", "iks", "sa", "sam", "sti", "stiftelsen",
    "nuf", "ab", "b", "v", "limited", "ltd", "inc", "plc", "the", "og", "and",
}


# domain -> registry organisation numbers that list it as homepage or e-mail
# domain (built from the frozen Brreg bulk at run start; empty = check off).
DOMAIN_OWNERS: dict[str, list[str]] = {}
SHARED_SITE_MIN_OWNERS = 3
_ORGNR_RE = re.compile(r"(?<!\d)(\d(?:[ .\u00a0]?\d){8})(?!\d)")
_MOD11 = [3, 2, 7, 6, 5, 4, 3, 2]
_LEGAL_FORMS = ("as", "asa", "ans", "da", "sa", "ba", "nuf", "ks", "hf", "iks", "sf", "kf", "stiftelse")


def set_domain_owners(owners: dict[str, list[str]]) -> None:
    DOMAIN_OWNERS.clear()
    DOMAIN_OWNERS.update(owners)


def host_key(url_or_host: Any) -> str:
    text = str(url_or_host or "").strip().lower()
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if "://" not in text:
        text = "http://" + text
    try:
        host = urllib.parse.urlparse(text).hostname or ""
    except ValueError:  # malformed registry values (e.g. stray brackets)
        return ""
    return host.removeprefix("www.").strip(".")


def valid_orgnr(digits: str) -> bool:
    if len(digits) != 9 or not digits.isdigit():
        return False
    remainder = sum(int(d) * w for d, w in zip(digits[:8], _MOD11)) % 11
    check = 0 if remainder == 0 else 11 - remainder
    return check != 10 and check == int(digits[8])


def org_numbers_in(text: Any) -> set[str]:
    found = {re.sub(r"\D", "", m.group(1)) for m in _ORGNR_RE.finditer(str(text or ""))}
    return {d for d in found if valid_orgnr(d)}


def _plain(value: Any) -> str:
    text = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return " " + " ".join(re.findall(r"[a-z0-9]+", text)) + " "


def full_legal_name_in(name: Any, text: Any) -> bool:
    """True when the complete registered name, legal form included ("Saks og
    Kam AS"), appears in ``text``. Norwegian registered names are unique, so this
    is exact-entity evidence (unlike loose token overlap)."""
    plain_name = _plain(name).strip()
    tokens = plain_name.split()
    if len(tokens) < 2 or tokens[-1] not in _LEGAL_FORMS or len("".join(tokens[:-1])) < 4:
        return False
    return (" " + plain_name + " ") in _plain(text)


def shared_site_owners(url: Any, organisation_number: Any) -> list[str]:
    """Other registry entities using the same homepage/e-mail domain, when the
    URL is a bare domain (a company-specific path is not a shared site)."""
    if not DOMAIN_OWNERS:
        return []
    try:
        parsed = urllib.parse.urlparse(str(url or "") if "://" in str(url or "") else "http://" + str(url or ""))
    except ValueError:
        return []
    if parsed.path.strip("/") or parsed.query:
        return []
    owners = DOMAIN_OWNERS.get(host_key(url), [])
    org = re.sub(r"\D", "", str(organisation_number or ""))
    return [o for o in owners if o != org]


def is_shared_manager_site(name: Any, organisation_number: Any, url: Any) -> bool:
    """A bare registry homepage domain listed by >= SHARED_SITE_MIN_OWNERS
    entities whose label is not this entity's own compact name (obos.no for a
    housing co-op; a chain's domain for one franchise store)."""
    others = shared_site_owners(url, organisation_number)
    if len(others) + 1 < SHARED_SITE_MIN_OWNERS:
        return False
    label = host_key(url).split(".")[0].replace("-", "")
    return label != "".join(_tokens(name))


def _tokens(value: Any) -> list[str]:
    text = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return [token for token in re.findall(r"[a-z0-9]+", text) if token not in LEGAL_AND_GENERIC and len(token) > 1]


def _structured_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"name", "legalName", "alternateName"} and isinstance(child, str):
                names.append(child)
            else:
                names.extend(_structured_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_structured_names(child))
    return names


STRONG_DISCOVERY_METHODS = {
    "exact_org_number_on_discovered_site": 1.0,
    "registry_contact_on_discovered_site": 0.95,
    "full_legal_name_on_discovered_site": 0.95,
    "unique_registry_email_domain_and_name": 0.95,
}


def assess_website_identity(profile: dict[str, Any]) -> dict[str, Any]:
    website = profile.get("evidence", {}).get("website", {})
    value = website.get("value") or {}
    core = _tokens(profile.get("name"))
    hostname = urllib.parse.urlparse(value.get("final_url") or website.get("source_url") or "").hostname or ""
    structured_names = _structured_names(value.get("structured_organisations") or [])
    rendered = value.get("js_fallback") or {}
    identity_text = value.get("identity_text_excerpt") or ""
    homepage_identity_parts = [
        value.get("title"), value.get("description"), hostname, *structured_names,
        rendered.get("title"),
    ]
    candidate_parts = [
        *homepage_identity_parts, value.get("main_text_excerpt"),
        *[page.get("title") for page in value.get("pages", [])],
        *[page.get("main_text_excerpt") for page in value.get("pages", [])],
        *[page.get("identity_text_excerpt") for page in value.get("pages", [])],
    ]
    candidate_parts.append(rendered.get("main_text_excerpt"))
    candidate_text = " ".join(str(part or "") for part in candidate_parts)
    homepage_candidate_text = " ".join(str(part or "") for part in [*homepage_identity_parts, identity_text, value.get("main_text_excerpt"), rendered.get("main_text_excerpt")])
    normalized_candidate_text = " ".join(_tokens(candidate_text))
    candidate_tokens = set(_tokens(candidate_text))
    org_digits = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    homepage_org_numbers = org_numbers_in(homepage_candidate_text)
    discovery = value.get("discovery_identity") or {}
    shared_with = shared_site_owners(value.get("requested_url") or website.get("source_url"), org_digits) \
        if not value.get("discovered") else []
    compact_core = "".join(core)
    host_label = hostname.removeprefix("www.").split(".")[0].replace("-", "") if hostname else ""
    owns_shared_domain = bool(compact_core) and host_label == compact_core
    overlap = sorted(set(core) & candidate_tokens)
    ratio = len(overlap) / len(set(core)) if core else 0.0
    reasons = []
    parked_markers = (
        "domain is for sale", "domain for sale", "hugedomains", "parked at", "miss hosting",
        "her flytter snart en ny gjest", "has been informing visitors",
        "find the best information and most relevant links on all topics related to",
    )
    normalized_raw = unicodedata.normalize("NFKD", candidate_text).encode("ascii", "ignore").decode().casefold()
    homepage_token_sets = [set(_tokens(part)) for part in homepage_identity_parts if part]
    exact_homepage_name = bool(core and any(set(core).issubset(tokens) for tokens in homepage_token_sets))
    substantive_homepage = len(str(value.get("main_text_excerpt") or "").strip()) >= 100
    is_business_sports_club = bool(re.search(r"(?:^|\s)B\.?\s*I\.?\s*L\.?(?:\s|$)", str(profile.get("name") or ""), re.I))
    if any(marker in normalized_raw for marker in parked_markers):
        score = 0.1
        reasons.append("captured page is a parked, for-sale, or generic hosting placeholder")
    elif is_business_sports_club and "bedriftsidrett" not in normalized_candidate_text and "b i l" not in normalized_candidate_text:
        score = 0.3
        reasons.append("business sports-club entity points to the operating company's site without club evidence")
    elif value.get("discovered") and discovery.get("method") in STRONG_DISCOVERY_METHODS:
        score = STRONG_DISCOVERY_METHODS[discovery["method"]]
        reasons.append(f"discovered site verified by {discovery['method']}")
    elif len(shared_with) + 1 >= SHARED_SITE_MIN_OWNERS and org_digits not in homepage_org_numbers \
            and not owns_shared_domain:
        # The registry homepage is a domain many registered entities list (a
        # housing manager, chain or group site), and it does not name this
        # entity. Keep the registry association, but never publish the site's
        # social accounts, news or jobs as this company's (evaluator audit rule).
        score = 0.6
        reasons.append(f"registry homepage domain is shared by {len(shared_with) + 1} registered entities; "
                       "treated as a manager/group site, cross-entity facts quarantined")
    elif org_digits and org_digits in homepage_org_numbers:
        # Homepage only. An interior page may be a group/parent contact page
        # listing many subsidiaries' org numbers, so matching there is NOT exact
        # (adversarial-audit case). The homepage is the entity's own statement.
        if len(homepage_org_numbers) > 1 and ratio < 0.5:
            # The registry-linked domain looks like a parent/group site: it lists
            # several organisation numbers but its own identity does not carry
            # this legal name. Publish the site association, but never its social
            # accounts, news or jobs as this company's (evaluator audit rule).
            score = 0.6
            reasons.append("group/parent site lists multiple organisation numbers without matching this legal name; cross-entity facts quarantined")
        else:
            score = 1.0
            reasons.append("exact organisation number appears in homepage identity evidence")
    elif full_legal_name_in(profile.get("name"), identity_text) and len(homepage_org_numbers - {org_digits}) == 0:
        score = 0.95
        reasons.append("full registered legal name (with legal form) appears in the homepage footer/imprint")
    elif len(core) >= 2 and exact_homepage_name:
        score = 0.95
        reasons.append("all normalized legal-name tokens appear together in homepage identity evidence")
    elif len(core) == 1 and exact_homepage_name and substantive_homepage:
        score = 0.95
        reasons.append("single distinctive legal-name token appears in homepage identity evidence with substantive content")
    elif ratio >= 0.75 and len(overlap) >= 2:
        score = 0.85
        reasons.append("most legal-name tokens appear, but exact identity is incomplete")
    elif ratio >= 0.5 and len(overlap) >= 2:
        score = 0.65
        reasons.append("partial legal-name overlap only")
    else:
        score = 0.3
        reasons.append("registry-linked URL lacks strong exact-entity identity evidence")
    # NOTE: a "corroboration escalation" (promote 0.85 name matches on a
    # registry signal) was built and then REJECTED by the §7 adversarial audit:
    # the registry email domain equals the registry-listed hostname (a
    # near-tautological, non-independent signal), and it promoted a parent/brand
    # match (AVARN SECURITY AVIATION AS -> nokas.no). The accuracy floor
    # forbids trading exact-entity identity for a handful of extra sites, so the
    # 0.9 exact threshold stands with no name-only escalation.
    status = "exact" if score >= 0.9 else "review" if score >= 0.8 else "related_or_uncertain"
    return {
        "status": status,
        "score": score,
        "publishable": status == "exact",
        "legal_name_tokens": core,
        "matched_tokens": overlap,
        "reasons": reasons,
        "method": "deterministic_name_org_evidence_v2",
    }


def assess_social_identity(profile: dict[str, Any], link: dict[str, str]) -> dict[str, Any]:
    core = _tokens(profile.get("name"))
    parsed = urllib.parse.urlparse(link.get("url") or "")
    handle_text = urllib.parse.unquote(parsed.path)
    handle_compact = "".join(_tokens(handle_text))
    matched = [token for token in core if token in handle_compact]
    core_compact = "".join(core)
    ratio = len(set(matched)) / len(set(core)) if core else 0.0
    if core_compact and core_compact in handle_compact:
        score = 0.98
        reason = "normalized legal-name sequence appears in the social handle"
    elif len(core) == 1 and matched:
        score = 0.95
        reason = "single distinctive legal-name token appears in the social handle"
    elif ratio >= 0.75 and len(set(matched)) >= 2:
        score = 0.9
        reason = "most legal-name tokens appear in the social handle"
    else:
        score = 0.3
        reason = "social handle lacks strong exact-entity name evidence"
    return {
        **link,
        "identity_score": score,
        "publishable": score >= 0.9,
        "matched_tokens": matched,
        "reason": reason,
        "method": "deterministic_social_handle_identity_v1",
    }


def apply_website_identity_gate(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    if website.get("status") != "available":
        return {"website": website, "assessment": None, "quarantined_social_links": 0}
    temporary_profile = {**profile, "evidence": {**profile.get("evidence", {}), "website": website}}
    value = website.get("value") or {}
    assessment = assess_website_identity(temporary_profile)
    value["identity_assessment"] = assessment
    original = list(value.get("discovered_social_links") or value.get("social_links") or [])
    value["discovered_social_links"] = original
    social_assessments = [assess_social_identity(profile, link) for link in original]
    value["social_link_assessments"] = social_assessments
    # A social handle that contains the full legal-name sequence is exact-entity
    # proof on its own (e.g. linkedin.com/company/g3-gausdal-treindustrier-sa),
    # independent of whether the homepage text confirmed the entity. Publish it
    # on the handle's own assessment; parent/aggregator handles still fail it.
    value["social_links"] = [
        {"platform": item["platform"], "url": item["url"]}
        for item in social_assessments
        if item["publishable"]
    ]
    # Company-reported facts and job postings are only publishable once the site
    # resolves to the exact legal entity. When the gate does not confirm the
    # entity, quarantine them (keep the raw capture, withhold the published
    # claims) so no fact can attach to the wrong company.
    quarantined_facts = 0
    quarantined_jobs = 0
    quarantined_news = 0
    if not assessment["publishable"]:
        quarantined_facts = 1 if value.get("structured_facts") else 0
        quarantined_jobs = len(value.get("job_postings") or [])
        quarantined_news = len(value.get("news_articles") or [])
        value["quarantined_structured_facts"] = value.get("structured_facts") or {}
        value["quarantined_job_postings"] = value.get("job_postings") or []
        value["quarantined_news_articles"] = value.get("news_articles") or []
        value["quarantined_careers_pages"] = value.get("careers_pages") or []
        value["structured_facts"] = {}
        value["job_postings"] = []
        value["news_articles"] = []
        value["careers_pages"] = []
    website["value"] = value
    return {
        "website": website,
        "assessment": assessment,
        "quarantined_social_links": len(original) - len(value["social_links"]),
        "quarantined_structured_facts": quarantined_facts,
        "quarantined_job_postings": quarantined_jobs,
        "quarantined_news_articles": quarantined_news,
    }
