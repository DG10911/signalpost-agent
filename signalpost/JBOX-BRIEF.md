# JBOX build brief — Signalpost company-intelligence UI

Paste this into **jboxai.com** ("Describe the product"). JBOX generates a hosted
Next.js + Supabase app. This brief builds the **product / UX layer** that the
Signalpost Python agent's output feeds. It is deliberately explicit about roles,
screens and states so JBOX maps every path before it builds.

> Important: JBOX builds the *interface*, not the crawler. The scored agent stays
> the Python engine in this repo. Keeping the two separate is what protects the
> qualification we already hold. See "Eligibility" at the bottom.

---

## One-line brief

Build **Signalpost** — a company-intelligence web app for Norway. A user enters
an organisation number (or a company name) and gets a verified company profile:
what the company does, who leads it, where it operates, its latest filed
numbers, whether it is hiring, and dated public activity — **every fact shown
next to its source and date**, with missing/blocked facts shown honestly rather
than as zero.

## Users and roles

- **Visitor (public)** — search and read profiles. No login. This is the demo /
  hosted-gallery surface.
- **Analyst (logged in)** — everything a visitor can do, plus: save companies to
  a watchlist, trigger a refresh, export a profile as JSON, and view the
  change history since the previous run.
- **Admin (internal)** — upload a batch of organisation numbers, see run status
  (loaded / done / failed), and download the submitted artifacts.

## Screens

1. **Home / search**
   - Large search box: accepts a 9-digit organisation number or a company name.
   - Live-suggest dropdown (name, org. no., municipality, industry).
   - "Try an example" chips (3 real companies).
   - Small strip of stats: companies covered, sources, last refresh.

2. **Company profile** (the core screen)
   - Header: legal name, org. no., legal form, municipality, industry, status
     badge (active / bankrupt / under liquidation).
   - **Fact cards**, each showing: value, a small "source" link (opens the
     evidence URL), the retrieval date, and a confidence chip. Sections:
     - *Legal identity & public brand*
     - *Latest annual accounts* (revenue, operating result, result, assets,
       equity, debt) with a small multi-year sparkline and filed-year list
     - *Leadership & registered workplaces* (role cards with name + role + since)
     - *Official website & company-owned profiles* (site + social handles)
     - *Hiring & dated public activity* (job postings, news items)
   - Every fact is clickable → opens an **Evidence drawer** showing source URL,
     source class, retrieval timestamp, content hash, and the exact claim span.
   - **Availability states** rendered distinctly and honestly:
     `available` (normal), `not_available` ("we looked, nothing there"),
     `blocked` ("source refused"), `not_applicable`, `ambiguous` ("could not
     confirm it is this company"), `failed`. Never show a missing fact as `0`.
   - **Refresh panel**: "Last refreshed <date> · <n> changes since previous run"
     with a change list (added / removed / changed), each linking evidence.

3. **Compare** (analyst)
   - Pick 2–4 companies; side-by-side table of key facts with source links.

4. **Watchlist** (analyst)
   - Table of saved companies with latest-refresh status and a "refresh" action.

5. **Batch / admin**
   - Paste or upload a list of org numbers; see a progress table
     (pending / done / failed) and a download button for the JSON export.

## Data model (Supabase)

- `companies` — org_number (pk), legal_name, legal_form, municipality, industry,
  status, website, summary, last_refreshed_at.
- `claims` — id, org_number (fk), field, value (jsonb), availability,
  confidence, reporting_period, evidence_id.
- `evidence` — id, source_url, source_class, retrieved_at, content_sha256,
  claim_span.
- `observations` — id, org_number, platform, signal_type, source_url,
  effective_at, metrics (jsonb).
- `snapshots` — id, org_number, taken_at, payload (jsonb) — append-only, for
  change history (never update in place).
- `watchlist` — user_id, org_number.

Seed from the engine's JSON export (`out/submission-contract.jsonl`), one object
per company with `claims[]` + `evidence[]`; split into the tables above.

## Non-negotiable product rules

- **Provenance first.** No fact renders without a source link and a date. If a
  fact has no evidence, do not show it.
- **Honest absence.** The six availability states above are first-class UI, not
  errors.
- **Refresh = history, not overwrite.** Show changes since the previous run;
  keep prior snapshots.
- **Mobile-first and accessible.** Responsive down to 360px; keyboard navigable;
  WCAG AA contrast; the evidence drawer reachable by keyboard.
- **No fabricated values.** Ever.

## Visual direction (tone)

Clean, editorial, trustworthy — closer to a data newsroom than a startup
dashboard. One restrained accent colour, generous whitespace, a serif for
company names and a grotesque for data, hairline rules, monospace for org
numbers and hashes. Light mode default, dark mode supported.

## Copilot prompt (to trigger the build)

> Build a company-intelligence web app called Signalpost for Norwegian
> companies. Users search by organisation number or name and see a verified
> profile. Every fact must display its source link and retrieval date. Support
> six availability states (available, not_available, blocked, not_applicable,
> ambiguous, failed) and never show a missing fact as zero. Include a company
> profile screen with fact cards and an evidence drawer, a refresh/change-history
> panel, a compare view, a watchlist, and a batch-upload admin screen. Use
> Next.js + Supabase, seed the schema from the JSON structure above, and make it
> responsive, keyboard-navigable and WCAG AA. Editorial, trustworthy,
> data-newsroom aesthetic — one accent colour, serif for names, monospace for
> IDs and hashes.

---

## Eligibility — what "built with JBOX" actually means

The public challenge page states the $500 bonus is
"for qualifying agents built with JBOX." The official participant brief only
says "$500 JBOX bonus pool" with no criterion. Two gates therefore apply:

1. **Qualifying** — the *agent* still needs ≥65/100 on the official run.
2. **Built with JBOX** — the *agent* (the thing that is evaluated) would have to
   be built on JBOX. JBOX builds Next.js/Supabase product UIs, not the Python
   crawler that is scored, so this UI alone almost certainly does **not** make
   the scored agent "built with JBOX."

**Before spending a revision on it, ask Soham:**

> Hi Soham — for the $500 JBOX bonus, what does "built with JBOX" concretely
> require for an entry? Does the *scored agent* have to run on JBOX, or does a
> JBOX-built interface over an otherwise-independent agent qualify? If it's a
> light requirement (host/wrap), I'll add it; if it means re-implementing the
> agent on JBOX, I'll pass. Thanks.
