"""
brain/research/vp_poc_filter.py

Volume Profile POC — ORB Target and Filter Research
IS: 2024  |  OOS: 2025-2026

What is the Volume Profile POC?
  Point of Control (POC): the price level with the HIGHEST total volume
  traded during the prior RTH session (9:30 AM - 4:00 PM ET).
  ES is mean-reverting and rotates around the prior day's POC far more
  consistently than NQ (which is momentum/trend-biased).

Hypotheses tested:
  H1 — POC as ORB target (adaptive vs fixed RR)
       Replace fixed 2.5x/3.0x RR target with prior-day POC as first target.
       Split into: "POC aligned" (POC in trade direction) vs "POC opposing"
       (POC against the trade — potential resistance before target).

  H2 — POC proximity filter (skip if price stuck on POC)
       If OR close price is within X pt of prior POC, the market is in
       balance — ORB breakouts from equilibrium have lower conviction.
       Test: skip trades where OR close is within 5/10/15pt of prior POC.

  H3 — POC relationship split
       Long above prior POC → institutional average long, tail-wind.
       Long below prior POC → fighting the institutional average, head-wind.
       Does the POC relationship predict ORB trade success?

POC Computation:
  For each bar in the prior RTH session (9:30-15:55 ET), we add that
  bar's volume to a price bucket rounded to the nearest TICK_SIZE.
  The bucket with the highest cumulative volume is the POC.
  This is an approximation (true VP distributes intra-bar volume
  uniformly across the bar's price range; we assign volume to close).
  For research purposes, close-weighted POC is stable and consistent.

Methodology:
  Same year-by-year fresh-bankroll approach as all other research.
  Prior POC computed from RTH bars of the previous calendar day.
  Works on both NQ (nq_full.csv) and ES (es_1min.csv).

Usage:
    cd /Users/Cruz/Desktop/nq_bot_final-main
    python3 brain/research/vp_poc_filter.py            # NQ + ES
    python3 brain/research/vp_poc_filter.py --quick    # IS/OOS only, NQ
    python3 brain/research/vp_poc_filter.py --nq       # NQ only
    python3 brain/research/vp_poc_filter.py --es       # ES only

NOTE (2026-07-03): regime-ATR warmup transplant fixed to a bounded 14-day
deque (was list(...) -> unbounded -> expanding mean; ref param_stability.py,
found by the parameter-stability audit). Results produced by this script
BEFORE this fix used the buggy expanding-mean regime gate - re-run before
citing absolute numbers (live-params morning-ORB effect: OOS N 52->64,
PF 2.84->2.35).
"""

import sys, os, argparse
from datetime import date, timedelta
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
        "wr":  round(len(wins) / len(trades) * 100, 1),
        "pf":  round(gw / gl, 3) if gl else 99.0,
        "avg": round((gw - gl) / len(trades), 0),
    }

def print_row(label, s, base_pf=None, width=22):
    dpf_str = ""
    if base_pf is not None and s["n"] > 0:
        dpf = s["pf"] - base_pf
        flag = "  ← BETTER" if dpf > 0.15 else ("  ← WORSE" if dpf < -0.15 else "")
        dpf_str = f"  {dpf:+.3f}{flag}"
    pf_str = f"{s['pf']:.3f}" if s["pf"] != 99.0 else "99.0"
    print(f"  {label:<{width}} {s['n']:>5}  {s['wr']:>5.1f}%  {pf_str:>6}"
          f"  {s['net']:>+10,.0f}  {s['avg']:>+7,.0f}{dpf_str}")


# ─────────────────────────────────────────────────────────────────────────────
# Volume Profile POC computation
# ─────────────────────────────────────────────────────────────────────────────

RTH_START_H, RTH_START_M = 9, 30
RTH_END_H,   RTH_END_M   = 15, 55   # matches FLATTEN_TIME

def compute_daily_poc(bars, tick_size=0.25):
    """
    Compute prior-day RTH (9:30-15:55 ET) POC for every calendar date.

    Method: assign each bar's volume to its close price bucket (rounded to
    tick_size). POC = bucket with highest cumulative volume.

    Returns dict: date -> POC price (or None if no RTH data for that date).
    """
    # Group RTH bars by calendar date
    daily_vol = defaultdict(lambda: defaultdict(float))  # {date: {price_bucket: vol}}

    for bar in bars:
        ts = bar["timestamp"]
        h, m = ts.hour, ts.minute
        in_rth = (h > RTH_START_H or (h == RTH_START_H and m >= RTH_START_M)) and \
                 (h < RTH_END_H   or (h == RTH_END_H   and m <= RTH_END_M))
        if not in_rth:
            continue
        d = ts.date()
        bucket = round(bar["close"] / tick_size) * tick_size
        daily_vol[d][bucket] += bar["volume"]

    # For each date, find the bucket with max volume
    poc_by_date = {}
    for d, vol_map in daily_vol.items():
        if vol_map:
            poc_by_date[d] = max(vol_map, key=vol_map.get)

    return poc_by_date


