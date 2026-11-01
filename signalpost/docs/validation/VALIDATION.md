# Hand check of published facts (1 Nov 2026)

A check that does not use the agent's own rules: every external fact the agent
published for a fresh random sample was fetched again with a separate script
(plain HTTP + regex, none of the agent's extraction or identity code) and
judged item by item by the builder. Per-item verdicts:
[`hand-check-2026-11-01.json`](hand-check-2026-11-01.json).

**Sample:** 100 companies drawn at random from the public universe (seed
99173), excluding the 1,000-company development cohort. Of these, 86 list no
homepage in the registry. Run with the documented command (`SUBMISSION.md`).

## How often each source type is right

| Source type | Published | Correct | Wrong | Could not re-check |
|---|---|---|---|---|
| Company website | 14 | 11 | 0 | 3 (site did not load; registry-listed) |
| Social profile | 13 | 13 | 0 | 0 |
| Dated news | 23 | 23 | 0 | 0 |
| Job ad | 1 | 1 | 0 | 0 |
| **Total** | **51** | **48** | **0** | **3** |

Notes:

- LinkedIn pages sit behind a captcha; those 4 handles were judged on the
  handle itself (the company's own name), not the page content.
- ACT GRUPPEN AS: the site is the group's own domain and its news items are
  about its subsidiary brand ACT Logimark. Counted correct; a stricter reading
  would count them as the subsidiary's.
- The job ad is a NAV listing matched on the ad's employer organisation number;
  NAV's feed needs a token, so it was checked against the feed, not re-opened.
- One wrong website was found in the first pass of this check and fixed before
  the counts above: a housing co-op's registry homepage
  `norian.no/eiendomsforvaltning` is its property manager's page. Registry
  homepages on a domain many entities list are now marked *ambiguous* even
  with a generic path; a path counts as the company's own only when it carries
  the company's name.

## What was left blank, and why

When a candidate is uncertain the agent leaves the fact blank and writes the
reason into the claim (`"reason"` field) and onto the browse page.
In this sample:

| Left out | Count | Reason |
|---|---|---|
| News candidates | 10 | headline is link text ("read more") or a date, not an article title |
| News candidates | 10 | not in a news or press section and the headline does not name the company |
| News candidates | 5 | page is a guide or advice article, not news about the company |
| News candidates | 3 | no article address |
| Careers pages | 2 | careers page lists no current opening |
| Careers pages | 1 | page says "ingen ledige stillinger" |

Other reasons the check uses: ordinary site page (product, service, privacy,
contact), opinion article, ESG/sustainability content, placeholder post
("Hello world!"), headline that is only the page address, no publication date,
duplicate address.

## Earlier hand checks

- **Website discovery, first version (25 Oct):** 6 sites found on 100
  companies, 5 wrong (a chain's site for a franchise, a housing manager's
  software, an owner's other company). The rules were rewritten against those
  cases.
- **Website discovery, current rules:** 6 of 6 correct on two fresh 100-company
  samples; 36 of 36 correct on the 1,000-company cohort.

## Limits

- One sample of 100 companies; small counts per source type (news comes from 3
  companies).
- "Correct" means the item belongs to this company and is the kind of fact it
  claims to be. It does not judge whether a news item is important.
