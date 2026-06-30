"""
brain/research/rolling_walk_forward.py

Anchored rolling walk-forward validation using the full 2022-2026 NQ dataset.

Methodology:
  Anchored expanding window:
    Window 1: IS=2022,       OOS=2023
    Window 2: IS=2022-23,    OOS=2024
    Window 3: IS=2022-24,    OOS=2025
    Window 4: IS=2022-25,    OOS=2026
    Combined: IS=2022,       OOS=2023-2026 (4-year OOS combined)

  Also runs the current v7 config to compare against baseline.

Usage:
    python3 brain/research/rolling_walk_forward.py
    python3 brain/research/rolling_walk_forward.py data/nq_full.csv
"""

import sys
import os
from datetime import date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv


def run_fresh(bars, start: date, end: date, label: str, prior_bars=None):
    """
    Run OOS window with a FRESH bankroll ($50k starting balance).
    Uses prior_bars ONLY to seed prev_close and regime warmup context
    into the first day — does NOT share the bankroll with the IS period.
    This properly separates parameter validation from live capital simulation.
    """
    from backtest import (SESSION_START, LAST_ENTRY, FLATTEN, OR_END,
                          LONDON_START, LONDON_RANGE_TO, LONDON_CLASSIFY,
                          LONDON_ENTRY, LONDON_EXIT, SLIP)
    from datetime import time as dt_time
    from regime import RegimeDetector
    from strategies.strategy_us import ORBStrategy
    from strategies.strategy_london import LondonStrategy

    subset = [b for b in bars if start <= b["timestamp"].date() < end]
    if not subset:
        return None

    # Seed prior context: last_close + regime history from IS bars
    bt = Backtester()
    if prior_bars:
        prior = [b for b in prior_bars if b["timestamp"].date() < start]
        # Run prior bars silently to warm up regime detector and prev_close
        # but do NOT count those trades (different BankrollManager)
        warmup_bt = Backtester()
        warmup_bt.run(prior, silent=True)
        # Transfer state: prev_close and regime daily_ranges
        bt._last_close = warmup_bt._last_close
        bt.regime.daily_ranges = warmup_bt.regime.daily_ranges
        bt.or_volume_history = warmup_bt.or_volume_history
        bt.prev_day_mode = warmup_bt.prev_day_mode

    bt.run(subset, silent=True)
    return _summarize(bt.bank.trade_log, bt.bank.s, label, len(subset))


def _summarize(trades, bank_state, label, n_bars):
    if not trades:
        return {
            "label": label, "n": 0, "pf": None, "net": 0,
            "wr": 0, "max_dd": 0, "max_dd_pct": 0, "n_bars": n_bars,
            "avg_win": 0, "avg_loss": 0,
        }

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = gw / gl if gl else float("inf")

    peak = config.STARTING_BALANCE
    max_dd = 0.0
    bal = config.STARTING_BALANCE
    for t in trades:
        bal += t["pnl"]  # Note: trade['balance'] already has running total
        # Use balance field if available
    peak = config.STARTING_BALANCE
    max_dd = 0.0
    for t in trades:
        b = t.get("balance", config.STARTING_BALANCE)
        peak = max(peak, b)
        max_dd = max(max_dd, peak - b)

    return {
        "label": label,
        "n": len(trades),
        "pf": pf,
        "net": sum(t["pnl"] for t in trades),
        "wr": len(wins) / len(trades),
        "max_dd": max_dd,
        "max_dd_pct": max_dd / peak if peak else 0,
        "n_bars": n_bars,
        "avg_win": gw / len(wins) if wins else 0,
        "avg_loss": gl / len(losses) if losses else 0,
        "gross_win": gw,
        "gross_loss": gl,
    }


