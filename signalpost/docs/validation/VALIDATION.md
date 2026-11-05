# Hand check of published facts (5 Nov 2026, build 1c8c2f8)

The same check as on 1 Nov, repeated for the new build's JBox dataset
(`jbox-data/companies.json`, 100 companies). Every published
(`availability == "available"`) fact under "Website & social" (official
website and social profiles), "Hiring" and "News" was fetched again with a
separate script (Python stdlib `urllib` and `curl`, regex only; none of the
agent's code). Each item was then judged by hand. Requests were capped at
under 2 per second, with a 15 s timeout and a browser User-Agent. Per-item
verdicts: [`hand-check-2026-11-05.json`](hand-check-2026-11-05.json).

## How often each source type is right

| Source type | Published | Correct | Wrong | Could not re-check |
|---|---|---|---|---|
| Company website | 57 | 35 | 18 | 4 (site did not load from two vantage points; registry-listed) |
| Social profile | 20 | 20 | 0 | 0 |
| Dated news | 5 | 4 | 1 | 0 |
| Job ad | 6 | 6 | 0 | 0 |
| Careers page | 4 | 2 | 2 | 0 |
| **Total** | **92** | **67** | **21** | **4** |

Websites split by how the agent got them:

| Website source | Published | Correct | Wrong | Could not re-check |
|---|---|---|---|---|
| Registry homepage published without a page check (`official_registry_bulk`) | 35 | 14 | 17 | 4 |
| Registry homepage the agent opened and matched to the company | 22 | 21 | 1 | 0 |

Most of the wrong items come from one cause. When the agent does not open
the registry homepage, it publishes the address as-is, and many of those
addresses are dead or belong to someone else.

## Wrong items

Websites, dead registry links (13). The address does not serve any site for
the company:

- ARGENITY AS, `argenity.no`: parked domain (redirects to webhuset.no/parkering).
- BARENTS GYM AS, `www.barentsgym.no`: domain does not exist (NXDOMAIN, confirmed via DNS-over-HTTPS).
- BERGTUN EIENDOM AS, `www.bergtungaard.no`: NXDOMAIN.
- CS BUSINESS PARTNER AS, `www.csbp.no`: NXDOMAIN.
- DATA BASIS AS, `www.databasis.no`: hosting account suspended.
- INTRO TRAINEE, `www.introtrainee.no`: Cloudflare error 1014 from both vantage points.
- JETTEBRA AS, `www.jettebra.no`: NXDOMAIN.
- NEWBIE AS, `www.vidayum.com`: domain for sale (redirects to atom.com/name/VidaYum).
- OKK-DRIFT AS, `www.hitt.no`: NXDOMAIN.
- SJØLVSAGT AS, `www.sjølvsagt.no`: "Squarespace - Website Expired".
- STEINSFJELLET BARNEHAGE SA, `www.steinsfjelletbarnehage.no`: Wix "ConnectYourDomain Error".
- SØRE ØYANE GRENDALAG NATURBARNEHAGE SA, `www.soreoyane.barnehage.no/`: HTTP 404 from both vantage points.
- UNIQUE EVENT AS, `www.arrangorservice.no`: NXDOMAIN.

Websites, other errors (5):

- CROSSBORDER AS, `https://www.crossborder.global/`: the page is the default "Welcome to nginx!" page and names no company. This is the one wrong item among the sites the agent opened. Its own quote for the fact is "Welcome to nginx!", so the identity check accepted a server's default page.
- REKDAL TRANSPORT AS, `wwwrekdaltransport`: the registry value is malformed (no dot, no domain), yet it was published as a website.
- AS ÅRVOLLVEIEN 15, `www.sgf.no`: the site of the SGF property and contractor group, which owns the company. The site never mentions Årvollveien 15.
- KOLLEKTIVTERMINALEN AS, `www.aa-f.kommune.no`: the domain of the former Aust-Agder county council (the owner). It also no longer exists.
- RØDBOKA AS, `www.rodboka.no`: answers 301 to `jdpower.com/business`, the owner's business page.

News (1):

- ARENA NORD UTVIKLING AS, "Skal bygge «tanks» i Norge" (Dagbladet): the article is about Ritek in Levanger building Leopard tanks and never names Arena Nord Utvikling (Narvik). It is a link the company shared on its homepage, not news about the company. The claimed date (2024-06-14) also differs from the article's (11.06.2024).

Careers pages (2):

- SSP SIKKERHET AS, `sspi.no/karriere/`: the page shows no current SSP opening. The only vacancy is a header banner, "Ledig stilling Lexov Lås", whose "Se annonse" button links nowhere.
- TORGHATTEN AS, `torghatten.no/career/apprentice-and-cadet`: an evergreen description of apprenticeships ("send an e-mail to rekruttering@torghatten.no") with no dated vacancy.

## Judgement calls (counted correct)

- ADVOKAT AMALIE HØIVANG → `hoivang.no`: "Advokatfirmaet Høivang", in the same town, with the owner's photo. This is a surname-brand match, not the registered name.
- ARTIGE INNSLAG AS → `gjelten.com`: the performer site of Geir Gjelten, who is CEO and chair of this one-person entertainment company. The page does not print "Artige Innslag".
- NORSK WAVIN AS → `wavin.no` redirects to `wavin.com/no/nb`, the group site's Norwegian section, headed "Norsk Wavin". A stricter reading would call it the group's site.
- NOSEFO BERGEN AS → `nosefo.no`: the shared NOSEFO brand site. It lists "Nosefo Bergen, Bontelabo 8a", the registered address.
- SOL SKJOLD AS → `sun2skjold.no`: a Webnode page for "Sun² Skjoldskiftet", a solarium in Bergen. This matches the registry activity, but the page does not print the registered name.
- VITO SETERMOEN AS → `vitosetermoen.no` redirects to a page on boat.no's site builder. The text is written by the company itself. A stricter reading would call it a directory page.
- FENOMENTAL AS → `fenomental.no`: the company's own domain, but only a placeholder ("Her kommer: www.fenomental.no").
- LINK REKRUTTERING AS news: a real dated post about the company (13 Mar 2011). It is 15 years old and sits at a reused WordPress `hello-world` address.
- BYBERG AS careers page: lists a CNC-operatør opening with the deadline "Snarest" and no date.
- TORGHATTEN AS: the NAV ad and the ReachMee ad for the same matros vacancy are published as two items. Both are correct, but they are a duplicate.

## How the check was done, and its limits

- Facebook (HTTP 400) and LinkedIn (HTTP 403) block scripted fetches. All 14 of those handles were judged on the handle plus the fact that the company's own site links to it. Each link was found in the site HTML by this script, on the homepage or a contact/about page. Instagram and X serve script shells. X page titles confirmed both X handles. The 4 Instagram handles were judged on the link from the site, or, for `sunn_hest`, on an exact handle match.
- The local network sits behind a Check Point filter that resets some Norwegian sites. Six sites were re-fetched through a remote reader (r.jina.ai): Horni, Wavin, Northbrew, God Mat Lokalt, Sunn-hest and Vito Setermoen. This is noted per item. Four more did not load from either vantage point and are counted as "could not re-check": Boligtakst Vestfold, Eidegruppen, Konstant and Speewell.
- NAV job ads were re-fetched from the NAV feed with NAV's published public token. All 3 are ACTIVE with 1 position each. The employer's sub-unit org number was looked up in Brønnøysund and belongs to the company in each case.
- "Dead registry link" is counted as wrong only when the failure is certain: NXDOMAIN, a parked, for-sale, suspended or expired page, a CDN or host configuration error, or a 404 from two vantage points. A timeout or reset counts as "could not re-check".

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
