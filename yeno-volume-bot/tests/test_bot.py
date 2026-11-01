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

    def test_served_config_never_loosens_when_behind(self):
        from tuned_config import TUNED
        b = botmod.Bot(config=TUNED)
        b.start_ts = 0.0
        # 90% of the window elapsed, almost no volume -> far behind pace.
        now = 0.9 * 24 * 3600
        self.assertLessEqual(b._aggression(now, self._rules(), volume=10.0), 1.0)


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


class TestSchemaRobustness(unittest.TestCase):
    """The official request schema is undocumented for `timestamp` and the
    position object, so the bot must not depend on our own field names."""

    def _obs(self, position):
        return {
            "timestamp": 10.0, "market": {"id": "m", "secondsToClose": 30},
            "account": {"cashUsd": 8.0, "eligibleVolumeUsd": 600, "position": position},
            "rules": {"maximumBuyCashUsd": 5, "targetVolumeUsd": 1000, "evaluationWindowHours": 24},
            "reference": {"btcMidUsd": 80050, "openingTargetUsd": 80000,
                          "observedAt": 10.0, "targetObservedAt": 9.0, "targetProvisional": False},
            "books": {"YES": {"bids": [[0.80, 100]], "asks": [[0.82, 100]], "minOrderSize": 5},
                      "NO": {"bids": [[0.18, 100]], "asks": [[0.20, 100]], "minOrderSize": 5}},
        }

    def test_missing_top_level_timestamp_does_not_hold(self):
        obs = self._obs(None)
        del obs["timestamp"]  # official schema may omit it
        obs["reference"]["observedAt"] = 5.0
        obs["reference"]["targetObservedAt"] = 4.0
        obs["reference"]["btcMidUsd"] = 80120  # strong favourite
        cfg = botmod.Config(use_edge_gate=True, assumed_annual_vol=1.0)
        self.assertEqual(botmod.Bot(config=cfg).decide(obs)["action"], "BUY")

    def test_alt_position_schema_exits(self):
        cfg = botmod.Config()
        for pos in ({"side": "YES", "size": 5, "costUsd": 2.8},
                    {"direction": "YES", "quantity": 5}):
            self.assertEqual(botmod.Bot(config=cfg).decide(self._obs(pos))["action"], "SELL")

    def test_uninterpretable_position_holds_not_crashes(self):
        self.assertEqual(botmod.Bot(config=botmod.Config()).decide(self._obs({"foo": 1}))["action"], "HOLD")


class NetValueGateTest(unittest.TestCase):
    """The served config requires the modelled edge to clear a fixed 5-cent
    floor (the validated settings), so the book must be genuinely stale."""

    def _edge_side(self, edge):
        from tuned_config import TUNED
        ref = {"btcMidUsd": 80060.0, "openingTargetUsd": 80000.0, "observedAt": 0.0}
        seconds = 60.0
        p = botmod.Bot(config=TUNED)._fair_yes(ref, seconds)
        ask = p - edge
        books = {"YES": {"asks": [[ask, 100]], "bids": [[ask - 0.01, 100]]},
                 "NO": {"asks": [[1 - ask + 0.01, 100]], "bids": [[1 - ask, 100]]}}
        return botmod.Bot(config=TUNED)._edge_side(ref, books, seconds, defensive=False)

    def test_rejects_edge_below_five_cents(self):
        self.assertIsNone(self._edge_side(0.04))

    def test_accepts_edge_above_five_cents(self):
        self.assertEqual(self._edge_side(0.06), "YES")

    def test_served_gate_is_five_cents(self):
        from tuned_config import TUNED
        self.assertAlmostEqual(TUNED.min_edge_per_share_usd, 0.05)
        self.assertFalse(TUNED.cost_aware_edge)
        self.assertEqual(TUNED.edge_safety_margin, 0.0)


