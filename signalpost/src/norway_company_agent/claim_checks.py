"""Pre-emit checks on website-derived claims (no network).

* ``recheck_excerpts``: every news headline, job title, careers-page title,
  site description and string structured fact must appear (normalised
  whitespace/case/diacritics) in the fetched document it was taken from.
  A claim whose quote cannot be found is dropped with a reason.
* ``rescue_group_site_items``: on a parent/sister/group site the site's news,
  jobs and careers pages are not the company's -- except an item whose own
  page names the company by full registered name or organisation number.
* ``withheld_counts``: run-report aggregate of excluded items by family and reason.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .identity import _plain, full_legal_name_in

EXCERPT_MISSING = "quoted excerpt not found in the fetched page text it cites"
SOURCES_KEY = "_source_texts"


def _found(excerpt: Any, text: str) -> bool:
    import html as _html
    needle = _plain(_html.unescape(str(excerpt)) if excerpt is not None else excerpt)
    needle = (needle or "").strip()  # a cut excerpt can end in a dangling space
    return needle == "" or needle in (text or "")


def _texts_for(item: dict[str, Any], texts: dict[str, str]) -> list[str]:
    page = item.get("source_page")
    if page and page in texts:
        return [texts[page]]
    return list(texts.values())  # older capture without provenance: any fetched document


def recheck_excerpts(website: dict[str, Any]) -> dict[str, Any]:
    value = website.get("value") if isinstance(website, dict) else None
    if not isinstance(value, dict):
        return website
    texts = value.get(SOURCES_KEY)
    if not isinstance(texts, dict) or not texts:
        return website  # nothing fetched to check against (resumed/legacy capture)
    dropped = 0
    for kept_key, excluded_key, field in (("news_articles", "excluded_news_articles", "headline"),
                                          ("job_postings", "excluded_job_postings", "title"),
                                          ("careers_pages", "excluded_careers_pages", "title")):
        kept, excluded = [], value.setdefault(excluded_key, [])
        for item in value.get(kept_key) or []:
            if any(_found(item.get(field), t) for t in _texts_for(item, texts)):
                kept.append(item)
            else:
                excluded.append({**item, "excluded_reason": EXCERPT_MISSING})
                dropped += 1
        value[kept_key] = kept
    homepage = texts.get(value.get("final_url")) or " ".join(texts.values())
    if value.get("description") and not any(_found(value["description"][:300], t) for t in (homepage, " ".join(texts.values()))):
        value["excluded_description"] = {"value": value.pop("description"), "excluded_reason": EXCERPT_MISSING}
        dropped += 1
    facts = value.get("structured_facts") or {}
    all_text = " ".join(texts.values())
    for key in list(facts):
        if isinstance(facts[key], str) and not _found(facts[key][:200], all_text):
            value.setdefault("excluded_structured_facts", {})[key] = {"value": facts.pop(key),
                                                                     "excluded_reason": EXCERPT_MISSING}
            dropped += 1
    value["excerpt_recheck"] = {"dropped": dropped, "documents": len(texts)}
    return website


def names_company(profile: dict[str, Any], text: str) -> bool:
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    plain = text or ""
    if org and (f" {org} " in plain or f" {org[:3]} {org[3:6]} {org[6:]} " in plain):
        return True
    return full_legal_name_in(profile.get("name"), plain)


def rescue_group_site_items(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    """Group site: republish only quarantined items whose page names the company."""
    value = website.get("value") if isinstance(website, dict) else None
    if not isinstance(value, dict) or not (value.get("identity_assessment") or {}).get("group_site"):
        return website
    texts = value.get(SOURCES_KEY) or {}
    value["group_site"] = True
    for family in ("news_articles", "job_postings", "careers_pages"):
        held = value.get(f"quarantined_{family}") or []
        named, rest = [], []
        for item in held:
            page_text = texts.get(item.get("source_page")) if item.get("source_page") else None
            (named if page_text and names_company(profile, page_text) else rest).append(item)
        value[family] = named
        value[f"quarantined_{family}"] = rest
    return website


def drop_source_texts(website: dict[str, Any]) -> dict[str, Any]:
    value = website.get("value") if isinstance(website, dict) else None
    if isinstance(value, dict):
        value.pop(SOURCES_KEY, None)
    return website


QUARANTINE_REASONS = {
    True: "group/parent/sister site: item's page does not name the company",
    False: "website identity not confirmed as this company",
}


def withheld_counts(profiles: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """{family: {reason: count}} over every profile's website layer."""
    out: dict[str, Counter] = {f: Counter() for f in ("website", "news", "jobs", "careers", "social")}
    for profile in profiles:
        web = ((profile.get("evidence") or {}).get("website") or {})
        value = web.get("value") or {}
        if not value:
            continue
        assessment = value.get("identity_assessment") or {}
        if assessment and not assessment.get("publishable"):
            out["website"][(assessment.get("reasons") or ["identity not confirmed"])[0]] += 1
        if value.get("excluded_description"):
            out["website"]["description: " + value["excluded_description"]["excluded_reason"]] += 1
        for key, item in (value.get("excluded_structured_facts") or {}).items():
            out["website"]["structured fact: " + item["excluded_reason"]] += 1
        group = bool(assessment.get("group_site"))
        for family, ex_key, q_key in (("news", "excluded_news_articles", "quarantined_news_articles"),
                                      ("jobs", "excluded_job_postings", "quarantined_job_postings"),
                                      ("careers", "excluded_careers_pages", "quarantined_careers_pages")):
            for item in value.get(ex_key) or []:
                out[family][item.get("excluded_reason") or "excluded"] += 1
            n = len(value.get(q_key) or [])
            if n:
                out[family][QUARANTINE_REASONS[group]] += n
        for link in value.get("social_link_assessments") or []:
            if not link.get("publishable"):
                out["social"][link.get("reason") or "not publishable"] += 1
    return {family: dict(counts.most_common()) for family, counts in out.items()}
