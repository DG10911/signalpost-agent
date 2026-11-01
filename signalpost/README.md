# Signalpost company research agent — Devansh Goenka

Researches Norwegian companies from their organisation numbers. It publishes
only facts it can tie to the exact legal entity, and it cites every claim with
the source URL, a content hash and the quoted text.

## Browse the results

Open **[`browse/index.html`](browse/index.html)** in a browser (download the
folder, then double-click). It holds a fresh random sample of 100 companies
with search, filters, a CSV export, and one page per company. Each page shows
every published fact with its source and date, plus the items the agent
deliberately left out and why.

To build the same page from your own run:

```bash
uv run python scripts/build_profile_site.py --profiles out/profiles.jsonl --output out/site
```

There is also an interactive app: `uv run --with streamlit streamlit run app.py`.

## Run it

The exact official command is in [`SUBMISSION.md`](SUBMISSION.md). In short:
`uv sync`, then `scripts/run_competition_batch.py` with the batch file and the
Brreg bulk snapshot. A 1,000-company run takes about 36 minutes and 8.5
requests per company. That fits the 45-minute window and the 2,000 requests
per 100 companies allowance, and no API keys are used.

## What it collects

| Family | Source | How it is tied to the company |
|---|---|---|
| Registry, accounts, roles, sub-units | Brønnøysund APIs | organisation number |
| Website | registry homepage; otherwise the registry e-mail domain or a name-derived domain | org number on the page, or the full registered name with legal form, or registry phone/e-mail plus the full name; never a domain another registered entity lists |
| Social profiles | links on the verified site | handle carries the company's name |
| Dated news | the verified site's news/press pages, RSS, WordPress posts, `<time>` dates | real publication date; second-pass check drops product pages, guides, opinion and ESG pieces, link-text headlines |
| Jobs | NAV's public vacancy feed; single-ad links and JobPosting data on the careers page | ad's employer org number (or a registered sub-unit); careers pages only if they show a current opening |

## How accurate it is

A hand check of every published fact for a fresh random 100 companies found
48 correct, 0 wrong and 3 that could not be re-checked (the sites did not
load). Details per source type, and what was left blank and why, are in
[`docs/validation/VALIDATION.md`](docs/validation/VALIDATION.md).

## Layout

- `src/norway_company_agent/`: the agent (identity gate, crawler, discovery, NAV, content-quality check)
- `scripts/run_competition_batch.py`: the batch entry point
- `tests/`: unit tests (`uv run --with pytest python -m pytest -q`)
- `docs/STARTER-KIT-README.md`: the original starter-kit notes this agent grew from
