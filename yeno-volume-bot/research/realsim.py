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
    fees = 0.0
    cycles = 0
    peak = 10.0
    mdd = 0.0
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
                fees += fee
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
                fees += fee
                position["shares"] -= filled
                position["sell_gross_usd"] += gross
                position["sell_fees_usd"] += fee
                if position["shares"] <= 1e-9:
                    volume += position["buy_gross_usd"] + position["sell_gross_usd"]
                    cycles += 1
                    peak = max(peak, cash)
                    mdd = max(mdd, peak - cash)
                    position = None
                    if volume >= 1000.0 - 1e-9:
                        return {"cash": cash, "volume": volume, "fees": fees,
                                "cycles": cycles, "mdd": mdd,
                                "hours": (m["start"] + (300 - s) - markets[start_idx]["start"]) / 3600.0}
        if position is not None:
            # Market changed with inventory: the replay would end. Resolve at 0/1.
            won = (position["outcome"] == "YES") == (m["winner"] == "Up")
            cash += position["shares"] * (1.0 if won else 0.0)
            position = None
    return {"cash": cash, "volume": volume, "fees": fees, "cycles": cycles,
            "mdd": mdd, "hours": None}



# --------------------------------------------------------------------------- #
# Faithful mode (official contract, as published).
# --------------------------------------------------------------------------- #

def _snapshot(S, t):
    """Latest row of one outcome at or before t: (row index, bids, asks)."""
    j = S.idx(t)
    if j < 0:
        return -1, None, None
    bp, bs, ap, asz = S.bp[j], S.bs[j], S.ap[j], S.asz[j]
    bids = [(float(p), float(q)) for p, q in zip(bp, bs) if p == p and q == q and q > 0]
    asks = [(float(p), float(q)) for p, q in zip(ap, asz) if p == p and q == q and q > 0]
    return j, bids, asks


def _later_update(S, t_decision, j_decision, delay=0.25):
    """The first L2 update of this outcome stamped >= decision + delay and strictly
    after the snapshot the decision was made on (a pre-decision quote is never
    reused). Returns the row index or -1."""
    import numpy as np
    k = int(np.searchsorted(S.ts, t_decision + delay - 1e-9, side="left"))
    k = max(k, j_decision + 1)
    return k if k < len(S.ts) else -1


def _missing_target(slug, frac):
    if frac <= 0.0:
        return False
    import zlib
    return (zlib.crc32(slug.encode()) % 10000) / 10000.0 < frac