def get_prior_poc(poc_by_date, trade_date):
    """
    Return the POC of the most recent PRIOR trading day.
    Looks back up to 7 calendar days to skip weekends/holidays.
    """
    for delta in range(1, 8):
        prior = trade_date - timedelta(days=delta)
        if prior in poc_by_date:
            return poc_by_date[prior]
    return None


def get_or_close_price(bars_by_date, trade):
    """
    Approximate the OR close price as the close of the bar at entry_time.
    Uses (date, hour, minute) matching.
    """
    td = date.fromisoformat(trade["date"])
    et = str(trade.get("entry_time", ""))
    if ":" not in et:
        return None
    parts = et.split(":")
    th, tm = int(parts[0]), int(parts[1])
    for bar in bars_by_date.get(td, []):
        bts = bar["timestamp"]
        if bts.hour == th and bts.minute == tm:
            return bar["close"]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# H1 — POC Relationship classifier
# ─────────────────────────────────────────────────────────────────────────────

def classify_poc_relationship(trade, poc, entry_price):
    """
    For H1 and H3:

    Long trade:
      tailwind  — entry price > prior POC (above institutional average,
                  institutions net profitable, momentum favours upside)
      headwind  — entry price < prior POC (fighting institutional average,
                  price may be magnetised back to POC below)
      on_poc    — entry within poc_zone of POC (equilibrium, choppy)

    Short trade:
      tailwind  — entry price < prior POC (below institutional average)
      headwind  — entry price > prior POC (above institutional average,
                  POC may act as support pulling price back up)
      on_poc    — entry within poc_zone of POC

    Returns: 'tailwind', 'headwind', or 'on_poc'
    """
    direction = trade["dir"]
    diff = entry_price - poc         # positive = above POC

    if direction == "long":
        if diff > 0:
            return "tailwind"
        elif diff < 0:
            return "headwind"
        else:
            return "on_poc"
    else:  # short
        if diff < 0:
            return "tailwind"
        elif diff > 0:
            return "headwind"
        else:
            return "on_poc"


# ─────────────────────────────────────────────────────────────────────────────
# H2 — POC proximity (skip if too close to POC)
# ─────────────────────────────────────────────────────────────────────────────

def classify_poc_proximity(poc, entry_price, zone_pt):
    """Returns True if entry is within zone_pt of prior POC (balance zone)."""
    return abs(entry_price - poc) <= zone_pt


# ─────────────────────────────────────────────────────────────────────────────
# Build trade feature table
# ─────────────────────────────────────────────────────────────────────────────

def annotate_trades(trades, poc_by_date, bars_by_date):
    """
    For each trade, add:
      poc           : prior day's POC price (or None)
      entry_price   : close of the entry bar
      poc_dist      : entry_price - poc (signed, positive = above POC)
      relationship  : tailwind / headwind / on_poc / unknown
    """
    annotated = []
    for trade in trades:
        td          = date.fromisoformat(trade["date"])
        poc         = get_prior_poc(poc_by_date, td)
        entry_price = get_or_close_price(bars_by_date, trade)

        if poc is None or entry_price is None:
            annotated.append({**trade, "_poc": None, "_ep": None,
                               "_dist": None, "_rel": "unknown"})
            continue

        dist = entry_price - poc
        rel  = classify_poc_relationship(trade, poc, entry_price)
        annotated.append({**trade, "_poc": poc, "_ep": entry_price,
                          "_dist": dist, "_rel": rel})

    return annotated


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

HDR = f"  {'Label':<22} {'N':>5}  {'WR%':>6}  {'PF':>6}  {'Net $':>10}  {'Avg $':>7}  ΔPF"
SEP = "  " + "─" * 70

def print_rel_table(annotated, title="", base_pf=None):
    if title:
        print(f"\n  {title}")
    print(HDR); print(SEP)

    all_trades = [t for t in annotated if t["_rel"] != "unknown"]
    base = stats(all_trades)
    bp   = base_pf if base_pf is not None else base["pf"]

    print_row("Baseline (known poc)", base, None)
    for rel in ["tailwind", "headwind", "on_poc", "unknown"]:
        subset = [t for t in annotated if t["_rel"] == rel]
        if not subset:
            continue
        s = stats(subset)
        print_row(rel, s, bp)


