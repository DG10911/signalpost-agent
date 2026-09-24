#!/usr/bin/env python3
"""Replay recorded /decide observation frames through the bot and score them.

This is the tool to use for *real* tuning: point it at a JSONL file of actual
recorded YeNo observation frames (one JSON observation per line, in
chronological order, each shaped like ``yeno-sample-observation.json``) and it
reconstructs cash, position, fees, eligible volume, drawdown and terminal-flat
status using the documented execution + fee model.

The starter kit does not ship recorded frames, so this file is inert until you
supply them (e.g. from a local capture of the paper API or Builderr's replay).
Once you have frames, ``replay.py`` gives fresh, data-grounded numbers that the
synthetic ``simulator.py`` cannot.

    python replay.py --frames recorded.jsonl
    python replay.py --frames recorded.jsonl --sweep   # grid-search key params

Execution assumptions mirror ``simulator.py`` and the evaluator contract: a
decision fills against the book *in the same frame* (the frames are the record
of what was executable), 5-decimal fee rounding, partial fills, one position at
a time, no new entries in the final 15 s, settlements add zero volume.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable, List, Optional

import bot as botmod


def _load(frames_path: Path) -> List[dict]:
    out = []
    with frames_path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def replay(frames: List[dict], config: "botmod.Config") -> dict:
    b = botmod.Bot(config=config)
    cash = None
    peak = None
    max_dd = 0.0
    volume = 0.0
    cycles = 0
    position: Optional[dict] = None

    for obs in frames:
        rules = obs.get("rules", {})
        seconds = float(obs["market"]["secondsToClose"])
        if cash is None:
            cash = float(obs["account"].get("cashUsd", 10.0))
            peak = cash
        # Feed the bot a view consistent with our reconstructed state.
        view = dict(obs)
        view["account"] = dict(obs["account"], cashUsd=cash, position=position,
                               eligibleVolumeUsd=volume, completedCycles=cycles)
        decision = b.decide(view)
        action = decision.get("action", "HOLD")
        books = obs.get("books", {})

        if action == "BUY" and position is None and seconds > 15.0:
            side = decision.get("outcome")
            shares = float(books.get(side, {}).get("minOrderSize", 5.0) or 5.0)
            quote = botmod.buy_cost(books.get(side, {}).get("asks", []), shares)
            if quote is not None:
                gross, fees = quote
                cap = min(float(decision.get("maxCashUsd", 0.0)), cash)
                if gross + fees <= cap + 1e-9:
                    cash -= gross + fees
                    volume += gross
                    position = {"outcome": side, "shares": shares,
                                "buy_gross_usd": gross, "buy_fees_usd": fees,
                                "sell_gross_usd": 0.0, "sell_fees_usd": 0.0}
        elif action == "SELL" and position is not None:
            side = position["outcome"]
            quote = botmod.sell_proceeds(books.get(side, {}).get("bids", []), position["shares"])
            if quote is not None:
                gross, fees = quote
                cash += gross - fees
                volume += gross
                cycles += 1
                position = None

        peak = max(peak, cash)
        max_dd = max(max_dd, peak - cash)

    return {
        "eligible_volume_usd": round(volume, 4),
        "final_cash_usd": round(cash if cash is not None else 0.0, 4),
        "terminal_flat": position is None,
        "completed_cycles": cycles,
        "max_drawdown_usd": round(max_dd, 4),
        "reached_target": volume >= 1000.0,
    }


def sweep(frames: List[dict], base: "botmod.Config") -> None:
    grid = {
        "min_entry_price": [0.5, 0.55, 0.6],
        "max_entry_price": [0.93, 0.95, 0.97],
        "min_reference_gap_usd": [8.0, 12.0, 20.0],
        "max_cycles_per_market": [4, 6, 8],
    }
    keys = list(grid)
    best = None
    print("Grid search over %d combinations..." % (
        len(list(itertools.product(*grid.values())))))
    for combo in itertools.product(*grid.values()):
        cfg = replace(base, **dict(zip(keys, combo)))
        r = replay(frames, cfg)
        # Rank: qualifying (>=1000 + flat) first, then by final cash, then vol.
        score = (r["reached_target"] and r["terminal_flat"], r["final_cash_usd"], r["eligible_volume_usd"])
        if best is None or score > best[0]:
            best = (score, dict(zip(keys, combo)), r)
    print("Best config:", json.dumps(best[1]))
    print("Result:", json.dumps(best[2]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=Path, required=True,
                    help="JSONL of recorded observation frames (chronological)")
    ap.add_argument("--sweep", action="store_true", help="grid-search key params")
    args = ap.parse_args()
    frames = _load(args.frames)
    if not frames:
        raise SystemExit("no frames loaded")
    if args.sweep:
        sweep(frames, botmod.Config())
    else:
        print(json.dumps(replay(frames, botmod.Config()), indent=2))


if __name__ == "__main__":
    main()