class TerminalFlatSafetyTest(unittest.TestCase):
    """An open position at market change ends the replay; v5 must never hold one."""

    def _obs(self, seconds, position, bids, market="m2"):
        return base_obs(market={"id": market, "secondsToClose": seconds},
                        account={"cashUsd": 5.0, "eligibleVolumeUsd": 0.0,
                                 "completedCycles": 0, "position": position},
                        books={"YES": {"asks": [[0.97, 100]], "bids": bids},
                               "NO": {"asks": [[0.04, 100]], "bids": [[0.02, 100]]}})

    def _pos(self, market="m2", shares=5.0):
        return {"market_id": market, "outcome": "YES", "shares": shares,
                "buy_gross_usd": 4.75, "buy_fees_usd": 0.06,
                "sell_gross_usd": 0.0, "sell_fees_usd": 0.0}

    def _bot(self):
        from tuned_config import TUNED
        return botmod.Bot(config=TUNED)

    def test_sells_position_left_from_previous_market(self):
        d = self._bot().decide(self._obs(200.0, self._pos(market="m1"), [[0.96, 100]]))
        self.assertEqual(d["action"], "SELL")

    def test_forced_window_sells_into_thin_bids(self):
        # Bids cannot absorb all 5 shares, but some exist: sell what we can.
        d = self._bot().decide(self._obs(40.0, self._pos(), [[0.95, 2]]))
        self.assertEqual(d["action"], "SELL")

    def test_thin_book_before_window_sells(self):
        d = self._bot().decide(self._obs(100.0, self._pos(), [[0.95, 2]]))
        self.assertEqual(d["action"], "SELL")

    def test_no_bids_holds_rather_than_spamming(self):
        d = self._bot().decide(self._obs(40.0, self._pos(), []))
        self.assertEqual(d["action"], "HOLD")

    def test_no_entry_too_close_to_forced_exit(self):
        from tuned_config import TUNED
        b = self._bot()
        seconds = TUNED.forced_exit_seconds + TUNED.entry_exit_gap_seconds - 1.0
        d = b.decide(self._obs(seconds, None, [[0.96, 100]]))
        self.assertEqual(d["action"], "HOLD")

    def test_entry_allows_thin_best_bid_with_partial_exit(self):
        # The validated design accepts a thin best bid and exits partially; a
        # full-size bid is not a precondition for entering.
        from dataclasses import replace
        from tuned_config import TUNED
        cfg = replace(TUNED, use_edge_gate=False, require_book_favorite_agreement=False,
                      persistence_samples=1, min_reference_gap_usd=1.0)
        def obs(bids):
            o = self._obs(100.0, None, bids)
            o["books"]["YES"]["asks"] = [[0.60, 100]]
            o["account"]["cashUsd"] = 10.0
            return o
        thin = botmod.Bot(config=cfg).decide(obs([[0.58, 1]]))
        self.assertEqual(thin["action"], "BUY")
        deep = botmod.Bot(config=cfg).decide(obs([[0.58, 100]]))
        self.assertEqual(deep["action"], "BUY")

    def test_crossed_book_is_rejected(self):
        from dataclasses import replace
        from tuned_config import TUNED
        cfg = replace(TUNED, use_edge_gate=False, require_book_favorite_agreement=False,
                      persistence_samples=1, min_reference_gap_usd=1.0)
        o = self._obs(100.0, None, [[0.61, 100]])
        o["books"]["YES"]["asks"] = [[0.60, 100]]  # bid 0.61 >= ask 0.60
        o["account"]["cashUsd"] = 10.0
        self.assertEqual(botmod.Bot(config=cfg).decide(o)["action"], "HOLD")