def run_window_faithful(markets, closes, btc_start, start_idx=0, n_markets=288, config=None,
                        call_from=299, step=1.0, delay=0.25, max_book_age=2.0,
                        entry_block=15.0, missing_target_frac=0.0, start_cash=10.0,
                        target_volume=1000.0, trace=None):
    """Drive the bot under the published contract.

    * BTC must come from a causal series (the *_lag1s.bin files: the close of the
      1 s kline that ENDED at t, i.e. the last trade before t). The opening target
      is the mean of that series over the 60 s before the market opens.
    * The bot is called every ``step`` seconds through the WHOLE market, down to
      1 s before the close, so SELLs are possible after the 15 s entry block.
    * An action waits ``delay`` (250 ms) and then fills against the first later
      L2 update of its own outcome; if none arrives before the close it fails.
      While an action is pending the bot is not called.
    * Actions on a book older than ``max_book_age`` (2 s) are rejected. BUYs at
      or inside ``entry_block`` (15 s) are rejected; SELLs are always allowed.
    * BUY: walk the displayed asks up to maxCashUsd (fee-inclusive, <= $5 and
      <= cash); fewer than 5 shares filled -> rejected. SELL: walk the displayed
      bids; partial fills accumulate into one cycle; a residual below 1e-6 share
      is dust and closes the cycle. Fees: 0.07*q*p*(1-p) + 1% overlay, each
      rounded to 5 decimals per side.
    * Volume is credited when a cycle fully exits. The run stops at the first
      fill that leaves the account flat with volume >= target.
    * A position still open at the close settles at 1/0 with no volume credit.
    """
    import numpy as np
    bot = botmod.Bot(config=config or TUNED)
    cash, volume, fees, cycles = start_cash, 0.0, 0.0, 0
    peak, mdd, low = start_cash, 0.0, start_cash
    stats = {"buys": 0, "sells": 0, "rejected_age": 0, "rejected_min": 0, "failed_fill": 0,
             "stuck": 0, "stuck_loss": 0.0, "partial_sells": 0, "calls": 0}
    position = None
    t0 = markets[start_idx]["start"] if start_idx < len(markets) else 0.0
    for m in markets[start_idx:start_idx + n_markets]:
        end = m["end"]
        tgt_missing = _missing_target(m["slug"], missing_target_frac)
        busy_until = -1.0
        s = float(call_from)
        while s >= 1.0 - 1e-9:
            t = end - s
            if t < busy_until - 1e-9:
                s -= step
                continue
            i = int(t) - btc_start
            if i < 1 or i >= len(closes):
                s -= step
                continue
            ju, ub, ua = _snapshot(m["Up"], t)
            jd, db, da = _snapshot(m["Down"], t)
            if ju < 0 or jd < 0:
                s -= step
                continue
            ref = {"btcMidUsd": float(closes[i]), "observedAt": float(t),
                   "targetObservedAt": float(m["start"]), "targetProvisional": False}
            if not tgt_missing:
                ref["openingTargetUsd"] = float(m["target"])
            obs = {"timestamp": float(t),
                   "market": {"id": m["slug"], "secondsToClose": float(s)},
                   "account": {"cashUsd": cash, "eligibleVolumeUsd": volume,
                               "completedCycles": cycles, "position": position},
                   "rules": {"maximumBuyCashUsd": 5.0, "targetVolumeUsd": target_volume,
                             "evaluationWindowHours": 24.0},
                   "reference": ref,
                   "books": {"YES": {"bids": [list(x) for x in ub], "asks": [list(x) for x in ua], "minOrderSize": 5.0},
                             "NO": {"bids": [list(x) for x in db], "asks": [list(x) for x in da], "minOrderSize": 5.0}}}
            stats["calls"] += 1
            d = bot.decide(obs)
            act = d.get("action")
            if act == "BUY" and position is None and d.get("outcome") in ("YES", "NO"):
                key = "Up" if d["outcome"] == "YES" else "Down"
                S = m[key]
                jdec = ju if key == "Up" else jd
                if s <= entry_block + 1e-9:
                    pass
                elif t - S.ts[jdec] > max_book_age + 1e-9:
                    stats["rejected_age"] += 1
                else:
                    k = _later_update(S, t, jdec, delay)
                    if k < 0 or S.ts[k] >= end:
                        stats["failed_fill"] += 1
                        busy_until = end
                    else:
                        busy_until = S.ts[k]
                        _, _, asks = _snapshot(S, S.ts[k])
                        try:
                            cap = min(float(d.get("maxCashUsd", 0.0)), 5.0, cash)
                        except (TypeError, ValueError):
                            cap = 0.0
                        sh, gross, fee = lab.walk_buy(asks or [], cap)
                        if sh < 5.0 - 1e-9 or gross + fee > min(5.0, cash) + 1e-9:
                            stats["rejected_min"] += 1
                        else:
                            cash -= gross + fee
                            fees += fee
                            stats["buys"] += 1
                            position = {"market_id": m["slug"], "outcome": d["outcome"], "shares": sh,
                                        "buy_gross_usd": gross, "buy_fees_usd": fee,
                                        "sell_gross_usd": 0.0, "sell_fees_usd": 0.0}
                            low = min(low, cash)
                            if trace is not None:
                                trace.append(("BUY", m["slug"], s, d["outcome"], sh, gross, fee, cash))
            elif act == "SELL" and position is not None:
                key = "Up" if position["outcome"] == "YES" else "Down"
                S = m[key]
                jdec = ju if key == "Up" else jd
                if t - S.ts[jdec] > max_book_age + 1e-9:
                    stats["rejected_age"] += 1
                else:
                    k = _later_update(S, t, jdec, delay)
                    if k < 0 or S.ts[k] >= end:
                        stats["failed_fill"] += 1
                        busy_until = end
                    else:
                        busy_until = S.ts[k]
                        _, bids, _ = _snapshot(S, S.ts[k])
                        filled, gross, fee = lab.walk_sell(bids or [], position["shares"])
                        if filled > 1e-9:
                            cash += gross - fee
                            fees += fee
                            stats["sells"] += 1
                            position["shares"] -= filled
                            position["sell_gross_usd"] += gross
                            position["sell_fees_usd"] += fee
                            if position["shares"] > 1e-6:
                                stats["partial_sells"] += 1
                            else:
                                volume += position["buy_gross_usd"] + position["sell_gross_usd"]
                                cycles += 1
                                if trace is not None:
                                    trace.append(("SELL", m["slug"], s, position["outcome"], filled, gross, fee, cash))
                                position = None
                                peak = max(peak, cash)
                                mdd = max(mdd, peak - cash)
                                low = min(low, cash)
                                if volume >= target_volume - 1e-9:
                                    return {"cash": cash, "volume": volume, "fees": fees, "cycles": cycles,
                                            "mdd": mdd, "low": low, "hours": (S.ts[k] - t0) / 3600.0, **stats}
            s -= step
        if position is not None:
            won = (position["outcome"] == "YES") == (m["winner"] == "Up")
            payout = position["shares"] * (1.0 if won else 0.0)
            cash += payout
            stats["stuck"] += 1
            stats["stuck_loss"] += position["buy_gross_usd"] + position["buy_fees_usd"] - payout \
                - (position["sell_gross_usd"] - position["sell_fees_usd"])
            if trace is not None:
                trace.append(("SETTLE", m["slug"], 0, position["outcome"], position["shares"], payout, 0.0, cash))
            position = None
            peak = max(peak, cash)
            mdd = max(mdd, peak - cash)
            low = min(low, cash)
    return {"cash": cash, "volume": volume, "fees": fees, "cycles": cycles, "mdd": mdd,
            "low": low, "hours": None, **stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", required=True)
    ap.add_argument("--btc", required=True)
    ap.add_argument("--windows", default="0,288,576")
    ap.add_argument("--offset-step", type=int, default=24)
    ap.add_argument("--mode", choices=("legacy", "faithful"), default="legacy",
                    help="legacy = original loop (120..16 s, same-snapshot fills); "
                         "faithful = published contract (pass a *_lag1s.bin BTC file)")
    args = ap.parse_args()
    runner = run_window if args.mode == "legacy" else (
        lambda ms, cl, bs, off, n: run_window_faithful(ms, cl, bs, off, n))
    markets, btc_start, closes = lab.load_all(args.books, args.btc)
    print(f"loaded {len(markets)} markets")
    fixed = [int(x) for x in args.windows.split(",") if x.strip()]
    for off in fixed:
        if off + 288 <= len(markets):
            r = runner(markets, closes, btc_start, off, 288)
            hours = "" if r["hours"] is None else f" at {r['hours']:.1f} h"
            print(f"window {off:4d}: cash ${r['cash']:8.2f} volume ${r['volume']:7.1f} "
                  f"fees ${r['fees']:6.2f} cycles {r['cycles']:3d} mdd ${r['mdd']:5.1f} "
                  f"cross={'yes' if r['volume'] >= 1000 else 'no'}{hours}")
    if args.offset_step > 0:
        q = 0
        total = 0
        for off in range(0, len(markets) - 288 + 1, args.offset_step):
            total += 1
            r = runner(markets, closes, btc_start, off, 288)
            if r["volume"] >= 1000:
                q += 1
        print(f"sliding windows: {q}/{total} qualified")


if __name__ == "__main__":
    main()
