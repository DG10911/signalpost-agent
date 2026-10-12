#!/usr/bin/env python3
"""Capture / generate observation frames for real tuning.

The starter kit ships **no** recorded market data, so there is nothing to
replay out of the box. This script produces a reusable frame dataset in the
exact observation shape the evaluator sends (see ``yeno-sample-observation.json``)
so that ``replay.py`` / ``tune.py`` can score any ``Config`` on the *same*
frames -- the closest thing to a held-in, data-grounded tuning set we can build
offline.

Frames are market-only (they carry the reference feed and the L2 books but no
account/position); ``replay.py`` reconstructs cash/position itself, exactly as
the official evaluator owns them.

Regimes vary spread, depth, volatility and the reference lead so a config is
tuned against benign *and* adverse books, not one lucky path.

    python capture_frames.py --out frames --paths 12 --hours 24 --regimes all

To capture from a *live* local server instead (e.g. a paper-API mirror), point
``--live-url`` at it and this script will POST each generated frame and log the
bot's decision alongside it:

    python capture_frames.py --out frames --live-url http://127.0.0.1:8080/decide
"""

from __future__ import annotations

import argparse
import json
import random
import urllib.request
from pathlib import Path
from typing import List

import simulator as sim


# Each regime mutates the synthetic market model. Keys are the knob, values the
# SimConfig override for that regime.
REGIMES = {
    "normal":        dict(base_spread=0.02, depth=200.0, annual_vol=0.6),
    "high_vol":      dict(base_spread=0.02, depth=200.0, annual_vol=1.2),
    "wide_spread":   dict(base_spread=0.06, depth=200.0, annual_vol=0.6),
    "thin_depth":    dict(base_spread=0.02, depth=10.0, annual_vol=0.6),
    "strong_edge":   dict(base_spread=0.02, depth=200.0, annual_vol=0.6, reference_lead_seconds=10.0),
    "no_edge":       dict(base_spread=0.02, depth=200.0, annual_vol=0.6, reference_lead_seconds=0.0),
    "choppy":        dict(base_spread=0.03, depth=200.0, annual_vol=0.9),
}


def _vol_per_step(sc: sim.SimConfig, cadence: float, btc: float) -> float:
    return sc.annual_vol * (cadence / (365.0 * 24.0 * 3600.0)) ** 0.5 * btc


def generate_frames(seed: int, sc: sim.SimConfig, cadence: float,
                    hours: float) -> List[dict]:
    """Produce a chronological list of observation frames for one regime/path."""
    rng = random.Random(seed)
    now = 0.0
    end_time = hours * 3600.0
    target = sc.btc_start
    frames: List[dict] = []
    market_index = 0

    while now < end_time:
        market_index += 1
        market_id = "cap-%d-%d" % (seed, market_index)
        btc = target
        btc_history = [(now, btc)]
        market_end = min(now + sc.market_seconds, end_time)
        vps = _vol_per_step(sc, cadence, btc)

        while now < market_end:
            seconds_left = market_end - now
            btc += rng.gauss(0.0, vps)
            btc_history.append((now, btc))
            # Book lags the reference feed by reference_lead_seconds.
            lag_t = now - sc.reference_lead_seconds
            btc_lagged = btc_history[0][1]
            for t, v in btc_history:
                if t <= lag_t:
                    btc_lagged = v
                else:
                    break
            prob = sim._fair_prob(btc_lagged, target, seconds_left, sc)
            book = sim._book_for(prob, sc, rng)
            frames.append({
                "schemaVersion": 1,
                "timestamp": now,
                "market": {"id": market_id, "secondsToClose": seconds_left},
                "account": {"cashUsd": sc.starting_cash, "eligibleVolumeUsd": 0.0,
                            "position": None},
                "rules": {
                    "maximumBuyCashUsd": sc.max_buy_cash,
                    "targetVolumeUsd": sc.target_volume,
                    "evaluationWindowHours": hours,
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
            })
            now += cadence

        target = btc

    return frames


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("frames"))
    ap.add_argument("--paths", type=int, default=12,
                    help="paths (distinct seeds) per regime")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--cadence", type=float, default=5.0,
                    help="seconds between observation frames")
    ap.add_argument("--seed0", type=int, default=1000)
    ap.add_argument("--regimes", default="all",
                    help="'all' or comma-separated regime names")
    ap.add_argument("--live-url", default=None,
                    help="optional /decide endpoint to also POST each frame to")
    args = ap.parse_args()

    names = list(REGIMES) if args.regimes == "all" else args.regimes.split(",")
    args.out.mkdir(parents=True, exist_ok=True)

    total = 0
    for name in names:
        base = sim.SimConfig(decision_interval=args.cadence)
        sc = sim.SimConfig(**{**base.__dict__, **REGIMES[name]})
        for p in range(args.paths):
            frames = generate_frames(args.seed0 + p, sc, args.cadence, args.hours)
            if args.live_url:
                for f in frames:
                    payload = json.loads(json.dumps(f))
                    payload["account"] = {"cashUsd": 10.0, "eligibleVolumeUsd": 0.0,
                                          "position": None}
                    try:
                        f["decision"] = _post(args.live_url, payload)
                    except Exception as exc:  # noqa: BLE001
                        f["decision"] = {"error": str(exc)}
            path = args.out / ("%s-%04d.jsonl" % (name, args.seed0 + p))
            with path.open("w") as fh:
                for f in frames:
                    fh.write(json.dumps(f, separators=(",", ":")) + "\n")
            total += 1
        print("regime %-12s wrote %d paths" % (name, args.paths))

    print("done: %d frame files in %s" % (total, args.out))


if __name__ == "__main__":
    main()