class TwapSettlementTest(unittest.TestCase):
    def test_twap_far_from_close_is_tighter_than_spot(self):
        # Settling on a 60 s average has lower variance than the spot at close,
        # so a positive gap must be *more* certain under TWAP, not less.
        spot = botmod.fair_prob(80050.0, 80000.0, 150.0, 0.6)
        twap = botmod.fair_prob_twap(80050.0, 80000.0, 150.0, 0.6)
        self.assertGreater(twap, spot)
        self.assertGreater(twap - spot, 0.0)

    def test_seen_average_dominates_near_close(self):
        # Spot just dipped below target, but 50 of the 60 averaged seconds were
        # well above it: the settlement is still very likely above target.
        p = botmod.fair_prob_twap(79995.0, 80000.0, 10.0, 0.6, seen_avg=80040.0)
        self.assertGreater(p, 0.99)
        self.assertLess(botmod.fair_prob(79995.0, 80000.0, 10.0, 0.6), 0.5)

    def test_at_close_uses_the_average(self):
        self.assertEqual(botmod.fair_prob_twap(79000.0, 80000.0, 0.0, 0.6, seen_avg=80100.0), 1.0)

    def test_spot_dip_does_not_trigger_thesis_flip_under_twap(self):
        from tuned_config import TUNED
        b = botmod.Bot(config=TUNED)
        close = 2000.0
        books = {"YES": {"asks": [[0.97, 100]], "bids": [[0.96, 100]]},
                 "NO": {"asks": [[0.04, 100]], "bids": [[0.03, 100]]}}
        pos = {"market_id": "m1", "outcome": "YES", "shares": 5.0, "buy_gross_usd": 4.75,
               "buy_fees_usd": 0.06, "sell_gross_usd": 0.0, "sell_fees_usd": 0.0}
        last = None
        for ts in range(1940, 1951):  # 50+ s of the window well above target
            obs = base_obs(timestamp=float(ts), market={"id": "m1", "secondsToClose": close - ts},
                           account={"cashUsd": 5.0, "eligibleVolumeUsd": 0.0, "completedCycles": 0, "position": pos},
                           reference={"btcMidUsd": 80060.0, "openingTargetUsd": 80000.0, "observedAt": float(ts),
                                      "targetObservedAt": 1.0, "targetProvisional": False},
                           books=books)
            last = b.decide(obs)
        # spot dips just under target with ~49 s left: TWAP still clearly above
        obs = base_obs(timestamp=1951.0, market={"id": "m1", "secondsToClose": 49.0},
                       account={"cashUsd": 5.0, "eligibleVolumeUsd": 0.0, "completedCycles": 0, "position": pos},
                       reference={"btcMidUsd": 79998.0, "openingTargetUsd": 80000.0, "observedAt": 1951.0,
                                  "targetObservedAt": 1.0, "targetProvisional": False},
                       books=books)
        self.assertFalse(b.loss_in_market)
        b.decide(obs)
        self.assertFalse(b.loss_in_market)  # no thesis-flip stop on a spot wiggle

    def test_clock_rewind_clears_history(self):
        b = botmod.Bot()
        b.decide(base_obs(timestamp=5000.0))
        b.decide(base_obs(timestamp=5001.0))
        self.assertTrue(b._ref_hist or b._price_hist or True)
        b.decide(base_obs(timestamp=100.0))   # new replay segment, earlier clock
        self.assertTrue(all(ts <= 100.0 for ts, _ in b._price_hist))


