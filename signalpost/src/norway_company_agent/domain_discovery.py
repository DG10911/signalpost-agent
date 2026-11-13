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
    """Deterministic domain guesses from a legal name (no network), best first:
    a domain written in the name itself ("HOTELL.NO AS" -> hotell.no), then the
    joined and hyphenated name on .no, then on .com. The caller skips guesses
    that do not resolve without counting them, so the list can be longer than
    the number of sites actually fetched."""
    text = (name or "").lower()
    for a, b in (("ø", "o"), ("å", "a"), ("æ", "ae"), ("é", "e")):
        text = text.replace(a, b)
    out: list[str] = []
    for written in re.findall(r"\b([a-z0-9][a-z0-9-]{1,62}\.(?:no|com|net|org|io))\b", text):
        if written not in out:
            out.append(written)
    tokens = [t for t in re.findall(r"[a-z0-9]+", text)
              if t not in {"as", "asa", "ans", "da", "nuf", "sa", "ba", "ba", "og"}]
    if not tokens:
        return out[:max_candidates]
    compact = "".join(tokens)
    hyphen = "-".join(tokens)
    stems: list[str] = []
    for stem in (compact, hyphen):
        if 2 <= len(stem) <= 63 and stem not in stems:
            stems.append(stem)
    for tld in (".no", ".com"):
        for stem in stems:
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
    parts = [value.get("title") or "", value.get("description") or "", value.get("main_text_excerpt") or "",
             value.get("identity_text_excerpt") or ""]
    for org in value.get("structured_organisations") or []:
        parts.append(str(org))
    for page in value.get("pages") or []:
        parts.append(page.get("title") or "")
        parts.append(page.get("main_text_excerpt") or "")
    return " ".join(parts)


def orgnr_verdict(org: str, value: dict[str, Any]) -> str:
    """Return 'match' (our org number present), 'conflict' (a different valid
    org number present and ours absent), or 'none'."""
    from .identity import org_numbers_in
    found = org_numbers_in(_page_text(value))
    if org in found:
        return "match"
    if found:                      # a valid, DIFFERENT org number is present
        return "conflict"
    return "none"


def _host(url: str) -> str:
    from .identity import host_key
    return host_key(url)


def contact_verdict(fingerprint: dict[str, Any], value: dict[str, Any], final_url: str) -> bool:
    """The site shows the registry phone number or the full registry e-mail
    address. (The e-mail *domain* matching the site's host is NOT evidence: for
    an e-mail-derived candidate it is circular, and it is how a franchise's
    @rema.no address once bound rema.no to the franchise.)"""
    text = _page_text(value)
    digits = re.sub(r"\D", "", text)
    reg_phone = re.sub(r"\D", "", str(fingerprint.get("phone") or ""))
    if reg_phone.startswith("47") and len(reg_phone) == 10:
        reg_phone = reg_phone[2:]
    if len(reg_phone) >= 8 and reg_phone in digits:
        return True
    email = str(fingerprint.get("email") or "").strip().lower()
    return "@" in email and email in text.lower()


def address_verdict(fingerprint: dict[str, Any], value: dict[str, Any]) -> bool:
    """The site shows the registered business street address (with its house
    number) AND its postcode. A post-box address is skipped: an accountant's
    "Postboks 123" is shared by every client company."""
    from .identity import _plain
    street = str(fingerprint.get("street") or "").split("\n")[0].strip()
    postcode = str(fingerprint.get("postcode") or "").strip()
    if not re.fullmatch(r"\d{4}", postcode) or not re.search(r"\d", street) or len(street) < 6:
        return False
    plain_street = _plain(street).strip()
    if plain_street.split()[0] in {"postboks", "pb", "boks", "postbox", "c", "co"}:
        return False
    text = _page_text(value)
    return (" " + plain_street + " ") in _plain(text) and re.search(rf"(?<!\d){postcode}(?!\d)", text) is not None


_LANGUAGE_PATHS = {"en", "no", "nb", "nn", "sv", "da", "de", "en-us", "en-gb", "nb-no", "no-nb", "nn-no", "en-no",
                   "home", "hjem", "forside", "index.html", "index.php"}


