"""
brain/research/fvg_filter.py

ICT Fair Value Gap (FVG) — ORB Confirmation Filter Research
IS: 2024  |  OOS: 2025-2026

What is a Fair Value Gap?
  A 3-bar imbalance where the middle bar moves so fast it leaves a price void:
    Bullish FVG: bars[i-1].high < bars[i+1].low   (unfilled zone above)
    Bearish FVG: bars[i-1].low  > bars[i+1].high  (unfilled zone below)
  These zones act as magnets (price returns to fill them) and resistance/support
  when approached from the other side.

Research question:
  At ORB entry time, if an unfilled FVG OPPOSES the trade direction
  (bearish FVG above a long, bullish FVG below a short), does that hurt
  outcomes vs neutral or aligned-FVG trades?

Trade classification at entry time:
  aligned — FVG exists as target/magnet in the trade direction
  opposed — FVG exists as resistance/support against the trade
  mixed   — both types present (rare)
  neutral — no qualifying FVG within max_distance

Parameters swept:
  min_fvg_size  : minimum price gap to qualify (NQ 8/16/24pt, ES 2/4/6pt)
  lookback_bars : how far back to scan (6h/12h/24h/48h worth of 1-min bars)
  max_distance  : max points from entry price for FVG to count (NQ 40/60/80pt)

Methodology:
  Same year-by-year fresh-bankroll as all other research.
  Window computed via index arithmetic to avoid naive/tz-aware datetime mixing.
  Entry lookup uses (date, hour, minute) index — O(1) per trade.
  Fill check scans bars between FVG creation and entry — O(w * n_fvg) per trade.

Usage:
    cd /Users/Cruz/Desktop/nq_bot_final-main
    python3 brain/research/fvg_filter.py            # full sweep, NQ + ES
    python3 brain/research/fvg_filter.py --quick    # IS/OOS only, NQ only
    python3 brain/research/fvg_filter.py --nq       # NQ only, full sweep
    python3 brain/research/fvg_filter.py --es       # ES only, full sweep

NOTE (2026-07-03): regime-ATR warmup transplant fixed to a bounded 14-day
deque (was list(...) -> unbounded -> expanding mean; ref param_stability.py,
found by the parameter-stability audit). Results produced by this script
BEFORE this fix used the buggy expanding-mean regime gate - re-run before
citing absolute numbers (live-params morning-ORB effect: OOS N 52->64,
PF 2.84->2.35).
"""

import sys, os, argparse
from datetime import date
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv

NQ_DATA = "data/nq_full.csv"
ES_DATA = "data/es_1min.csv"

IS_YEARS  = [2024]
OOS_YEARS = [2025, 2026]

ES_OVERRIDES = {
    "SYMBOL": "ES", "POINT_VALUE": 50.0, "TICK_SIZE": 0.25,
    "COMMISSION_PER_SIDE": 2.50, "SLIPPAGE_TICKS": 2,
    "ORB_FIXED_STOP_POINTS": 7.0, "ORB_STOP_BUFFER_POINTS": 2.0,
    "ORB_BREAKOUT_BUFFER_POINTS": 1.0, "ORB_MIN_RANGE_POINTS": 5.0,
    "ORB_MAX_RANGE_POINTS": 30.0, "ORB_BREAKOUT_RR_TARGET": 2.5,
    "ORB_BREAKOUT_CONFIRM": "close", "GAP_FILTER_POINTS": 5.0,
    "BREAKOUT_MIN_VOLUME": 500, "SIGNAL_STRENGTH_MIN_SCORE": 101,
    "SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP": 101, "HIGH_GAP_THRESHOLD": 10.0,
    "LONDON_ENABLED": False, "VWAP_PULLBACK_ENABLED": False,
    "PM_VWAP_ENABLED": False, "GAP_FILL_ENABLED": False,
    "ASIA_ENABLED": False, "SECOND_BREAKOUT_ENABLED": False,
    "BREAKOUT_MIN_OR_VOLUME_RATIO": 0.0, "BREAKOUT_MAX_OR_VOLUME_RATIO": 0.0,
    "GAP_EXCLUDE_MIN": 0.0, "GAP_EXCLUDE_MAX": 0.0,
    "PYRAMIDING_ENABLED": False, "EVAL_MODE": False,
    "RISK_PER_TRADE_PCT": 0.01, "MIN_RR": 1.9, "MAX_CONTRACTS": 2,
    "STARTING_BALANCE": 50000.0, "DAILY_LOSS_LIMIT_PCT": 0.015,
    "MAX_CONSECUTIVE_LOSING_DAYS": 2, "MAX_TRADES_PER_DAY": 2,
    "MAX_LOSSES_PER_DAY": 2, "DAILY_PROFIT_LOCK_PCT": 0.03,
    "WEEKLY_LOSS_LIMIT_PCT": 0.05, "MAX_TOTAL_DRAWDOWN_PCT": 0.12,
    "RECOVERY_MODE_TRIGGER_PCT": 0.05, "RECOVERY_SIZE_MULTIPLIER": 0.5,
    "APEX_TRAILING_DD": 7000.0, "ENFORCE_APEX_RULES": True,
    "SKIP_MONDAYS": True, "PARTIAL_EXIT_ENABLED": False,
    "PYRAMID_WARMUP_TRADES": 5,
    "SKIP_MONTHS": [1, 5, 6, 7, 8, 10],
    "LAST_ENTRY_TIME": "10:15",
}


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

