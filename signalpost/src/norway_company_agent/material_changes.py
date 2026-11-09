"""Material changes for the contract envelope: ``changes[]`` and ``refresh``.

Two kinds of change, both cited to evidence in the envelope:

1. ``dated_in_source``: changes a source itself dates within this run, e.g.
   revenue between the two latest filed annual accounts, a roles-register entry
   updated in the last 12 months, a registry date field (employee count, share
   capital, articles, bankruptcy, liquidation).
2. ``since_previous_run``: a diff of material fields against the previous
   run's envelope for the same organisation number (``--previous-contract``):
   name, employees, municipality, address, registry status, website, CEO /
   chair / owner / board, latest filed accounts, job ads, dated news, social
   profiles.

Rules that keep the false-change rate down:
- A field is compared only when BOTH runs checked it definitively (a fetch that
  failed, was blocked or ran out of time is skipped, not reported as a change).
- Job ads, news and social profiles are compared only when the verified
  website is the same in both runs (a newly found site is one change, not a
  burst of "new" old articles). News counts only items dated after the
  previous run's newest item, because news lists are capped.
- A dated source event is reported once: every run stores the keys of the
  events it saw (``refresh.dated_source_event_keys``) and the next run reports
  only new keys. Re-running the same snapshot therefore yields no changes.
- When a field changed since the previous run, new dated events of the same
  family are folded into that one change (one changed field = one change).
- Evidence the previous run cited for a changed field is copied into this
  envelope's ``evidence[]`` with a ``prev-`` id, so prior evidence is preserved.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any

REFRESH_VERSION = "refresh-v1"
RECENT_DAYS = 365

_DEFINITIVE = {"available", "not_available"}
_WEB_FAMILIES = ("job_postings", "news", "social_links")
# The roles register dates each role GROUP (``sistEndret``), not each person,
# so roles are compared and dated per group: CEO, owner, contact person, and
# the board (members and deputies, the chair marked) as one field.
_ROLE_FIELDS = {"dagl": "roles.ceo", "innh": "roles.owner", "kont": "roles.contact_person"}
_BOARD_CODES = {"lede", "nest", "medl", "vara", "obs"}
_OTHER_ROLE_FIELDS = {"revi": "roles.auditor", "regn": "roles.accountant",
                      "ffør": "roles.business_manager", "komp": "roles.general_partner"}
# Update-feed fields that only re-date a value; on their own they are not a material change.
_DATE_ONLY_FEED_FIELDS = {"employee count date", "employee count date (NAV)"}


def role_group(code: str) -> str:
    code = str(code or "").lower()
    if code in _BOARD_CODES:
        return "roles.board"
    return _ROLE_FIELDS.get(code) or _OTHER_ROLE_FIELDS.get(code) or f"roles.{code}"


def board_member(claim: dict) -> str:
    code = str(claim.get("field", "")).split(".", 1)[-1]
    tag = {"lede": " (chair)", "nest": " (deputy chair)", "vara": " (deputy member)", "obs": " (observer)"}.get(code, "")
    return f"{claim.get('value')}{tag}"


def material_feed_event(value: dict | None) -> bool:
    """A registry update that changed something beyond re-dating a value."""
    fields = set((value or {}).get("fields_changed") or [])
    return bool(fields - _DATE_ONLY_FEED_FIELDS)


# ------------------------------------------------------------------ helpers

def _claims(env: dict, field: str, available_only: bool = True) -> list[dict]:
    return [c for c in env.get("claims", []) or []
            if c.get("field") == field and (not available_only or c.get("availability") == "available")]


def _first(env: dict, field: str) -> dict | None:
    found = _claims(env, field)
    return found[0] if found else None


def _evidence_ids(env: dict) -> set[str]:
    return {e.get("id") for e in env.get("evidence", []) or [] if e.get("id")}


def _evidence(env: dict, ev_id: str) -> dict | None:
    for e in env.get("evidence", []) or []:
        if e.get("id") == ev_id:
            return e
    return None


def _date(value: Any) -> date | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _recent(when: Any, reference: Any) -> bool:
    d, ref = _date(when), _date(reference)
    return bool(d and ref and 0 <= (ref - d).days <= RECENT_DAYS)


def _norm_url(url: Any) -> str | None:
    if not url:
        return None
    u = re.sub(r"^[a-z]+://", "", str(url).strip().lower())
    u = u[4:] if u.startswith("www.") else u
    return u.rstrip("/") or None


def _period_end(rp: Any) -> str:
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", str(rp or ""))
    return max(dates) if dates else str(rp or "")


# Registry update-feed field names -> the since-previous-run field they explain.
_FEED_TO_DIFF = {"employee count": "registry.employees", "name": "registry.name",
                 "business address": "registry.business_address", "bankruptcy": "registry.status",
                 "liquidation": "registry.status"}


def family(field: str) -> str:
    return "financials" if field.startswith("financials.") else field


# ------------------------------------------------------------ material state

def material_state(env: dict) -> dict[str, dict]:
    """field -> {value, definitive, evidence_ids} for every material field."""
    valid = _evidence_ids(env)
    state: dict[str, dict] = {}

    def put(field: str, value: Any, ids: list[str], definitive: bool = True) -> None:
        state[field] = {"value": value, "definitive": definitive, "evidence_ids": [i for i in ids if i in valid]}

    def ids_of(*claims: dict | None) -> list[str]:
        out: list[str] = []
        for c in claims:
            for i in (c or {}).get("evidence_ids", []) or []:
                if i not in out:
                    out.append(i)
        return out

    # Registry (bulk record is always read; absent ev-registry = not checked).
    if "ev-registry" in valid:
        for field, claim_field in (("registry.name", "legal_name"), ("registry.employees", "employees_registered"),
                                   ("registry.municipality", "municipality"),
                                   ("registry.business_address", "business_address")):
            c = _first(env, claim_field)
            put(field, c["value"] if c else None, ids_of(c) or ["ev-registry"])
        b, liq = _first(env, "bankrupt"), _first(env, "under_liquidation")
        put("registry.status", {"bankrupt": bool(b), "under_liquidation": bool(liq)}, ids_of(b, liq) or ["ev-registry"])

    # Website: only a confirmed site or a definitive "none" is comparable.
    site = next(iter(_claims(env, "official_website", available_only=False)), None)
    if site is not None:
        avail = site.get("availability")
        put("official_website", _norm_url(site.get("value")) if avail == "available" else None,
            ids_of(site), definitive=avail in _DEFINITIVE)

    # Roles: definitive when the roles register answered.
    roles = next(iter(_claims(env, "roles", available_only=False)), None)
    if roles is not None:
        definitive = roles.get("availability") in _DEFINITIVE
        role_claims = [c for c in env.get("claims", []) or []
                       if str(c.get("field", "")).startswith("role.") and c.get("availability") == "available"]
        for code, field in _ROLE_FIELDS.items():
            holders = [c for c in role_claims if c["field"] == f"role.{code}"]
            put(field, sorted({str(c.get("value")) for c in holders}) or None, ids_of(*holders) or ids_of(roles), definitive)
        board = [c for c in role_claims if c["field"].split(".", 1)[1] in _BOARD_CODES]
        put("roles.board", sorted({board_member(c) for c in board}) or None, ids_of(*board) or ids_of(roles), definitive)

    # Latest filed accounts.
    fin_claims = [c for c in env.get("claims", []) or []
                  if str(c.get("field", "")).startswith("financials.") and c.get("availability") == "available"
                  and not str(c.get("field")).startswith("financials.change.")]
    fin_aggregate = next(iter(_claims(env, "financials", available_only=False)), None)
    if fin_claims:
        latest_rp = max((c.get("reporting_period") for c in fin_claims), key=_period_end)
        latest = [c for c in fin_claims if c.get("reporting_period") == latest_rp]
        value: dict[str, Any] = {"period_end": _period_end(latest_rp)}
        for c in latest:
            value.setdefault(c["field"].split(".", 1)[1], c.get("value"))
        put("financials.latest", value, ids_of(*latest))
    elif fin_aggregate is not None:
        put("financials.latest", None, ids_of(fin_aggregate), fin_aggregate.get("availability") in _DEFINITIVE)

    # Website-derived families (compared only when the site is the same).
    if site is not None:
        definitive = site.get("availability") in _DEFINITIVE
        jobs = _claims(env, "job_posting") + _claims(env, "job_board.job_posting")
        job_keys = sorted({str((c.get("value") or {}).get("url") or (c.get("value") or {}).get("title") or "")
                           + ("|" + str((c.get("value") or {}).get("published")) if (c.get("value") or {}).get("published") else "")
                           for c in jobs if isinstance(c.get("value"), dict)} - {""})
        nav_read = ((env.get("run") or {}).get("source_checks") or {}).get("nav_jobs") != "not_read"
        put("job_postings", job_keys, ids_of(*jobs) or ids_of(site), definitive and nav_read)
        news = [c for c in _claims(env, "news") if isinstance(c.get("value"), dict)]
        items = sorted({(str(c["value"].get("date_published") or ""), str(c["value"].get("headline") or ""))
                        for c in news})
        put("news", [{"date_published": d or None, "headline": h} for d, h in items], ids_of(*news) or ids_of(site), definitive)
        social = [c for c in env.get("claims", []) or []
                  if str(c.get("field", "")).startswith("social.") and c.get("availability") == "available"]
        put("social_links", sorted({_norm_url(c.get("value")) for c in social} - {None}), ids_of(*social) or ids_of(site), definitive)
    return state


# ------------------------------------------------------- dated source events

def dated_source_events(env: dict) -> list[dict]:
    """Changes the sources themselves date, found in this run's claims."""
    valid = _evidence_ids(env)
    events: list[dict] = []

    def add(field: str, before: Any, after: Any, effective_at: Any, ids: list[str], basis: str, key: str) -> None:
        events.append({"field": field, "before": before, "after": after, "effective_at": effective_at,
                       "evidence_ids": [i for i in ids if i in valid], "change_type": "dated_in_source",
                       "basis": basis, "key": key})

    # Financials: two consecutive filed periods (claims built by synthesis).
    for c in env.get("claims", []) or []:
        f = str(c.get("field", ""))
        if f.startswith("financials.change.") and isinstance(c.get("value"), dict):
            v = c["value"]
            if v.get("before") == v.get("after"):
                continue
            metric = f.rsplit(".", 1)[1]
            end = _period_end(c.get("reporting_period")) or v.get("to_period")
            add(f"financials.{metric}", {"period": v.get("from_period"), "value": v.get("before")},
                {"period": v.get("to_period"), "value": v.get("after"), "change": v.get("change"),
                 "change_pct": v.get("change_pct")}, end, c.get("evidence_ids", []),
                "two consecutive filed annual accounts", f"financials.{metric}@{end}")

    # Role groups whose roles-register entry changed within the last 12 months
    # (one event per group: the register dates the group, not each person).
    roles_ev = _evidence(env, "ev-roles") or {}
    groups: dict[str, list[dict]] = {}
    for c in env.get("claims", []) or []:
        f = str(c.get("field", ""))
        if f.startswith("role.") and c.get("availability") == "available":
            groups.setdefault(role_group(f.split(".", 1)[1]), []).append(c)
    for field, members in sorted(groups.items()):
        dates = sorted({str(c.get("reporting_period")) for c in members if c.get("reporting_period")})
        if not dates or not _recent(dates[-1], roles_ev.get("retrieved_at")):
            continue
        holders = sorted({board_member(c) if field == "roles.board" else str(c.get("value")) for c in members})
        ids = [i for c in members for i in c.get("evidence_ids", [])]
        add(field, None, holders, dates[-1], ids,
            "roles register entry for this role group last changed within 12 months; "
            "the register does not publish the previous holders", f"{field}@{dates[-1]}")

    # Brønnøysund update feed: each dated update is its own event (key = update id).
    feed = _evidence(env, "ev-registry-updates")
    for c in _claims(env, "registry_event"):
        v = c.get("value") or {}
        if feed and v.get("date") and _recent(v.get("date"), feed.get("retrieved_at")) and material_feed_event(v):
            add("registry_event", None, {"change_type": v.get("change_type"), "fields_changed": v.get("fields_changed"),
                                         "description": v.get("description")},
                v.get("date"), c.get("evidence_ids", []), "Brønnøysund registry update feed",
                f"registry_event|{v.get('update_id')}")

    # Registry date fields (the update feed, when read, already dates these).
    reg_ev = _evidence(env, "ev-registry") or {}
    ref = reg_ev.get("retrieved_at")
    feed_read = any(c.get("field") == "registry_event" and c.get("availability") in _DEFINITIVE
                    for c in env.get("claims", []) or [])
    for date_field, field, value_field, always in (
            ("bankruptcy_date", "registry.status", "bankrupt", True),
            ("liquidation_date", "registry.status", "under_liquidation", True),
            ("employees_registered_date", "registry.employees", "employees_registered", False),
            ("share_capital_date", "registry.share_capital", "share_capital", False),
            ("articles_date", "registry.articles_of_association", None, False),
            ("registered_at", "registry.registered", "legal_name", False)):
        dc = _first(env, date_field)
        if not dc or not (always or _recent(dc.get("value"), ref)):
            continue
        if feed_read and not always:
            continue
        vc = _first(env, value_field) if value_field else None
        if value_field and field != "registry.status" and vc is None:
            continue  # a date without the value it dates says nothing about what changed
        after = {value_field: True} if field == "registry.status" else (vc or {}).get("value", dc.get("value"))
        add(field, None, after, dc.get("value"), list(dc.get("evidence_ids", [])) + list((vc or {}).get("evidence_ids", [])),
            f"registry date field {date_field}", f"{field}|{date_field}@{dc.get('value')}")
    seen: set[str] = set()
    unique = []
    for e in events:
        if e["key"] not in seen:
            seen.add(e["key"])
            unique.append(e)
    return unique


