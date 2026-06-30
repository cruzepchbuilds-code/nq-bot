"""
Signal Strength Scorer  -- 0 to 100 composite score.
Derived from exhaustive research on 812 breakout signals (2024-2026).

Scores < 60 are filtered out entirely.
Contract sizing: 60-74 -> 1 contract, 75-89 -> 2, 90+ -> 3 (capped by regime calendar)

NOTE: OOS research shows score 60-69 has higher WR than 70-89, suggesting the
component weights are miscalibrated. Simple inversion of contract allocation was
tested and REJECTED (4-yr OOS: -$14,775 vs baseline) because giving 2c upfront
blocks the pyramid add-on, converting a low-loss 1c+pyramid setup into a high-loss
2c-no-pyramid one. Component recalibration (vol direction flip) was also tested
(2026-06-29) and REJECTED: PF 2.30->1.93, OOS net -$15k vs baseline. The bucket
WR data does not translate cleanly to component-level weights without per-component
OOS isolation testing. Do not modify without running stop_size_test.py first.
"""
from datetime import time as dt_time


def score_signal(entry_time, gap_aligned_with_direction, vol_ratio, or_size,
                 prev_day_breakout):
    """
    entry_time                  : datetime.time  -- bar's local (ET) time
    gap_aligned_with_direction  : int  +1=aligned, -1=against, 0=neutral
    vol_ratio                   : float  today's OR volume / 20-day avg OR volume
    or_size                     : float  opening range in NQ points
    prev_day_breakout           : bool   did yesterday classify as a breakout day?

    Returns int 0-100.
    """
    score = 0

    # -- Time window (20 pts max) -----------------------------------------------
    # Research: 10:15-10:45 PF 1.32 >> 09:45-10:15 PF 1.13, 10:45-11:15 PF 1.21
    # After 11:15 WR collapses to 18.8% / PF 0.48 -- no points awarded.
    t = entry_time
    if dt_time(10, 15) <= t < dt_time(10, 45):
        score += 20   # best window
    elif dt_time(9, 45) <= t < dt_time(10, 15):
        score += 14   # second best
    elif dt_time(10, 45) <= t < dt_time(11, 15):
        score += 12   # acceptable
    # 11:15+: 0 pts -- PF 0.48, WR 18.8%

    # -- Gap alignment (25 pts max) ---------------------------------------------
    # Research: aligned PF 1.16, neutral PF 0.82, against PF 1.00
    if gap_aligned_with_direction > 0:
        score += 25   # gap confirms trade direction
    elif gap_aligned_with_direction == 0:
        score += 10   # neutral gap (no signal gap)
    # against: 0 pts

    # -- OR volume ratio (25 pts max) -------------------------------------------
    # Research: high volume (1.2-1.8x) PF 1.12; avg (0.8-1.2x) PF 1.12;
    #           low (<0.8x) PF 1.00; spike (>1.8x) noisy -- 6 samples.
    if 1.2 <= vol_ratio <= 1.8:
        score += 25
    elif 0.8 <= vol_ratio < 1.2:
        score += 15
    elif vol_ratio > 1.8:
        score += 8    # spike can be noise
    # low: 0 pts

    # -- Opening range size (20 pts max) ----------------------------------------
    # Thresholds are instrument-specific; read from config so ES can scale them.
    import config as _cfg
    _t = getattr(_cfg, 'OR_SIZE_SCORE_BOUNDS', (62.0, 86.0, 120.0))
    if _t[0] <= or_size <= _t[1]:
        score += 20
    elif _t[1] < or_size <= _t[2]:
        score += 12
    elif or_size > _t[2]:
        score += 4
    # below lower bound: 0 pts

    # -- Regime continuation (10 pts) -------------------------------------------
    # Previous day classified as a breakout day increases confidence
    if prev_day_breakout:
        score += 10

    return min(score, 100)


def contracts_for_score(score, max_contracts=2):
    """Return contract count based on signal strength score.
    Returns 0 if score is below the minimum threshold (skip the trade)."""
    if score >= 90:
        return min(3, max_contracts)
    elif score >= 75:
        return min(2, max_contracts)
    elif score >= 60:
        return 1
    return 0  # below threshold -- skip
