# Signalpost site — JBox build brief

For the Signalpost **JBox bonus** ($250 / $150 / $100, separate from the agent
score). The organisers score the site on UX, on laptop and phone, with four tasks:

1. **Find** a company
2. **Open** it
3. **Compare** two companies
4. **Check where a fact came from**

They host the sites and run a popular-choice vote. Send the link with the next
submission, by **7 November 2026, 23:59 IST**.

The data is our agent's own output (100 Norwegian companies), published as:

- JSON (one object per company, facts nested):
  `https://raw.githubusercontent.com/DG10911/signalpost-agent/signalpost-upgrade-nov2/signalpost/jbox-data/companies.json`
- CSV for a database import: `jbox-data/companies.csv` (one row per company) and
  `jbox-data/facts.csv` (one row per fact)

---

## Paste this into JBox ("Describe the product")

> Build **Signalpost**, a public website for browsing verified profiles of
> Norwegian companies. No login, no accounts, no admin screens. It must be
> fast and easy on a laptop and on a phone (360 px wide and up).
>
> **Data.** Load the company data from this JSON file at build time and store
> it in the database (or bundle it as static data):
> https://raw.githubusercontent.com/DG10911/signalpost-agent/signalpost-upgrade-nov2/signalpost/jbox-data/companies.json
> It is an array of companies. Each company has: organisation_number, name,
> legal_form, municipality, industry, summary, employees, website, the flags
> has_finances / has_website / has_social / has_news / has_hiring, fact_count,
> and facts[]. Each fact has: id, section (Overview, Finances, People,
> Website & social, Hiring, News), label, value, link, availability,
> availability_text, period, source_name, source_url, source_type,
> retrieved_at, quote, content_sha256. Never invent or change values.
>
> **1. Home / search page.**
> - A large search box at the top: "Search 100 Norwegian companies by name or
>   organisation number". It filters as you type (name, organisation number,
>   municipality, industry) and works with or without Norwegian letters
>   (æ ø å), so "lillestrom" finds "LILLESTRØM".
> - Under it, filter chips: Has website, Has news, Hiring, Has accounts.
> - Results as a list of cards: company name, organisation number (monospace),
>   municipality, industry, and small badges for what was found (Website,
>   News, Hiring, Accounts). Tapping a card opens the company page.
> - Each card has a "Compare" checkbox. When 2 are ticked, a sticky bar at the
>   bottom says "Compare 2 companies →".
> - A short line explains the site: "Every fact links to where it came from."
>
> **2. Company page.**
> - Header: name, organisation number, legal form, municipality, industry,
>   and the summary. A "Compare with…" button and a "Back to search" link.
> - Below, the facts grouped by section in this order: Overview, Finances,
>   People, Website & social, Hiring, News. Sticky section tabs on the side
>   (laptop) or a horizontal scroller (phone).
> - Each fact is a row: label, value (links open in a new tab), period if
>   present (e.g. "2025-01-01 – 2025-12-31"), and a small **"Source"** button.
> - Finances: show the latest year prominently and the earlier years in a small
>   table or sparkline per metric (facts with the same label and different
>   periods).
> - Facts whose availability is not "available" are shown greyed, with their
>   availability_text ("Looked, nothing found", "Could not confirm it is this
>   company", …). Never show a missing value as 0 or as an empty row.
>
> **3. Source panel (the key feature).**
> Clicking **Source** on any fact opens a side panel (laptop) or bottom sheet
> (phone) showing: the source name, a button "Open original source" (the
> source_url, new tab), when it was retrieved, the exact quoted text in a
> quote block, and the content fingerprint (content_sha256, monospace, with a
> copy button). It closes with Esc, a close button, or swiping down.
>
> **4. Compare page.**
> - Two companies side by side (stacked cards with a sticky name header on
>   phone). Each can be changed with a search box at the top of its column.
> - Rows: organisation number, legal form, municipality, industry, employees,
>   founded, website, latest revenue / operating result / equity (with year),
>   number of news items, number of job ads, social profiles.
> - Every value has the same Source button as on the company page. Missing
>   values show the availability_text, not a dash.
> - The URL encodes the two organisation numbers (e.g. /compare?a=…&b=…) so a
>   comparison can be shared.
>
> **Also.**
> - Every company page has its own URL (/company/<organisation_number>).
> - An "About the data" page: 100 companies, sources (Brønnøysund registers,
>   NAV job board, the companies' own websites and social profiles), retrieved
>   on the dates shown, made by an automated agent with every fact cited.
> - Keyboard-friendly, visible focus, WCAG AA contrast, works without hover.
>
> **Look.** Clean and trustworthy, like a data newsroom: white background,
> one deep blue accent, a serif for company names, a clean sans-serif for
> everything else, monospace for organisation numbers and fingerprints, thin
> divider lines, generous spacing. Light mode first.

---

## After JBox builds it: test before sending (both laptop and phone)

| # | Task | Pass when |
|---|---|---|
| 1 | Search "torghatten", then "916819927", then "lillestrom" (should find LILLESTRØM companies) | Right company appears as you type each time |
| 2 | Tap a result | Company page opens with sections and facts |
| 3 | Tap Source on a revenue figure | Panel shows Brønnøysund accounts register, period, quote, fingerprint; "Open original source" works |
| 4 | Open VANG ALMENNING, tap Source on a news item; open TORGHATTEN AS, tap Source on a job ad | Panel shows the company's own page link and the quoted headline |
| 5 | Open AASE GAUSTAD INVEST AS (no website) | Website shows "Looked, nothing found", not blank or 0 |
| 6 | Tick two companies, Compare | Side by side, every value has Source; URL is shareable |
| 7 | Phone width (360 px) | No sideways scrolling; source panel is a bottom sheet; buttons easy to tap |
| 8 | Keyboard only (Tab / Enter / Esc) | Can search, open, open and close a source |

If a test fails, describe the failure to JBox in plain words ("On phone, the
source panel covers the close button; make it a bottom sheet with a visible
close button") and let it fix it.

## Refreshing the data

```
uv run python scripts/run_competition_batch.py ... --contract-output out/submission-contract.jsonl
uv run python scripts/export_jbox_dataset.py out/submission-contract.jsonl jbox-data/
```
Commit `jbox-data/` and redeploy the JBox site so it picks up the new JSON.
