"""Publish external-footprint observations from ALREADY-VERIFIED, permitted,
company-owned data — never from name-only matching.

Every observation emitted here is derived from a website that has already passed
``apply_website_identity_gate`` (``identity_assessment.publishable is True``),
i.e. the site resolves to the EXACT legal entity. We therefore inherit that
exact-entity proof and target entity precision = 1.0. Observations that do not
carry full provenance are dropped by ``publishable_observation`` downstream.

Emitted (all behind the verified-site gate):
  * company_site / company_profile   — the verified official web presence
  * <platform> / profile_handle       — each company-authorised social profile
  * company_site / job_posting        — JobPosting structured data on the site

Workforce snapshots (brreg annual reports) are emitted by the annual-report
connector and merged separately. Third-party signals (reviews, buzz, sentiment,
news) are NOT emitted here: they require permitted third-party sources with
independent exact-entity proof, which company-owned pages cannot supply.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .external_footprint import publishable_observation


def _sha64(value: str | None, seed: str) -> str:
    """Return a 64-char sha256 hex (the validator requires exactly 64 chars)."""
    if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower()):
        return value.lower()
    return hashlib.sha256((value or seed).encode("utf-8")).hexdigest()


def build_observations(profile: dict[str, Any]) -> list[dict[str, Any]]:
    org = str(profile.get("organisation_number") or "")
    if not org.isdigit():
        return []
    web = (profile.get("evidence", {}) or {}).get("website", {}) or {}
    if web.get("status") != "available":
        return []
    value = web.get("value") or {}
    assessment = value.get("identity_assessment") or {}
    if not assessment.get("publishable"):
        return []

    final_url = value.get("final_url")
    retrieved_at = web.get("retrieved_at")
    page_hash = _sha64(web.get("content_sha256") or value.get("content_sha256"), seed=org + (final_url or ""))
    identity_proof = [{
        "type": "website_identity_gate",
        "assessment": assessment.get("status"),
        "score": assessment.get("score"),
        "organisation_number": org,
    }]

    def base(**over: Any) -> dict[str, Any]:
        record = {
            "organisation_number": org,
            "source_url": final_url,
            "retrieved_at": retrieved_at,
            "content_sha256": page_hash,
            "exact_entity": True,
            "identity_proof": identity_proof,
            "acquisition_mode": "permitted_public_page",
            "rights_status": "approved",
            "source_class": "company_owned",
            "effective_at": retrieved_at,
        }
        record.update(over)
        return record

    observations: list[dict[str, Any]] = []

    # 1) Verified official web presence.
    observations.append(base(
        id=f"company-profile-{org}",
        platform="company_site",
        signal_type="company_profile",
        metrics={"registered_domain": value.get("registered_domain")},
        evidence_span=(value.get("title") or "")[:300] or None,
    ))

    # 2) Company-authorised social profiles (already identity-filtered by the gate).
    for link in value.get("social_links") or []:
        platform = str(link.get("platform") or "")
        url = link.get("url")
        if not platform or not url:
            continue
        observations.append(base(
            id=f"handle-{org}-{platform}",
            platform=platform,
            signal_type="profile_handle",
            source_url=url,
            acquisition_mode="company_authorized_export",
            evidence_span=None,
        ))

    # 3) Job postings from the verified site's structured data.
    for i, job in enumerate(value.get("job_postings") or []):
        observations.append(base(
            id=f"job-{org}-{i}",
            platform="company_site",
            signal_type="job_posting",
            source_url=job.get("url") or final_url,
            effective_at=job.get("date_posted") or retrieved_at,
            evidence_span=(job.get("title") or "")[:300] or None,
            metrics={"title": job.get("title"), "date_posted": job.get("date_posted")},
        ))

    # Only return observations that clear the full publication validator.
    return [obs for obs in observations if publishable_observation(obs)]