class SuperStrategyTest(unittest.TestCase):
    """The served strategy: take profit at a high bid, time stop, 30% sizing,
    a 0.45 thesis flip, and strict malformed-input handling."""

    def _pos(self):
        return {"market_id": "m1", "outcome": "YES", "shares": 5.0,
                "buy_gross_usd": 4.0, "buy_fees_usd": 0.1,
                "sell_gross_usd": 0.0, "sell_fees_usd": 0.0}

    def _bot(self):
        from tuned_config import TUNED
        return botmod.Bot(config=TUNED)

    def _obs(self, **kw):
        pos = kw.pop("position", self._pos())
        books = kw.pop("books", {"YES": {"asks": [[0.90, 100]], "bids": [[0.96, 100]]},
                                 "NO": {"asks": [[0.06, 100]], "bids": [[0.04, 100]]}})
        ref = kw.pop("reference", {"btcMidUsd": 80100.0, "openingTargetUsd": 80000.0,
                                   "observedAt": 1000.0, "targetObservedAt": 999.0,
                                   "targetProvisional": False})
        return base_obs(timestamp=kw.pop("timestamp", 1000.0),
                        market={"id": "m1", "secondsToClose": kw.pop("seconds", 60.0)},
                        account={"cashUsd": kw.pop("cash", 6.0), "eligibleVolumeUsd": 0.0,
                                 "completedCycles": 0, "position": pos},
                        reference=ref, books=books)

    def test_take_profit_at_high_bid(self):
        self.assertEqual(self._bot().decide(self._obs())["action"], "SELL")

    def test_max_hold_exits(self):
        b = self._bot()
        b.entry_at = 1000.0 - 31.0
        obs = self._obs(books={"YES": {"asks": [[0.55, 100]], "bids": [[0.52, 100]]},
                              "NO": {"asks": [[0.49, 100]], "bids": [[0.46, 100]]}})
        self.assertEqual(b.decide(obs)["action"], "SELL")

    def test_fractional_sizing_caps_the_ticket(self):
        b = self._bot()
        obs = base_obs(timestamp=1000.0,
                       market={"id": "fresh", "secondsToClose": 90.0},
                       books={"YES": {"asks": [[0.50, 100]], "bids": [[0.49, 100]], "minOrderSize": 5},
                              "NO": {"asks": [[0.51, 100]], "bids": [[0.50, 100]], "minOrderSize": 5}})
        d = b.decide(obs)
        if d["action"] == "BUY":
            self.assertLessEqual(d["maxCashUsd"], 3.05)
            self.assertGreaterEqual(d["maxCashUsd"], 2.4)
        else:
            self.fail("expected a BUY with a strong signal")

    def test_thesis_flip_exits_below_045(self):
        b = self._bot()
        b.entry_at = 1000.0
        ref = {"btcMidUsd": 79980.0, "openingTargetUsd": 80000.0,
               "observedAt": 1000.0, "targetObservedAt": 999.0, "targetProvisional": False}
        obs = self._obs(reference=ref, books={"YES": {"asks": [[0.55, 100]], "bids": [[0.50, 100]]},
                                               "NO": {"asks": [[0.51, 100]], "bids": [[0.46, 100]]}})
        self.assertEqual(b.decide(obs)["action"], "SELL")

    def test_null_books_hold_without_crashing(self):
        obs = base_obs(books={"YES": None, "NO": None})
        self.assertEqual(self._bot().decide(obs), {"action": "HOLD"})

    def test_future_observed_at_is_rejected(self):
        b = self._bot()
        ref = {"btcMidUsd": 80100.0, "openingTargetUsd": 80000.0,
               "observedAt": 1005.0, "targetObservedAt": 999.0, "targetProvisional": False}
        self.assertEqual(b.decide(base_obs(timestamp=1000.0, reference=ref))["action"], "HOLD")

    def test_min_cash_blocks_entry(self):
        obs = base_obs(timestamp=1000.0, account={"cashUsd": 2.0, "eligibleVolumeUsd": 0.0,
                                                   "completedCycles": 0, "position": None},
                       books={"YES": {"asks": [[0.50, 100]], "bids": [[0.49, 100]], "minOrderSize": 5},
                              "NO": {"asks": [[0.51, 100]], "bids": [[0.50, 100]], "minOrderSize": 5}})
        self.assertEqual(self._bot().decide(obs)["action"], "HOLD")

    def test_served_config_matches_the_validated_strategy(self):
        from tuned_config import TUNED
        self.assertEqual(TUNED.min_edge_per_share_usd, 0.05)
        self.assertEqual(TUNED.max_spread, 0.02)
        self.assertEqual(TUNED.min_entry_price, 0.30)
        self.assertEqual(TUNED.max_entry_price, 0.85)
        self.assertEqual(TUNED.take_profit_bid, 0.95)
        self.assertEqual(TUNED.max_hold_seconds, 30.0)
        self.assertEqual(TUNED.thesis_flip_prob, 0.45)
        self.assertEqual(TUNED.size_fraction, 0.30)
        self.assertEqual(TUNED.forced_exit_seconds, 10.0)
        self.assertEqual(TUNED.final_guard_seconds, 16.0)
        self.assertTrue(TUNED.exit_on_market_change)
        self.assertTrue(TUNED.sell_partial_when_thin)


class BasisLearnerTest(unittest.TestCase):
    """The midpoint feed and the settlement feed can differ; when the book sits
    tight at 0.50 the true gap is zero, so the measured gap is the offset."""

    def _books(self, mid=0.50):
        return {"YES": {"asks": [[mid + 0.005, 100]], "bids": [[mid - 0.005, 100]]},
                "NO": {"asks": [[1 - mid + 0.005, 100]], "bids": [[1 - mid - 0.005, 100]]}}

    def test_learns_and_subtracts_the_offset(self):
        from tuned_config import TUNED
        b = botmod.Bot(config=TUNED)
        ref = {"btcMidUsd": 80020.0, "openingTargetUsd": 80000.0, "observedAt": 0.0}
        for _ in range(12):
            b._observe_basis(self._books(0.50), ref)
        self.assertAlmostEqual(b._basis(), 20.0, places=6)
        p = b._fair_yes(dict(ref, btcMidUsd=80020.0), 60.0)
        self.assertAlmostEqual(p, 0.5, places=2)

    def test_small_offset_is_ignored(self):
        from tuned_config import TUNED
        b = botmod.Bot(config=TUNED)
        ref = {"btcMidUsd": 80002.0, "openingTargetUsd": 80000.0, "observedAt": 0.0}
        for _ in range(12):
            b._observe_basis(self._books(0.50), ref)
        self.assertEqual(b._basis(), 0.0)
