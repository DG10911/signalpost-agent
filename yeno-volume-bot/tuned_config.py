"""Frozen strategy for the served bot: v9 "contract" (strategy='v9').

Selected in a faithful replay of the published contract (research/realsim.py
--mode faithful): causal BTC, the bot called every second to the close, 250 ms
then the next later L2 update of the action's outcome, 2 s book-age rejection,
15 s entry block (SELLs allowed after), 5-share minimum, both fee layers,
partial exits, positions left at the close settle with no volume credit.
Selected on Oct 3-6 + Oct 17-26 2026 sliding 24 h windows; confirmed on held-out
Oct 27 - Nov 4 (real Polymarket BTC-5m L2 books).

Economics. The 1% overlay alone on $1,000 of volume is $10 = the whole bankroll,
and the books are calibrated near the top, so volume must be bought as cheaply
as possible and the bankroll must not stall:
  * FAV (volume engine): a clear favourite (causal TWAP model >= 0.90, ask
    0.85-0.98, spread <= 2c, any time before the 15 s block) bought with the
    full fee-inclusive ticket and sold into the deep 0.99 closing bid (or at a
    0.99 bid earlier) -- ~$9.8 of volume for ~1-1.5% cost. Exit early if the
    model probability for the position drops below 0.5 (while bids exist), so a
    reversal completes the cycle instead of becoming an unsellable loser.
  * Low-cash mode: below $5.50 a full favourite ticket no longer reliably fits,
    so an idle bot has already failed. It then takes the one measured gross edge
    -- inside the settlement minute the TWAP model prices a side >= 16c above its
    ask -- with 70% of cash: a loss ends a run that was over anyway, a win
    restores the engine (and a cheap winner brings large sell volume).
  * Locks after the first clean crossing (rank 1 = cash at that crossing).
"""
import dataclasses

from bot import Config

# v8.1 (previous submission), kept for reference and regression tests.
TUNED_V81 = Config(
    # Timing.
    entry_window_seconds=120.0,
    final_guard_seconds=16.0,
    forced_exit_seconds=10.0,
    entry_exit_gap_seconds=0.0,
    exit_on_market_change=True,
    sell_partial_when_thin=True,
    max_hold_seconds=30.0,

    # Entry signal: the TWAP model vs the stale book.
    use_edge_gate=True,
    cost_aware_edge=False,
    min_edge_per_share_usd=0.055,
    max_spread=0.02,
    min_reference_gap_usd=0.0,
    max_reference_age_seconds=1.5,
    persistence_samples=1,
    reference_history=8,
    require_book_favorite_agreement=False,
    min_entry_price=0.30,
    max_entry_price=0.85,

    # Settlement model: the last 60 s average (not spot at close).
    assumed_annual_vol=1.0,
    adaptive_vol=True,
    vol_window_seconds=600.0,
    min_vol_samples=60,
    vol_floor=0.25,
    vol_cap=3.0,
    vol_multiplier=1.0,
    settlement_twap_seconds=60.0,
    thesis_flip_prob=0.45,

    # Exit.
    take_profit_bid=0.95,
    profit_floor_usd=0.0,
    min_profit_per_share_usd=0.0,
    exit_on_thesis_flip=True,
    stop_loss_per_share_usd=0.35,
    abort_market_after_loss=False,

    # Sizing: 30% of cash, capped by the $5 fee-inclusive ceiling and the
    # exchange's 5-share minimum. A losing streak cannot wipe the bankroll.
    size_to_cap=True,
    size_fraction=0.30,
    low_cash_fraction=0.30,
    min_cash_usd=2.6,
    cap_buffer_usd=0.01,
    target_shares=6.0,
    max_cycles_per_market=4,
    reentry_cooldown_seconds=0.5,
    max_immediate_roundtrip_loss_usd=0.9,
    require_immediate_exit_depth=False,
    min_exit_bid_multiple=0.0,
    lock_after_target=True,

    # Capital protection is the cash-fraction sizing above; the old defensive
    # price cap and the pace-based loosening are disabled (organiser audit:
    # never lower the entry bar because volume is behind).
    starting_cash_usd=10.0,
    cash_floor_usd=0.0,
    defensive_max_entry_price=0.85,
    defensive_gap_multiplier=1.0,
    pacing_enabled=False,
    max_aggression=1.0,
    min_aggression=1.0,
)


TUNED = dataclasses.replace(
    TUNED_V81,
    strategy="v9",
    exact_twap_variance=True,        # pre-window TWAP spread sigma*sqrt(s - 2w/3)
    record_reference_always=True,    # vol / seen-average / derived target
    max_cycles_per_market=4,
    reentry_cooldown_seconds=0.5,
    # FAV volume engine.
    v9_fav_enabled=True,
    v9_fav_min_prob=0.90,
    v9_fav_min_ask=0.85,
    v9_fav_max_ask=0.98,             # 5 shares at 0.99 exceed the $5 fee-inclusive cap
    v9_fav_min_s=16.0,
    v9_fav_max_s=299.0,
    v9_fav_min_cash=0.0,
    v9_fav_stop_prob=0.5,
    v9_fav_stop_bid=0.0,
    v9_max_spread=0.02,
    v9_exit_seconds=2.0,
    v9_take_profit_bid=0.99,
    v9_buy_headroom=0.03,
    # VALUE only in low-cash mode.
    v9_value_enabled=False,
    v9_value_min_s=16.0,
    v9_value_max_s=58.0,
    v9_value_min_ask=0.15,
    v9_value_max_ask=0.85,
    v9_value_stop_prob=0.15,
    v9_low_cash_usd=5.5,
    v9_low_cash_min_edge=0.16,
    v9_low_cash_value_fraction=0.7,
    v9_derive_missing_target=True,
)
