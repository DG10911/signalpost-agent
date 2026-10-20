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


def fair_prob(btc: float, target: float, seconds_left: float, annual_vol: float) -> float:
    """P(close > target) for a lognormal BTC walk -- the value of a YES share.

    Used by the optional edge gate to price a side from the *reference feed*
    (btcMid vs openingTarget) rather than from the stale order book, so the bot
    can enter the side the book has not yet repriced to.
    """
    if seconds_left <= 0.0:
        return 1.0 if btc > target else 0.0
    years = seconds_left / (365.0 * 24.0 * 3600.0)
    sigma = annual_vol * math.sqrt(max(years, 1e-12))
    if sigma <= 1e-12:
        return 1.0 if btc > target else 0.0
    d = (math.log(btc / target) - 0.5 * sigma * sigma) / sigma
    return 0.5 * (1.0 + math.erf(d / math.sqrt(2.0)))


# --------------------------------------------------------------------------- #
# Defensive readers for the evaluator-owned position object (schema unverified).
# --------------------------------------------------------------------------- #

_SIDE_KEYS = ("outcome", "side", "direction", "result", "asset", "token", "instrument")
_SHARE_KEYS = ("shares", "size", "quantity", "qty", "amount", "positionShares",
               "position_shares", "positionSize", "sharesHeld")


def _pos_side(position: Any) -> Optional[str]:
    if not isinstance(position, dict):
        return None
    for key in _SIDE_KEYS:
        val = position.get(key)
        if isinstance(val, str):
            up = val.upper()
            if up == "YES" or "YES" in up:
                return "YES"
            if up == "NO" or "NO" in up:
                if up == "NO" or up.endswith("NO") or "NO" in up.split():
                    return "NO"
    # scan string values for a YES/NO token
    for val in position.values():
        if isinstance(val, str):
            up = val.upper()
            if up == "YES":
                return "YES"
            if up == "NO":
                return "NO"
    return None


def _pos_shares(position: Any) -> Optional[float]:
    if not isinstance(position, dict):
        return None
    for key in _SHARE_KEYS:
        val = position.get(key)
        if val is not None:
            try:
                f = float(val)
                if f > 0.0:
                    return f
            except (TypeError, ValueError):
                continue
    return None