# ------------------------------------------------------------------ diffing

def diff_envelopes(previous: dict, current: dict, detected_at: str | None) -> tuple[list[dict], list[dict], list[str]]:
    """(changes, skipped, compared) between two envelopes of one organisation."""
    prev_state, cur_state = material_state(previous), material_state(current)
    prev_run = (previous.get("run") or {}).get("run_id")
    changes: list[dict] = []
    skipped: list[dict] = []
    compared: list[str] = []
    site_p, site_c = prev_state.get("official_website"), cur_state.get("official_website")
    same_site = bool(site_p and site_c and site_p["definitive"] and site_c["definitive"]
                     and site_p["value"] == site_c["value"])
    for field in sorted(set(prev_state) | set(cur_state)):
        p, c = prev_state.get(field), cur_state.get(field)
        if p is None or c is None:
            skipped.append({"field": field, "reason": "not checked in both runs"})
            continue
        if not (p["definitive"] and c["definitive"]):
            skipped.append({"field": field, "reason": "a source could not be read in one of the runs"})
            continue
        if field in _WEB_FAMILIES and not same_site:
            skipped.append({"field": field, "reason": "website differs between runs; items are not comparable"})
            continue
        compared.append(field)
        row: dict[str, Any] = {}
        if field == "news":
            newest = max((i["date_published"] or "" for i in p["value"]), default="")
            added = [i for i in c["value"] if i not in p["value"] and (i["date_published"] or "") > newest]
            if not added:
                continue
            row = {"before": {"items": len(p["value"]), "newest": newest or None}, "after": added}
        elif p["value"] == c["value"]:
            continue
        else:
            row = {"before": p["value"], "after": c["value"]}
            if isinstance(p["value"], list) or isinstance(c["value"], list):
                pv, cv = p["value"] or [], c["value"] or []
                row["added"] = [x for x in cv if x not in pv]
                row["removed"] = [x for x in pv if x not in cv]
        changes.append({"field": field, **row, "detected_at": detected_at, "effective_at": None,
                        "evidence_ids": c["evidence_ids"],
                        "previous_evidence_ids": [f"prev-{i}" for i in p["evidence_ids"]],
                        "change_type": "since_previous_run", "previous_run_id": prev_run})
    return changes, skipped, compared


