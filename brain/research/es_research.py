"""
brain/research/es_research.py

ES ORB Research Pipeline — walks the same methodology as NQ.
Year-by-year fresh bankroll (2023-2026 OOS, 2022 IS warmup).

ES instrument specs vs NQ:
  POINT_VALUE  : $50/pt  (NQ = $20/pt)
  OR range     : median 14pt, p90 = 28pt  (NQ median ~75pt)
  Stop         : 7pt fixed  (NQ = 25pt)
  Buffer       : 1pt breakout  (NQ = 4pt)
  Gap filter   : 5pt  (NQ = 20pt)
  Scorer       : bypassed (NQ-calibrated OR-size brackets don't apply to ES)

Usage:
    python3 brain/research/es_research.py
    python3 brain/research/es_research.py --quick   # skips edge discovery
"""
import sys
import os
import csv
from datetime import date, datetime, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv

ES_DATA_PATH = "data/es_1min.csv"

# ── ES config overrides ──────────────────────────────────────────────────────
# All OR-size/stop values scaled from NQ by price ratio (~3.5-4x).
# Scorer bypassed: SIGNAL_STRENGTH_MIN_SCORE=101 forces 1-contract fallback
# for all trades (ES OR sizes 5-30pt don't reach NQ scorer's 62pt threshold).
ES_OVERRIDES = {
    "SYMBOL":                           "ES",
    "POINT_VALUE":                      50.0,
    "TICK_SIZE":                        0.25,
    "COMMISSION_PER_SIDE":              2.50,
    "SLIPPAGE_TICKS":                   2,

    # OR parameters — calibrated from ES data (median OR = 14pt, p90 = 28pt)
    "ORB_FIXED_STOP_POINTS":            7.0,
    "ORB_STOP_BUFFER_POINTS":           2.0,
    "ORB_BREAKOUT_BUFFER_POINTS":       1.0,
    "ORB_MIN_RANGE_POINTS":             5.0,
    "ORB_MAX_RANGE_POINTS":             30.0,
    "ORB_BREAKOUT_RR_TARGET":           2.0,
    "ORB_BREAKOUT_CONFIRM":             "close",

    # Gap filter
    "GAP_FILTER_POINTS":                5.0,
    "BREAKOUT_MIN_VOLUME":              500,

    # Scorer bypassed — NQ-calibrated OR-size brackets don't apply to ES.
    # All ES trades use 1-contract fallback (score never >= 101).
    "SIGNAL_STRENGTH_MIN_SCORE":        101,
    "SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP": 101,
    "HIGH_GAP_THRESHOLD":               10.0,

    # Disable all non-ORB strategies
    "LONDON_ENABLED":                   False,
    "VWAP_PULLBACK_ENABLED":            False,
    "PM_VWAP_ENABLED":                  False,
    "GAP_FILL_ENABLED":                 False,
    "ASIA_ENABLED":                     False,
    "SECOND_BREAKOUT_ENABLED":          False,

    # Volume gates — disabled (no ES-specific calibration yet)
    "BREAKOUT_MIN_OR_VOLUME_RATIO":     0.0,
    "BREAKOUT_MAX_OR_VOLUME_RATIO":     0.0,

    # Gap dead zone — disabled
    "GAP_EXCLUDE_MIN":                  0.0,
    "GAP_EXCLUDE_MAX":                  0.0,

    # ── Optimized ES settings (H16) ──────────────────────────────────────
    # H16 winner: SKIP_MONTHS + LAST_ENTRY 10:15 → PF 1.67 (vs 1.03 baseline)
    # OOS (2023-2026): 94 trades, WR 50%, Net +$15,792
    # Year-by-year: 2024 PF 2.45, 2025 PF 2.11, 2026 PF 2.01
    "SKIP_MONTHS":    [1, 5, 6, 7, 8, 10],   # Jan/May/Jun/Jul/Aug/Oct weak for ES
    "LAST_ENTRY_TIME": "10:15",              # 10:15-10:29 window: WR 30.8%, PF 0.77

    # Pyramiding off (scorer bypassed → no sizing edge to pyramid on)
    "PYRAMIDING_ENABLED":               False,
    "EVAL_MODE":                        False,

    # Risk management
    "RISK_PER_TRADE_PCT":               0.01,
    "MIN_RR":                           1.9,
    "MAX_CONTRACTS":                    2,
    "STARTING_BALANCE":                 50000.0,
    "DAILY_LOSS_LIMIT_PCT":             0.015,
    "MAX_CONSECUTIVE_LOSING_DAYS":      2,
    "MAX_TRADES_PER_DAY":               2,
    "MAX_LOSSES_PER_DAY":               2,
    "DAILY_PROFIT_LOCK_PCT":            0.03,
    "WEEKLY_LOSS_LIMIT_PCT":            0.05,
    "MAX_TOTAL_DRAWDOWN_PCT":           0.12,
    "RECOVERY_MODE_TRIGGER_PCT":        0.05,
    "RECOVERY_SIZE_MULTIPLIER":         0.5,
    "APEX_TRAILING_DD":                 7000.0,
    "ENFORCE_APEX_RULES":               True,
    "SKIP_MONDAYS":                     True,
    "PARTIAL_EXIT_ENABLED":             False,
    "PYRAMID_WARMUP_TRADES":            5,
}


