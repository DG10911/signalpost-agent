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
python -m unittest discover -s tests  # 34 tests, all pass
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

## v3 strategy + measured results (reference-edge gate)

The decisive upgrade over the house baseline: price each side from the
**reference feed** (BTC mid vs the YeNo opening target, lognormal fair value)
rather than a raw USD gap, estimate realised volatility **online** from the feed,
and require the edge to cover the **live round-trip cost** (both fee layers plus
the quoted spread) before entering. The bot then holds until the book reprices
into profit.

Fee-accurate closed-loop simulator, held-out fresh seeds, 72 paths, both a fine
(2 s) and coarse (5 s) decision cadence:

| | House Bot v1 | this bot |
|---|---|---|
| Qualifying paths (of 72) | 0 | **48 (100% of qualifiable)** |
| Median volume | $138 | **$1,411–1,870** |
| Median cash at crossing | — | **$72–93** |
| Terminal-flat / invalid | 100% / 0 | **100% / 0** |

It qualifies in every regime with exploitable edge (normal / high-vol /
thin-depth / strong-edge) and, when there is **no** edge or a wide spread, it
holds and preserves the full $10 instead of churning.

Honesty: the kit ships no real recorded frames, so these are synthetic-harness
numbers (validated against the published evaluator contract), not a guaranteed
official crossing.

Tools: `capture_frames.py`, `tune.py`, `tune_sim.py`, `tune_edge.py`,
`scenarios.py --tuned`, `tuned_config.py` (served by `server.py`).

## v4 — cash-first (organizer feedback, 24 Oct 2026)

The real 12 h sample of `8a66c5d` made $459.38 of volume in 59 cycles but ended
at $1.15 from $10 ($13.00 fees). The organizer's guidance was to keep cash, not
lower the entry bar when volume is behind, and check net value after fees and
the likely exit. Changes to the served config (`tuned_config.py`):

1. **No behind-pace loosening.** `max_aggression=1.0`: being behind on volume
   never widens the entry gates (tightening when ahead is kept).
2. **Net-value margin from the real sample.** Entries already required modelled
   edge ≥ spread + both fee layers. In the sample that realised only
   ~+$0.013/share before fees against ~$0.042/share of fees, so ≥ ~$0.029/share
   of modelled edge is lost to latency and adverse fills. `edge_safety_margin`
   is now $0.045/share (was $0.015), so an entry has to clear spread, fees, the
   measured shortfall and a small profit.

The probability model itself was checked against 14 days of Binance BTCUSDT
1-second prices (4,032 five-minute windows, 84,588 samples, last 4 days held
out). It is calibrated: e.g. model 0.90–0.95 → 0.926 actual, 0.95–1.00 → 0.981,
and a vol multiplier of 1.0 has the best held-out log loss. The losses come from
execution, not from mispriced probabilities, which is why the fix is the margin.

Expect fewer trades and lower volume; when no entry clears the margin the bot
holds and keeps its cash. Synthetic battery: terminal-flat in all 10 regimes,
0 invalid actions, 27 tests pass. No real order-book data was available, so the
margin is estimated from one aggregate sample, not tuned.

## v5 — terminal-flat safety + low-fee zone (25 Oct 2026)

From a review of the active competitor bots and the current evaluator contract:

1. **Never hold at market change.** Organisers have told builders that a
   position still open at market change ends the whole replay. v5 SELLs at
   once if the position's `market_id` differs from the current market, sells
   into whatever bids exist (partial fills are allowed) once the book cannot
   absorb the whole position, and starts the forced exit at **45 s** (was 25 s).
   Entries stop 5 s before that window.
2. **Exit depth at entry.** The favourite's best bid alone must hold at least
   the shares we buy (Builderr hint cited by another entrant, 24 Oct).
3. **Calibrated tail.** On 14 days of Binance 1 s BTC (55–150 s before close)
   the fair-value model at vol ×1.0 was overconfident by 1–3 points whenever it
   said ≥ 0.97. Vol ×1.2 brings that within ~0.6 points on both the first 10
   days and the held-out last 4 (`vol_multiplier=1.2`).
4. **Margin scales with price uncertainty.** The $0.045/share margin measured
   on the real sample (average entry ≈ 0.74) now scales with p·(1−p), so it is
   ~$0.011 at 0.95, where the fee fraction is lowest (1.35% per side vs 2.8%),
   and ~$0.058 at parity. The entry ceiling rises to 0.969, also in defensive
   mode, so low cash no longer forces mid-price, high-fee entries.

Unchanged: no loosening when behind pace, profit-target exit, thesis-flip exit,
abort after a loss, crossing lock. 34 tests pass; synthetic battery
terminal-flat in all 10 regimes, 0 invalid actions; decisions take < 3 ms.
The synthetic harness cannot predict real volume; expect fewer, cheaper trades.
