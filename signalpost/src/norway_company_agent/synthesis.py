"""Deterministic, evidence-grounded company synthesis (no LLM, no keys).

The synthesis is written FROM THE CONTRACT CLAIMS, after they are built, so it
can only repeat what the envelope already states and can cite the envelope's
own evidence ids. It never infers, estimates or grades the company:

- what the company does: registry legal form, industry and stated activity;
  the website's own description only when the site passed the identity gate,
  and then labelled as company-described (never as an independent assessment);
- size and trend: registered employees with their registry date; the latest
  filed accounts with their period, and a change only when two consecutive
  filed periods of the same account type exist;
- leadership: CEO / chair (or owner / contact person) with roles-register dates;
- footprint: business address and registered sub-units;
- online presence: website, linked social profiles, job ads, dated news;
- what changed: changes the sources date (filed-account year-on-year, recent
  roles-register entries, the Brønnøysund update feed);
- unknowns: every family that was not established, with its state and reason.

The summary is written as labelled blocks ("What it does: ... What changed:
... Unknowns: ...") and every block carries its evidence ids AND their source
URLs and retrieval times.

Missing is never written as zero. Every sentence lists the evidence ids that
support it, and derived numbers (changes, counts) are emitted as their own
claims so every number in the summary is a claim value.
"""

from __future__ import annotations

import re
from typing import Any

from .material_changes import material_feed_event, role_group

SYNTHESIS_VERSION = "synthesis-v3"
MAX_SUMMARY_WORDS = 120

_LEGAL_FORMS = {
    "AS": "private limited company (AS)",
    "ASA": "public limited company (ASA)",
    "ENK": "sole proprietorship (ENK)",
    "NUF": "Norwegian-registered foreign company (NUF)",
    "SA": "cooperative (SA)",
    "BA": "limited-liability company (BA)",
    "BRL": "housing cooperative (BRL)",
    "BBL": "housing-building cooperative (BBL)",
    "STI": "foundation (STI)",
    "FLI": "association (FLI)",
    "ESEK": "condominium owners' association (ESEK)",
    "ANS": "general partnership (ANS)",
    "DA": "partnership with shared liability (DA)",
    "KS": "limited partnership (KS)",
    "SAM": "co-ownership (SAM)",
    "SF": "state enterprise (SF)",
    "IKS": "inter-municipal company (IKS)",
    "KF": "municipal enterprise (KF)",
    "FKF": "county municipal enterprise (FKF)",
    "SPA": "savings bank (SPA)",
    "PK": "pension fund (PK)",
    "GFS": "mutual insurance company (GFS)",
    "VPFO": "securities fund (VPFO)",
    "ORGL": "organisational unit (ORGL)",
    "UTLA": "foreign entity (UTLA)",
    "BO": "bankruptcy estate (BO)",
    "PRE": "shipping partnership (PRE)",
    "KOMM": "municipality (KOMM)",
    "FYLK": "county authority (FYLK)",
    "STAT": "state body (STAT)",
    "KIRK": "church body (KIRK)",
}

# Roles shown in the leadership sentence, in priority order.
_LEAD_ROLES = (
    ("dagl", "CEO (daglig leder)"),
    ("lede", "board chair"),
    ("innh", "owner (innehaver)"),
    ("kont", "contact person in Norway"),
    ("ffør", "business manager (forretningsfører)"),
    ("komp", "general partner"),
    ("dtpr", "partner (pro rata liability)"),
    ("dtso", "partner (joint liability)"),
)

_FIN_METRICS = (("revenue", "revenue"), ("operating_result", "operating result"), ("equity", "equity"))


# ---------------------------------------------------------------- helpers

def _available(claims: list[dict], field: str) -> list[dict]:
    return [c for c in claims if c.get("field") == field and c.get("availability") == "available"]


def _first(claims: list[dict], field: str) -> dict | None:
    found = _available(claims, field)
    return found[0] if found else None


def _any(claims: list[dict], field: str) -> dict | None:
    for c in claims:
        if c.get("field") == field:
            return c
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _money(value: float, currency: str | None) -> str:
    return f"{currency or 'NOK'} {_fmt(value)}"


def _quote(text: Any, max_words: int = 12) -> str:
    words = str(text or "").split()
    if len(words) > max_words:
        return " ".join(words[:max_words]).rstrip(",.;:") + " …"
    return " ".join(words).rstrip(" .")