def _identity_page_method(org: str, name: Any, fingerprint: dict[str, Any], page: dict[str, Any],
                          names_us: bool) -> str | None:
    """'conflict' or a binding method read from a contact / about / privacy page
    of the candidate site (same gates as the homepage)."""
    from .identity import full_legal_name_in
    verdict = orgnr_verdict(org, page)
    if verdict == "match":
        return "exact_org_number_on_discovered_site"
    if verdict == "conflict":
        return "conflict"
    if full_legal_name_in(name, _page_text(page)):
        return "full_legal_name_on_discovered_site"
    if names_us and contact_verdict(fingerprint, page, page.get("final_url") or ""):
        return "registry_contact_on_discovered_site"
    if names_us and address_verdict(fingerprint, page):
        return "registry_address_on_discovered_site"
    return None


def _names_entity(name: Any, value: dict[str, Any]) -> bool:
    """Every distinctive legal-name token appears on the page. One shared token
    is not enough: a sister company's site in the same valley mentions
    "Hallingdal" without being Hallingdal Tomteutvikling AS."""
    from .identity import _tokens
    core = set(_tokens(name))
    return bool(core) and core <= set(_tokens(_page_text(value)))


def domain_owned_elsewhere(host: str, org: str) -> bool:
    """True when another registered entity lists this domain as its homepage or
    e-mail domain, or several do: then it is not this company's own site."""
    from .identity import DOMAIN_OWNERS
    owners = DOMAIN_OWNERS.get(host, [])
    return any(o != org for o in owners)


