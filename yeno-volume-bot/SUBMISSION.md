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

1. `python -m unittest discover -s tests` → **48 passing**.
2. `python server.py` then run the kit's `yeno-smoke-test.py` → `contractSmokePassed: true`.
3. `python scenarios.py --paths 40` → terminal-flat 100%, 0 invalid actions in
   all 10 regimes.
4. Commit, then paste the commit hash above and in the email.

## What the served config does

`tuned_config.py` is the frozen strategy:

* settles on the 60 s TWAP, not spot at close;
* buys a side only when its ask is ≥ 5 cents below the model's fair
  probability, in the 0.30–0.85 band, with a ≤ 2-cent spread;
* takes profit at a 0.95 bid, exits when the thesis flips below 0.45, and
  times out after 30 s;
* risks 30% of paper cash per BUY (minimum 5 shares, $5 fee-inclusive cap);
* never carries a position across a market change and force-flattens from 10 s;
* stops trading forever at the first clean $1,000 crossing.

## Validation

On 862 real Polymarket BTC-5m L2 markets (2026-10-03 → 2026-10-06), driving the
actual bot under the published contract: all 3 full 24 h days crossed $1,000
with **$48.68, $72.46 and $59.44** cash; 19 of 24 sliding 24 h windows qualified
(median $53.20). See `README.md` and `research/realsim.py`. This is a research
estimate, not an official score.