def apply_refresh(env: dict, previous: dict | None, detected_at: str | None) -> dict:
    """Fill ``env['changes']`` and ``env['refresh']`` in place; returns env."""
    events = dated_source_events(env)
    keys = sorted(e["key"] for e in events)
    refresh: dict[str, Any] = {"version": REFRESH_VERSION, "baseline": None, "compared_fields": [],
                               "skipped_fields": [], "dated_source_event_keys": keys}
    if previous and previous.get("organisation_number") not in (None, env.get("organisation_number")):
        previous = None
        refresh["baseline_rejected"] = "previous envelope is for a different organisation number"
    if not previous:
        changes = [{**{k: v for k, v in e.items() if k != "key"}, "detected_at": detected_at} for e in events]
    else:
        prev_run = previous.get("run") or {}
        refresh["baseline"] = {"run_id": prev_run.get("run_id"), "started_at": prev_run.get("started_at"),
                               "completed_at": prev_run.get("completed_at")}
        diffs, skipped, compared = diff_envelopes(previous, env, detected_at)
        refresh["compared_fields"], refresh["skipped_fields"] = compared, skipped
        prev_ledger = (previous.get("refresh") or {}).get("dated_source_event_keys")
        baseline_date = _date(prev_run.get("started_at"))
        diff_families = {family(d["field"]) for d in diffs}
        new_events = []
        for e in events:
            if e["field"] == "registry_event":
                targets = {_FEED_TO_DIFF.get(f) for f in (e["after"] or {}).get("fields_changed") or []}
                if targets and None not in targets and targets <= diff_families:
                    for d in diffs:  # the feed dates the change it explains: one change, not two
                        if d["field"] in targets and not d.get("effective_at"):
                            d["effective_at"] = e["effective_at"]
                            d["evidence_ids"] = d["evidence_ids"] + [i for i in e["evidence_ids"] if i not in d["evidence_ids"]]
                    continue
            if prev_ledger is not None:
                if e["key"] in prev_ledger:
                    continue
            elif not (baseline_date and _date(e["effective_at"]) and _date(e["effective_at"]) > baseline_date):
                continue  # older baseline without a ledger: only events dated after it are new
            if family(e["field"]) in diff_families:
                continue  # folded into the since-previous-run change for this field
            new_events.append({**{k: v for k, v in e.items() if k != "key"}, "detected_at": detected_at})
        # Preserve the previous run's evidence for every changed field.
        have = _evidence_ids(env)
        preserved = 0
        for d in diffs:
            for pid in d["previous_evidence_ids"]:
                if pid in have:
                    continue
                old = _evidence(previous, pid[len("prev-"):])
                if old is None:
                    continue
                kept = copy.deepcopy(old)
                kept["id"] = pid
                kept["preserved_from_run"] = prev_run.get("run_id")
                env.setdefault("evidence", []).append(kept)
                have.add(pid)
                preserved += 1
            d["previous_evidence_ids"] = [i for i in d["previous_evidence_ids"] if i in have]
        refresh["prior_evidence_preserved"] = preserved
        changes = diffs + new_events
    refresh["material_changes"] = len(changes)
    env["changes"] = changes
    env["refresh"] = refresh
    return env
