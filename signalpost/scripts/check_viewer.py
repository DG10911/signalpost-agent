#!/usr/bin/env python3
"""End-to-end check of the static viewer with Playwright (Chromium).

Serves <viewer> on a local port and runs the four user tasks — find, open,
compare, verify — at desktop (1440 px) and phone (390 px, plus compare at
360 px). Fails on any console error, page error, failed request or
horizontal overflow, and saves screenshots.

    python -m venv /tmp/vw && /tmp/vw/bin/pip install playwright && /tmp/vw/bin/playwright install chromium
    /tmp/vw/bin/python scripts/check_viewer.py --viewer viewer --shots /tmp/viewer-shots
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import sys
import threading
import unicodedata
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass


def serve(root: Path) -> tuple[socketserver.TCPServer, str]:
    handler = functools.partial(Quiet, directory=str(root))
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def ascii_fold(s: str) -> str:
    s = s.lower().replace("ø", "o").replace("æ", "ae")
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if not unicodedata.combining(ch))


class Checker:
    def __init__(self, base: str, shots: Path, viewer: Path):
        self.base, self.shots = base, shots
        self.index = json.loads((viewer / "data" / "index.json").read_text(encoding="utf-8"))
        self.stats = json.loads((viewer / "data" / "stats.json").read_text(encoding="utf-8"))
        self.results: list[tuple[str, bool, str]] = []
        self.errors: list[str] = []

    def ok(self, name: str, cond: bool, detail: str = "") -> None:
        self.results.append((name, bool(cond), detail))
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

    def no_overflow(self, page: Page, where: str) -> None:
        sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        self.ok(f"no horizontal overflow: {where}", sw <= iw, f"scrollWidth {sw} vs {iw}")

    def shot(self, page: Page, name: str, full: bool = False) -> None:
        page.screenshot(path=str(self.shots / f"{name}.png"), full_page=full)

    def watch(self, page: Page, tag: str) -> None:
        page.on("console", lambda m: m.type == "error" and self.errors.append(f"{tag} console: {m.text}"))
        page.on("pageerror", lambda e: self.errors.append(f"{tag} pageerror: {e}"))
        page.on("requestfailed", lambda r: self.errors.append(f"{tag} requestfailed: {r.url}"))
        page.on("response", lambda r: r.status >= 400 and self.errors.append(f"{tag} HTTP {r.status}: {r.url}"))

    # ---- flows ----
    def find(self, page: Page, tag: str) -> None:
        page.goto(self.base + "#/")
        expect(page.locator("#results .card").first).to_be_visible()
        self.shot(page, f"{tag}-1-home")
        self.no_overflow(page, f"{tag} home")
        q = page.locator("#q")
        # accent-insensitive: a company whose name has æ/ø/å, searched in plain ASCII
        target = next(c for c in self.index if any(ch in c["n"].lower() for ch in "æøå"))
        q.fill(ascii_fold(target["n"]))
        expect(page.locator("#results .card h3").first).to_have_text(target["n"])
        self.ok(f"{tag} find: accent-insensitive name '{ascii_fold(target['n'])}' -> {target['n']}", True)
        muni = next(c for c in self.index if "Ø" in c["m"])
        q.fill(ascii_fold(muni["m"]))
        page.wait_for_timeout(150)
        metas = page.locator("#results .card .meta").all_inner_texts()
        self.ok(f"{tag} find: municipality '{ascii_fold(muni['m'])}' matches", bool(metas) and any(ascii_fold(muni["m"]) in ascii_fold(m) for m in metas[:3]))
        org = self.index[0]["o"]
        for typed in (org, f"{org[:3]} {org[3:6]} {org[6:]}"):
            q.fill(typed)
            expect(page.locator("#results .card h3").first).to_have_text(self.index[0]["n"])
            self.ok(f"{tag} find: org number '{typed}'", True)
        q.fill("")
        page.locator('[data-flag="w"]').click()
        page.wait_for_timeout(100)
        cnt = page.locator("#count").inner_text()
        self.ok(f"{tag} filter: has website -> {self.stats['with_website']}", cnt.startswith(f"{self.stats['with_website']} "), cnt)
        page.locator('[data-flag="w"]').click()
        page.select_option("#sort", "revenue")
        page.wait_for_timeout(100)
        top = max((c for c in self.index if c["r"]), key=lambda c: c["r"][0])
        expect(page.locator("#results .card h3").first).to_have_text(top["n"])
        self.ok(f"{tag} sort: revenue -> {top['n']}", True)
        page.select_option("#muni", "OSLO")
        page.wait_for_timeout(100)
        oslo = sum(c["m"] == "OSLO" for c in self.index)
        cnt = page.locator("#count").inner_text()
        self.ok(f"{tag} filter: municipality OSLO -> {oslo}", cnt.startswith(f"{oslo} "), cnt)
        self.ok(f"{tag} find state in URL", "m=OSLO" in page.url and "sort=revenue" in page.url, page.url)
        self.shot(page, f"{tag}-2-find-filtered")
        self.no_overflow(page, f"{tag} filtered")

    def open_and_verify(self, page: Page, tag: str) -> None:
        page.goto(self.base + "#/")
        expect(page.locator("#results .card").first).to_be_visible()
        first = page.locator("#results .card h3 a").first
        name = first.inner_text()
        first.click()
        expect(page.locator("h1#h-co")).to_have_text(name)
        expect(page.locator("#sec-key")).to_be_visible()
        expect(page.locator("#sec-found .ci").first).to_be_visible()
        n_src = page.locator("main [data-fact]").count()
        n_rows = page.locator("main .fact").count()
        self.ok(f"{tag} open: company page '{name}' ({n_rows} fact rows, {n_src} source buttons)", n_src > 0 and n_rows > 0)
        rows_wo = page.evaluate("Array.from(document.querySelectorAll('main .fact')).filter(r=>!r.querySelector('[data-fact]')).length")
        self.ok(f"{tag} verify: every fact row has a Source button", rows_wo == 0, f"{rows_wo} rows without")
        self.shot(page, f"{tag}-3-company")
        self.shot(page, f"{tag}-3-company-full", full=True)
        self.no_overflow(page, f"{tag} company")
        # Verify: open evidence via the keyboard
        btn = page.locator("main .src-btn").first
        btn.focus()
        page.keyboard.press("Enter")
        drawer = page.locator("#drawer")
        expect(drawer).to_be_visible()
        body = page.locator("#dr-body").first.inner_text()
        self.ok(f"{tag} verify: drawer shows source, retrieved time, quote, SHA-256",
                all(k in body for k in ("Open original source", "Retrieved", "SHA-256", "Quoted from the source")))
        sha = page.locator("#dr-body .hash code").first.inner_text()
        self.ok(f"{tag} verify: hash looks like SHA-256", len(sha) == 64 and all(ch in "0123456789abcdef" for ch in sha), sha)
        href = page.locator("#dr-body a.ev-open").first.get_attribute("href") or ""
        self.ok(f"{tag} verify: original source link", href.startswith("http"), href)
        self.ok(f"{tag} verify: focus moved into drawer", page.evaluate("document.getElementById('drawer').contains(document.activeElement)"))
        self.ok(f"{tag} verify: fact deep link in URL", "?fact=" in page.url, page.url)
        pos1 = page.locator("#dr-pos").inner_text()
        page.locator("#dr-next").click()
        pos2 = page.locator("#dr-pos").inner_text()
        self.ok(f"{tag} verify: next fact", pos1 != pos2, f"{pos1} -> {pos2}")
        page.locator("#dr-prev").click()
        self.shot(page, f"{tag}-4-evidence")
        self.no_overflow(page, f"{tag} evidence drawer")
        page.locator("#dr-body [data-copy]").first.click()
        expect(page.locator("#toast")).to_contain_text("SHA-256 copied")
        page.keyboard.press("Escape")
        expect(drawer).to_be_hidden()
        self.ok(f"{tag} verify: Esc closes, focus returns to the Source button",
                page.evaluate("document.activeElement && document.activeElement.hasAttribute('data-fact')"))
        # deep link straight into a fact
        url = page.url
        fid = page.locator("main [data-fact]").nth(3).get_attribute("data-fact")
        page.goto(url.split("#")[0] + "#/company/" + url.split("/company/")[1].split("?")[0] + f"?fact={fid}")
        page.reload()
        expect(drawer).to_be_visible()
        self.ok(f"{tag} verify: ?fact= deep link opens the evidence panel", True)
        page.keyboard.press("Escape")

    def compare(self, page: Page, tag: str) -> None:
        page.goto(self.base + "#/")
        page.evaluate("localStorage.clear()")
        page.reload()
        expect(page.locator("#results .card").first).to_be_visible()
        for i in range(3):
            page.locator("#results [data-cmp]").nth(i).click()
        expect(page.locator("#tray")).to_be_visible()
        expect(page.locator("#tray-go")).to_have_text("Compare 3")
        self.shot(page, f"{tag}-5-tray")
        page.locator("#tray-go").click()
        expect(page.locator("table.cmp")).to_be_visible()
        ids = page.url.split("ids=")[1].split("&")[0].split(",")
        self.ok(f"{tag} compare: shareable URL with 3 ids", len(ids) == 3, page.url)
        # add a 4th from the company page flow: open a company, add, come back
        org4 = next(c["o"] for c in self.index[10:] if c["o"] not in ids and c["fl"].find("w") >= 0)
        page.goto(self.base + f"#/company/{org4}")
        page.locator(".co-actions [data-cmp]").click()
        page.goto(self.base + "#/compare")
        expect(page.locator("table.cmp")).to_be_visible()
        n_cos = page.locator(".cmp-co").count()
        self.ok(f"{tag} compare: 4 companies (added from a company page)", n_cos == 4, str(n_cos))
        diffs = page.locator("table.cmp tr.differs").count()
        self.ok(f"{tag} compare: differing rows highlighted", diffs > 0, f"{diffs} rows")
        bad = page.evaluate("""Array.from(document.querySelectorAll('table.cmp tbody td')).filter(td =>
            !td.querySelector('[data-fact]') && !/No filed figure|Not found/.test(td.textContent)).length""")
        self.ok(f"{tag} compare: every value has a source icon", bad == 0, f"{bad} cells without")
        self.shot(page, f"{tag}-6-compare")
        self.shot(page, f"{tag}-6-compare-full", full=True)
        self.no_overflow(page, f"{tag} compare (4)")
        page.locator("table.cmp [data-fact]").first.click()
        expect(page.locator("#drawer")).to_be_visible()
        self.ok(f"{tag} compare: source icon opens evidence", "SHA-256" in page.locator("#dr-body").first.inner_text())
        self.shot(page, f"{tag}-7-compare-evidence")
        page.keyboard.press("Escape")
        page.locator("#only-diff").click()
        page.wait_for_timeout(200)
        same = page.locator("table.cmp tbody tr:not(.grp):not(.differs)").count()
        self.ok(f"{tag} compare: 'Only differences' hides equal rows", same == 0, str(same))
        # shared URL in a fresh context renders the same comparison
        shared = self.base + "#/compare?ids=" + ",".join(c["o"] for c in self.index[:2])
        page.evaluate("localStorage.clear()")
        page.goto(shared)
        page.reload()
        expect(page.locator(".cmp-co")).to_have_count(2)
        self.ok(f"{tag} compare: shared URL opens directly", True)

    def about(self, page: Page, tag: str) -> None:
        page.goto(self.base + "#/about")
        expect(page.locator("#h-about")).to_be_visible()
        self.no_overflow(page, f"{tag} about")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewer", type=Path, default=Path("viewer"))
    ap.add_argument("--shots", type=Path, default=Path("/tmp/viewer-shots"))
    args = ap.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)
    srv, base = serve(args.viewer.resolve())
    ck = Checker(base, args.shots, args.viewer)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for tag, vp, mobile in (("desktop-1440", {"width": 1440, "height": 900}, False),
                                ("phone-390", {"width": 390, "height": 844}, True)):
            ctx = browser.new_context(viewport=vp, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
            ctx.grant_permissions(["clipboard-read", "clipboard-write"])
            page = ctx.new_page()
            ck.watch(page, tag)
            ck.find(page, tag)
            ck.open_and_verify(page, tag)
            ck.compare(page, tag)
            ck.about(page, tag)
            ctx.close()
        ctx = browser.new_context(viewport={"width": 360, "height": 740}, is_mobile=True, has_touch=True, device_scale_factor=2)
        page = ctx.new_page()
        ck.watch(page, "phone-360")
        page.goto(base + "#/compare?ids=" + ",".join(c["o"] for c in ck.index[:4]))
        expect(page.locator("table.cmp")).to_be_visible()
        ck.no_overflow(page, "phone-360 compare (4)")
        ck.shot(page, "phone-360-compare", full=True)
        ctx.close()
        browser.close()
    srv.shutdown()
    for e in ck.errors:
        print("ERROR", e)
    ck.ok("no console errors, page errors or failed requests", not ck.errors, f"{len(ck.errors)} errors")
    failed = [r for r in ck.results if not r[1]]
    print(f"\n{len(ck.results) - len(failed)}/{len(ck.results)} checks passed; screenshots in {args.shots}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