def print_prox_table(annotated, zone_pts, title=""):
    if title:
        print(f"\n  {title}")
    print(HDR); print(SEP)

    known = [t for t in annotated if t["_poc"] is not None]
    base  = stats(known)
    print_row("Baseline", base, None)

    for z in zone_pts:
        on_poc_trades  = [t for t in known if abs(t["_dist"]) <= z]
        off_poc_trades = [t for t in known if abs(t["_dist"]) >  z]
        s_on  = stats(on_poc_trades)
        s_off = stats(off_poc_trades)
        print(f"\n  zone = ±{z:.0f}pt  (on={s_on['n']}  off={s_off['n']})")
        print_row(f"  skip on_poc (±{z:.0f}pt)", s_off, base["pf"])
        print_row(f"  only on_poc (±{z:.0f}pt)", s_on,  base["pf"])


def print_dist_buckets(annotated, bucket_size, title=""):
    """Show PF by distance bucket: how far is entry from prior POC?"""
    if title:
        print(f"\n  {title}")

    known = [t for t in annotated if t["_dist"] is not None]
    if not known:
        print("  No annotated trades with POC data.")
        return

    buckets = defaultdict(list)
    for t in known:
        b = round(t["_dist"] / bucket_size) * bucket_size
        buckets[b].append(t)

    all_dists = [t["_dist"] for t in known]
    print(f"  Distance range: {min(all_dists):+.0f}pt  to  {max(all_dists):+.0f}pt")
    print(f"  Bucket size: {bucket_size}pt  (+ = above POC, - = below)")
    print(f"\n  {'Dist bucket':>14}  {'N':>5}  {'WR%':>6}  {'PF':>6}  {'Net $':>10}")
    print(f"  {'─'*50}")

    base_pf = stats(known)["pf"]
    for b in sorted(buckets):
        s = stats(buckets[b])
        flag = "  ↑" if s["pf"] > base_pf * 1.2 else ("  ↓" if s["pf"] < base_pf * 0.8 else "")
        dist_str = f"{b:+.0f}pt"
        print(f"  {dist_str:>14}  {s['n']:>5}  {s['wr']:>5.1f}%  {s['pf']:>6.3f}"
              f"  {s['net']:>+10,.0f}{flag}")


# ─────────────────────────────────────────────────────────────────────────────
# Per-instrument analysis
# ─────────────────────────────────────────────────────────────────────────────

