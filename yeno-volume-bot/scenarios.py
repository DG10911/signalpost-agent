#!/usr/bin/env python3
"""Adversarial regime harness for the YeNo bot.

Runs the strategy across many synthetic market regimes and reports the metrics
that decide qualification and rank, with the invariant checks that must hold in
EVERY regime:

  * terminal-flat rate  == 100%   (qualification-critical)
  * invalid bot actions == 0      (never BUY over $5, never BUY while holding,
                                    never a malformed action)

HONESTY: these are SYNTHETIC regimes, not the official evaluator and not real
YeNo frames. They validate robustness and contract-safety; the absolute volume
and $1,000-qualification numbers do NOT transfer to the real market (a synthetic
martingale has no alpha to capture). Use replay.py on real frames for that.

    python scenarios.py --paths 40
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import replace
from typing import List

import bot as botmod
from simulator import SimConfig, simulate_path

# name -> SimConfig overrides describing an adversarial regime.
SCENARIOS = {
    "normal":            dict(),
    "wide_spread":       dict(base_spread=0.06),
    "thin_depth":        dict(depth=6.0),
    "low_liquidity":     dict(depth=5.0, base_spread=0.05),
    "adverse_2c":        dict(latency_move_sd=0.02),
    "adverse_5c":        dict(latency_move_sd=0.05),
    "strong_trend":      dict(annual_vol=1.2, reference_lead_seconds=6.0),
    "choppy_no_edge":    dict(annual_vol=0.9, reference_lead_seconds=0.0),
    "high_vol":          dict(annual_vol=1.6),
    "no_reference_edge": dict(reference_lead_seconds=0.0),
}


def _pct(xs: List[float]) -> str:
    return "%d%%" % round(100.0 * (sum(1 for x in xs if x) / len(xs)))


def run(paths: int, seed0: int) -> int:
    hdr = ("regime", "flat%", "invalid", "qual%", "med_vol", "medΔcash", "max_dd")
    print("Adversarial regimes (SYNTHETIC — robustness/contract only, not official)")
    print("%-18s %6s %8s %6s %8s %9s %7s" % hdr)
    total_invalid = 0
    all_flat = True
    for name, overrides in SCENARIOS.items():
        sc = SimConfig(**overrides)
        results = [simulate_path(botmod.Bot(), seed0 + i, sc) for i in range(paths)]
        flat = [r.terminal_flat for r in results]
        invalid = sum(r.invalid_actions for r in results)
        vols = [r.eligible_volume_usd for r in results]
        dd = statistics.median(r.max_drawdown_usd for r in results)
        cash_flat = [r.final_cash_usd for r in results if r.terminal_flat]
        total_invalid += invalid
        all_flat = all_flat and all(flat)
        print("%-18s %6s %8d %6s %8.2f %9.2f %7.2f" % (
            name, _pct(flat), invalid,
            _pct([r.reached_target for r in results]),
            statistics.median(vols),
            statistics.median(cash_flat) if cash_flat else float("nan"),
            dd,
        ))
    print("-" * 66)
    print("INVARIANTS: terminal-flat everywhere = %s | total invalid actions = %d"
          % (all_flat, total_invalid))
    ok = all_flat and total_invalid == 0
    print("RESULT:", "PASS (contract-safe across all regimes)" if ok else "FAIL")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paths", type=int, default=40)
    ap.add_argument("--seed", type=int, default=2024)
    args = ap.parse_args()
    raise SystemExit(run(args.paths, args.seed))


if __name__ == "__main__":
    main()