def discover_website(
    profile: dict[str, Any],
    fetch_website: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
    *,
    max_candidates: int = 4,
    extra_candidates: list[str] | None = None,
    email_candidates: list[str] | None = None,
    max_identity_pages: int = 2,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Try discovered candidates; return a verified website evidence record only
    on exact-entity proof. Returns (record_or_None, diagnostics).

    Candidates: ``extra_candidates`` (URLs an official source already keys to
    this org number, e.g. a NAV ad's employer homepage), ``email_candidates``
    (the registry e-mail domain), then deterministic name guesses. At most
    ``max_candidates`` name guesses are fetched; a guess whose host does not
    resolve costs no request and is not counted. Every candidate is rejected
    when the page shows a different valid org number, or when another
    registered entity lists the domain as its own. It is accepted on:

    * our org number on the page;
    * the full registered legal name with legal form ("Saks og Kam AS") on the
      page -- registered names are unique in Norway;
    * every distinctive name token plus the registry phone / full e-mail
      address, or plus the registered business street address and postcode;
    * for the registry e-mail domain only: no other entity uses the domain and
      the homepage identity carries the legal name (identity gate >= 0.9).

    When the homepage names the company but proves nothing, up to
    ``max_identity_pages`` of its own contact / about / privacy pages are read
    with the same gates (small sites state "Firma AS, org.nr ..." only there).
    """
    from .identity import assess_website_identity, full_legal_name_in
    import urllib.parse as _up
    org = str(profile.get("organisation_number") or "")
    diag = {"candidates": [], "requests": 0, "verdict": "no_candidate"}
    if not org.isdigit():
        return None, diag
    reg_value = (profile.get("evidence", {}) or {}).get("registry", {}).get("value") or {}
    fingerprint = {"email": reg_value.get("epostadresse"),
                   "phone": reg_value.get("telefon") or reg_value.get("mobil"),
                   "street": reg_value.get("forretningsadresse.adresse"),
                   "postcode": reg_value.get("forretningsadresse.postnummer")}
    queue: list[tuple[str, str]] = []
    seen: set[str] = set()

    def push(url: str, kind: str) -> None:
        url = str(url or "").strip()
        if not url:
            return
        url = url if "://" in url else "https://" + url
        host = _host(url)
        if host and host not in seen:
            seen.add(host)
            queue.append((url, kind))

    for url in extra_candidates or []:
        push(url, "official")
    for url in email_candidates or []:
        push(url, "email")
    for domain in candidate_domains(profile.get("name") or "", max_candidates=max(6, max_candidates)):
        push("https://" + domain, "name_guess")
    guesses_fetched = 0
    for url, kind in queue:
        if kind == "name_guess" and guesses_fetched >= max_candidates:
            break
        diag["candidates"].append(url)
        if domain_owned_elsewhere(_host(url), org):
            diag["verdict"] = "rejected_domain_owned_by_other_entity"
            continue
        record, metrics = fetch_website(url)
        spent = int(metrics.get("requests", 0) or 0)
        diag["requests"] += spent
        if kind == "name_guess" and (spent or record.get("status") == "available"):
            guesses_fetched += 1
        if record.get("status") != "available":
            continue
        value = record.get("value") or {}
        final_host = _host(value.get("final_url") or url)
        if final_host != _host(url) and domain_owned_elsewhere(final_host, org):
            diag["verdict"] = "rejected_redirect_to_other_entity"
            continue
        verdict = orgnr_verdict(org, value)
        if verdict == "conflict":
            diag["verdict"] = "rejected_conflicting_orgnr"   # never bind; try other candidates
            continue
        # A redirect to another host, or landing on a sub-page, is somebody
        # else's site talking about the company (an owner's portfolio page):
        # only the org number itself can bind it. A language or start page on
        # the same host (/en, /nb, /home) is still the homepage.
        try:
            final_path = _up.urlparse(value.get("final_url") or url).path.strip("/").lower()
        except ValueError:
            final_path = ""
        moved = final_host != _host(url) or bool(final_path and final_path not in _LANGUAGE_PATHS)
        names_us = _names_entity(profile.get("name"), value)
        method = None
        identity_page = None
        if verdict == "match":
            method = "exact_org_number_on_discovered_site"
        elif moved:
            diag["verdict"] = "rejected_redirect_without_orgnr"
            continue
        elif names_us and contact_verdict(fingerprint, value, value.get("final_url") or url):
            # Same phone/e-mail alone is shared by sister companies of one owner;
            # the page must also name this entity.
            method = "registry_contact_on_discovered_site"
        elif full_legal_name_in(profile.get("name"), _page_text(value)):
            method = "full_legal_name_on_discovered_site"
        elif names_us and address_verdict(fingerprint, value):
            method = "registry_address_on_discovered_site"
        elif kind in ("email", "official") and assess_website_identity(
                {**profile, "evidence": {**(profile.get("evidence") or {}), "website": record}}
        ).get("score", 0.0) >= 0.9:
            method = "unique_registry_email_domain_and_name"
        elif names_us:
            # The homepage names the company but proves nothing: read its own
            # contact / about / privacy pages with the same gates.
            conflict = False
            for link in (value.get("identity_links") or [])[:max_identity_pages]:
                if _host(link) != final_host:
                    continue
                page_record, page_metrics = fetch_website(link)
                diag["requests"] += int(page_metrics.get("requests", 0) or 0)
                page = page_record.get("value") or {}
                if page_record.get("status") != "available" or _host(page.get("final_url") or link) != final_host:
                    continue
                found = _identity_page_method(org, profile.get("name"), fingerprint, page, names_us)
                if found == "conflict":
                    conflict = True
                    break
                if found:
                    method, identity_page = found, page.get("final_url") or link
                    break
            if conflict:
                diag["verdict"] = "rejected_conflicting_orgnr_on_identity_page"
                continue
        if method:
            value["discovered"] = True
            value["discovery_identity"] = {"method": method, "organisation_number": org, "candidate_kind": kind}
            if identity_page:
                value["discovery_identity"]["identity_page"] = identity_page
            record["value"] = value
            diag["verdict"] = "verified_" + method
            return record, diag
    if not diag["verdict"].startswith("rejected"):
        diag["verdict"] = "no_match"
    return None, diag


def build_domain_owners(bulk_path: str, *, max_owners: int = 5) -> dict[str, list[str]]:
    """``domain -> [orgnr, ...]`` for every homepage and non-free-mail e-mail
    domain in the frozen Brreg bulk (no network). Used to reject a discovered
    domain another entity lists as its own, and to spot registry homepages
    shared by many entities (housing managers, chains, groups)."""
    import csv
    import gzip
    from .identity import host_key
    owners: dict[str, list[str]] = {}
    opener = gzip.open if str(bulk_path).endswith(".gz") else open
    with opener(bulk_path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)  # plain reader + column indices: ~2x faster than DictReader
        header = next(reader, [])
        idx = {name: i for i, name in enumerate(header)}
        i_org, i_home, i_mail = idx.get("organisasjonsnummer"), idx.get("hjemmeside"), idx.get("epostadresse")
        if i_org is None:
            return owners
        for cells in reader:
            org = cells[i_org] if len(cells) > i_org else ""
            for col in (i_home, i_mail):
                if col is None or len(cells) <= col or not cells[col]:
                    continue
                host = host_key(cells[col])
                if not host or "." not in host or host in FREEMAIL_DOMAINS:
                    continue
                bucket = owners.setdefault(host, [])
                if org not in bucket and len(bucket) < max_owners:
                    bucket.append(org)
    return owners
