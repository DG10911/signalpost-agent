"""Auto-generated: best measured config (reference-edge + adaptive vol + cost-aware edge)."""
from bot import Config

TUNED = Config(
    entry_window_seconds=150.0,
    final_guard_seconds=16.0,
    # v5: exit from 45 s (25 s was the latest of the active bots; an open
    # position at market change ends the replay).
    forced_exit_seconds=45.0,
    entry_exit_gap_seconds=5.0,
    exit_on_market_change=True,
    sell_partial_when_thin=True,
    min_exit_bid_multiple=1.0,
    min_reference_gap_usd=8.0,
    max_reference_age_seconds=1.5,
    persistence_samples=1,
    reference_history=8,
    require_book_favorite_agreement=True,
    use_edge_gate=True,
    assumed_annual_vol=1.0,
    min_edge_per_share_usd=0.02,
    max_spread=1.0,
    profit_floor_usd=0.2,
    exit_on_thesis_flip=True,
    abort_market_after_loss=True,
    size_to_cap=True,
    cap_buffer_usd=0.01,
    lock_after_target=True,
    adaptive_vol=True,
    vol_window_seconds=600.0,
    min_vol_samples=8,
    vol_floor=0.25,
    vol_cap=3.0,
    cost_aware_edge=True,
    # Measured on the real 12 h sample (8a66c5d: $459.38 vol, $13.00 fees,
    # 59 cycles, $10 -> $1.15): entries cleared model edge >= spread + fees,
    # yet realised only ~+$0.013/share before fees vs ~$0.042/share in fees.
    # So >= ~$0.029/share of modelled edge is lost to latency/adverse fills;
    # the margin must exceed that for an entry to keep cash after fees and exit.
    edge_safety_margin=0.045,
    margin_variance_scaled=True,
    margin_ref_price=0.74,
    vol_multiplier=1.2,
    target_shares=6.0,
    min_entry_price=0.4,
    max_entry_price=0.969,
    max_cycles_per_market=8,
    reentry_cooldown_seconds=0.5,
    max_immediate_roundtrip_loss_usd=0.9,
    require_immediate_exit_depth=True,
    min_profit_per_share_usd=0.0,
    stop_loss_per_share_usd=0.25,
    starting_cash_usd=10.0,
    cash_floor_usd=7.0,
    # Low cash should not force us into mid prices (highest fee fraction);
    # defensive mode keeps the x1.6 edge requirement instead.
    defensive_max_entry_price=0.969,
    defensive_gap_multiplier=1.6,
    pacing_enabled=True,
    # Never lower the entry bar because volume is behind pace (organizer audit,
    # 24 Oct): the real sample burned cash, so behind-pace stays at 1.0.
    max_aggression=1.0,
    min_aggression=0.7,
)