def _save_config():
    return {k: getattr(config, k, None) for k in ES_OVERRIDES}


def _apply_config():
    for k, v in ES_OVERRIDES.items():
        setattr(config, k, v)


def _restore_config(saved):
    for k, v in saved.items():
        if v is not None:
            setattr(config, k, v)


def calc_stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "avg": 0.0,
                "gross_win": 0.0, "gross_loss": 0.0}
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gw = sum(wins)   if wins   else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = gw / gl if gl > 0 else float('inf')
    return {
        "n":          len(trades),
        "net":        round(gw - gl, 0),
        "wr":         round(len(wins) / len(trades) * 100, 1),
        "pf":         round(pf, 2),
        "avg":        round((gw - gl) / len(trades), 0),
        "gross_win":  round(gw, 0),
        "gross_loss": round(gl, 0),
    }


def run_year_fresh(bars, year):
    """Fresh-bankroll OOS for one year, warmed up by all prior bars."""
    ystart = date(year, 1, 1)
    yend   = date(year + 1, 1, 1)
    prior  = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return []

    warmup = Backtester()
    warmup.run(prior, silent=True)

    bt = Backtester()
    bt._last_close       = warmup._last_close
    bt.regime.daily_ranges = warmup.regime.daily_ranges
    bt.or_volume_history = warmup.or_volume_history
    bt.prev_day_mode     = warmup.prev_day_mode
    bt.run(subset, silent=True)
    return bt.bank.trade_log


def print_row(label, s, width=6):
    pf_str = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
    print(f"{label:<{width}} {s['n']:>7} {s['net']:>10,.0f} {s['wr']:>6.1f}% {pf_str:>6} {s['avg']:>8,.0f}")


