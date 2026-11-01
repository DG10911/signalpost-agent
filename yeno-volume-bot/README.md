# YeNo × Builderr Volume Bot — `twap-edge-v8`

A `/decide` trading agent for the [YeNo × Builderr Volume Bot Challenge](https://builderr.ai).
Start with $10 paper cash; in 24 h produce ≥ $1,000 of eligible BUY+SELL
volume while controlling fees, spreads and losses, and finish terminal-flat.

Only `bot.py`'s strategy is ours. Cash, fills, fees, volume and position
accounting are evaluator-owned; the bot only returns `HOLD` / `BUY` / `SELL` and
*estimates* fees locally to size orders inside the $5 fee-inclusive cap.

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Strategy + fee/TWAP models. `Bot` class and a module-level `decide()`. |
| `tuned_config.py` | **The frozen strategy that `server.py` serves.** |
| `server.py` | `POST /decide` HTTP server (`python server.py`). |
| `research/realsim.py` | Drives the real bot over recorded Polymarket BTC-5m L2 books under the published evaluator contract. |
| `tests/test_bot.py` | `unittest` suite (48 tests), pure stdlib. |
| `simulator.py` | Synthetic fee-accurate harness for contract/robustness checks. |
| `scenarios.py` | 10 adversarial synthetic regimes; asserts terminal-flat and zero invalid actions. |

Python 3.9+ (developed/tested on 3.9.6; 3.12+ per the starter kit). **No third-party
dependencies, no model/API calls, no network. Expected external cost: $0.**

## Run it

```bash
python server.py                      # serves POST http://127.0.0.1:8080/decide
python -m unittest discover -s tests  # 48 tests, all pass
python scenarios.py --paths 40        # adversarial regime battery
```

Official contract smoke test (the kit's `yeno-smoke-test.py`):

```bash
python server.py &
python yeno-smoke-test.py --url http://127.0.0.1:8080/decide \
  --observation yeno-sample-observation.json
# -> {"contractSmokePassed": true, ...}
```

## The served strategy

These are the numbers in `tuned_config.py`. They were chosen on real order-book
data, not on the synthetic simulator.

**What it believes.** YeNo's BTC five-minute markets settle on the **average of
BTC over the final 60 seconds** against the **average of the 60 seconds before
the market opened**. On 862 real resolved Polymarket BTC-5m markets
(2026-10-03 → 2026-10-06) that definition matches **97.8%** of the recorded
winners; spot-at-close matches 87%. The bot therefore prices
`P(TWAP > openingTarget)` with a lognormal model, estimating realised
volatility online and handling the part of the final minute that has already
been observed.

**Entry.** Buy the side whose ask is at least **5 cents below** that fair
probability, only when:

* the best ask is in **0.30–0.85** (below 0.50 the book has not caught up with
  BTC yet; above ~0.85 the fees eat the remaining upside);
* the spread is **≤ 2 cents** and the book is not crossed;
* at most **4 completed cycles** per market;
* at least 16 seconds remain (the evaluator blocks new entries inside 15 s).

**Exit.** Sell when any of these happens:

* the best bid is **≥ 0.95** and the cycle is not a loss (take profit);
* the model's probability for the held side falls **below 0.45** (thesis flip);
* the position has been held for **30 seconds** (time stop);
* a position belongs to a previous market (never carry inventory);
* the book can no longer absorb the whole position (sell into whatever bids
  exist, partial fills allowed);
* from **10 seconds** before close (forced flat; the evaluator allows a SELL
  against bids after asks disappear).

**Sizing.** Risk **30% of current paper cash** per BUY, capped by the $5
fee-inclusive ceiling and the exchange's 5-share minimum. This is the main
capital-protection rule: a losing streak cannot wipe the $10 bankroll before the
target crossing. No entry below $2.60 cash.

**Lock.** Once a completed cycle takes eligible volume to $1,000 and the
position is flat, the bot HOLDs forever: the score is cash at that first clean
crossing.

**Never loosen when behind.** There is no pace-based widening of the entry bar.
The old v4 `max_aggression=1.5` is gone (`max_aggression=1.0`).

## Validation — real order books

The public starter kit ships no market data, so the synthetic simulator can only
prove contract safety, not volume. To test the strategy honestly we used the
public **PMData / PolyOrderBooks** research archive:

* **862 resolved Polymarket BTC five-minute markets**, 2026-10-03 13:55 to
  2026-10-06 13:40 UTC, at **250 ms full L2 snapshots** (Polymarket's own book
  feed, 1.7 M snapshots), plus Binance 1-second BTC.
* The harness applies the published contract: 250 ms execution latency, fills
  against a **later** book for the same outcome, ladder walking, both fee
  layers, the $5 fee-inclusive cap, the 5-share minimum, partial exits, the
  final-15-s entry block, and a 2 s book-age limit.

**Result — driving the actual `bot.py` (not a model of it):**

| Window | Cash at $1,000 crossing | Crossing time |
|--------|------------------------|---------------|
| 24 h day 1 (markets 0–287) | **$48.68** | 21.0 h |
| 24 h day 2 (markets 288–575) | **$72.46** | 10.9 h |
| 24 h day 3 (markets 574–861) | **$59.44** | 9.6 h |
| 24 sliding 24 h windows (step 24) | **19 / 24 qualified** | median $53.20 |

The 3 full days all crossed $1,000 with positive cash; the 5 failing sliding
windows are regimes that start mid-drawdown. This is a research estimate on one
three-day period, **not an official score or a promise**. It is, however,
measured on real order books rather than a synthetic random walk.

`research/realsim.py` reproduces it:

```bash
python research/realsim.py \
  --books /path/books_flat.parquet --btc /path/btc_1s.bin \
  --windows 0,288,574 --offset-step 24
```

The data files are large and are not committed. The harness needs
`uv run --with pyarrow,numpy`; the bot itself needs nothing.

### Bug fixed in v8

The v6 TWAP model was missing the `1/60` normalisation on the variance before
the final minute, so its fair probability was far too close to 0.5 and it
rejected most genuine edges. With the correction the model matches the
independent 14-day BTC calibration (within ~0.6 points above 0.94). The real-L2
qualification rate rose from 11/24 to 19/24 sliding windows.

### Honest caveats

* One three-day period is not the unseen official 24-hour window.
* The Polymarket book is the closest public analogue to YeNo's book, not the
  same venue; liquidity and latency differ.
* The official run starts with a fresh price history, so the first ~10 minutes
  use the configured prior volatility instead of a fully warmed estimate.
* No bot has yet reached $1,000 in the organisers' runs. This strategy is
  designed to keep cash and take a real shot, not to guarantee a prize.

## Rules compliance

Builds only the algorithm. No real trades, deposits, or trading account. Never
edits evaluator-owned cash/fills/fees/volume/position accounting. Returns
exactly one of the three permitted decisions. No fabricated outputs.

## History

* **v1–v3** — fee-aware favourite trading, synthetic validation.
* **v4** — cash-first margin measured from the real 12 h sample.
* **v5** — never hold across a market change; partial/thin exits; 45 s window.
* **v6** — price the 60 s settlement TWAP (outcome differs from spot in 11.5%
  of real windows).
* **v8** — frozen `twap-edge-v8`: real-L2-calibrated 5-cent edge gate, 0.30–0.85
  band, 2-cent spread limit, 0.95 take-profit, 0.45 thesis flip, 30 s time
  stop, 30%-of-cash sizing, fixed TWAP variance.
