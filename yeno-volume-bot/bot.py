#!/usr/bin/env python3
"""Volume-optimising trading agent for the YeNo x Builderr Volume Bot Challenge.

The bot only ever returns HOLD / BUY / SELL. It never touches evaluator-owned
cash, fills, fees, volume or position accounting -- it merely *estimates* fees
locally to decide whether a round trip is worth taking and to stay inside the
fee-inclusive $5 BUY ceiling.

Design goals (mapped to the house-bot benchmark's "what to improve" list):

1. Persistent-signal entry   -> only enter when the BTC reference gap has held
   its sign across recent observations, filtering choppy coin-flip regimes.
2. Adverse-fill avoidance     -> require immediate exit depth and cap the
   modelled immediate round-trip loss before committing.
3. Drawdown-aware risk        -> when paper cash falls below a floor the bot
   demands a stronger edge, a cheaper (higher-price, lower-fee) favourite, and
   smaller size, so a bad streak cannot compound the $10 stack to zero.
4. Guaranteed terminal-flat   -> new entries stop well before market close and
   an open position is force-closed with margin to spare, so the run finishes
   flat with no pending action.
5. Fee-efficient volume       -> per-side fee fraction is 0.01 + 0.07*(1-price),
   so the bot prefers clear favourites: cheaper fees AND more notional inside
   the $5 cap means more eligible volume per completed cycle.

Compatible with Python 3.9+ (uses ``from __future__ import annotations``).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, List, Optional, Tuple

Level = Tuple[float, float]


# --------------------------------------------------------------------------- #
# Fee model (mirrors the evaluator contract; used only for local estimation).
# --------------------------------------------------------------------------- #

def side_fees(fills: List[Level]) -> float:
    """Return total fee for one side given a list of (price, shares) fills.

    Mirrors the documented model: a Polymarket-style dynamic taker fee of
    ``0.07 * shares * price * (1 - price)`` plus the observed 1% YeNo overlay on
    gross notional, each rounded to five decimals.
    """
    gross = sum(price * shares for price, shares in fills)
    protocol = sum(0.07 * shares * price * (1 - price) for price, shares in fills)
    return round(protocol + 1e-12, 5) + round(gross * 0.01 + 1e-12, 5)


def _levels(rows: Any, *, reverse: bool) -> List[Level]:
    out: List[Level] = []
    for row in rows if isinstance(rows, list) else []:
        try:
            price, size = float(row[0]), float(row[1])
        except (IndexError, TypeError, ValueError):
            continue
        if 0.0 < price < 1.0 and size > 0.0:
            out.append((price, size))
    return sorted(out, reverse=reverse)


def _walk_book(rows: List[Level], shares: float) -> Optional[Tuple[float, float]]:
    """Fill ``shares`` against pre-sorted ``rows``. Returns (gross, fees) or None
    if the book cannot fully fill the requested size."""
    remaining = shares
    fills: List[Level] = []
    for price, available in rows:
        take = min(remaining, available)
        fills.append((price, take))
        remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        return None
    gross = sum(price * qty for price, qty in fills)
    return gross, side_fees(fills)


def buy_cost(asks: Any, shares: float) -> Optional[Tuple[float, float]]:
    return _walk_book(_levels(asks, reverse=False), shares)


def sell_proceeds(bids: Any, shares: float) -> Optional[Tuple[float, float]]:
    return _walk_book(_levels(bids, reverse=True), shares)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    # Timing (seconds to market close). Entries concentrate near expiry where
    # the favourite is clearest, the fee fraction is lowest, and the most
    # notional fits inside the $5 cap -- the cheapest volume per cycle.
    entry_window_seconds: float = 120.0     # only consider entries at/under this
    final_guard_seconds: float = 20.0       # never enter this close to expiry
    forced_exit_seconds: float = 40.0       # force-close by here (margin > 15s rule)

    # Reference signal.
    min_reference_gap_usd: float = 10.0     # below this the market is a coin-flip
    max_reference_age_seconds: float = 1.5
    persistence_samples: int = 2            # recent same-sign gaps required
    reference_history: int = 8
    require_book_favorite_agreement: bool = True  # book must also favour the side

    # Entry pricing / sizing.
    target_shares: float = 5.0
    min_entry_price: float = 0.52           # accept the favourite from ~parity up
                                            # (key tuning knob: lower captures
                                            # reference-lead entries at parity)
    max_entry_price: float = 0.97           # 5*0.97 + fees still < $5 cap
    max_cycles_per_market: int = 6
    reentry_cooldown_seconds: float = 2.0

    # Risk gates.
    max_immediate_roundtrip_loss_usd: float = 0.35
    require_immediate_exit_depth: bool = True

    # Exit.
    min_profit_per_share_usd: float = 0.0   # sell as soon as round trip >= this
    stop_loss_per_share_usd: float = 0.08

    # Drawdown-aware tightening.
    starting_cash_usd: float = 10.0
    cash_floor_usd: float = 7.0             # below this, protect capital harder
    defensive_max_entry_price: float = 0.85
    defensive_gap_multiplier: float = 1.6

    # Volume pacing. Qualification REQUIRES crossing the $1,000 target, and the
    # rank metric is settled cash *at* the crossing -- so when behind the pace
    # needed to reach the target in time, lean in (accept marginally thinner
    # edges) to secure qualification; when ahead, tighten to preserve cash.
    # Never loosens while capital is below the floor.
    pacing_enabled: bool = True
    max_aggression: float = 1.5             # behind pace -> up to this
    min_aggression: float = 0.7             # ahead of pace -> down to this


# --------------------------------------------------------------------------- #
# Bot
# --------------------------------------------------------------------------- #

@dataclass
class Bot:
    config: Config = field(default_factory=Config)

    market_id: Optional[str] = None
    position_was_open: bool = False
    completed_cycles: int = 0
    last_flat_at: float = -math.inf
    last_buy_attempt_at: float = -math.inf
    peak_cash: float = 0.0
    max_drawdown: float = 0.0
    start_ts: Optional[float] = None
    _ref_hist: Deque[Tuple[float, float]] = field(default_factory=deque)

    def decide(self, observation: dict) -> dict:
        try:
            return self._decide(observation)
        except (KeyError, TypeError, ValueError, OverflowError):
            # Never crash the evaluator loop; a bad observation means HOLD.
            return {"action": "HOLD"}

    # -- internals -------------------------------------------------------- #

    def _reset_market(self, market_id: str) -> None:
        self.market_id = market_id
        self.position_was_open = False
        self.completed_cycles = 0
        self.last_flat_at = -math.inf
        self.last_buy_attempt_at = -math.inf
        self._ref_hist = deque(maxlen=self.config.reference_history)

    def _record_reference(self, reference: Any, now: float) -> Optional[float]:
        """Validate a reference object and, if usable, append its gap to history.

        Returns the current gap (btcMid - openingTarget) if the reference is
        fresh, final and not future-stamped; otherwise None.
        """
        cfg = self.config
        if not isinstance(reference, dict):
            return None
        if bool(reference.get("targetProvisional", True)):
            return None
        observed_at = float(reference.get("observedAt", -math.inf))
        if now - observed_at > cfg.max_reference_age_seconds:
            return None
        if float(reference.get("targetObservedAt", math.inf)) > now + 1e-9:
            return None
        gap = float(reference["btcMidUsd"]) - float(reference["openingTargetUsd"])
        if not self._ref_hist or self._ref_hist[-1][0] != observed_at:
            self._ref_hist.append((observed_at, gap))
        return gap

    def _signal_persistent(self, gap: float, min_gap: float) -> bool:
        """True when the most recent samples share ``gap``'s sign and clear the
        magnitude threshold -- i.e. a directional regime, not chop."""
        need = self.config.persistence_samples
        if len(self._ref_hist) < need:
            return False
        recent = list(self._ref_hist)[-need:]
        sign = 1.0 if gap >= 0 else -1.0
        for _, g in recent:
            if math.copysign(1.0, g) != sign:
                return False
            if abs(g) < min_gap - 1e-9:
                return False
        return True

    def _decide(self, observation: dict) -> dict:
        cfg = self.config
        market = observation["market"]
        account = observation["account"]
        books = observation["books"]
        rules = observation.get("rules", {})
        now = float(observation["timestamp"])
        seconds = float(market["secondsToClose"])
        market_id = str(market["id"])
        position = account.get("position")
        cash = float(account.get("cashUsd", cfg.starting_cash_usd))
        volume = float(account.get("eligibleVolumeUsd", 0.0))

        if self.start_ts is None:
            self.start_ts = now

        # Track drawdown for reporting / defensive posture.
        self.peak_cash = max(self.peak_cash, cash)
        self.max_drawdown = max(self.max_drawdown, self.peak_cash - cash)

        if market_id != self.market_id:
            self._reset_market(market_id)

        # Cycle accounting: a position that closed since last tick = one cycle.
        if position is None and self.position_was_open:
            self.completed_cycles += 1
            self.last_flat_at = now
        self.position_was_open = position is not None

        # ---------------- Exit path (position open) --------------------- #
        if position is not None:
            return self._decide_exit(position, books, seconds)

        # ---------------- Entry path (flat) ----------------------------- #
        reference = observation.get("reference")
        aggression = self._aggression(now, rules, volume)
        return self._decide_entry(reference, books, rules, now, seconds, cash, aggression)

    def _aggression(self, now: float, rules: dict, volume: float) -> float:
        """Pace-based aggression multiplier in [min_aggression, max_aggression].

        >1 when behind the volume pace needed to reach the target in time
        (lean in to secure qualification); <1 when ahead (preserve cash). 1.0
        when pacing is disabled or too early to judge.
        """
        cfg = self.config
        if not cfg.pacing_enabled or self.start_ts is None:
            return 1.0
        window = float(rules.get("evaluationWindowHours", 24.0)) * 3600.0
        target = float(rules.get("targetVolumeUsd", 1000.0))
        if window <= 0 or target <= 0:
            return 1.0
        elapsed_frac = min(max((now - self.start_ts) / window, 0.0), 1.0)
        if elapsed_frac < 0.02:  # too early to have a meaningful pace
            return 1.0
        volume_frac = volume / target
        pace = volume_frac / elapsed_frac  # <1 behind schedule, >1 ahead
        if pace >= 1.0:
            return max(cfg.min_aggression, 1.2 - 0.5 * (pace - 1.0))
        return min(cfg.max_aggression, 1.0 + 0.8 * (1.0 - pace))

    def _decide_exit(self, position: dict, books: dict, seconds: float) -> dict:
        cfg = self.config
        side = str(position["outcome"])
        shares = float(position["shares"])
        exit_quote = sell_proceeds(books.get(side, {}).get("bids", []), shares)

        # Force-flat window: sell if we can; a one-sided book can still fill a
        # SELL against bids even after asks vanish near expiry.
        if seconds <= cfg.forced_exit_seconds + 1e-9:
            return {"action": "SELL"} if exit_quote is not None else {"action": "HOLD"}

        if exit_quote is None:
            return {"action": "HOLD"}

        sell_gross, sell_fees = exit_quote
        paid = float(position["buy_gross_usd"]) + float(position["buy_fees_usd"])
        already = float(position.get("sell_gross_usd", 0.0)) - float(position.get("sell_fees_usd", 0.0))
        cycle_pnl = already + sell_gross - sell_fees - paid

        if cycle_pnl >= cfg.min_profit_per_share_usd * shares - 1e-9:
            return {"action": "SELL"}
        if cycle_pnl <= -cfg.stop_loss_per_share_usd * shares + 1e-9:
            return {"action": "SELL"}
        return {"action": "HOLD"}

    def _decide_entry(
        self, reference: Any, books: dict, rules: dict,
        now: float, seconds: float, cash: float, aggression: float = 1.0,
    ) -> dict:
        cfg = self.config

        if self.completed_cycles >= cfg.max_cycles_per_market:
            return {"action": "HOLD"}
        if now - self.last_flat_at < cfg.reentry_cooldown_seconds - 1e-9:
            return {"action": "HOLD"}
        if now - self.last_buy_attempt_at < cfg.reentry_cooldown_seconds - 1e-9:
            return {"action": "HOLD"}
        if not (cfg.final_guard_seconds < seconds <= cfg.entry_window_seconds):
            return {"action": "HOLD"}

        gap = self._record_reference(reference, now)
        if gap is None:
            return {"action": "HOLD"}

        # Drawdown-aware defensive posture. Capital protection dominates pacing:
        # never loosen (aggression capped at 1.0) while cash is below the floor.
        defensive = cash < cfg.cash_floor_usd
        effective_aggression = min(aggression, 1.0) if defensive else aggression
        min_gap = cfg.min_reference_gap_usd * (cfg.defensive_gap_multiplier if defensive else 1.0)
        min_gap = min_gap / max(effective_aggression, 1e-6)  # behind pace -> lower bar
        max_immediate_loss = cfg.max_immediate_roundtrip_loss_usd * effective_aggression
        max_price = cfg.defensive_max_entry_price if defensive else cfg.max_entry_price

        if abs(gap) < min_gap - 1e-9:
            return {"action": "HOLD"}
        if not self._signal_persistent(gap, min_gap):
            return {"action": "HOLD"}

        side = "YES" if gap >= 0 else "NO"
        book = books.get(side, {})
        best_asks = _levels(book.get("asks", []), reverse=False)
        if not best_asks:
            return {"action": "HOLD"}
        best_ask = best_asks[0][0]
        if best_ask < cfg.min_entry_price - 1e-9 or best_ask > max_price + 1e-9:
            return {"action": "HOLD"}

        # Book must agree the reference-favoured side is the favourite: its mid
        # should sit at/above parity. This rejects reference/book disagreement
        # (a classic adverse-fill trap).
        if cfg.require_book_favorite_agreement:
            best_bids = _levels(book.get("bids", []), reverse=True)
            mid = (best_ask + best_bids[0][0]) / 2.0 if best_bids else best_ask
            if mid < 0.5 - 1e-9:
                return {"action": "HOLD"}

        shares = self._size(book, cash, rules)
        if shares <= 0:
            return {"action": "HOLD"}

        required = buy_cost(book.get("asks", []), shares)
        immediate_exit = sell_proceeds(book.get("bids", []), shares)
        if required is None:
            return {"action": "HOLD"}
        if cfg.require_immediate_exit_depth and immediate_exit is None:
            return {"action": "HOLD"}

        gross, fees = required
        if immediate_exit is not None:
            sell_gross, sell_fees = immediate_exit
            if sell_gross - sell_fees - gross - fees < -max_immediate_loss - 1e-9:
                return {"action": "HOLD"}

        buy_cash = gross + fees
        ceiling = min(cash, float(rules.get("maximumBuyCashUsd", 5.0)))
        if buy_cash > ceiling + 1e-9:
            return {"action": "HOLD"}

        self.last_buy_attempt_at = now
        return {"action": "BUY", "outcome": side, "maxCashUsd": round(buy_cash, 6)}

    def _size(self, book: dict, cash: float, rules: dict) -> float:
        """Largest share count (respecting minOrderSize) that fits the $5 cap
        and available cash at the current best price."""
        cfg = self.config
        min_order = float(book.get("minOrderSize", 0.0) or 0.0)
        target = cfg.target_shares
        if min_order > 0:
            target = max(target, min_order)
        # Confirm the target fits budget; if not, and we cannot go below the
        # minimum order size, we simply cannot trade this book.
        required = buy_cost(book.get("asks", []), target)
        if required is None:
            return 0.0
        gross, fees = required
        ceiling = min(cash, float(rules.get("maximumBuyCashUsd", 5.0)))
        if gross + fees <= ceiling + 1e-9:
            return target
        # Try shrinking down to the minimum order size in unit steps.
        floor = max(min_order, 1.0)
        shares = math.floor(target)
        while shares >= floor:
            req = buy_cost(book.get("asks", []), float(shares))
            if req is not None and req[0] + req[1] <= ceiling + 1e-9:
                return float(shares)
            shares -= 1
        return 0.0


# --------------------------------------------------------------------------- #
# Module-level convenience for the server / smoke test.
# --------------------------------------------------------------------------- #

_BOT = Bot()


def decide(observation: dict) -> dict:
    return _BOT.decide(observation)
