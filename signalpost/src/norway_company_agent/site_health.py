"""Is a registry-listed website a live site of this company? (no network)

The registry's homepage field is often stale: domains lapse, hosting is
suspended, or the address now redirects to an owner or buyer. A 5 Nov hand
check of 35 registry homepages the crawl could not verify found 17 wrong.
This module reads what the crawl already saw and returns a reason when the
address should not be published as the company's website.

Errors caused by the local network (connection resets, a DNS filter answering
with a private address, robots.txt) say nothing about the site and are not
treated as dead.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .identity import _plain

# Default, parked, expired or suspended pages: the domain shows no company site.
_DEAD_PAGE = re.compile(
    r"welcome to nginx|apache2? (?:ubuntu |debian )?default page|test page for the apache"
    r"|account suspended|suspendedpage|website expired|this domain (?:is|may be) for sale"
    r"|buy this domain|domenet er til salgs|parked free|domain parking"
    r"|isn.t connected to a site|domain is not connected to a website",
)
# Kept narrow on purpose: these markers are only read on the title and the first
# part of the page text, where a real company homepage does not say them.
_DEAD_TEXT_CHARS = 600

_DEAD_NOTES = (
    ("Hostname did not resolve", "the domain does not exist"),
    ("HTTP 404", "the homepage returns HTTP 404 (not found)"),
    ("HTTP 410", "the homepage returns HTTP 410 (gone)"),
    ("HTTP 400", "the homepage returns HTTP 400"),
    ("HTTP 403", "the homepage refuses access (HTTP 403)"),
    ("HTTP 5", "the homepage returns a server error"),
    ("RemoteDisconnected", "the server closes the connection without a page"),
    ("redirected outside registered domain", "the address redirects to another domain"),
)


def _host_ok(url: str) -> bool:
    raw = url if "://" in url else f"http://{url}"
    host = urllib.parse.urlparse(raw).hostname or ""
    return "." in host and not host.startswith(".") and not host.endswith(".")


def _names_company(text: str, name: Any) -> bool:
    plain = _plain(text)
    tokens = [t for t in _plain(name).split() if len(t) >= 4 and t not in {"holding", "invest", "eiendom", "gruppen"}]
    return any(t in plain.replace(" ", "") or t in plain for t in tokens)


def dead_page_reason(title: Any, text: Any) -> str | None:
    """A reason when a loaded page is a parked, default, expired or suspended page."""
    head = _plain(f"{title or ''} {str(text or '')[:_DEAD_TEXT_CHARS]}")
    m = _DEAD_PAGE.search(head)
    return f"the address shows a placeholder page ({m.group(0)!r}), not a company site" if m else None


def registry_site_problem(registry_site: str, web: dict[str, Any], company_name: Any) -> str | None:
    """None when the registry homepage may be published as the company's website;
    otherwise a short reason to publish it as not confirmed instead."""
    site = str(registry_site or "").strip()
    if not _host_ok(site):
        return f"the registry lists {site!r}, which is not a web address"
    note = str((web or {}).get("note") or "")
    for marker, why in _DEAD_NOTES:
        if marker in note:
            return f"the registry lists {site}, but {why}"
    value = (web or {}).get("value") or {}
    if (web or {}).get("status") == "available" and isinstance(value, dict):
        dead = dead_page_reason(value.get("title"), value.get("main_text_excerpt") or value.get("description"))
        if dead:
            return f"the registry lists {site}, but {dead}"
        final = str(value.get("final_url") or "")
        if final and _registered(final) and _registered(site) and _registered(final) != _registered(site):
            if not _names_company(f"{value.get('title') or ''} {final}", company_name):
                return (f"the registry lists {site}, but it redirects to {_registered(final)}, "
                        f"which does not name the company")
    return None


def _registered(url: str) -> str:
    raw = url if "://" in url else f"http://{url}"
    host = (urllib.parse.urlparse(raw).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in {"co.uk", "com.au"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host