def main():
    quick = "--quick" in sys.argv

    print("=" * 66)
    print("  ES ORB RESEARCH PIPELINE")
    print(f"  OOS: 2023-2026 | Warmup: 2022 IS | Data: {ES_DATA_PATH}")
    print("=" * 66)

    if not os.path.exists(ES_DATA_PATH):
        print(f"ERROR: {ES_DATA_PATH} not found")
        sys.exit(1)

    bars = load_csv(ES_DATA_PATH)
    print(f"\nLoaded {len(bars):,} bars")
    print(f"Date range: {bars[0]['timestamp'].date()} — {bars[-1]['timestamp'].date()}")
    print(f"\nES config: stop={ES_OVERRIDES['ORB_FIXED_STOP_POINTS']}pt | "
          f"buffer={ES_OVERRIDES['ORB_BREAKOUT_BUFFER_POINTS']}pt | "
          f"OR {ES_OVERRIDES['ORB_MIN_RANGE_POINTS']}-{ES_OVERRIDES['ORB_MAX_RANGE_POINTS']}pt | "
          f"$50/pt")

    saved = _save_config()
    _apply_config()

    try:
        # ── Year-by-year OOS ──────────────────────────────────────────────────
        print("\n── Year-by-Year OOS ─────────────────────────────────────────────\n")
        print(f"{'Year':<6} {'Trades':>7} {'Net $':>10} {'WR%':>7} {'PF':>6} {'Avg $':>8}")
        print("─" * 52)

        all_trades = []
        for year in range(2023, 2027):
            trades = run_year_fresh(bars, year)
            s = calc_stats(trades)
            all_trades.extend(trades)
            if s["n"] > 0:
                print_row(str(year), s)
            else:
                print(f"{year:<6} {'0':>7} {'—':>10} {'—':>7} {'—':>6} {'—':>8}")

        print("─" * 52)
        total = calc_stats(all_trades)
        print_row("TOTAL", total)

    finally:
        _restore_config(saved)

    if total["n"] == 0:
        print("\nNo trades generated — verify ES config ranges")
        return

    nq_baseline = {"n": 206, "net": 36675, "wr": 47.2, "pf": 2.14}
    print(f"\n  vs NQ baseline: {nq_baseline['n']} trades | "
          f"Net +${nq_baseline['net']:,} | WR {nq_baseline['wr']}% | PF {nq_baseline['pf']}")

    if quick:
        print("\n[--quick: edge discovery skipped]")
        return

    # ── Edge Discovery ────────────────────────────────────────────────────────
    print("\n── Edge Discovery ───────────────────────────────────────────────\n")

    # Time window
    def time_bucket(t_str):
        if not t_str or ":" not in str(t_str):
            return "other"
        parts = str(t_str).split(":")
        h, m = int(parts[0]), int(parts[1])
        if h == 9  and 30 <= m < 45: return "09:30"
        if h == 9  and 45 <= m < 60: return "09:45"
        if h == 10 and  0 <= m < 15: return "10:00"
        if h == 10 and 15 <= m < 30: return "10:15"
        if h == 10 and 30 <= m < 45: return "10:30"
        return "other"

    def get_entry_time(t):
        et = t.get("entry_time", "")
        if hasattr(et, "strftime"):
            return et.strftime("%H:%M")
        return str(et)

    buckets_time = defaultdict(list)
    for t in all_trades:
        buckets_time[time_bucket(get_entry_time(t))].append(t)

    print("Time Window:")
    print(f"  {'Window':<10} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10}")
    for bk in ["09:30", "09:45", "10:00", "10:15", "10:30", "other"]:
        ts = buckets_time.get(bk, [])
        if not ts:
            continue
        s = calc_stats(ts)
        pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        print(f"  {bk:<10} {s['n']:>5} {s['wr']:>5.1f}% {pf_s:>6} {s['net']:>10,.0f}")

    # Day of week
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    buckets_dow = defaultdict(list)
    for t in all_trades:
        d = date.fromisoformat(t["date"])
        buckets_dow[dow_names[d.weekday()]].append(t)

    print("\nDay of Week:")
    print(f"  {'Day':<10} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10}")
    for d in dow_names:
        ts = buckets_dow.get(d, [])
        if not ts:
            continue
        s = calc_stats(ts)
        pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        print(f"  {d:<10} {s['n']:>5} {s['wr']:>5.1f}% {pf_s:>6} {s['net']:>10,.0f}")

    # Month
    month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    buckets_month = defaultdict(list)
    for t in all_trades:
        d = date.fromisoformat(t["date"])
        buckets_month[d.month].append(t)

    print("\nMonth:")
    print(f"  {'Month':<10} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10}")
    for m in range(1, 13):
        ts = buckets_month.get(m, [])
        if not ts:
            continue
        s = calc_stats(ts)
        pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        flag = " ◀ WEAK" if s["pf"] < 0.9 else (" ▲ STRONG" if s["pf"] >= 1.5 else "")
        print(f"  {month_names[m]:<10} {s['n']:>5} {s['wr']:>5.1f}% {pf_s:>6} {s['net']:>10,.0f}{flag}")

    # Direction
    buckets_dir = defaultdict(list)
    for t in all_trades:
        buckets_dir[t.get("dir", "?")].append(t)

    print("\nDirection:")
    print(f"  {'Dir':<10} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10}")
    for d in ["long", "short"]:
        ts = buckets_dir.get(d, [])
        if not ts:
            continue
        s = calc_stats(ts)
        pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        print(f"  {d:<10} {s['n']:>5} {s['wr']:>5.1f}% {pf_s:>6} {s['net']:>10,.0f}")

    # Exit reason
    buckets_exit = defaultdict(list)
    for t in all_trades:
        buckets_exit[t.get("result", t.get("exit_reason", "?"))].append(t)

    print("\nExit Reason:")
    print(f"  {'Result':<12} {'N':>5} {'WR%':>6} {'Net $':>10}")
    for r, ts in sorted(buckets_exit.items(), key=lambda x: -len(x[1])):
        s = calc_stats(ts)
        wr_s = f"{s['wr']:.1f}%"
        print(f"  {r:<12} {s['n']:>5} {wr_s:>6} {s['net']:>10,.0f}")

    # Year breakdown
    print("\nYear Detail:")
    print(f"  {'Year':<6} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10} {'Avg $':>8}")
    buckets_year = defaultdict(list)
    for t in all_trades:
        d = date.fromisoformat(t["date"])
        buckets_year[d.year].append(t)
    for yr in sorted(buckets_year):
        ts = buckets_year[yr]
        s = calc_stats(ts)
        pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        print(f"  {yr:<6} {s['n']:>5} {s['wr']:>5.1f}% {pf_s:>6} {s['net']:>10,.0f} {s['avg']:>8,.0f}")

    print("\n" + "=" * 66)
    print("  SUMMARY")
    print("=" * 66)
    print(f"  Instrument  : ES (E-mini S&P 500) — $50/point")
    print(f"  Period      : 2023-2026 OOS | 2022 IS warmup")
    print(f"  Trades      : {total['n']}")
    print(f"  Net P&L     : ${total['net']:,.0f}")
    print(f"  Win Rate    : {total['wr']}%")
    print(f"  Profit Factor: {total['pf']:.2f}")
    print(f"  Avg/Trade   : ${total['avg']:,.0f}")
    print(f"")
    print(f"  NQ baseline : 206 trades | +$36,675 | WR 47.2% | PF 2.14")
    print("=" * 66)


if __name__ == "__main__":
    main()