def run_instrument(data_path, name, es_overrides=None, quick=False):
    tick = 0.25
    if es_overrides:
        tick = es_overrides.get("TICK_SIZE", 0.25)

    print(f"\n{'='*72}")
    print(f"  {name} — Volume Profile POC Filter")
    print(f"  IS: {IS_YEARS}  |  OOS: {OOS_YEARS}")
    print(f"{'='*72}")

    if not os.path.exists(data_path):
        print(f"  ERROR: {data_path} not found")
        return

    bars = load_csv(data_path)
    print(f"  {len(bars):,} bars  |  {bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()}")

    # Pre-compute POC for every trading day
    print("  Computing daily POCs...", end=" ", flush=True)
    poc_by_date = compute_daily_poc(bars, tick)
    print(f"{len(poc_by_date)} trading days")

    # Build date-indexed bar dict for fast entry-price lookup
    bars_by_date = defaultdict(list)
    for bar in bars:
        bars_by_date[bar["timestamp"].date()].append(bar)

    # Run backtests
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
        print("  No trades — check config"); return

    all_trades = is_trades + oos_trades

    # Annotate all trades with POC info
    is_ann  = annotate_trades(is_trades,  poc_by_date, bars_by_date)
    oos_ann = annotate_trades(oos_trades, poc_by_date, bars_by_date)
    all_ann = is_ann + oos_ann

    # Instrument-specific proximity zones
    if es_overrides:
        prox_zones = [3.0, 5.0, 8.0, 10.0]
        dist_bkt   = 5.0
    else:
        prox_zones = [10.0, 20.0, 30.0, 40.0]
        dist_bkt   = 20.0

    # ─────────────────────────────────────────────────────────────────────────
    # H3 — POC relationship: tailwind / headwind (full sweep, IS+OOS combined)
    # ─────────────────────────────────────────────────────────────────────────
    if not quick:
        n_known = sum(1 for t in all_ann if t["_poc"] is not None)
        print(f"\n  ── H3: POC Relationship  (IS+OOS combined, {n_known}/{len(all_ann)} with POC) ──")
        print_rel_table(all_ann, "Combined IS+OOS")

        # By direction
        for d in ["long", "short"]:
            sub = [t for t in all_ann if t["dir"] == d]
            print_rel_table(sub, f"{d.upper()} trades")

        # H2 proximity filter
        print(f"\n  ── H2: POC Proximity Filter (IS+OOS combined) ──")
        print_prox_table(all_ann, prox_zones)

        # Distance distribution
        print_dist_buckets(all_ann, dist_bkt,
                           f"\n  POC Distance Distribution  (bucket={dist_bkt:.0f}pt)")

    # ─────────────────────────────────────────────────────────────────────────
    # IS vs OOS split (default view)
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n  ── IS vs OOS: H3 POC Relationship ──")

    is_base_pf = stats(is_trades)["pf"]
    print(f"\n  IS {IS_YEARS}  (N={len(is_trades)}):")
    print_rel_table(is_ann, base_pf=is_base_pf)

    oos_base_pf = stats(oos_trades)["pf"]
    print(f"\n  OOS {OOS_YEARS}  (N={len(oos_trades)}):")
    print_rel_table(oos_ann, base_pf=oos_base_pf)

    # OOS year-by-year
    print(f"\n  ── OOS Year-by-Year ──")
    for year in OOS_YEARS:
        yr_ann = [t for t in oos_ann if t["date"][:4] == str(year)]
        yr_bp  = stats([t for t in yr_ann if t["_poc"] is not None])["pf"]
        if yr_ann:
            print(f"\n  {year}  (N={len(yr_ann)}):")
            print_rel_table(yr_ann, base_pf=yr_bp)

    # ─────────────────────────────────────────────────────────────────────────
    # IS vs OOS proximity filter
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n  ── IS vs OOS: H2 Proximity Filter ──")
    print(f"\n  IS {IS_YEARS}:")
    print_prox_table(is_ann, prox_zones[:2])
    print(f"\n  OOS {OOS_YEARS}:")
    print_prox_table(oos_ann, prox_zones[:2])

    # ─────────────────────────────────────────────────────────────────────────
    # VERDICT
    # ─────────────────────────────────────────────────────────────────────────
    oos_tw  = [t for t in oos_ann if t["_rel"] == "tailwind"]
    oos_hw  = [t for t in oos_ann if t["_rel"] == "headwind"]
    oos_all = [t for t in oos_ann if t["_rel"] != "unknown"]

    tw_s  = stats(oos_tw)
    hw_s  = stats(oos_hw)
    base_s = stats(oos_all)

    # Best proximity filter (check if skipping on-poc improves things)
    best_skip_pf, best_zone = 0.0, 0.0
    for z in prox_zones:
        known = [t for t in oos_ann if t["_poc"] is not None]
        off   = [t for t in known if abs(t["_dist"]) > z]
        s     = stats(off)
        if s["pf"] > best_skip_pf and s["n"] >= 10:
            best_skip_pf, best_zone = s["pf"], z

    print(f"\n  ── Verdict ──")
    print(f"  OOS Baseline PF : {base_s['pf']:.3f}  (N={base_s['n']})")
    print(f"  Tailwind PF     : {tw_s['pf']:.3f}  (N={tw_s['n']})")
    print(f"  Headwind PF     : {hw_s['pf']:.3f}  (N={hw_s['n']})")
    if best_zone > 0:
        print(f"  Best prox skip  : ±{best_zone:.0f}pt → PF {best_skip_pf:.3f}")

    # Decision
    verdicts = []
    if tw_s["n"] >= 10 and hw_s["n"] >= 10:
        diff = tw_s["pf"] - hw_s["pf"]
        if diff >= 0.40:
            verdicts.append(f"POC DIRECTION FILTER ✓ — tailwind vs headwind ΔPF={diff:+.3f}")
        elif diff >= 0.20:
            verdicts.append(f"POC DIRECTION MARGINAL — ΔPF={diff:+.3f}, more data needed")
        else:
            verdicts.append(f"POC DIRECTION: no meaningful signal (ΔPF={diff:+.3f})")
    else:
        verdicts.append(f"INSUFFICIENT SAMPLE — tailwind N={tw_s['n']}, headwind N={hw_s['n']}")

    if best_skip_pf > base_s["pf"] * 1.10 and best_zone > 0:
        verdicts.append(f"PROXIMITY SKIP ✓ — skip within ±{best_zone:.0f}pt of POC → +{best_skip_pf - base_s['pf']:.3f} PF")
    else:
        verdicts.append("PROXIMITY SKIP: no material improvement from skipping near-POC entries")

    for v in verdicts:
        print(f"\n  → {v}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Volume Profile POC filter research")
    parser.add_argument("--quick", action="store_true", help="Skip full sweep — IS/OOS only")
    parser.add_argument("--nq",    action="store_true", help="NQ only")
    parser.add_argument("--es",    action="store_true", help="ES only")
    args = parser.parse_args()

    do_nq = not args.es
    do_es = not args.nq

    print("\nCruzCapital — Volume Profile POC Research")
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
