# YeNo Volume Bot — submission checklist

Email `submit@builderr.ai` with the fields below.

| Field | Value |
|-------|-------|
| Bot name | `twap-edge-v8` |
| Contact | devanshgoenka03@gmail.com |
| Repository | https://github.com/DG10911/signalpost-agent (folder `yeno-volume-bot/`, branch `yeno-super-v8`) |
| Immutable commit | `git rev-parse HEAD` after committing |
| Run command | `python server.py` (serves `POST /decide` on `127.0.0.1:8080`) |
| Models / APIs | **None.** Pure Python stdlib; no LLM, no external API, no network. |
| Expected external cost | **$0** — no third-party calls. |
| Language / runtime | Python 3.9+ (no dependencies). |

## Before you submit

1. `python -m unittest discover -s tests` → **50 passing**.
2. `python server.py` then run the kit's `yeno-smoke-test.py` → `contractSmokePassed: true`.
3. `python scenarios.py --paths 40` → terminal-flat 100%, 0 invalid actions in
   all 10 regimes.
4. Commit, then paste the commit hash above and in the email.

## What the served config does

`tuned_config.py` is the frozen v9 "contract" strategy (`strategy="v9"`):

* FAV volume engine: buys a clear favourite (causal TWAP model >= 0.90, ask
  0.85-0.98, spread <= 2c) with the full $5 fee-inclusive ticket (5-share
  minimum, 3% cash headroom) and sells into the 0.99 bid / 2 s before close;
  exits early if the model probability falls below 0.5 while bids exist;
* low-cash mode (< $5.50): final-minute TWAP-value entries (edge >= 16c) with
  70% of cash, because an idle bot below a full ticket has already failed;
* never enters inside the final 15 s, never carries a position across a market
  change, HOLDs on malformed input, rebuilds a missing opening target from its
  own record of the feed, and stops trading at the first clean $1,000 crossing.

Faithful replay (see README, "v9"): 45% of train and 33% of held-out 24 h
windows cross (v8.1: 5% / 1%).

## Validation

On 862 real Polymarket BTC-5m L2 markets (2026-10-03 → 2026-10-06), driving the
actual bot under the published contract: all 3 full 24 h days crossed $1,000
with **$48.68, $72.46 and $59.44** cash; 19 of 24 sliding 24 h windows qualified
(median $53.20). See `README.md` and `research/realsim.py`. This is a research
estimate, not an official score.
