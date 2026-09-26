"""Wikidata exact-entity facts via the official Norwegian organisation-number
property (P2333).

A Wikidata item is matched ONLY when its P2333 statement equals the requested
organisation number — so every result is exact-entity by construction (no name
matching, no wrong-company surface). Wikidata's SPARQL endpoint is an official
public API (CC0 data); results are batched (~50 orgnrs/query), so cost is a
handful of requests for a 100-company cohort — no firehose, unlike NAV.

Yields facts for the (few) notable companies present in Wikidata: canonical
website, employee count, inception date, Norwegian Wikipedia article, and
company-published social handles (sameAs). Registry-only holdcos simply return
nothing — legitimately.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Iterable

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "builderr-signalpost-poc/0.4 (+https://builderr.ai)"

# Wikidata social-ID properties -> (platform, URL template)
_SOCIAL = {
    "P2013": ("facebook", "https://facebook.com/{}"),
    "P2003": ("instagram", "https://instagram.com/{}"),
    "P2002": ("x", "https://x.com/{}"),
    "P2397": ("youtube", "https://youtube.com/channel/{}"),
    "P4264": ("linkedin", "https://linkedin.com/company/{}"),
}


def _query(sparql: str, timeout: float = 40.0) -> dict[str, Any]:
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": sparql, "format": "json"})
    req = urllib.request.Request(url, headers={"Accept": "application/sparql-results+json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def query_p2333(organisation_numbers: Iterable[str], *, batch_size: int = 50,
                querier=_query) -> dict[str, dict[str, Any]]:
    """Return {orgnr: {item, label, website, employees, inception, wikipedia,
    social:{platform:url}}} for the org numbers present in Wikidata."""
    orgs = [str(o) for o in organisation_numbers if str(o).isdigit()]
    social_opt = "\n".join(f'OPTIONAL {{ ?item wdt:{p} ?{p}. }}' for p in _SOCIAL)
    social_sel = " ".join(f"?{p}" for p in _SOCIAL)
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(orgs), batch_size):
        chunk = orgs[i:i + batch_size]
        values = " ".join('"%s"' % o for o in chunk)
        sparql = f"""SELECT ?orgnr ?item ?itemLabel ?website ?employees ?inception ?article {social_sel} WHERE {{
  VALUES ?orgnr {{ {values} }}
  ?item wdt:P2333 ?orgnr.
  OPTIONAL {{ ?item wdt:P856 ?website. }}
  OPTIONAL {{ ?item wdt:P1128 ?employees. }}
  OPTIONAL {{ ?item wdt:P571 ?inception. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://no.wikipedia.org/>. }}
  {social_opt}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,no". }}
}}"""
        try:
            data = querier(sparql)
        except Exception:
            continue
        for b in data.get("results", {}).get("bindings", []):
            org = b.get("orgnr", {}).get("value")
            if not org:
                continue
            rec = out.setdefault(org, {"item": None, "label": None, "website": None,
                                       "employees": None, "inception": None,
                                       "wikipedia": None, "social": {}})
            rec["item"] = rec["item"] or b.get("item", {}).get("value")
            rec["label"] = rec["label"] or b.get("itemLabel", {}).get("value")
            for key in ("website", "employees", "inception"):
                if b.get(key, {}).get("value") and not rec[key]:
                    rec[key] = b[key]["value"]
            if b.get("article", {}).get("value") and not rec["wikipedia"]:
                rec["wikipedia"] = b["article"]["value"]
            for prop, (platform, tmpl) in _SOCIAL.items():
                v = b.get(prop, {}).get("value")
                if v and platform not in rec["social"]:
                    rec["social"][platform] = tmpl.format(v)
    return out


def observations_for(entity: dict[str, Any], organisation_number: str, *, retrieved_at: str) -> list[dict[str, Any]]:
    """Publishable observations from a P2333-matched Wikidata entity."""
    org = str(organisation_number)
    item = entity.get("item")
    if not item:
        return []
    proof = [{"type": "wikidata_p2333_organisation_number", "value": org, "item": item}]
    import hashlib
    def obs(oid_suffix, platform, signal_type, url, metrics=None):
        return {
            "id": f"wikidata-{org}-{oid_suffix}",
            "organisation_number": org,
            "platform": platform,
            "signal_type": signal_type,
            "source_url": url,
            "retrieved_at": retrieved_at,
            "effective_at": entity.get("inception"),
            "content_sha256": hashlib.sha256(f"{org}|{item}|{oid_suffix}".encode()).hexdigest(),
            "exact_entity": True,
            "identity_proof": proof,
            "acquisition_mode": "official_api",
            "rights_status": "approved",
            "source_class": "wikidata",
            "evidence_span": (entity.get("label") or "")[:300] or None,
            "metrics": metrics or {},
        }
    result = [obs("profile", "wikidata", "company_profile", item,
                  {"website": entity.get("website"), "employees": entity.get("employees"),
                   "inception": entity.get("inception")})]
    if entity.get("wikipedia"):
        result.append(obs("wikipedia", "wikipedia", "company_profile", entity["wikipedia"]))
    for platform, url in (entity.get("social") or {}).items():
        result.append(obs(f"handle-{platform}", platform, "profile_handle", url))
    return result
