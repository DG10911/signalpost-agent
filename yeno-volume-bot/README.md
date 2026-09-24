# YeNo × Builderr Volume Bot

A `/decide` trading agent for the [YeNo × Builderr Volume Bot Challenge](https://builderr.ai).
Start with $10 paper cash; in 24 h produce ≥ $1,000 of eligible BUY+SELL volume
while controlling fees, spreads and losses, and finish terminal-flat.

Only `bot.py`'s strategy is ours. Cash, fills, fees, volume and position
accounting are evaluator-owned; the bot only returns `HOLD` / `BUY` / `SELL` and
*estimates* fees locally to size orders inside the $5 fee-inclusive cap.

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Strategy + fee model. `Bot` class and a module-level `decide()`. |
| `server.py` | `POST /decide` HTTP server (`python server.py`). |
| `simulator.py` | **Synthetic** fee-accurate 24 h harness for correctness/robustness. |
| `replay.py` | Replays **real** recorded observation frames (JSONL) and grid-searches params. |
| `tests/test_bot.py` | `unittest` suite (16 tests), pure stdlib. |

Python 3.9+ (developed/tested on 3.9.6; 3.12+ per the starter kit). **No third-party
dependencies, no model/API calls, no network. Expected external cost: $0.**

## Run it

```bash
python server.py                      # serves POST http://127.0.0.1:8080/decide
python -m unittest discover -s tests  # 21 tests, all pass
python simulator.py --paths 30        # synthetic sanity/stress harness
python scenarios.py --paths 40        # adversarial regime battery (see below)
```

`scenarios.py` runs the strategy across 10 adverse regimes (wide spread, thin
depth, heavy adverse fills, high vol, no-edge, strong trend, …) and asserts the
invariants that decide qualification: **terminal-flat 100%** and **0 invalid
actions** in every regime (verified). It also prints median volume / ending
cash / drawdown per regime — SYNTHETIC only (qualification volume is not
provable without real frames; use `replay.py`).

Official contract smoke test (from the starter kit) passes against this server:

```bash
python /path/to/yeno-smoke-test.py --url http://127.0.0.1:8080/decide \
  --observation /path/to/yeno-sample-observation.json
# -> {"contractSmokePassed": true, ...}
```

## Strategy

The economics of the challenge decide the design:

* **Per-side fee fraction = `0.01 + 0.07·(1 − price)`.** It shrinks toward 1.0,
  so a **clear favourite** is both cheaper to trade *and* packs more notional
  into the $5 cap → more eligible volume per completed cycle. The bot buys
  favourites, concentrated in the last ~2 minutes of each market where the
  outcome is clearest.
* **Volume needs many cycles; $10 can't absorb losses.** Each round trip must be
  ~break-even, so entries require a *confirming, persistent* signal and exits
  lock in the moment a full round trip is non-negative.

Improvements over the shipped house bot (per its benchmark's "what to improve"):

1. **Persistent-signal entry** — the BTC reference gap must hold its sign across
   consecutive fresh observations (not choppy), rejecting coin-flip regimes.
2. **Book/reference agreement** — the reference-favoured side must also be the
   book favourite (mid ≥ 0.5), rejecting a classic adverse-fill trap.
3. **Adverse-fill guards** — require immediate exit depth; cap modelled
   immediate round-trip loss; reject stale/provisional/future-stamped
   references and books.
4. **Drawdown-aware risk** — below a cash floor the bot demands a stronger edge,
   a cheaper (higher-price, lower-fee) favourite and smaller size, so a bad
   streak cannot compound the $10 stack to zero.
5. **Guaranteed terminal-flat** — new entries stop before a final guard window
   and open positions are force-closed with margin to spare (> the 15 s rule).

All thresholds live in the `Config` dataclass in `bot.py` and are frozen for a
run. See `bot.py`'s module docstring for the full rationale.

## Validation — read this honestly

The starter kit ships **no recorded market data or replay paths**, so this repo
**cannot reproduce Builderr's 30-path house-bot validation** and its numbers are
**not fresh-forward evidence**.

* `simulator.py` is a *synthetic* market (a near-efficient 5-minute BTC random
  walk with an optional reference-lead edge). It confirms the bot **obeys the
  contract and is robust**: across 30 synthetic paths the bot is **terminal-flat
  30/30** (the house bot was 18/30) and never breaches the $5 cap or crashes.
  Its **absolute volume numbers do not transfer** to the real evaluator, because
  a synthetic martingale has no real alpha to capture — as does, largely, a real
  5-minute BTC up/down market (Builderr's own house bot reached $1,000 in
  **0/30** paths).
* `replay.py` is the tool for **real tuning**: feed it a JSONL of actual recorded
  observation frames and it reconstructs volume / cash / drawdown / flatness with
  the documented fee model, and `--sweep` grid-searches the key parameters.
  **Recommended before submission:** capture real frames (local paper-API run or
  Builderr replay) and run `python replay.py --frames real.jsonl --sweep` to
  freeze the winning `Config`.

## Rules compliance

Builds only the algorithm. No real trades, deposits, or trading account. Never
edits evaluator-owned cash/fills/fees/volume/position accounting. Returns exactly
one of the three permitted decisions. No fabricated outputs.
