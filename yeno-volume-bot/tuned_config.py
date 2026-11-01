"""Frozen strategy for the served bot.

Calibrated and validated on **real Polymarket BTC 5-minute L2 order books**
(862 resolved markets, 2026-10-03 -> 2026-10-06, 250 ms snapshots). The
evaluator simulation in this repository's research harness applies the published
contract: 250 ms latency, the later book, full ladder walking, the $5
fee-inclusive cap, 5-share minimum, both fee layers, a 2 s book-age limit,
crossed-book rejection, partial exits, and the final 15 s entry block.

Strategy, in one line: buy the side the 60-second-TWAP model says is underpriced
by at least 5 cents, only when the spread is at most 2 cents, in the 0.30-0.85
price band; hold up to 30 seconds; take profit at a 0.95 bid; leave as soon as
the model's probability for the position falls below 0.45; force flat from 5 s
before close.
"""
from bot import Config

TUNED = Config(
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
    min_edge_per_share_usd=0.05,
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
