"""
brain/research/stop_size_test.py

NQ Stop Size Optimization with $2,500 Apex Trailing DD constraint.

Tests fixed stop sizes: 15pt, 18pt, 20pt, 22pt, 25pt (current).
Buffer kept at 5pt constant (noise floor around OR edge).
APEX_TRAILING_DD = 2,500 (tight prop firm account — smaller than our default 7k).

Metrics:
  Pass rate   = years (2023-2026) where account is NOT halted by Apex DD
  PF          = 4-yr aggregated profit factor
  Net $       = 4-yr net P&L

Viable threshold: Pass rate >= 75% AND PF >= 1.5.

Usage:
    python3 brain/research/stop_size_test.py
"""
import sys
import os
from datetime import date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv

NQ_DATA_PATH = "data/nq_full.csv"

STOP_SIZES = [
    {"fixed": 15.0, "buffer": 5.0, "label": "15pt (20pt eff.)"},
    {"fixed": 18.0, "buffer": 5.0, "label": "18pt (23pt eff.)"},
    {"fixed": 20.0, "buffer": 5.0, "label": "20pt (25pt eff.)"},
    {"fixed": 22.0, "buffer": 5.0, "label": "22pt (27pt eff.)"},
    {"fixed": 25.0, "buffer": 5.0, "label": "25pt (30pt eff.) ← current"},
]

APEX_DD_TEST = 2500.0


def calc_stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "avg": 0.0}
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = gw / gl if gl > 0 else float('inf')
    return {"n": len(trades), "net": round(gw-gl, 0), "wr": round(len(wins)/len(trades)*100, 1),
            "pf": round(pf, 2), "avg": round((gw-gl)/len(trades), 0)}


def run_year(bars, year, fixed_stop, buffer):
    """Run one year with specified stop size. Returns (trades, halted)."""
    ystart = date(year, 1, 1)
    yend   = date(year + 1, 1, 1)
    prior  = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return [], False

    # Save and override stop + apex dd
    orig_fixed  = config.ORB_FIXED_STOP_POINTS
    orig_buffer = config.ORB_STOP_BUFFER_POINTS
    orig_apex   = config.APEX_TRAILING_DD
    config.ORB_FIXED_STOP_POINTS  = fixed_stop
    config.ORB_STOP_BUFFER_POINTS = buffer
    config.APEX_TRAILING_DD       = APEX_DD_TEST

    try:
        warmup = Backtester()
        warmup.run(prior, silent=True)
        bt = Backtester()
        bt._last_close         = warmup._last_close
        bt.regime.daily_ranges = warmup.regime.daily_ranges
        bt.or_volume_history   = warmup.or_volume_history
        bt.prev_day_mode       = warmup.prev_day_mode
        bt.run(subset, silent=True)
        return bt.bank.trade_log, bt.bank.s.halted_permanently
    finally:
        config.ORB_FIXED_STOP_POINTS  = orig_fixed
        config.ORB_STOP_BUFFER_POINTS = orig_buffer
        config.APEX_TRAILING_DD       = orig_apex


def max_consecutive_losses(trades):
    streak = max_s = 0
    for t in trades:
        if t["pnl"] <= 0:
            streak += 1
            max_s = max(max_s, streak)
        else:
            streak = 0
    return max_s


