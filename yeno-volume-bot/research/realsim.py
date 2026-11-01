#!/usr/bin/env python3
"""Drive the real bot over recorded Polymarket BTC-5m L2 books.

This is the validation harness behind the frozen strategy. It implements the
published evaluator contract as closely as the public description allows:

  * the bot sees the latest recorded L2 snapshot at each second (and is not
    allowed to enter on a snapshot older than 2 s or a crossed book);
  * every action fills 250 ms later, against a later snapshot of the same
    outcome, by walking the ladder;
  * both fee layers, the $5 fee-inclusive cap, the 5-share minimum, partial
    exits and the final-15-s entry block are applied;
  * one position at a time; volume is credited only when a full cycle closes;
  * the run stops at the first clean $1,000 crossing.

Usage:
  python research/realsim.py \
      --books /path/books_flat.parquet \
      --btc /path/btc_1s.bin \
      [--windows 0,288,576] [--offset-step 24]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lab  # noqa: E402
import bot as botmod  # noqa: E402
from tuned_config import TUNED  # noqa: E402


def _obs(m, t, s, cash, volume, position, books):
    return {
        "timestamp": t,
        "market": {"id": m["slug"], "secondsToClose": s},
        "account": {"cashUsd": cash, "eligibleVolumeUsd": volume,
                    "completedCycles": 0, "position": position},
        "rules": {"maximumBuyCashUsd": 5.0, "targetVolumeUsd": 1000.0,
                  "evaluationWindowHours": 24.0, "onePositionAtATime": True,
                  "buyFeesIncludedInMaximum": True},
        "reference": {"btcMidUsd": m["_price"], "openingTargetUsd": m["target"],
                      "observedAt": t, "targetObservedAt": m["start"] - 1.0,
                      "targetProvisional": False},
        "books": books,
    }


def _book(books, t):
    out = {}
    for name, key in (("YES", "Up"), ("NO", "Down")):
        lv = books[key].lv(t)
        if lv is None:
            return None, None
        bids, asks = lv
        out[name] = {"bids": bids, "asks": asks, "minOrderSize": 5.0}
    return out, {k: books[k] for k in ("Up", "Down")}


def run_window(markets, closes, btc_start, start_idx=0, n_markets=288, verbose=False, warmup=600, config=None):
    bot = botmod.Bot(config=config or TUNED)
    # Prime the online volatility/seen-average history the way a bot that had
    # already been running would see it. The official run starts fresh, so this
    # is the optimistic bound for the first ten minutes.
    if warmup > 0 and markets:
        t0 = int(markets[start_idx]["start"])
        for k in range(max(btc_start, t0 - warmup), t0):
            i = k - btc_start
            if 0 <= i < len(closes):
                bot._price_hist.append((float(k), float(closes[i])))
    cash, volume = 10.0, 0.0
    position = None
    i_end = start_idx + n_markets
    for m in markets[start_idx:i_end]:
        for s in range(120, 15, -1):
            t = m["end"] - s
            i = int(t) - btc_start
            if i < 1 or i >= len(closes):
                continue
            m["_price"] = float(closes[i])
            ob, _ = _book(m, t)
            if ob is None:
                continue
            age = t - m["Up"].ts[m["Up"].idx(t)]
            d = bot.decide(_obs(m, t, s, cash, volume, position, ob))
            action = d.get("action")
            if action == "BUY" and position is None:
                if age > 2.0:
                    continue
                side = d.get("outcome")
                if side not in ("YES", "NO"):
                    continue
                key = "Up" if side == "YES" else "Down"
                S = m[key]
                j = S.idx(t + 0.25)
                if j < 0:
                    continue
                bids, asks = S.lv(S.ts[j])
                if not asks:
                    continue
                shares, gross, fee = lab.walk_buy(asks, float(d.get("maxCashUsd", 0.0)))
                if shares < 5.0 - 1e-9 or gross + fee > 5.0 + 1e-9:
                    continue
                cash -= gross + fee
                position = {"market_id": m["slug"], "outcome": side, "shares": shares,
                            "buy_gross_usd": gross, "buy_fees_usd": fee,
                            "sell_gross_usd": 0.0, "sell_fees_usd": 0.0}
            elif action == "SELL" and position is not None:
                key = "Up" if position["outcome"] == "YES" else "Down"
                S = m[key]
                j = S.idx(t + 0.25)
                if j < 0:
                    continue
                bids, asks = S.lv(S.ts[j])
                if not bids:
                    continue
                filled, gross, fee = lab.walk_sell(bids, position["shares"])
                if filled <= 1e-9:
                    continue
                cash += gross - fee
                position["shares"] -= filled
                position["sell_gross_usd"] += gross
                position["sell_fees_usd"] += fee
                if position["shares"] <= 1e-9:
                    volume += position["buy_gross_usd"] + position["sell_gross_usd"]
                    position = None
                    if volume >= 1000.0 - 1e-9:
                        return {"cash": cash, "volume": volume,
                                "hours": (m["start"] + (300 - s) - markets[start_idx]["start"]) / 3600.0}
        if position is not None:
            # Market changed with inventory: the replay would end. Resolve at 0/1.
            won = (position["outcome"] == "YES") == (m["winner"] == "Up")
            cash += position["shares"] * (1.0 if won else 0.0)
            position = None
    return {"cash": cash, "volume": volume, "hours": None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", required=True)
    ap.add_argument("--btc", required=True)
    ap.add_argument("--windows", default="0,288,576")
    ap.add_argument("--offset-step", type=int, default=24)
    args = ap.parse_args()
    markets, btc_start, closes = lab.load_all(args.books, args.btc)
    print(f"loaded {len(markets)} markets")
    fixed = [int(x) for x in args.windows.split(",") if x.strip()]
    for off in fixed:
        if off + 288 <= len(markets):
            r = run_window(markets, closes, btc_start, off, 288)
            hours = "" if r["hours"] is None else f" at {r['hours']:.1f} h"
            print(f"window {off:4d}: cash ${r['cash']:8.2f} volume ${r['volume']:7.1f} "
                  f"cross={'yes' if r['volume'] >= 1000 else 'no'}{hours}")
    if args.offset_step > 0:
        q = 0
        total = 0
        for off in range(0, len(markets) - 288 + 1, args.offset_step):
            total += 1
            r = run_window(markets, closes, btc_start, off, 288)
            if r["volume"] >= 1000:
                q += 1
        print(f"sliding windows: {q}/{total} qualified")


if __name__ == "__main__":
    main()