def _pos_paid(position: Any) -> Optional[float]:
    """Fee-inclusive entry cost of the open position, if the position carries it."""
    if not isinstance(position, dict):
        return None
    gross = None
    for key in ("buy_gross_usd", "cost_usd", "costUsd", "entry_cost_usd",
                "entryCostUsd", "cost_basis_usd", "notional_usd"):
        if position.get(key) is not None:
            try:
                gross = float(position[key])
                break
            except (TypeError, ValueError):
                continue
    if gross is None:
        return None
    fees = 0.0
    for key in ("buy_fees_usd", "fees_usd", "feesUsd", "entry_fees_usd"):
        if position.get(key) is not None:
            try:
                fees = float(position[key])
            except (TypeError, ValueError):
                fees = 0.0
            break
    return gross + fees


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

    # Optional edge/value gate (off by default; frozen config may enable it).
    # Prices a side from the reference feed via a lognormal fair value and only
    # enters when the book ask is *below* that fair value by min_edge, i.e. there
    # is a positive expected edge to capture as the book reprices.
    use_edge_gate: bool = False
    assumed_annual_vol: float = 0.6
    min_edge_per_share_usd: float = 0.0

    # Size every BUY to the fee-inclusive $5 cap (minus a small buffer) instead of
    # a fixed share count: a cycle then carries ~$9.8 of volume rather than ~$6.9,
    # so fewer cycles are needed to reach $1,000.
    size_to_cap: bool = False
    cap_buffer_usd: float = 0.01

    # Once the volume target is met and we are flat, stop trading forever so the
    # cash at the crossing is locked (ranking is cash at that first clean cross).
    lock_after_target: bool = True

    # Execution-quality gates (from the strongest public entries): reject wide
    # spreads, require a real profit target rather than exiting at break-even,
    # exit when the reference thesis flips, and stop trading a market after a
    # loss so one bad regime cannot compound.
    max_spread: float = 0.03
    profit_floor_usd: float = 0.20
    exit_on_thesis_flip: bool = True
    abort_market_after_loss: bool = True

    # Self-calibration: estimate realised BTC vol online from the reference feed
    # and price the side with it, instead of a fixed assumption (robust across
    # quiet/volatile regimes).
    adaptive_vol: bool = False
    vol_window_seconds: float = 600.0
    min_vol_samples: int = 8
    vol_floor: float = 0.25
    vol_cap: float = 3.0

    # Cost-aware edge: require the expected edge to cover the *actual* live
    # round-trip cost (both fee layers + the quoted spread) plus a safety margin,
    # rather than a fixed edge floor.
    cost_aware_edge: bool = False
    edge_safety_margin: float = 0.0

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
    _price_hist: Deque[Tuple[float, float]] = field(default_factory=deque)
    entry_cost: Optional[float] = None
    loss_in_market: bool = False

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
        self.loss_in_market = False
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
        btc = float(reference["btcMidUsd"])
        if not self._price_hist or self._price_hist[-1][0] != observed_at:
            self._price_hist.append((observed_at, btc))
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

    @staticmethod
    def _now(observation: dict) -> float:
        """Derive 'now' from the observation without assuming a top-level
        ``timestamp`` field (the official request schema does not guarantee one).

        Preference order: top-level ``timestamp`` -> ``reference.observedAt``
        (a receive-time stamp that is always present) -> 0.0.
        """
        for candidate in (observation.get("timestamp"),
                          (observation.get("reference") or {}).get("observedAt")
                          if isinstance(observation.get("reference"), dict) else None):
            if candidate is not None:
                try:
                    return float(candidate)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _decide(self, observation: dict) -> dict:
        cfg = self.config
        market = observation["market"]
        account = observation["account"]
        books = observation["books"]
        rules = observation.get("rules", {})
        now = self._now(observation)
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
            self.entry_cost = None
        self.position_was_open = position is not None

        # Clean-crossing lock: once the volume target is reached and we are flat,
        # never trade again — the ranking metric is cash at that first clean
        # crossing, so further trading can only risk it.
        reference = observation.get("reference")
        target_volume = float(rules.get("targetVolumeUsd", 1000.0))
        if cfg.lock_after_target and position is None and volume >= target_volume - 1e-9:
            return {"action": "HOLD"}

        # ---------------- Exit path (position open) --------------------- #
        if position is not None:
            current_gap = None
            if isinstance(reference, dict):
                try:
                    current_gap = float(reference["btcMidUsd"]) - float(reference["openingTargetUsd"])
                except (KeyError, TypeError, ValueError):
                    current_gap = None
            return self._decide_exit(position, books, seconds, current_gap)

        # ---------------- Entry path (flat) ----------------------------- #
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

    def _decide_exit(self, position: dict, books: dict, seconds: float,
                     current_gap: Optional[float] = None) -> dict:
        cfg = self.config
        # The evaluator owns the position object and its field names are not
        # documented, so read them defensively (never crash -> never fail flat).
        side = _pos_side(position)
        shares = _pos_shares(position)
        if side is None or shares is None or shares <= 0.0:
            return {"action": "HOLD"}
        exit_quote = sell_proceeds(books.get(side, {}).get("bids", []), shares)

        # Force-flat window: sell if we can; a one-sided book can still fill a
        # SELL against bids even after asks vanish near expiry.
        if seconds <= cfg.forced_exit_seconds + 1e-9:
            return {"action": "SELL"} if exit_quote is not None else {"action": "HOLD"}

        if exit_quote is None:
            return {"action": "HOLD"}

        sell_gross, sell_fees = exit_quote
        # Prefer the evaluator's cost basis if present; otherwise fall back to the
        # entry cost we recorded when we issued the BUY. Never KeyError.
        paid = _pos_paid(position)
        if paid is None:
            paid = self.entry_cost if self.entry_cost is not None else 0.0
        already = float(position.get("sell_gross_usd", 0.0)) - float(position.get("sell_fees_usd", 0.0))
        cycle_pnl = already + sell_gross - sell_fees - paid

        target_profit = cfg.min_profit_per_share_usd * shares
        if cfg.profit_floor_usd > 0.0:
            target_profit = max(target_profit, cfg.profit_floor_usd)
        if cycle_pnl >= target_profit - 1e-9:
            return {"action": "SELL"}
        if cfg.exit_on_thesis_flip and current_gap is not None:
            if side == "YES" and current_gap < 0.0 - 1e-9:
                self.loss_in_market = True
                return {"action": "SELL"}
            if side == "NO" and current_gap > 0.0 + 1e-9:
                self.loss_in_market = True
                return {"action": "SELL"}
        if cycle_pnl <= -cfg.stop_loss_per_share_usd * shares + 1e-9:
            self.loss_in_market = True
            return {"action": "SELL"}
        return {"action": "HOLD"}

    def _decide_entry(
        self, reference: Any, books: dict, rules: dict,
        now: float, seconds: float, cash: float, aggression: float = 1.0,
    ) -> dict:
        cfg = self.config

        if self.completed_cycles >= cfg.max_cycles_per_market:
            return {"action": "HOLD"}
        if cfg.abort_market_after_loss and self.loss_in_market:
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
        max_immediate_loss = cfg.max_immediate_roundtrip_loss_usd * effective_aggression
        max_price = cfg.defensive_max_entry_price if defensive else cfg.max_entry_price

        if cfg.use_edge_gate:
            side = self._edge_side(reference, books, seconds, defensive)
            if side is None:
                return {"action": "HOLD"}
        else:
            min_gap = cfg.min_reference_gap_usd * (cfg.defensive_gap_multiplier if defensive else 1.0)
            min_gap = min_gap / max(effective_aggression, 1e-6)  # behind pace -> lower bar
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
        best_bids_for_spread = _levels(book.get("bids", []), reverse=True)
        if best_bids_for_spread and best_ask - best_bids_for_spread[0][0] > cfg.max_spread + 1e-9:
            return {"action": "HOLD"}

        # Book must agree the reference-favoured side is the favourite: its mid
        # should sit at/above parity. This rejects reference/book disagreement
        # (a classic adverse-fill trap). Skipped under the edge gate, which prices
        # the side from the reference instead of trusting the (stale) book.
        if cfg.require_book_favorite_agreement and not cfg.use_edge_gate:
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
        self.entry_cost = buy_cash
        return {"action": "BUY", "outcome": side, "maxCashUsd": round(buy_cash, 6)}

    def _realized_vol(self, now: float) -> Optional[float]:
        """Annualised realised vol of BTC from the reference price history.

        Uses per-second log-return variance over a rolling window; returns None
        until enough fresh samples exist, so the caller can fall back to the
        configured prior.
        """
        cfg = self.config
        while self._price_hist and now - self._price_hist[0][0] > cfg.vol_window_seconds:
            self._price_hist.popleft()
        hist = list(self._price_hist)
        if len(hist) < cfg.min_vol_samples:
            return None
        var_per_sec = 0.0
        n = 0
        for i in range(1, len(hist)):
            dt = hist[i][0] - hist[i - 1][0]
            if dt <= 0.0 or hist[i - 1][1] <= 0.0:
                continue
            r = math.log(hist[i][1] / hist[i - 1][1])
            var_per_sec += (r * r) / dt
            n += 1
        if n == 0:
            return None
        sigma = math.sqrt(var_per_sec / n) * math.sqrt(365.0 * 24.0 * 3600.0)
        return min(max(sigma, cfg.vol_floor), cfg.vol_cap)

    def _edge_side(self, reference: Any, books: dict, seconds: float,
                   defensive: bool) -> Optional[str]:
        """Return the side with positive expected edge, or None.

        The reference feed (btcMid vs openingTarget) gives the current fair
        probability of YES; the order book still reflects an older BTC level.
        Enter the side whose ask is below its fair value by at least the required
        edge (a fixed floor, or -- when ``cost_aware_edge`` -- the live
        round-trip cost). That gap is the edge captured when the book catches up.
        """
        cfg = self.config
        if not isinstance(reference, dict):
            return None
        try:
            btc = float(reference["btcMidUsd"])
            target = float(reference["openingTargetUsd"])
        except (KeyError, TypeError, ValueError):
            return None

        vol = cfg.assumed_annual_vol
        if cfg.adaptive_vol:
            est = self._realized_vol(float(reference.get("observedAt", 0.0)))
            if est is not None:
                vol = est
        fair = fair_prob(btc, target, seconds, vol)
        side = "YES" if fair >= 0.5 else "NO"
        p_fav = fair if side == "YES" else 1.0 - fair
        book = books.get(side, {})
        asks = _levels(book.get("asks", []), reverse=False)
        bids = _levels(book.get("bids", []), reverse=True)
        if not asks:
            return None
        ask = asks[0][0]

        if cfg.cost_aware_edge:
            bid = bids[0][0] if bids else ask
            fee_buy = ask * (0.01 + 0.07 * (1.0 - ask))
            fee_sell = bid * (0.01 + 0.07 * (1.0 - bid))
            required = (ask - bid) + fee_buy + fee_sell + cfg.edge_safety_margin
        else:
            required = cfg.min_edge_per_share_usd
        if defensive:
            required *= cfg.defensive_gap_multiplier

        if p_fav - ask < required - 1e-9:
            return None
        return side

    def _size(self, book: dict, cash: float, rules: dict) -> float:
        """Largest share count that fits the fee-inclusive BUY cap.

        In ``size_to_cap`` mode this fills the cap (full ticket); otherwise it
        targets ``target_shares`` and shrinks only if that would breach the cap.
        """
        cfg = self.config
        min_order = float(book.get("minOrderSize", 0.0) or 0.0)
        ceiling = min(cash, float(rules.get("maximumBuyCashUsd", 5.0)))
        asks = book.get("asks", [])

        if cfg.size_to_cap:
            ceiling -= max(0.0, cfg.cap_buffer_usd)
            levels = _levels(asks, reverse=False)
            if not levels or ceiling <= 0.0:
                return 0.0
            best_ask = levels[0][0]
            approx = int(ceiling / max(best_ask, 1e-9)) + 1
            for shares in range(approx, 0, -1):
                req = buy_cost(asks, float(shares))
                if req is not None and req[0] + req[1] <= ceiling + 1e-9:
                    if min_order and shares < min_order:
                        return 0.0
                    return float(shares)
            return 0.0

        target = cfg.target_shares
        if min_order > 0:
            target = max(target, min_order)
        required = buy_cost(asks, target)
        if required is None:
            return 0.0
        gross, fees = required
        if gross + fees <= ceiling + 1e-9:
            return target
        floor = max(min_order, 1.0)
        shares = math.floor(target)
        while shares >= floor:
            req = buy_cost(asks, float(shares))
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