def main():
    print("=" * 72)
    print("  NQ STOP SIZE OPTIMIZATION — APEX DD $2,500 CONSTRAINT")
    print(f"  APEX_TRAILING_DD = ${APEX_DD_TEST:,.0f}  |  OOS Years: 2023-2026")
    print("=" * 72)

    bars = load_csv(NQ_DATA_PATH)
    print(f"\nLoaded {len(bars):,} NQ bars")

    results = []

    print("\n── Year-by-Year Results ─────────────────────────────────────────────\n")

    for s in STOP_SIZES:
        fixed  = s["fixed"]
        buffer = s["buffer"]
        label  = s["label"]
        eff    = fixed + buffer
        risk_per_trade = eff * config.POINT_VALUE  # rough: no slip/comm

        print(f"Stop: {label}")
        print(f"  Risk/trade ≈ ${risk_per_trade:.0f} | "
              f"DD allows ≈ {APEX_DD_TEST/risk_per_trade:.1f} consecutive losses")
        print(f"  {'Year':<6} {'N':>5} {'Net $':>9} {'WR%':>6} {'PF':>5} "
              f"{'Max Streak':>10} {'Halted':>8}")

        all_trades = []
        passes = 0
        year_results = []

        for year in range(2023, 2027):
            trades, halted = run_year(bars, year, fixed, buffer)
            s_yr = calc_stats(trades)
            streak = max_consecutive_losses(trades)
            pf_s = f"{s_yr['pf']:.2f}" if s_yr['pf'] != float('inf') else "inf"
            halt_s = "HALT ✗" if halted else "pass ✓"
            if not halted:
                passes += 1
            print(f"  {year:<6} {s_yr['n']:>5} {s_yr['net']:>9,.0f} "
                  f"{s_yr['wr']:>5.1f}% {pf_s:>5} {streak:>10} {halt_s:>8}")
            all_trades.extend(trades)
            year_results.append({"year": year, "halted": halted, "trades": trades})

        total = calc_stats(all_trades)
        pf_s = f"{total['pf']:.2f}" if total['pf'] != float('inf') else "inf"
        pass_rate = passes / 4
        all_streaks = max_consecutive_losses(all_trades)

        print(f"  {'TOTAL':<6} {total['n']:>5} {total['net']:>9,.0f} "
              f"{total['wr']:>5.1f}% {pf_s:>5} {all_streaks:>10}  "
              f"Pass: {passes}/4 ({pass_rate:.0%})")

        viable = pass_rate >= 0.75 and total["pf"] >= 1.5
        print(f"  → {'VIABLE ✓' if viable else 'NOT VIABLE ✗'}\n")

        results.append({
            "label": label, "fixed": fixed, "buffer": buffer,
            "total": total, "pass_rate": pass_rate, "passes": passes,
            "viable": viable, "max_streak": all_streaks,
        })

    # Summary table
    print("\n── Summary ──────────────────────────────────────────────────────────\n")
    print(f"{'Stop':<28} {'N':>5} {'Net $':>9} {'WR%':>6} {'PF':>5} "
          f"{'Pass':>7} {'Viable':>8}")
    print("─" * 72)
    best_viable = None
    for r in results:
        pf_s = f"{r['total']['pf']:.2f}" if r['total']['pf'] != float('inf') else "inf"
        v_s = "YES ✓" if r["viable"] else "no"
        print(f"{r['label']:<28} {r['total']['n']:>5} {r['total']['net']:>9,.0f} "
              f"{r['total']['wr']:>5.1f}% {pf_s:>5} {r['passes']}/4 {r['pass_rate']:>5.0%} "
              f"{v_s:>8}")
        if r["viable"] and (best_viable is None or
                r["total"]["pf"] > best_viable["total"]["pf"]):
            best_viable = r

    print()
    if best_viable:
        print(f"  RECOMMENDATION: {best_viable['label']}")
        print(f"  PF {best_viable['total']['pf']:.2f} | "
              f"Pass rate {best_viable['pass_rate']:.0%} | "
              f"Net ${best_viable['total']['net']:,.0f}")
        print(f"  ORB_FIXED_STOP_POINTS = {best_viable['fixed']}")
        print(f"  ORB_STOP_BUFFER_POINTS = {best_viable['buffer']}")
    else:
        print("  NO STOP SIZE achieves >= 75% pass rate AND PF >= 1.5")
        print("  with $2,500 Apex trailing DD.")
        print("  Consider: larger Apex DD account, or reduce risk per trade.")

    print("\n" + "=" * 72)
    print(f"  NQ baseline (current, APEX_DD=7k): PF 2.14, Net +$36,675")
    print(f"  APEX_DD tested: ${APEX_DD_TEST:,.0f}")
    print("=" * 72)


if __name__ == "__main__":
    main()