def _place(name: Any) -> str:
    return str(name or "").strip().title()


def _period_end(period: Any) -> str | None:
    if isinstance(period, dict):
        return period.get("tilDato") or period.get("fraDato")
    return str(period) if period else None


def period_label(period: Any) -> str:
    """'2025' for a calendar year, otherwise 'the year ending 2025-06-30'."""
    if isinstance(period, dict):
        start, end = str(period.get("fraDato") or ""), str(period.get("tilDato") or "")
        if start.endswith("-01-01") and end.endswith("-12-31") and start[:4] == end[:4]:
            return end[:4]
        if end:
            return f"the year ending {end}"
        return start or "an undated period"
    return str(period) if period else "an undated period"


def _year(date: Any) -> int | None:
    m = re.match(r"(\d{4})", str(date or ""))
    return int(m.group(1)) if m else None


def latest_two_periods(records: list[dict]) -> tuple[dict | None, dict | None]:
    """Latest filed record and, when filed, the immediately preceding period
    of the same account type (company accounts preferred over group)."""
    dated = [r for r in records or [] if _period_end(r.get("period"))]
    if not dated:
        return None, None
    latest_end = max(_period_end(r["period"]) for r in dated)
    at_latest = [r for r in dated if _period_end(r["period"]) == latest_end]
    at_latest.sort(key=lambda r: 0 if r.get("account_type") == "SELSKAP" else 1)
    latest = at_latest[0]
    same_type = [r for r in dated if r.get("account_type") == latest.get("account_type") and r is not latest
                 and _period_end(r["period"]) < latest_end]
    same_type.sort(key=lambda r: _period_end(r["period"]), reverse=True)
    prior = same_type[0] if same_type else None
    if prior is not None:
        ly, py = _year(_period_end(latest["period"])), _year(_period_end(prior["period"]))
        if ly is None or py is None or ly - py != 1:
            prior = None  # not two consecutive filed periods: no change is computed
    return latest, prior


def _fin_claim(claims: list[dict], metric: str, record: dict) -> dict | None:
    rp = str(record.get("period"))
    for c in _available(claims, f"financials.{metric}"):
        if c.get("reporting_period") == rp and _num(c.get("value")) == _num(record.get(metric)):
            return c
    return None


def _ids(*sources: Any, valid: set[str]) -> list[str]:
    out: list[str] = []
    for src in sources:
        if src is None:
            continue
        items = src.get("evidence_ids", []) if isinstance(src, dict) else src
        for i in items or []:
            if i in valid and i not in out:
                out.append(i)
    return out


_PLATFORMS = {"linkedin": "LinkedIn", "youtube": "YouTube", "tiktok": "TikTok", "x": "X"}


