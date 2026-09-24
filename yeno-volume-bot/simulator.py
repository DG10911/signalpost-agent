#!/usr/bin/env python3
"""Synthetic, fee-accurate paper-market simulator for the YeNo volume bot.

IMPORTANT: this is a *synthetic development harness*, not the official YeNo
evaluator and not fresh-forward evidence. The starter kit ships no recorded
market data or replay paths, so we cannot reproduce Builderr's 30-path
validation. This engine generates plausible 5-minute BTC up/down markets with
L2 books and applies the documented execution + fee model so we can:

  * confirm the bot obeys the contract (>=$1k target logic, $5 cap, one
    position, terminal-flat) under many random paths, and
  * tune parameters against adverse-fill and choppy regimes.

Execution model implemented (from yeno-evaluator-contract.md):
  * dynamic taker fee 0.07*shares*price*(1-price) + 1% overlay per side
  * 250 ms latency: a decision fills against a *later* (perturbed) book
  * L2 depth with partial fills; a SELL can fill against bids alone
  * books older than 2 s rejected (we always feed fresh books)
  * no new entries inside the final 15 s
  * settlements add zero eligible volume (only closed SELL cycles count)
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
from dataclasses import dataclass, field
from typing import List, Optional

import bot as botmod


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class PathResult:
    eligible_volume_usd: float
    final_cash_usd: float
    terminal_flat: bool
    completed_cycles: int
    max_drawdown_usd: float
    reached_target: bool


@dataclass
class SimConfig:
    market_seconds: float = 300.0        # 5-minute markets
    decision_interval: float = 2.0       # observation cadence
    window_hours: float = 24.0
    starting_cash: float = 10.0
    max_buy_cash: float = 5.0
    target_volume: float = 1000.0
    min_order_size: float = 5.0
    annual_vol: float = 0.6              # BTC vol used for fair-prob model
    btc_start: float = 80000.0
    base_spread: float = 0.02            # half-spread around fair price
    depth: float = 200.0                 # shares per level
    latency_move_sd: float = 0.004       # adverse micro-move over 250 ms
    reference_lead_seconds: float = 4.0  # book lags the reference feed by this
                                         # (0.0 = efficient market, no edge)


def _fair_prob(btc: float, target: float, seconds_left: float, sc: SimConfig) -> float:
    """P(BTC_close > target) under a lognormal random walk."""
    if seconds_left <= 0:
        return 1.0 if btc > target else 0.0
    years = seconds_left / (365.0 * 24.0 * 3600.0)
    sigma = sc.annual_vol * math.sqrt(max(years, 1e-12))
    if sigma <= 1e-12:
        return 1.0 if btc > target else 0.0
    d = (math.log(btc / target) - 0.5 * sigma * sigma) / sigma
    return _norm_cdf(d)


def _book_for(prob: float, sc: SimConfig, rng: random.Random) -> dict:
    """Two-sided L2 book for YES/NO given the YES fair probability."""
    prob = min(max(prob, 0.01), 0.99)
    spread = sc.base_spread * (1.0 + rng.random())
    def side(mid: float) -> dict:
        ask = min(0.99, mid + spread)
        bid = max(0.01, mid - spread)
        return {
            "asks": [[round(ask, 3), sc.depth], [round(min(0.99, ask + 0.01), 3), sc.depth]],
            "bids": [[round(bid, 3), sc.depth], [round(max(0.01, bid - 0.01), 3), sc.depth]],
            "minOrderSize": sc.min_order_size,
        }
    return {"YES": side(prob), "NO": side(1.0 - prob)}


def _perturb(book: dict, sc: SimConfig, rng: random.Random) -> dict:
    """Apply a small adverse micro-move to model 250 ms execution latency."""
    shift = rng.gauss(0.0, sc.latency_move_sd)
    out = {}
    for name, s in book.items():
        def mv(levels, sign):
            res = []
            for p, q in levels:
                np_ = min(0.99, max(0.01, p + sign * shift))
                res.append([round(np_, 3), q])
            return res
        out[name] = {
            "asks": mv(s["asks"], +1.0),   # asks drift up (adverse to BUY)
            "bids": mv(s["bids"], +1.0),   # bids drift up too (same regime)
            "minOrderSize": s["minOrderSize"],
        }
    return out


def _fill_buy(asks, shares):
    return botmod.buy_cost(asks, shares)


def _fill_sell(bids, shares):
    return botmod.sell_proceeds(bids, shares)


def simulate_path(bot: "botmod.Bot", seed: int, sc: SimConfig) -> PathResult:
    rng = random.Random(seed)
    cash = sc.starting_cash
    peak_cash = cash
    max_dd = 0.0
    eligible_volume = 0.0
    cycles = 0
    position: Optional[dict] = None

    now = 0.0
    end_time = sc.window_hours * 3600.0
    market_index = 0

    while now < end_time:
        # New 5-minute market.
        market_index += 1
        market_id = "sim-%d" % market_index
        target = sc.btc_start if market_index == 1 else target  # opening target
        btc = target
        vol_per_step = sc.annual_vol * math.sqrt(sc.decision_interval / (365 * 24 * 3600)) * btc
        market_end = min(now + sc.market_seconds, end_time)
        btc_history: List[tuple] = [(now, btc)]

        while now < market_end:
            seconds_left = market_end - now
            btc += rng.gauss(0.0, vol_per_step)
            btc_history.append((now, btc))
            # The order book reprices with a lag: it reflects the BTC level
            # ~reference_lead_seconds ago, while the reference feed is current.
            # A bot that trusts a fresh, persistent reference gap can enter the
            # favourite before the book fully catches up -- the intended edge.
            lag_target_time = now - sc.reference_lead_seconds
            btc_lagged = btc_history[0][1]
            for t, v in btc_history:
                if t <= lag_target_time:
                    btc_lagged = v
                else:
                    break
            prob = _fair_prob(btc_lagged, target, seconds_left, sc)
            book = _book_for(prob, sc, rng)

            obs = {
                "schemaVersion": 1,
                "timestamp": now,
                "market": {"id": market_id, "secondsToClose": seconds_left},
                "account": {
                    "cashUsd": cash,
                    "eligibleVolumeUsd": eligible_volume,
                    "completedCycles": cycles,
                    "position": position,
                },
                "rules": {
                    "maximumBuyCashUsd": sc.max_buy_cash,
                    "targetVolumeUsd": sc.target_volume,
                    "evaluationWindowHours": sc.window_hours,
                    "onePositionAtATime": True,
                    "buyFeesIncludedInMaximum": True,
                },
                "reference": {
                    "btcMidUsd": btc,
                    "openingTargetUsd": target,
                    "observedAt": now,
                    "targetObservedAt": now - 1.0,
                    "targetProvisional": False,
                },
                "books": book,
            }

            decision = bot.decide(obs)
            action = decision.get("action", "HOLD")
            fill_book = _perturb(book, sc, rng)  # 250 ms later

            if action == "BUY" and position is None:
                if seconds_left <= 15.0:
                    pass  # evaluator blocks new entries in final 15 s
                else:
                    side = decision.get("outcome")
                    max_cash = float(decision.get("maxCashUsd", 0.0))
                    asks = fill_book.get(side, {}).get("asks", [])
                    # Find largest fillable size within cash cap and min order.
                    shares = sc.min_order_size
                    quote = _fill_buy(asks, shares)
                    if quote is not None:
                        gross, fees = quote
                        if gross + fees <= min(max_cash, cash) + 1e-9:
                            cash -= gross + fees
                            eligible_volume += gross
                            position = {
                                "outcome": side, "shares": shares,
                                "buy_gross_usd": gross, "buy_fees_usd": fees,
                                "sell_gross_usd": 0.0, "sell_fees_usd": 0.0,
                            }
            elif action == "SELL" and position is not None:
                side = position["outcome"]
                bids = fill_book.get(side, {}).get("bids", [])
                quote = _fill_sell(bids, position["shares"])
                if quote is not None:
                    gross, fees = quote
                    cash += gross - fees
                    eligible_volume += gross
                    cycles += 1
                    position = None

            peak_cash = max(peak_cash, cash)
            max_dd = max(max_dd, peak_cash - cash)
            now += sc.decision_interval

        # Market settlement: any still-open position settles (zero volume).
        if position is not None:
            settled = 1.0 if btc > target else 0.0
            cash += settled * position["shares"]
            position = None
        # Next market's opening target is the current BTC level.
        target = btc

    terminal_flat = position is None
    return PathResult(
        eligible_volume_usd=eligible_volume,
        final_cash_usd=cash,
        terminal_flat=terminal_flat,
        completed_cycles=cycles,
        max_drawdown_usd=max_dd,
        reached_target=eligible_volume >= sc.target_volume,
    )


def run(paths: int, seed0: int, sc: SimConfig, config: "botmod.Config") -> None:
    results: List[PathResult] = []
    for i in range(paths):
        bot = botmod.Bot(config=config)
        results.append(simulate_path(bot, seed0 + i, sc))

    vols = [r.eligible_volume_usd for r in results]
    flat = [r for r in results if r.terminal_flat]
    reached = [r for r in results if r.reached_target]
    print("Synthetic simulation (NOT the official evaluator, NOT fresh-forward)")
    print("paths: %d | decision cadence: %.1fs | window: %.0fh"
          % (paths, sc.decision_interval, sc.window_hours))
    print("median volume:        $%.2f" % statistics.median(vols))
    print("mean volume:          $%.2f" % statistics.mean(vols))
    print("best volume:          $%.2f" % max(vols))
    print("terminal-flat paths:  %d / %d" % (len(flat), paths))
    print("paths >= $1000:       %d / %d" % (len(reached), paths))
    print("paths >= $750:        %d / %d" % (sum(v >= 750 for v in vols), paths))
    print("median final cash:    $%.2f" % statistics.median(r.final_cash_usd for r in results))
    print("median max drawdown:  $%.2f" % statistics.median(r.max_drawdown_usd for r in results))
    print("median cycles:        %d" % statistics.median(r.completed_cycles for r in results))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paths", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--cadence", type=float, default=2.0)
    ap.add_argument("--vol", type=float, default=0.6, help="annualised BTC vol")
    ap.add_argument("--latency-sd", type=float, default=0.004,
                    help="adverse micro-move stdev over 250ms (stress with e.g. 0.02)")
    ap.add_argument("--lead", type=float, default=4.0,
                    help="seconds the book lags the reference feed (0 = no edge)")
    args = ap.parse_args()
    sc = SimConfig(decision_interval=args.cadence, annual_vol=args.vol,
                   latency_move_sd=args.latency_sd, reference_lead_seconds=args.lead)
    run(args.paths, args.seed, sc, botmod.Config())


if __name__ == "__main__":
    main()