def print_result(r, prefix=""):
    if r is None or r["n"] == 0:
        print(f"  {prefix}{r['label'] if r else '???':<40}  NO TRADES")
        return
    pf_str = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
    halt = " HALTED" if r.get("halted") else ""
    print(f"  {prefix}{r['label']:<40}  T={r['n']:>4}  WR={r['wr']:.1%}  PF={pf_str:>5}  "
          f"Net={r['net']:>+10,.0f}  MaxDD={r['max_dd_pct']:.1%}{halt}")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/nq_full.csv"
    if not os.path.exists(path):
        path = "data/nq_1min.csv"
        print(f"WARNING: nq_full.csv not found, falling back to {path}")

    print(f"Loading {path}...")
    bars = load_csv(path)
    print(f"Loaded {len(bars):,} bars  {bars[0]['timestamp'].date()} -> {bars[-1]['timestamp'].date()}")

    sep = "=" * 100
    print(f"\n{sep}")
    print(f"  ANCHORED ROLLING WALK-FORWARD — {config.SYMBOL} v7 Config")
    print(sep)
    print(f"  {'Period':<40}  {'T':>4}  {'WR':>6}  {'PF':>5}  {'Net P&L':>10}  {'MaxDD':>6}")
    print(sep)

    # Define windows
    windows = [
        # (label, IS_start, IS_end, OOS_start, OOS_end)
        ("2022 IS → 2023 OOS",  date(2022,1,1), date(2023,1,1), date(2023,1,1), date(2024,1,1)),
        ("2022-23 IS → 2024 OOS", date(2022,1,1), date(2024,1,1), date(2024,1,1), date(2025,1,1)),
        ("2022-24 IS → 2025 OOS", date(2022,1,1), date(2025,1,1), date(2025,1,1), date(2026,1,1)),
        ("2022-25 IS → 2026 OOS", date(2022,1,1), date(2026,1,1), date(2026,1,1), date(2027,1,1)),
    ]

    oos_results = []
    all_oos_trades = []

    for label, is_start, is_end, oos_start, oos_end in windows:
        is_bars = [b for b in bars if is_start <= b["timestamp"].date() < is_end]
        oos_subset = [b for b in bars if oos_start <= b["timestamp"].date() < oos_end]

        if not oos_subset:
            print(f"  {label}: no OOS data")
            continue

        # Run OOS with FRESH bankroll, but IS bars used to seed context
        result = run_fresh(bars, oos_start, oos_end, label, prior_bars=is_bars)
        if result:
            print_result(result, "OOS ")
            oos_results.append(result)

    # Combined OOS (2023-2026 combined, seeded with 2022 IS context)
    print(sep)
    combined = run_fresh(bars, date(2023,1,1), date(2027,1,1),
                         "2023-2026 COMBINED OOS (4yr)",
                         prior_bars=[b for b in bars if b["timestamp"].date() < date(2023,1,1)])
    if combined:
        print_result(combined, ">>> ")

    # Current v7 reported baseline for comparison
    v7_oos = run_fresh(bars, date(2025,1,1), date(2027,1,1),
                       "v7 Baseline: 2025-26 OOS only",
                       prior_bars=[b for b in bars if b["timestamp"].date() < date(2025,1,1)])
    print()
    print_result(v7_oos, "v7  ")

    print(sep)

    # Year-by-year breakdown
    print(f"\n  Year-by-Year OOS Performance")
    print(f"  {'Year':<10}  {'T':>4}  {'WR':>6}  {'PF':>5}  {'Net P&L':>10}  {'MaxDD':>6}")
    print("  " + "-" * 60)
    for year in range(2022, 2027):
        ystart = date(year, 1, 1)
        yend = date(year + 1, 1, 1)
        prior = [b for b in bars if b["timestamp"].date() < ystart]
        yr = run_fresh(bars, ystart, yend, str(year), prior_bars=prior)
        if yr and yr["n"] > 0:
            pf_str = f"{yr['pf']:.2f}" if yr["pf"] != float("inf") else "inf"
            print(f"  {year:<10}  {yr['n']:>4}  {yr['wr']:.1%}  {pf_str:>5}  "
                  f"{yr['net']:>+10,.0f}  {yr['max_dd_pct']:.1%}")
        else:
            print(f"  {year:<10}  {'—':>4}")

    # Monthly breakdown for 2022-2026
    print(f"\n  Month-by-Month OOS (using nq_full.csv)")
    print(f"  {'Month':<10}  {'T':>4}  {'Wins':>4}  {'Net P&L':>10}")
    print("  " + "-" * 38)

    monthly_totals = defaultdict(lambda: {"n": 0, "wins": 0, "net": 0})
    seed_so_far = []

    months_sorted = sorted(set(b["timestamp"].strftime("%Y-%m") for b in bars))
    for ym in months_sorted:
        month_bars = [b for b in bars if b["timestamp"].strftime("%Y-%m") == ym]
        yr = int(ym[:4])
        mo = int(ym[5:7])
        # Only OOS years (anything not used as IS in the primary v7 walk-forward)
        if yr < 2023:
            seed_so_far.extend(month_bars)
            continue
        result = run_fresh(bars,
                           month_bars[0]["timestamp"].date(),
                           month_bars[-1]["timestamp"].date(),
                           ym, prior_bars=seed_so_far)
        if result and result["n"] > 0:
            wins = round(result["wr"] * result["n"])
            monthly_totals[mo]["n"] += result["n"]
            monthly_totals[mo]["wins"] += wins
            monthly_totals[mo]["net"] += result["net"]
            print(f"  {ym:<10}  {result['n']:>4}  {wins:>4}  {result['net']:>+10,.0f}")
        seed_so_far.extend(month_bars)

    # Seasonal summary
    month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    print(f"\n  Seasonal Aggregation (all OOS years combined)")
    print(f"  {'Month':<6}  {'Trades':>6}  {'WR':>6}  {'Net Total':>10}  {'Net/Trade':>10}")
    print("  " + "-" * 48)
    for mo in range(1, 13):
        mt = monthly_totals[mo]
        if mt["n"] > 0:
            wr = mt["wins"] / mt["n"]
            npt = mt["net"] / mt["n"]
            print(f"  {month_names[mo]:<6}  {mt['n']:>6}  {wr:>5.1%}  "
                  f"{mt['net']:>+10,.0f}  {npt:>+10,.0f}")

    print(sep)
    print("  ROLLING WALK-FORWARD COMPLETE")
    print(sep)

    # Save results
    out_path = "brain/research/rolling_wf_results.md"
    with open(out_path, "w") as f:
        f.write("# Rolling Walk-Forward Results\n\n")
        f.write(f"Date: 2026-06-14 | Config: v7 | Data: {path}\n\n")
        f.write("## OOS Windows\n\n")
        f.write("| Period | Trades | WR | PF | Net P&L | MaxDD |\n")
        f.write("|--------|--------|----|----|---------|-------|\n")
        for r in oos_results:
            if r["n"] > 0:
                pf_s = f"{r['pf']:.2f}"
                f.write(f"| {r['label']} | {r['n']} | {r['wr']:.1%} | {pf_s} | "
                        f"${r['net']:+,.0f} | {r['max_dd_pct']:.1%} |\n")
        if combined and combined["n"] > 0:
            f.write(f"\n## Combined 4-Year OOS (2023-2026)\n\n")
            f.write(f"- Trades: {combined['n']}\n")
            f.write(f"- Win Rate: {combined['wr']:.1%}\n")
            f.write(f"- Profit Factor: {combined['pf']:.2f}\n")
            f.write(f"- Net P&L: ${combined['net']:+,.0f}\n")
            f.write(f"- Max DD: {combined['max_dd_pct']:.1%}\n")
        f.write("\n## Seasonal Analysis\n\n")
        f.write("| Month | Trades | WR | Net Total | Net/Trade |\n")
        f.write("|-------|--------|----|-----------|-----------|\n")
        for mo in range(1, 13):
            mt = monthly_totals[mo]
            if mt["n"] > 0:
                wr = mt["wins"] / mt["n"]
                npt = mt["net"] / mt["n"]
                f.write(f"| {month_names[mo]} | {mt['n']} | {wr:.1%} | "
                        f"${mt['net']:+,.0f} | ${npt:+,.0f} |\n")
    print(f"\n  Saved: {out_path}")


if __name__ == "__main__":
    main()