def _parse_date(value: str) -> str | None:
    """Sortable YYYY-MM-DD for ISO or DD.MM.YYYY dates; None otherwise."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return "-".join(m.groups())
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _words(text: str) -> int:
    return len(text.split())


# --------------------------------------------------------------- synthesis

_PART_LABELS = (("key_fact", "Key fact"), ("what_it_does", "What it does"), ("size_and_trend", "Size"), ("leadership", "Leadership"),
                ("footprint", "Footprint"), ("online_presence", "Online"), ("what_changed", "What changed"),
                ("unknowns", "Unknowns"))
# Shrink order when the summary is over budget (first = shortened first).
_SHRINK_ORDER = ("what_it_does", "footprint", "leadership", "what_changed", "unknowns")
_UNKNOWN_LABELS = {"employees": "employee count", "financials": "filed accounts", "leadership": "leadership",
                   "sub-units": "sub-units", "website": "website", "job_ads": "job ads",
                   "dated_news": "dated news", "social_profiles": "social profiles",
                   "registry_events": "registry update history"}
_FAMILY_FIELDS = {"website": "official_website", "job_ads": "job_posting", "dated_news": "news",
                  "social_profiles": "social"}
_STATE = {"not_found": "not_available", "available": "not_available", "source_error": "failed",
          "failed": "failed", "blocked": "blocked", "blocked_robots": "blocked", "blocked_policy": "blocked",
          "not_fetched": "failed"}


def _recent(date: Any, reference: Any, days: int = 365) -> bool:
    from datetime import date as _d
    a, b = _parse_date(str(date or "")), _parse_date(str(reference or ""))
    if not a or not b:
        return False
    delta = (_d.fromisoformat(b) - _d.fromisoformat(a)).days
    return 0 <= delta <= days


def synthesis_claims(profile: dict[str, Any], claims: list[dict], evidence: list[dict]) -> list[dict]:
    """Derived claims (financial changes, counts, not-found families) plus the
    labelled synthesis parts and summary. ``claims``/``evidence`` are the
    envelope's, already built."""
    valid = {e.get("id") for e in evidence if e.get("id")}
    by_id = {e.get("id"): e for e in evidence if e.get("id")}
    ev = profile.get("evidence", {}) or {}
    out: list[dict] = []
    fallback_id = "ev-registry" if "ev-registry" in valid else (evidence[0]["id"] if evidence else None)

    def derived(field: str, value: Any, ids: list[str], period: str | None = None,
                availability: str = "available", reason: str | None = None) -> dict:
        row = {"field": field, "value": value, "availability": availability, "confidence": 1.0,
               "evidence_ids": ids or ([fallback_id] if fallback_id else []), "reporting_period": period,
               "derived": True}
        if reason:
            row["reason"] = reason
        out.append(row)
        return row

    # part -> list of (text, ids) variants, longest first; "" drops the part.
    variants: dict[str, list[tuple[str, list[str]]]] = {}
    unknowns: list[dict] = []

    def unknown(family: str, state: str, reason: str, ids: list[str]) -> None:
        ids = [i for i in ids if i in valid] or ([fallback_id] if fallback_id else [])
        unknowns.append({"family": family, "state": state, "reason": reason, "evidence_ids": ids})

    name = (_first(claims, "legal_name") or {}).get("value") or profile.get("name") or profile.get("organisation_number")

    # 1) What the company does ------------------------------------------------
    form_c = _first(claims, "legal_form_code")
    form = _LEGAL_FORMS.get(str((form_c or {}).get("value") or "").upper())
    if not form and _first(claims, "legal_form"):
        form_c = _first(claims, "legal_form")
        form = str(form_c["value"])
    muni_c, founded_c = _first(claims, "municipality"), _first(claims, "founding_date")
    industry_c = _first(claims, "industry")
    if industry_c and str(industry_c.get("value")).strip().lower() in {"uoppgitt", "unspecified"}:
        industry_c = None
    activity_c = _first(claims, "activity_description") or _first(claims, "statutory_purpose")
    bankrupt_c = _first(claims, "bankruptcy_date") or _first(claims, "bankrupt")
    liq_c = _first(claims, "liquidation_date") or _first(claims, "under_liquidation")
    desc_c = _first(claims, "website_description")

    def what(with_activity: bool, desc_words: int) -> tuple[str, list[str]]:
        bits: list[str] = []
        ids: list[str] = []
        if form_c or muni_c:
            s = f"{name} is a {form or 'registered entity'}"
            if muni_c:
                s += f" registered in {_place(muni_c['value'])}"
            if founded_c:
                s += f", founded {founded_c['value']}"
            bits.append(s + ".")
            ids += _ids(form_c, muni_c, founded_c, valid=valid)
        seg = []
        if industry_c:
            seg.append(f"registry industry “{industry_c['value']}”")
            ids += _ids(industry_c, valid=valid)
        if activity_c and with_activity:
            seg.append(f"stated activity “{_quote(activity_c['value'])}”")
            ids += _ids(activity_c, valid=valid)
        if seg:
            bits.append(_cap("; ".join(seg)) + ".")
        if bankrupt_c:
            when = f" dated {bankrupt_c['value']}" if bankrupt_c["field"] == "bankruptcy_date" else ""
            bits.append(f"The registry records a bankruptcy{when}.")
            ids += _ids(bankrupt_c, valid=valid)
        if liq_c:
            when = f" dated {liq_c['value']}" if liq_c["field"] == "liquidation_date" else ""
            bits.append(f"The registry records that it is being wound up (liquidation{when}).")
            ids += _ids(liq_c, valid=valid)
        if desc_c and desc_words:
            bits.append(f"Its own website describes it as “{_quote(desc_c['value'], desc_words)}” (company-described).")
            ids += _ids(desc_c, valid=valid)
        return " ".join(bits), ids

    variants["what_it_does"] = [what(True, 16), what(False, 16), what(False, 8), what(False, 0)]

    # 2) Size ------------------------------------------------------------------
    size: list[str] = []
    size_ids: list[str] = []
    size_period: str | None = None
    emp_c, emp_date_c = _first(claims, "employees_registered"), _first(claims, "employees_registered_date")
    emp_flag_c = _first(claims, "employees_registered_flag")
    if emp_c:
        dated = f" (registry entry dated {emp_date_c['value']})" if emp_date_c else ""
        size.append(f"{emp_c['value']} registered employees{dated};")
        size_ids += _ids(emp_c, emp_date_c, valid=valid)
    elif emp_flag_c and emp_flag_c.get("value") is False:
        size.append("the registry lists no registered employees;")
        size_ids += _ids(emp_flag_c, valid=valid)
    else:
        unknown("employees", "not_available", "the registry publishes no employee count",
                _ids(emp_flag_c, valid=valid) or ["ev-registry"])

    changed: list[tuple[str, str, list[str]]] = []  # (sort date, text, ids) for "What changed"
    fin = ev.get("financials", {}) or {}
    latest, prior = latest_two_periods((fin.get("value") or {}).get("records") or [])
    if latest is not None:
        cur = latest.get("currency") or "NOK"
        label = period_label(latest.get("period"))
        prior_label = period_label(prior.get("period")) if prior else None
        size_period = str(latest.get("period"))
        figures: list[str] = []
        for metric, text in _FIN_METRICS:
            c = _fin_claim(claims, metric, latest)
            if c is None:
                continue
            size_ids += _ids(c, valid=valid)
            figures.append(f"{text} {_money(_num(c['value']), cur)}")
            pc = _fin_claim(claims, metric, prior) if prior else None
            if pc is None:
                continue
            before, after = _num(pc["value"]), _num(c["value"])
            change = round(after - before, 2)
            pct = round(change / before * 100, 1) if before and before > 0 else None
            row = derived(f"financials.change.{metric}", {
                "from_period": prior_label, "to_period": label, "before": before, "after": after,
                "change": change, "change_pct": pct, "currency": cur,
                "basis": "two consecutive filed annual accounts"}, _ids(pc, c, valid=valid), size_period)
            if metric in ("revenue", "operating_result") and before != after:
                pct_txt = f", {'+' if pct > 0 else ''}{pct}%" if pct is not None else ""
                changed.append((_period_end(latest.get("period")) or "",
                                f"{text} went from {_money(before, cur)} in {prior_label} to {_money(after, cur)} in {label}"
                                + (f" ({pct_txt.lstrip(', ')})" if pct_txt else ""),
                                row["evidence_ids"]))
        if figures:
            size.append(f"filed accounts for {label} show " + ", ".join(figures) + ".")
    else:
        status = fin.get("status")
        state = _STATE.get(str(status), "not_checked") if status else "not_checked"
        reason = {"not_found": "no annual accounts were found in the Register of Company Accounts",
                  "source_error": "the accounts register could not be read in this run",
                  "blocked": "the accounts register refused the request in this run"}.get(
            str(status), "annual accounts were not checked in this run")
        if status == "not_found" and str((form_c or {}).get("value") or "").upper() in {
                "ENK", "ANS", "DA", "SA", "FLI", "ESEK", "NUF", "SAM", "KS"}:
            reason += " (filing depends on size or activity for this legal form)"
        unknown("financials", state, reason, ["ev-financials"])
    if size and size_ids:
        text = " ".join(size)
        text = text[:-1] + "." if text.endswith(";") else text
        variants["size_and_trend"] = [(_cap(text), size_ids)]

    # 3) Leadership ------------------------------------------------------------
    roles_rec = ev.get("roles", {}) or {}
    role_claims = [c for c in claims if str(c.get("field", "")).startswith("role.") and c.get("availability") == "available"]
    lead: list[tuple[str, dict]] = []
    for code, label in _LEAD_ROLES:
        holders = [c for c in role_claims if c["field"] == f"role.{code}"]
        if holders:
            lead.append((label, holders[0]))
        if len(lead) == 2:
            break
    if lead:
        lead_ids = _ids(*(c for _, c in lead), valid=valid)

        def lead_text(with_dates: bool) -> str:
            def dated(c: dict) -> str:
                return f" (roles entry last changed {c['reporting_period']})" if with_dates and c.get("reporting_period") else ""
            if len(lead) == 2 and lead[0][1]["value"] == lead[1][1]["value"]:
                (l1, c1), (l2, c2) = lead
                if not with_dates or c1.get("reporting_period") == c2.get("reporting_period"):
                    when = dated(c1).replace("roles entry", "roles entries")
                else:
                    when = f" (roles entries last changed {c1.get('reporting_period')} and {c2.get('reporting_period')})"
                return _cap(f"{l1} and {l2}: {c1['value']}{when}") + "."
            return _cap("; ".join(f"{label}: {c['value']}{dated(c)}" for label, c in lead)) + "."

        variants["leadership"] = [(lead_text(True), lead_ids), (lead_text(False), lead_ids)]
    else:
        status = roles_rec.get("status")
        if status in ("available", "not_found"):
            unknown("leadership", "not_available", "no active roles are listed in the roles register", ["ev-roles"])
        else:
            unknown("leadership", _STATE.get(str(status), "not_checked") if status else "not_checked",
                    "the roles register could not be read in this run" if status else
                    "the roles register was not checked in this run", ["ev-roles"])
    # The roles register dates role GROUPS, so a change is reported per group.
    roles_retrieved = roles_rec.get("retrieved_at")
    groups: dict[str, list[dict]] = {}
    for c in role_claims:
        groups.setdefault(role_group(c["field"].split(".", 1)[1]), []).append(c)
    for group, label in (("roles.ceo", "CEO"), ("roles.board", "board"), ("roles.owner", "owner")):
        members = groups.get(group) or []
        dates = sorted({str(c.get("reporting_period")) for c in members if c.get("reporting_period")})
        if not dates or not _recent(dates[-1], roles_retrieved):
            continue
        if group == "roles.board":
            chair = next((c["value"] for c in members if c["field"] == "role.lede"), None)
            who = f"chair {chair}" if chair else f"{len(members)} members"
        else:
            who = members[0]["value"]
        changed.append((dates[-1], f"{label} entry in the roles register changed {dates[-1]} (now {who})",
                        _ids(*members, valid=valid)))

    # 4) Footprint ---------------------------------------------------------------
    loc_rec = ev.get("locations", {}) or {}
    all_locs = (loc_rec.get("value") or {}).get("locations") or []
    addr_c = _first(claims, "business_address")
    addr = (f"business address {' '.join(str(addr_c['value']).split())}.", _ids(addr_c, valid=valid)) if addr_c else ("", [])
    subunits = ("", [])
    if loc_rec.get("status") in ("available", "not_found") and "ev-locations" in valid:
        n = len(all_locs)
        munis = sorted({_place((loc.get("address") or {}).get("kommune")) for loc in all_locs
                        if isinstance(loc.get("address"), dict) and (loc.get("address") or {}).get("kommune")})
        if n:
            where = f" in {', '.join(munis[:4])}{' and other municipalities' if len(munis) > 4 else ''}" if munis else ""
            subunits = (f"{n} registered sub-unit{'s' if n != 1 else ''}{where}.", ["ev-locations"])
        else:
            subunits = ("No registered sub-units.", ["ev-locations"])
    elif loc_rec:
        unknown("sub-units", _STATE.get(str(loc_rec.get("status")), "failed"),
                "the sub-unit register could not be read in this run", ["ev-locations"])
    if addr[0] or subunits[0]:
        variants["footprint"] = [(_cap(" ".join(t for t in (addr[0], subunits[0]) if t)), addr[1] + subunits[1]),
                                 (subunits[0], subunits[1]), ("", [])]

    # 5) Online presence -----------------------------------------------------------
    site_c = _any(claims, "official_website")
    web_rec = ev.get("website", {}) or {}
    jobs = _available(claims, "job_posting") + _available(claims, "job_board.job_posting")
    careers = _available(claims, "careers_page")
    news = _available(claims, "news")
    social = [c for c in claims if str(c.get("field", "")).startswith("social.") and c.get("availability") == "available"]
    online: list[str] = []
    online_ids: list[str] = []
    site_verified = bool(site_c and site_c.get("availability") == "available"
                         and "ev-website" in site_c.get("evidence_ids", []))
    site_state = "not_available"
    if site_c and site_c.get("availability") == "available":
        how = "identity confirmed on the site" if site_verified else "listed in the registry"
        online.append(f"website {site_c['value']} ({how})")
        online_ids += _ids(site_c, valid=valid)
    else:
        site_state = (site_c or {}).get("availability") or _STATE.get(str(web_rec.get("status")), "not_available")
        if site_c and site_c.get("reason"):
            reason = site_c["reason"]
        elif site_state == "ambiguous":
            reason = ("the registry homepage is a domain several registered entities list"
                      if (site_c or {}).get("evidence_ids") == ["ev-registry"] else
                      "a candidate site was found but could not be tied to this organisation number")
        elif site_state == "blocked":
            reason = "the website refused automated access"
        elif site_state == "failed":
            reason = "the website could not be fetched in this run"
        else:
            reason = "no website is listed in the registry and none could be verified"
        if site_c is not None and not site_c.get("reason"):
            site_c["reason"] = reason  # every not-established family carries its reason
        unknown("website", site_state, reason, _ids(site_c, valid=valid) or ["ev-website", "ev-registry"])
    if social:
        platforms = sorted({c["field"].split(".", 1)[1] for c in social})
        online.append(", ".join(_PLATFORMS.get(p, p.capitalize()) for p in platforms)
                      + " profile" + ("s" if len(platforms) > 1 else ""))
        online_ids += _ids(*social, valid=valid)
    if jobs:
        online.append(f"{len(jobs)} job ad{'s' if len(jobs) != 1 else ''}")
        online_ids += _ids(*jobs, valid=valid)
    elif careers:
        online.append("a careers page with current openings")
        online_ids += _ids(*careers, valid=valid)
    if news:
        dated_news = [(_parse_date(d), d) for d in (str((c.get("value") or {}).get("date_published") or "") for c in news)]
        dated_news = [(k, d) for k, d in dated_news if k]
        latest_news = max(dated_news)[1] if dated_news else None
        if latest_news and re.match(r"\d{4}-\d{2}-\d{2}T", latest_news):
            latest_news = latest_news[:10]
        online.append(f"{len(news)} dated news item{'s' if len(news) != 1 else ''}"
                      + (" on its website" if site_verified else " naming the company on a linked site")
                      + (f", latest {latest_news}" if latest_news else ""))
        online_ids += _ids(*news, valid=valid)
    if online and online_ids:
        variants["online_presence"] = [(_cap("; ".join(online)) + ".", online_ids)]

    site_ids = _ids(site_c, valid=valid) or ["ev-website"]
    dependent_state = "not_available" if site_verified or site_state in ("not_available", "ambiguous") else site_state
    if not jobs and not careers:
        unknown("job_ads", "not_available", "no job ads were found in the sources checked in this run", site_ids)
    if not news:
        why = next((c.get("reason") for c in claims if c.get("field") == "news" and c.get("reason")), None)
        unknown("dated_news", dependent_state, why or (
            "no dated news items were found on the verified website" if site_verified
            else "there is no verified company website to read dated news from"), site_ids)
    if not social:
        unknown("social_profiles", dependent_state, "no social profiles are linked from the verified website"
                if site_verified else "there is no verified company website linking social profiles", site_ids)
    # An explicit not_available/failed claim (with reason) per external family
    # that has nothing, unless the contract already carries one.
    for u in unknowns:
        field = _FAMILY_FIELDS.get(u["family"])
        if field and field != "official_website" and not any(c.get("field") == field for c in claims):
            derived(field, None, u["evidence_ids"], availability=u["state"], reason=u["reason"])
    if site_c is None and any(u["family"] == "website" for u in unknowns):
        u = next(u for u in unknowns if u["family"] == "website")
        derived("official_website", None, u["evidence_ids"], availability=u["state"], reason=u["reason"])

    # 6) Registry update feed: dated official events -----------------------------------
    upd_rec = ev.get("registry_updates", {}) or {}
    reg_events = _available(claims, "registry_event")
    seen_kinds: set[tuple] = set()
    for c in reg_events:  # newest first; one line per kind of update keeps the summary short
        v = c.get("value") or {}
        kind = tuple(sorted(set(v.get("fields_changed") or []) - {"employee count date", "employee count date (NAV)"}))
        if not material_feed_event(v) or kind in seen_kinds or len(seen_kinds) >= 3:
            continue
        seen_kinds.add(kind)
        changed.append((str(v.get("date") or ""), f"registry {v.get('description') or v.get('date')}", _ids(c, valid=valid)))
    if upd_rec and upd_rec.get("status") not in ("available", "not_found"):
        unknown("registry_events", _STATE.get(str(upd_rec.get("status")), "failed"),
                "the registry update feed could not be read in this run", ["ev-registry-updates"])

    counts = {"job_ads": len(jobs), "careers_pages": len(careers), "dated_news": len(news),
              "social_profiles": len(social), "registry_events": len(reg_events),
              "registered_subunits": len(all_locs) if loc_rec.get("status") in ("available", "not_found") else None}
    derived("synthesis.counts", counts,
            _ids(["ev-locations"] if counts["registered_subunits"] is not None else [],
                 *jobs, *careers, *news, *social, *reg_events, valid=valid))

    # 7) What changed (dated by the sources) ---------------------------------------
    if changed:
        # Filed-account changes first (they carry the size trend), then the
        # other dated events, newest first.
        financial = [x for x in changed if " went from " in x[1]]
        others = sorted((x for x in changed if " went from " not in x[1]), key=lambda x: x[0], reverse=True)
        changed = financial[:1] + others + financial[1:]

        def changed_variant(k: int) -> tuple[str, list[str]]:
            items = changed[:k]
            return _cap("; ".join(t for _, t, _ in items)) + ".", _ids(*(i for _, _, i in items), valid=valid)
        variants["what_changed"] = [changed_variant(5), changed_variant(3), changed_variant(2), changed_variant(1)]

    # 8) Unknowns ---------------------------------------------------------------------
    unknown_ids = _ids(*unknowns, valid=valid)
    if unknowns and unknown_ids:
        def unknown_variant(with_reasons: bool) -> tuple[str, list[str]]:
            items = []
            for u in unknowns:
                lab = _UNKNOWN_LABELS.get(u["family"], u["family"])
                key = u["family"] in ("website", "financials", "employees", "leadership") or u["state"] != "not_available"
                items.append(f"{lab} ({u['reason']})" if with_reasons and key else lab)
            return _cap("; ".join(items)) + ".", unknown_ids
        variants["unknowns"] = [unknown_variant(True), unknown_variant(False)]

    # 8b) Key fact: the one thing a buyer would want first, with where it came from.
    key: tuple[str, list[str]] | None = None
    nav_jobs = [c for c in jobs if c.get("field") == "job_board.job_posting"]
    if jobs:
        where = ("the NAV job board" if nav_jobs and len(nav_jobs) == len(jobs)
                 else "the company's own website" if not nav_jobs else "the NAV job board and the company's website")
        key = (f"hiring now, with {len(jobs)} open job ad{'s' if len(jobs) != 1 else ''} (source: {where}).",
               _ids(*jobs, valid=valid))
    elif latest is not None and _fin_claim(claims, "revenue", latest) is not None \
            and (_num(_fin_claim(claims, "revenue", latest)["value"]) or 0) > 0:
        c = _fin_claim(claims, "revenue", latest)
        cur = latest.get("currency") or "NOK"
        txt = f"revenue was {_money(_num(c['value']), cur)} in {period_label(latest.get('period'))}"
        pc = _fin_claim(claims, "revenue", prior) if prior else None
        ids = _ids(c, pc, valid=valid) if pc else _ids(c, valid=valid)
        if pc is not None:
            before, after = _num(pc["value"]), _num(c["value"])
            if before and before > 0 and after is not None:
                pct = round((after - before) / before * 100, 1)
                txt += f", {'up' if pct > 0 else 'down' if pct < 0 else 'unchanged'}" + (f" {abs(pct)}%" if pct else "") \
                       + f" on {period_label(prior.get('period'))}"
        key = (txt + " (source: Brønnøysund Register of Company Accounts).", ids)
    elif latest is not None and (_fin_claim(claims, "equity", latest) or _fin_claim(claims, "operating_result", latest)):
        metric, label_txt = ("equity", "equity") if _fin_claim(claims, "equity", latest) else ("operating_result", "operating result")
        c = _fin_claim(claims, metric, latest)
        key = (f"{label_txt} was {_money(_num(c['value']), latest.get('currency') or 'NOK')} in "
               f"{period_label(latest.get('period'))} (source: Brønnøysund Register of Company Accounts).", _ids(c, valid=valid))
    elif emp_c:
        key = (f"{emp_c['value']} registered employees (source: Brønnøysund Central Coordinating Register).",
               _ids(emp_c, emp_date_c, valid=valid))
    elif site_verified:
        key = (f"its own website is {site_c.get('value')} (source: identity confirmed on the site).", _ids(site_c, valid=valid))
    if key and key[1]:
        variants["key_fact"] = [(_cap(key[0]), key[1])]

    # 9) Compose labelled blocks within the word budget ------------------------------------
    pick = {part: 0 for part in variants}

    def text_of(part: str) -> str:
        return variants[part][pick[part]][0]

    def total() -> int:
        return sum(_words(f"{label}: {text_of(p)}") for p, label in _PART_LABELS if p in variants and text_of(p))

    for part in _SHRINK_ORDER:
        while part in variants and total() > MAX_SUMMARY_WORDS and pick[part] < len(variants[part]) - 1:
            pick[part] += 1

    sentences = []
    def cited(ids: list[str]) -> list[str]:
        return [i for i in ids if i in valid] or ([fallback_id] if fallback_id else [])

    for part, label in _PART_LABELS:
        if part not in variants:
            continue
        # The structured part always carries its full text; the summary may use a shorter variant.
        full_text, full_ids = variants[part][0]
        derived(f"synthesis.{part}", f"{label}: {full_text}", cited(full_ids),
                size_period if part == "size_and_trend" else None)
        if not text_of(part):
            continue
        text, ids = variants[part][pick[part]]
        ids = cited(ids)
        sources = [{"evidence_id": i, "source_url": (by_id.get(i) or {}).get("source_url"),
                    "retrieved_at": (by_id.get(i) or {}).get("retrieved_at")} for i in ids]
        sentences.append({"part": part, "label": label, "text": f"{label}: {text}",
                          "evidence_ids": ids, "sources": sources})
    if unknowns:
        derived("synthesis.unknowns_detail", unknowns, unknown_ids)

    summary = " ".join(s["text"] for s in sentences)
    if summary:
        cited = [c for c in claims if set(c.get("evidence_ids") or []) & {i for s in sentences for i in s["evidence_ids"]}
                 and not c.get("derived") and c.get("availability") == "available"
                 and isinstance(c.get("confidence"), (int, float))]
        families = ("what_it_does", "size_and_trend", "leadership", "footprint", "online_presence")
        row = derived("synthesis.summary", summary, _ids(*sentences, valid=valid), size_period)
        row["confidence"] = round(min((c["confidence"] for c in cited), default=1.0), 3)
        row["sentences"] = sentences
        row["coverage"] = {"established": [p for p in families if p in variants],
                           "not_established": [u["family"] for u in unknowns]}
        row["method"] = (f"{SYNTHESIS_VERSION}: deterministic template over this envelope's claims; no model, "
                         "no inference; company-described text is labelled as such; confidence is the lowest "
                         "confidence among the cited claims")
    return out


