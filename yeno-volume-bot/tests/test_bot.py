#!/usr/bin/env python3
"""Unit tests for the YeNo volume bot. Run with:  python -m unittest -v

Pure stdlib (unittest) so no dependencies are required.
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as botmod  # noqa: E402


def base_obs(**overrides):
    ts = float(overrides.get("timestamp", 1000.0))
    obs = {
        "schemaVersion": 1,
        "timestamp": ts,
        "market": {"id": "m1", "secondsToClose": 90.0},
        "account": {"cashUsd": 10.0, "eligibleVolumeUsd": 0.0,
                    "completedCycles": 0, "position": None},
        "rules": {"maximumBuyCashUsd": 5.0, "targetVolumeUsd": 1000.0,
                  "evaluationWindowHours": 24.0, "onePositionAtATime": True,
                  "buyFeesIncludedInMaximum": True},
        # Reference timestamps track the frame time so persistence can build up
        # across successive frames unless a test overrides ``reference``.
        "reference": {"btcMidUsd": 80100.0, "openingTargetUsd": 80000.0,
                      "observedAt": ts, "targetObservedAt": ts - 1.0,
                      "targetProvisional": False},
        "books": {
            "YES": {"asks": [[0.80, 100]], "bids": [[0.78, 100]], "minOrderSize": 5},
            "NO": {"asks": [[0.22, 100]], "bids": [[0.20, 100]], "minOrderSize": 5},
        },
    }
    obs.update(overrides)
    return obs


class FeeModelTest(unittest.TestCase):
    def test_matches_contract_formula(self):
        # One fill of 5 shares at 0.5: taker 0.07*5*0.5*0.5=0.0875; overlay 1% of
        # gross 2.5 = 0.025 -> total 0.1125.
        self.assertAlmostEqual(botmod.side_fees([(0.5, 5.0)]), 0.1125, places=5)

    def test_fee_fraction_falls_with_price(self):
        # Fee/notional should shrink as price -> 1 (favourites are cheaper).
        def frac(p):
            g = p * 5.0
            return botmod.side_fees([(p, 5.0)]) / g
        self.assertGreater(frac(0.5), frac(0.9))


class ContractComplianceTest(unittest.TestCase):
    def setUp(self):
        self.bot = botmod.Bot()

    def test_action_shape(self):
        d = self.bot.decide(base_obs())
        self.assertIn(d["action"], {"HOLD", "BUY", "SELL"})

    def test_buy_respects_five_dollar_cap(self):
        # Prime persistence, then a favourite at high price must stay <= $5.
        b = botmod.Bot()
        b.decide(base_obs(timestamp=998.0))
        d = b.decide(base_obs(timestamp=1000.0,
                              books={"YES": {"asks": [[0.97, 100]], "bids": [[0.95, 100]], "minOrderSize": 5},
                                     "NO": {"asks": [[0.05, 100]], "bids": [[0.03, 100]], "minOrderSize": 5}}))
        if d["action"] == "BUY":
            self.assertLessEqual(d["maxCashUsd"], 5.0 + 1e-9)
            self.assertIn(d["outcome"], {"YES", "NO"})

    def test_malformed_observation_holds(self):
        self.assertEqual(self.bot.decide({"garbage": True}), {"action": "HOLD"})
        self.assertEqual(self.bot.decide({}), {"action": "HOLD"})


class EntryLogicTest(unittest.TestCase):
    def test_requires_persistence(self):
        b = botmod.Bot()
        # First observation: only one reference sample -> cannot confirm.
        self.assertEqual(b.decide(base_obs())["action"], "HOLD")

    def test_enters_favorite_after_persistent_signal(self):
        b = botmod.Bot()
        b.decide(base_obs(timestamp=998.0))
        d = b.decide(base_obs(timestamp=1000.0))
        self.assertEqual(d["action"], "BUY")
        self.assertEqual(d["outcome"], "YES")

    def test_no_entry_in_final_guard_window(self):
        b = botmod.Bot()
        b.decide(base_obs(timestamp=998.0, market={"id": "m1", "secondsToClose": 12.0}))
        d = b.decide(base_obs(timestamp=1000.0, market={"id": "m1", "secondsToClose": 10.0}))
        self.assertEqual(d["action"], "HOLD")

    def test_rejects_provisional_reference(self):
        b = botmod.Bot()
        ref = {"btcMidUsd": 80100.0, "openingTargetUsd": 80000.0,
               "observedAt": 998.0, "targetObservedAt": 997.0, "targetProvisional": True}
        b.decide(base_obs(timestamp=998.0, reference=ref))
        d = b.decide(base_obs(timestamp=1000.0, reference=dict(ref, observedAt=1000.0)))
        self.assertEqual(d["action"], "HOLD")

    def test_rejects_stale_reference(self):
        b = botmod.Bot()
        ref = {"btcMidUsd": 80100.0, "openingTargetUsd": 80000.0,
               "observedAt": 990.0, "targetObservedAt": 989.0, "targetProvisional": False}
        d = b.decide(base_obs(timestamp=1000.0, reference=ref))  # 10s old > 1.5s
        self.assertEqual(d["action"], "HOLD")

    def test_rejects_future_stamped_target(self):
        b = botmod.Bot()
        ref = {"btcMidUsd": 80100.0, "openingTargetUsd": 80000.0,
               "observedAt": 1000.0, "targetObservedAt": 1005.0, "targetProvisional": False}
        d = b.decide(base_obs(timestamp=1000.0, reference=ref))
        self.assertEqual(d["action"], "HOLD")

    def test_rejects_book_reference_disagreement(self):
        # Reference says YES but the YES book is a heavy underdog -> skip.
        b = botmod.Bot()
        books = {"YES": {"asks": [[0.30, 100]], "bids": [[0.28, 100]], "minOrderSize": 5},
                 "NO": {"asks": [[0.72, 100]], "bids": [[0.70, 100]], "minOrderSize": 5}}
        b.decide(base_obs(timestamp=998.0, books=books))
        d = b.decide(base_obs(timestamp=1000.0, books=books))
        self.assertEqual(d["action"], "HOLD")


class ExitLogicTest(unittest.TestCase):
    def _pos(self, **kw):
        p = {"outcome": "YES", "shares": 5.0, "buy_gross_usd": 4.0,
             "buy_fees_usd": 0.1, "sell_gross_usd": 0.0, "sell_fees_usd": 0.0}
        p.update(kw)
        return p

    def test_take_profit_when_roundtrip_nonnegative(self):
        b = botmod.Bot()
        obs = base_obs(account={"cashUsd": 6.0, "position": self._pos(),
                                "eligibleVolumeUsd": 0.0, "completedCycles": 0},
                       books={"YES": {"asks": [[0.90, 100]], "bids": [[0.90, 100]], "minOrderSize": 5},
                              "NO": {"asks": [[0.10, 100]], "bids": [[0.10, 100]], "minOrderSize": 5}})
        # Sell 5 @0.90 -> gross 4.5 - fees > paid 4.1 -> profit -> SELL.
        self.assertEqual(b.decide(obs)["action"], "SELL")

    def test_stop_loss_triggers(self):
        b = botmod.Bot()
        obs = base_obs(account={"cashUsd": 6.0, "position": self._pos(),
                                "eligibleVolumeUsd": 0.0, "completedCycles": 0},
                       books={"YES": {"asks": [[0.50, 100]], "bids": [[0.50, 100]], "minOrderSize": 5},
                              "NO": {"asks": [[0.50, 100]], "bids": [[0.50, 100]], "minOrderSize": 5}})
        # Sell 5 @0.50 -> ~2.5 vs paid 4.1 -> big loss beyond stop -> SELL.
        self.assertEqual(b.decide(obs)["action"], "SELL")

    def test_forced_exit_near_close(self):
        b = botmod.Bot()
        obs = base_obs(market={"id": "m1", "secondsToClose": 30.0},
                       account={"cashUsd": 6.0, "position": self._pos(),
                                "eligibleVolumeUsd": 0.0, "completedCycles": 0},
                       books={"YES": {"asks": [[0.60, 100]], "bids": [[0.60, 100]], "minOrderSize": 5},
                              "NO": {"asks": [[0.40, 100]], "bids": [[0.40, 100]], "minOrderSize": 5}})
        self.assertEqual(b.decide(obs)["action"], "SELL")


class RiskTest(unittest.TestCase):
    def test_defensive_when_cash_low(self):
        # Below the cash floor, a marginal favourite that would pass at full cash
        # is rejected because the defensive max entry price is stricter.
        b = botmod.Bot()
        books = {"YES": {"asks": [[0.90, 100]], "bids": [[0.88, 100]], "minOrderSize": 5},
                 "NO": {"asks": [[0.12, 100]], "bids": [[0.10, 100]], "minOrderSize": 5}}
        b.decide(base_obs(timestamp=998.0, account={"cashUsd": 5.0, "position": None,
                                                    "eligibleVolumeUsd": 0.0, "completedCycles": 0}, books=books))
        d = b.decide(base_obs(timestamp=1000.0, account={"cashUsd": 5.0, "position": None,
                                                        "eligibleVolumeUsd": 0.0, "completedCycles": 0}, books=books))
        # 0.90 > defensive_max_entry_price (0.85) -> HOLD.
        self.assertEqual(d["action"], "HOLD")


class PacingTest(unittest.TestCase):
    def _rules(self):
        return {"evaluationWindowHours": 24.0, "targetVolumeUsd": 1000.0}

    def test_behind_pace_increases_aggression(self):
        b = botmod.Bot()
        b.start_ts = 0.0
        # 50% of the window elapsed, only 5% of target volume -> well behind.
        now = 0.5 * 24 * 3600
        agg = b._aggression(now, self._rules(), volume=50.0)
        self.assertGreater(agg, 1.0)
        self.assertLessEqual(agg, b.config.max_aggression)

    def test_ahead_of_pace_reduces_aggression(self):
        b = botmod.Bot()
        b.start_ts = 0.0
        # 20% elapsed, 60% of target already -> well ahead.
        now = 0.2 * 24 * 3600
        agg = b._aggression(now, self._rules(), volume=600.0)
        self.assertLess(agg, 1.0)
        self.assertGreaterEqual(agg, b.config.min_aggression)

    def test_pacing_disabled_is_neutral(self):
        cfg = botmod.Config(pacing_enabled=False)
        b = botmod.Bot(config=cfg)
        b.start_ts = 0.0
        self.assertEqual(b._aggression(1e5, self._rules(), volume=0.0), 1.0)

    def test_too_early_is_neutral(self):
        b = botmod.Bot()
        b.start_ts = 0.0
        self.assertEqual(b._aggression(10.0, self._rules(), volume=0.0), 1.0)


class RobustnessInvariantTest(unittest.TestCase):
    """Across adverse regimes the bot must stay contract-safe: always finish
    terminal-flat and never emit an invalid action. Uses short windows so the
    test is fast; scenarios.py runs the full-length battery."""

    def test_flat_and_no_invalid_actions_across_regimes(self):
        from simulator import SimConfig, simulate_path
        regimes = [
            dict(),
            dict(base_spread=0.06),                       # wide spread
            dict(depth=5.0, base_spread=0.05),            # low liquidity
            dict(latency_move_sd=0.05),                   # heavy adverse fills
            dict(annual_vol=1.6),                         # high vol
            dict(reference_lead_seconds=0.0),             # no edge
        ]
        for overrides in regimes:
            sc = SimConfig(window_hours=0.5, **overrides)  # short window = fast
            for seed in range(4):
                r = simulate_path(botmod.Bot(), 5000 + seed, sc)
                self.assertTrue(r.terminal_flat, f"not flat in {overrides}")
                self.assertEqual(r.invalid_actions, 0, f"invalid action in {overrides}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
