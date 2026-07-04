"""
brain/research/nq_param_sweep.py

Systematic grid search over 7 untested NQ ORB parameters.
IS: 2024 (in-sample optimization)  |  OOS: 2025-Jun 2026 (validation)

What's tested here that prior scripts did NOT cover:
  1. SIGNAL_STRENGTH_MIN_SCORE   — currently 60; test 50-80
  2. BREAKOUT_MAX_OR_VOLUME_RATIO — currently off; test ceiling filters
  3. GAP_EXCLUDE_MAX              — currently off; test gap dead-zone filters
  4. SECOND_BREAKOUT_MIN_TIME     — currently 10:00; test 10:15 / 10:30
  5. ORB_FUNDED_RR_TARGET         — currently 3.0; test 2.0-4.0
  6. FLATTEN_TIME                 — currently 15:55; test earlier exits
  7. July / August classification — currently neutral; test adding to weak

Prior research (v9_optimization_test.py) already tested and CLOSED:
  Friday skip, ATR floors, week-2 skip, score-window 70-79, Asia 2c

Baseline (v10.1): IS 2024 / OOS 2025-26 fresh bankrolls, 1c until $1,500 gate
Usage:
    cd /Users/Cruz/Desktop/nq_bot_final-main
    python3 brain/research/nq_param_sweep.py

NOTE (2026-07-03): regime-ATR warmup transplant fixed to a bounded 14-day
deque (was list(...) -> unbounded -> expanding mean; ref param_stability.py,
found by the parameter-stability audit). Results produced by this script
BEFORE this fix used the buggy expanding-mean regime gate - re-run before
citing absolute numbers (live-params morning-ORB effect: OOS N 52->64,
PF 2.84->2.35).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from collections import deque
import backtest as bt_mod
from backtest import Backtester, load_csv
from datetime import date, time

DATA = "data/nq_full.csv"

# ── period definitions ──────────────────────────────────────────────────────
IS_YEARS  = [2024]
OOS_YEARS = [2025, 2026]    # 2026 goes to Jun 28 (data limit)


# ── core runner ─────────────────────────────────────────────────────────────

def _run_year(bars, year):
    ystart = date(year, 1, 1)
    yend   = date(year + 1, 1, 1)
    prior  = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return []
    warmup = Backtester()
    warmup.run(prior, silent=True)
    bt = Backtester()
    bt._last_close         = warmup._last_close
    bt.regime.daily_ranges = deque(warmup.regime.daily_ranges, maxlen=config.REGIME_ATR_PERIOD)
    bt.or_volume_history   = list(warmup.or_volume_history)
    bt.prev_day_mode       = warmup.prev_day_mode
    bt.run(subset, silent=True)
    return bt.bank.trade_log


def run_years(bars, years):
    trades = []
    for y in years:
        trades.extend(_run_year(bars, y))
    return trades


# ── stats ────────────────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0}
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    return {
        "n":   len(trades),
        "net": round(sum(t["pnl"] for t in trades), 0),
        "wr":  len(wins) / len(trades),
        "pf":  round(gw / gl, 3) if gl else 99.0,
    }


# ── print helpers ────────────────────────────────────────────────────────────

def hdr(title):
    print(f"\n{'='*90}")
    print(f"  {title}")
    print(f"{'='*90}")
    print(f"  {'Param value':<30}  IS-N  IS-PF  IS-Net       OOS-N  OOS-PF  OOS-Net     ΔPF")
    print(f"  {'-'*88}")


def row(label, is_s, oos_s, base_oos_pf):
    dpf = oos_s["pf"] - base_oos_pf
    flag = "  ← KEEP" if dpf > 0.05 and oos_s["pf"] > base_oos_pf else (
           "  ~ flat"  if abs(dpf) <= 0.05 else "")
    print(f"  {label:<30}  {is_s['n']:>4}  {is_s['pf']:.3f}  ${is_s['net']:>+9,.0f}  "
          f"  {oos_s['n']:>4}  {oos_s['pf']:.3f}  ${oos_s['net']:>+9,.0f}  "
          f"{dpf:+.3f}{flag}")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\nLoading {DATA}...")
    bars = load_csv(DATA)
    print(f"  {len(bars):,} bars loaded")

    # ── baseline ─────────────────────────────────────────────────────────────
    print("\n  Computing baseline (current config)...", end=" ", flush=True)
    base_is  = stats(run_years(bars, IS_YEARS))
    base_oos = stats(run_years(bars, OOS_YEARS))
    print(f"IS={base_is['n']}T/{base_is['pf']:.3f}PF  OOS={base_oos['n']}T/{base_oos['pf']:.3f}PF")
    BASE_OOS_PF = base_oos["pf"]

    # ═════════════════════════════════════════════════════════════════════════
    # 1. SIGNAL_STRENGTH_MIN_SCORE
    # ═════════════════════════════════════════════════════════════════════════
    hdr("1. SIGNAL_STRENGTH_MIN_SCORE  (current: 60)")
    orig_min  = config.SIGNAL_STRENGTH_MIN_SCORE
    orig_high = config.SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP

    for score in [50, 55, 60, 65, 70, 75, 80]:
        config.SIGNAL_STRENGTH_MIN_SCORE = score
        config.SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP = score
        label = f"min_score={score}" + (" (baseline)" if score == 60 else "")
        is_s  = stats(run_years(bars, IS_YEARS))
        oos_s = stats(run_years(bars, OOS_YEARS))
        row(label, is_s, oos_s, BASE_OOS_PF)

    config.SIGNAL_STRENGTH_MIN_SCORE = orig_min
    config.SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP = orig_high

    # ═════════════════════════════════════════════════════════════════════════
    # 2. BREAKOUT_MAX_OR_VOLUME_RATIO  (volume ceiling filter)
    # ═════════════════════════════════════════════════════════════════════════
    hdr("2. BREAKOUT_MAX_OR_VOLUME_RATIO  (current: 0.0=off)")
    orig_vol = config.BREAKOUT_MAX_OR_VOLUME_RATIO

    for ratio in [0.0, 1.2, 1.5, 2.0, 2.5, 3.0]:
        config.BREAKOUT_MAX_OR_VOLUME_RATIO = ratio
        label = f"max_vol_ratio={ratio:.1f}" + (" (baseline/off)" if ratio == 0.0 else "")
        is_s  = stats(run_years(bars, IS_YEARS))
        oos_s = stats(run_years(bars, OOS_YEARS))
        row(label, is_s, oos_s, BASE_OOS_PF)

    config.BREAKOUT_MAX_OR_VOLUME_RATIO = orig_vol

    # ═════════════════════════════════════════════════════════════════════════
    # 3. GAP_EXCLUDE_MAX  (gap dead-zone ceiling)
    # Strategy: gaps in range (GAP_FILTER_POINTS, GAP_EXCLUDE_MAX] treated neutral
    # GAP_FILTER_POINTS=20 (current) → dead zone starts at 20pt
    # ═════════════════════════════════════════════════════════════════════════
    hdr("3. GAP_EXCLUDE_MAX  (dead zone: 20-MAX pt; current: 0=off)")
    orig_gex = config.GAP_EXCLUDE_MAX

    for max_gap in [0.0, 40.0, 60.0, 80.0, 100.0]:
        config.GAP_EXCLUDE_MAX = max_gap
        label = (f"gap_dead_zone=off (baseline)" if max_gap == 0.0 else
                 f"gap_dead_zone=20-{max_gap:.0f}pt")
        is_s  = stats(run_years(bars, IS_YEARS))
        oos_s = stats(run_years(bars, OOS_YEARS))
        row(label, is_s, oos_s, BASE_OOS_PF)

    config.GAP_EXCLUDE_MAX = orig_gex

    # Also test raising GAP_FILTER_POINTS so the dead zone starts higher
    hdr("3b. Higher GAP_FILTER_POINTS (raise min gap threshold; current: 20pt)")
    orig_gfp = config.GAP_FILTER_POINTS

    for gfp in [20.0, 30.0, 40.0, 50.0, 60.0]:
        config.GAP_FILTER_POINTS = gfp
        label = f"gap_filter_pts={gfp:.0f}" + (" (baseline)" if gfp == 20.0 else "")
        is_s  = stats(run_years(bars, IS_YEARS))
        oos_s = stats(run_years(bars, OOS_YEARS))
        row(label, is_s, oos_s, BASE_OOS_PF)

    config.GAP_FILTER_POINTS = orig_gfp

    # ═════════════════════════════════════════════════════════════════════════
    # 4. SECOND_BREAKOUT_MIN_TIME  (module-level constant — patch bt_mod directly)
    # ═════════════════════════════════════════════════════════════════════════
    hdr("4. SECOND_BREAKOUT_MIN_TIME  (current: 10:00)")
    orig_sbt = bt_mod.SECOND_BREAKOUT_AFTER

    for h, m in [(9, 45), (10, 0), (10, 15), (10, 30), (11, 0)]:
        bt_mod.SECOND_BREAKOUT_AFTER = time(h, m)
        label = f"2nd_bo_min={h:02d}:{m:02d}" + (" (baseline)" if (h,m) == (10,0) else "")
        is_s  = stats(run_years(bars, IS_YEARS))
        oos_s = stats(run_years(bars, OOS_YEARS))
        row(label, is_s, oos_s, BASE_OOS_PF)

    bt_mod.SECOND_BREAKOUT_AFTER = orig_sbt

    # ═════════════════════════════════════════════════════════════════════════
    # 5. ORB_FUNDED_RR_TARGET
    # ═════════════════════════════════════════════════════════════════════════
    hdr("5. ORB_FUNDED_RR_TARGET  (current: 3.0)")
    orig_rr = config.ORB_FUNDED_RR_TARGET

    for rr in [2.0, 2.5, 3.0, 3.5, 4.0]:
        config.ORB_FUNDED_RR_TARGET = rr
        label = f"funded_rr={rr:.1f}" + (" (baseline)" if rr == 3.0 else "")
        is_s  = stats(run_years(bars, IS_YEARS))
        oos_s = stats(run_years(bars, OOS_YEARS))
        row(label, is_s, oos_s, BASE_OOS_PF)

    config.ORB_FUNDED_RR_TARGET = orig_rr

    # ═════════════════════════════════════════════════════════════════════════
    # 6. FLATTEN_TIME  (module-level constant — patch bt_mod directly)
    # ═════════════════════════════════════════════════════════════════════════
    hdr("6. FLATTEN_TIME  (current: 15:55)")
    orig_fl = bt_mod.FLATTEN

    for h, m in [(15, 0), (15, 15), (15, 30), (15, 45), (15, 55)]:
        bt_mod.FLATTEN = time(h, m)
        label = f"flatten={h:02d}:{m:02d}" + (" (baseline)" if (h,m) == (15,55) else "")
        is_s  = stats(run_years(bars, IS_YEARS))
        oos_s = stats(run_years(bars, OOS_YEARS))
        row(label, is_s, oos_s, BASE_OOS_PF)

    bt_mod.FLATTEN = orig_fl

    # ═════════════════════════════════════════════════════════════════════════
    # 7. July / August classification  (currently neutral)
    # ═════════════════════════════════════════════════════════════════════════
    hdr("7. July/August month classification  (current: neutral)")
    orig_strong = list(config.STRONG_MONTHS)
    orig_weak   = list(config.WEAK_MONTHS)

    scenarios = [
        ("neutral (baseline)",     [1,2,3,4,5,10,11],      [6,9,12]),
        ("Jul=weak",               [1,2,3,4,5,10,11],      [6,7,9,12]),
        ("Aug=weak",               [1,2,3,4,5,10,11],      [6,8,9,12]),
        ("Jul+Aug=weak",           [1,2,3,4,5,10,11],      [6,7,8,9,12]),
        ("Jul=strong",             [1,2,3,4,5,7,10,11],    [6,9,12]),
        ("Aug=strong",             [1,2,3,4,5,8,10,11],    [6,9,12]),
        ("Jul+Aug=strong",         [1,2,3,4,5,7,8,10,11],  [6,9,12]),
    ]

    for label, strong, weak in scenarios:
        config.STRONG_MONTHS = strong
        config.WEAK_MONTHS   = weak
        is_s  = stats(run_years(bars, IS_YEARS))
        oos_s = stats(run_years(bars, OOS_YEARS))
        row(label, is_s, oos_s, BASE_OOS_PF)

    config.STRONG_MONTHS = orig_strong
    config.WEAK_MONTHS   = orig_weak

    # ═════════════════════════════════════════════════════════════════════════
    # SUMMARY — baseline reference
    # ═════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*90}")
    print(f"  SUMMARY")
    print(f"{'='*90}")
    print(f"  Baseline IS 2024:    T={base_is['n']}  WR={base_is['wr']:.1%}  PF={base_is['pf']:.3f}  Net=${base_is['net']:+,.0f}")
    print(f"  Baseline OOS 25-26:  T={base_oos['n']}  WR={base_oos['wr']:.1%}  PF={base_oos['pf']:.3f}  Net=${base_oos['net']:+,.0f}")
    print(f"\n  Look for OOS PF improvement > 0.05 AND net improvement to keep.")
    print(f"  Only combine improvements that are INDEPENDENT (don't overlap in mechanism).")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