def summarize_profile(profile: dict) -> dict[str, Any]:
    """Profile-level view of the same synthesis (used by the site and app).

    Builds the contract claims offline and returns the summary in the legacy
    shape: headline, narrative, statements (with evidence ids), unknowns."""
    from .output_contract import to_contract  # lazy: output_contract imports this module

    if "registry_claims" not in profile:
        from .registry_claims import registry_claims
        profile = {**profile, "registry_claims": registry_claims(profile)}
    env = to_contract(profile, run_id="summary", started_at="", completed_at="", terminal_status="completed")
    by_field = {c["field"]: c for c in env["claims"] if str(c.get("field", "")).startswith("synthesis.")}
    summary = by_field.get("synthesis.summary")
    statements = []
    for s in (summary or {}).get("sentences", []):
        first = (s.get("sources") or [{}])[0]
        statements.append({"text": s["text"], "part": s["part"], "evidence_ids": s["evidence_ids"],
                           "source_url": first.get("source_url"), "retrieved_at": first.get("retrieved_at")})
    unknowns = [f"{u['family'].replace('_', ' ')}: {u['reason']}"
                for u in (by_field.get("synthesis.unknowns_detail") or {}).get("value", [])]
    return {
        "headline": profile.get("name") or profile.get("organisation_number") or "Company",
        "narrative": (summary or {}).get("value") or "No verified public facts were found for this organisation number in this run.",
        "statements": statements,
        "unknowns": unknowns,
        "policy": "Deterministic template over verified structured evidence only; no inference, estimation, or generated prose. Every statement cites the evidence ids it rests on.",
    }