def apply_config(overrides):
    saved = {}
    for k, v in overrides.items():
        saved[k] = getattr(config, k, None)
        setattr(config, k, v)
    return saved

def restore_config(saved):
    for k, v in saved.items():
        if v is not None:
            setattr(config, k, v)


# ─────────────────────────────────────────────────────────────────────────────
# Backtest runners
# ─────────────────────────────────────────────────────────────────────────────

def run_year_fresh(bars, year):
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
        trades.extend(run_year_fresh(bars, y))
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "avg": 0.0}
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    return {
        "n":   len(trades),
        "net": round(gw - gl, 0),
        "wr":  round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "pf":  round(gw / gl, 3) if gl else 99.0,
        "avg": round((gw - gl) / len(trades), 0) if trades else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Index builders
# ─────────────────────────────────────────────────────────────────────────────

def build_entry_index(bars):
    """
    Returns dict: (date_obj, hour, minute) -> bar_index
    Allows O(1) lookup for any trade's entry bar.
    Uses .date(), .hour, .minute to avoid naive/tz-aware comparisons.
    """
    idx = {}
    for i, bar in enumerate(bars):
        ts = bar["timestamp"]
        key = (ts.date(), ts.hour, ts.minute)
        if key not in idx:
            idx[key] = i
    return idx


# ─────────────────────────────────────────────────────────────────────────────
# FVG detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_fvgs(bars, min_size):
    """
    Scan a bar slice and return all Fair Value Gaps >= min_size.

    Bullish FVG: bars[i-1].high < bars[i+1].low
      Gap zone: [bars[i-1].high, bars[i+1].low]  (unfilled space above)

    Bearish FVG: bars[i-1].low > bars[i+1].high
      Gap zone: [bars[i+1].high, bars[i-1].low]  (unfilled space below)

    Each FVG is keyed by its local slice index (bar i = middle of 3).
    """
    fvgs = []
    for i in range(1, len(bars) - 1):
        b0, b2 = bars[i - 1], bars[i + 1]

        bull_lo, bull_hi = b0["high"], b2["low"]
        if bull_hi > bull_lo and (bull_hi - bull_lo) >= min_size:
            fvgs.append({
                "type": "bull",
                "lo":   bull_lo,
                "hi":   bull_hi,
                "size": bull_hi - bull_lo,
                "idx":  i,          # index within the window slice
            })

        bear_lo, bear_hi = b2["high"], b0["low"]
        if bear_hi > bear_lo and (bear_hi - bear_lo) >= min_size:
            fvgs.append({
                "type": "bear",
                "lo":   bear_lo,
                "hi":   bear_hi,
                "size": bear_hi - bear_lo,
                "idx":  i,
            })

    return fvgs


def get_active_fvgs(bars, entry_idx, min_size, lookback_bars, max_distance):
    """
    Find unfilled FVGs within [entry_idx - lookback_bars, entry_idx) that are
    within max_distance points of the entry bar's close.

    Fill rule: an FVG is filled if any bar's close falls inside [lo, hi]
    after the FVG was created and before the entry bar.

    Uses pure index arithmetic — no datetime comparison needed.
    """
    window_start = max(0, entry_idx - lookback_bars)
    window       = bars[window_start:entry_idx]   # bars BEFORE entry

    if len(window) < 3:
        return []

    entry_price = bars[entry_idx]["close"]
    all_fvgs    = detect_fvgs(window, min_size)
    active      = []

    for fvg in all_fvgs:
        fvg_i = fvg["idx"]     # index within window

        # Check if filled by any bar after fvg creation inside the window
        filled = False
        for bar in window[fvg_i + 1:]:
            if fvg["lo"] <= bar["close"] <= fvg["hi"]:
                filled = True
                break
        if filled:
            continue

        # Distance filter: FVG midpoint must be within max_distance of entry
        fvg_mid = (fvg["lo"] + fvg["hi"]) / 2
        if abs(fvg_mid - entry_price) > max_distance:
            continue

        active.append({**fvg, "mid": fvg_mid})

    return active


# ─────────────────────────────────────────────────────────────────────────────
# Trade classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_trade(trade, active_fvgs, entry_price):
    """
    Long trade:
      aligned → bullish FVG above entry (price magnetised upward to fill it)
      opposed → bearish FVG above entry (resistance cap before target)
    Short trade:
      aligned → bearish FVG below entry (price magnetised downward to fill it)
      opposed → bullish FVG below entry (support floor before target)
    Mixed when both are present. Neutral when no qualifying FVG found.
    """
    direction = trade["dir"]
    aligned = opposed = False

    for fvg in active_fvgs:
        above = fvg["mid"] > entry_price

        if direction == "long":
            if above:
                if fvg["type"] == "bull":
                    aligned = True
                else:
                    opposed = True
        else:  # short
            if not above:
                if fvg["type"] == "bear":
                    aligned = True
                else:
                    opposed = True

    if aligned and opposed:
        return "mixed"
    if aligned:
        return "aligned"
    if opposed:
        return "opposed"
    return "neutral"


def classify_all(bars, entry_index, trades, min_size, lookback_bars, max_distance):
    """
    Classify every trade in the list. Returns list of (trade, label) tuples.
    Trades with no matching entry bar default to 'neutral'.
    """
    results = []
    for trade in trades:
        td      = date.fromisoformat(trade["date"])
        et      = str(trade.get("entry_time", ""))
        if ":" not in et:
            results.append((trade, "neutral"))
            continue
        parts = et.split(":")
        th, tm  = int(parts[0]), int(parts[1])
        eidx    = entry_index.get((td, th, tm))

        if eidx is None or eidx == 0:
            results.append((trade, "neutral"))
            continue

        entry_price = bars[eidx]["close"]
        active      = get_active_fvgs(bars, eidx, min_size, lookback_bars, max_distance)
        label       = classify_trade(trade, active, entry_price)
        results.append((trade, label))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

CAT_ORDER = ["aligned", "opposed", "mixed", "neutral"]

def print_table(classified, title=""):
    cats = {c: [] for c in CAT_ORDER}
    for trade, cat in classified:
        cats[cat].append(trade)

    all_trades = [t for t, _ in classified]
    base = stats(all_trades)

    if title:
        print(f"\n  {title}")
    print(f"  {'Category':<12} {'N':>5}  {'WR%':>6}  {'PF':>6}  {'Net $':>10}  {'Avg $':>7}  ΔPF")
    print(f"  {'─'*64}")
    print(f"  {'Baseline':<12} {base['n']:>5}  {base['wr']:>5.1f}%  {base['pf']:>6.3f}"
          f"  {base['net']:>+10,.0f}  {base['avg']:>+7,.0f}  —")

    for cat in CAT_ORDER:
        s = stats(cats[cat])
        if s["n"] == 0:
            continue
        dpf  = s["pf"] - base["pf"]
        flag = ("  ← WORSE"  if dpf < -0.15 else
                "  ← BETTER" if dpf >  0.15 else "")
        print(f"  {cat:<12} {s['n']:>5}  {s['wr']:>5.1f}%  {s['pf']:>6.3f}"
              f"  {s['net']:>+10,.0f}  {s['avg']:>+7,.0f}  {dpf:>+.3f}{flag}")

    return cats


def print_distribution(classified):
    total = len(classified)
    if total == 0:
        return
    cats = {c: 0 for c in CAT_ORDER}
    for _, cat in classified:
        cats[cat] += 1
    parts = [f"{c}: {cats[c]} ({cats[c]/total*100:.0f}%)" for c in CAT_ORDER if cats[c]]
    print(f"  Distribution: {' | '.join(parts)}")


# ─────────────────────────────────────────────────────────────────────────────
# Per-instrument analysis
# ─────────────────────────────────────────────────────────────────────────────

def run_instrument(data_path, name, es_overrides=None, quick=False):
    print(f"\n{'='*72}")
    print(f"  {name} — ICT Fair Value Gap Filter")
    print(f"  IS: {IS_YEARS}  |  OOS: {OOS_YEARS}")
    print(f"{'='*72}")

    if not os.path.exists(data_path):
        print(f"  ERROR: {data_path} not found")
        return

    bars = load_csv(data_path)
    print(f"  {len(bars):,} bars  |  {bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()}")

    saved = apply_config(es_overrides) if es_overrides else {}
    try:
        print("  Running IS backtest...", end=" ", flush=True)
        is_trades = run_years(bars, IS_YEARS)
        print(f"{len(is_trades)} trades")

        print("  Running OOS backtest...", end=" ", flush=True)
        oos_trades = run_years(bars, OOS_YEARS)
        print(f"{len(oos_trades)} trades")
    finally:
        if es_overrides:
            restore_config(saved)

    if not is_trades and not oos_trades:
        print("  No trades — check config")
        return

    all_trades = is_trades + oos_trades
    entry_index = build_entry_index(bars)

    # ── Instrument-scaled defaults ────────────────────────────────────────────
    if es_overrides:   # ES — smaller ranges
        sizes_pt    = [2.0, 4.0, 6.0]
        size_def    = 4.0
        dists_pt    = [10.0, 20.0, 30.0]
        dist_def    = 20.0
    else:              # NQ — larger ranges
        sizes_pt    = [8.0, 16.0, 24.0]
        size_def    = 16.0
        dists_pt    = [40.0, 60.0, 80.0]
        dist_def    = 60.0

    # Lookback in bars (approx 1-min bars, NQ trades ~23h/day)
    # 6h=360, 12h=720, 24h=1440, 48h=2880
    lookback_map = {6: 360, 12: 720, 24: 1440, 48: 2880}
    lb_def_bars  = lookback_map[24]

    n_all = len(all_trades)

    # ─────────────────────────────────────────────────────────────────────────
    # FULL PARAMETER SWEEP (skipped with --quick)
    # ─────────────────────────────────────────────────────────────────────────
    if not quick:
        print(f"\n  ── A. Min FVG Size sweep  (lb=24h  dist={dist_def:.0f}pt  N={n_all}) ──")
        for sz in sizes_pt:
            cl = classify_all(bars, entry_index, all_trades, sz, lb_def_bars, dist_def)
            print_distribution(cl)
            print_table(cl, f"min_size = {sz:.0f}pt")

        print(f"\n  ── B. Lookback sweep  (size={size_def:.0f}pt  dist={dist_def:.0f}pt  N={n_all}) ──")
        for hours, lb_bars in lookback_map.items():
            cl = classify_all(bars, entry_index, all_trades, size_def, lb_bars, dist_def)
            print_distribution(cl)
            print_table(cl, f"lookback = {hours}h ({lb_bars} bars)")

        print(f"\n  ── C. Distance sweep  (size={size_def:.0f}pt  lb=24h  N={n_all}) ──")
        for dist in dists_pt:
            cl = classify_all(bars, entry_index, all_trades, size_def, lb_def_bars, dist)
            print_distribution(cl)
            print_table(cl, f"max_dist = {dist:.0f}pt")

    # ─────────────────────────────────────────────────────────────────────────
    # IS vs OOS VALIDATION using default params
    # ─────────────────────────────────────────────────────────────────────────
    params_desc = f"size={size_def:.0f}pt  lb=24h  dist={dist_def:.0f}pt"
    print(f"\n  ── IS vs OOS Validation  ({params_desc}) ──")

    is_cl  = classify_all(bars, entry_index, is_trades,  size_def, lb_def_bars, dist_def)
    oos_cl = classify_all(bars, entry_index, oos_trades, size_def, lb_def_bars, dist_def)

    print(f"\n  IS {IS_YEARS}  (N={len(is_trades)}):")
    print_distribution(is_cl)
    print_table(is_cl)

    print(f"\n  OOS {OOS_YEARS}  (N={len(oos_trades)}):")
    print_distribution(oos_cl)
    oos_cats = print_table(oos_cl)

    # Year-by-year OOS
    print(f"\n  ── OOS Year-by-Year ──")
    for year in OOS_YEARS:
        yr_cl = [(t, c) for t, c in oos_cl if t["date"][:4] == str(year)]
        if yr_cl:
            print(f"\n  {year}  (N={len(yr_cl)}):")
            print_distribution(yr_cl)
            print_table(yr_cl)

    # ─────────────────────────────────────────────────────────────────────────
    # DIRECTION BREAKDOWN (OOS)
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n  ── OOS Direction Breakdown ──")
    for d in ["long", "short"]:
        sub = [(t, c) for t, c in oos_cl if t["dir"] == d]
        if sub:
            print(f"\n  {d.upper()}  (N={len(sub)}):")
            print_distribution(sub)
            print_table(sub)

    # ─────────────────────────────────────────────────────────────────────────
    # FINAL VERDICT
    # ─────────────────────────────────────────────────────────────────────────
    base_oos_pf = stats(oos_trades)["pf"]
    opp_trades  = [t for t, c in oos_cl if c == "opposed"]
    aln_trades  = [t for t, c in oos_cl if c == "aligned"]
    opp_s       = stats(opp_trades)
    aln_s       = stats(aln_trades)

    print(f"\n  ── Verdict ──")
    print(f"  Baseline OOS PF : {base_oos_pf:.3f}  (N={len(oos_trades)})")
    print(f"  Opposed  OOS PF : {opp_s['pf']:.3f}  (N={opp_s['n']})")
    print(f"  Aligned  OOS PF : {aln_s['pf']:.3f}  (N={aln_s['n']})")

    if opp_s["n"] >= 15:
        if opp_s["pf"] < base_oos_pf * 0.75:
            action = "FILTER OPPOSED ✓  — skipping opposed trades boosts PF materially"
        elif opp_s["pf"] < base_oos_pf * 0.90:
            action = "BORDERLINE  — opposed trades underperform; validate with more data"
        else:
            action = "NO FILTER  — opposed trades near baseline; FVG context not predictive"
    else:
        action = f"INSUFFICIENT SAMPLE  (opposed N={opp_s['n']}, need ≥15 to conclude)"

    if aln_s["n"] >= 15:
        if aln_s["pf"] > base_oos_pf * 1.25:
            action += "\n  ALIGNED BONUS ✓  — consider 2c sizing on aligned trades"
        elif aln_s["pf"] > base_oos_pf * 1.10:
            action += "\n  ALIGNED MARGINAL — slight improvement; watch with more data"

    print(f"\n  → {action}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICT FVG filter research")
    parser.add_argument("--quick", action="store_true", help="Skip param sweep — IS/OOS only")
    parser.add_argument("--nq",    action="store_true", help="NQ only")
    parser.add_argument("--es",    action="store_true", help="ES only")
    args = parser.parse_args()

    do_nq = not args.es
    do_es = not args.nq

    print("\nCruzCapital — ICT Fair Value Gap Filter Research")
    print(f"IS: {IS_YEARS}  |  OOS: {OOS_YEARS}")

    if do_nq:
        run_instrument(NQ_DATA, "NQ", es_overrides=None,         quick=args.quick)
    if do_es:
        run_instrument(ES_DATA, "ES", es_overrides=ES_OVERRIDES, quick=args.quick)

    print(f"\n{'='*72}")
    print("  Done.")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()
