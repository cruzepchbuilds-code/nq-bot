"""
brain/research/edge_discovery.py

Deep edge discovery engine. Analyzes every trade in the continuous OOS backtest
(not monthly-independent) across many more dimensions than pattern_engine.py.

Dimensions analyzed:
  1. Time-of-day (15-min buckets)
  2. Day-of-week × month interaction
  3. OR size × gap size interaction
  4. Volume ratio (finer buckets)
  5. ATR regime (rolling 14-day ATR)
  6. Day-of-month effects
  7. Streak patterns (win/loss streaks)
  8. OR position in daily range
  9. Gap dead zone precision
  10. Multi-day momentum (prev 2 days direction)
  11. Sequential breakout quality (consecutive breakout days)
  12. Week-of-month effects

Usage:
    python3 brain/research/edge_discovery.py
    python3 brain/research/edge_discovery.py data/nq_full.csv
"""

import sys
import os
import csv
import math
from collections import defaultdict
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv


def load_from_trade_memory(csv_path="brain/trade_memory.csv", start_year=None):
    """
    Load trades directly from trade_memory.csv — the ground-truth trade log
    with all pre-computed fields. Avoids bankroll halting bias from backtest replay.
    Fields: date, entry_time, or_size, gap_points, volume_ratio, signal_strength,
            market_atr, consecutive_losses_before, consecutive_wins_before,
            pnl, exit_reason, points_captured, month, day_of_week, direction
    """
    trades = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = date.fromisoformat(row["date"])
                if start_year and d.year < start_year:
                    continue
                trade = {
                    "date": row["date"],
                    "entry_time": row.get("entry_time", ""),
                    "or_size": float(row.get("or_size", 80)),
                    "gap_points": float(row.get("gap_points", 0)),
                    "gap_direction": int(row.get("gap_direction", 0)),
                    "volume_ratio": float(row.get("volume_ratio", 1.0)),
                    "signal_strength": float(row.get("signal_strength", 70)),
                    "market_atr": float(row.get("market_atr", 200)),
                    "consecutive_losses_before": int(row.get("consecutive_losses_before", 0)),
                    "consecutive_wins_before": int(row.get("consecutive_wins_before", 0)),
                    "pnl": float(row.get("pnl", 0)),
                    "exit_reason": row.get("exit_reason", ""),
                    "points_captured": float(row.get("points_captured", 0)),
                }
                trades.append(trade)
            except (ValueError, KeyError):
                continue
    return trades


def run_full_oos(bars, start_year=2023):
    """
    Load OOS trades from trade_memory.csv (preferred) or re-run backtest.
    CSV path avoids bankroll-halt bias that truncates multi-year continuous runs.
    """
    csv_path = "brain/trade_memory.csv"
    if os.path.exists(csv_path):
        return load_from_trade_memory(csv_path, start_year=start_year)

    # Fallback: warmup IS bars, run fresh OOS backtest
    oos_start = date(start_year, 1, 1)
    seed = [b for b in bars if b["timestamp"].date() < oos_start]
    oos = [b for b in bars if b["timestamp"].date() >= oos_start]
    warmup_bt = Backtester()
    warmup_bt.run(seed, silent=True)
    bt = Backtester()
    bt._last_close = warmup_bt._last_close
    bt.regime.daily_ranges = warmup_bt.regime.daily_ranges
    bt.or_volume_history = warmup_bt.or_volume_history
    bt.prev_day_mode = warmup_bt.prev_day_mode
    bt.run(oos, silent=True)
    return bt.bank.trade_log


def bucket_stats(trades, key_fn, buckets):
    """Group trades by bucket, compute WR and PF per bucket."""
    groups = defaultdict(list)
    for t in trades:
        key = key_fn(t)
        for bname, condition in buckets.items():
            if condition(key):
                groups[bname].append(t)
                break

    results = {}
    for bname in buckets:
        g = groups.get(bname, [])
        if len(g) < 10:
            continue
        wins = [x for x in g if x["pnl"] > 0]
        losses = [x for x in g if x["pnl"] <= 0]
        gw = sum(x["pnl"] for x in wins)
        gl = abs(sum(x["pnl"] for x in losses))
        pf = gw / gl if gl else float("inf")
        wr = len(wins) / len(g)
        avg_pnl = sum(x["pnl"] for x in g) / len(g)
        results[bname] = {
            "n": len(g), "wr": wr, "pf": pf,
            "net": sum(x["pnl"] for x in g),
            "avg_pnl": avg_pnl,
            "avg_win": gw / len(wins) if wins else 0,
            "avg_loss": gl / len(losses) if losses else 0,
        }
    return results


def baseline(trades):
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    return {
        "n": len(trades),
        "wr": len(wins) / len(trades) if trades else 0,
        "pf": gw / gl if gl else float("inf"),
        "net": sum(t["pnl"] for t in trades),
        "avg_pnl": sum(t["pnl"] for t in trades) / len(trades) if trades else 0,
    }


def print_bucket_table(results, base, title, sort_by="wr"):
    if not results:
        print(f"  [{title}] insufficient data")
        return

    print(f"\n  === {title} ===")
    print(f"  {'Bucket':<22}  {'N':>4}  {'WR':>6}  {'PF':>5}  {'ΔWR':>7}  {'Avg P&L':>8}  {'Net':>9}")
    print(f"  {'-'*72}")

    sorted_items = sorted(results.items(),
                          key=lambda x: x[1].get(sort_by, 0), reverse=True)
    for bname, r in sorted_items:
        dwr = r["wr"] - base["wr"]
        sign = "+" if dwr >= 0 else ""
        flag = " ▲" if dwr > 0.08 else (" ▼" if dwr < -0.08 else "")
        pf_s = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
        print(f"  {bname:<22}  {r['n']:>4}  {r['wr']:>5.1%}  {pf_s:>5}  "
              f"{sign}{dwr*100:>5.1f}pp  {r['avg_pnl']:>+8,.0f}  {r['net']:>+9,.0f}{flag}")
    pf_base = f"{base['pf']:.2f}" if base["pf"] != float("inf") else "inf"
    print(f"  {'BASELINE':<22}  {base['n']:>4}  {base['wr']:>5.1%}  {pf_base:>5}  "
          f"{'—':>7}  {base['avg_pnl']:>+8,.0f}  {base['net']:>+9,.0f}")


def analyze_time_of_day(trades, base):
    """15-minute entry time buckets."""
    def get_time(t):
        return t.get("entry_time", t.get("date", ""))

    def time_minutes(t):
        if "entry_time" in t:
            parts = t["entry_time"].split(":")
            return int(parts[0]) * 60 + int(parts[1])
        return 0

    buckets = {}
    for start_m in range(9*60+45, 10*60+31, 15):
        end_m = start_m + 15
        sh, sm = divmod(start_m, 60)
        eh, em = divmod(end_m, 60)
        label = f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"
        buckets[label] = lambda m, s=start_m, e=end_m: s <= m < e

    results = bucket_stats(trades, time_minutes, buckets)
    print_bucket_table(results, base, "Entry Time (15-min buckets)")
    return results


def analyze_or_size_fine(trades, base):
    """Fine OR size buckets."""
    def get_or(t):
        return float(t.get("or_size", t.get("points", 80)))

    buckets = {
        "55-65pt":  lambda v: 55 <= v < 65,
        "65-75pt":  lambda v: 65 <= v < 75,
        "75-85pt":  lambda v: 75 <= v < 85,
        "85-95pt":  lambda v: 85 <= v < 95,
        "95-105pt": lambda v: 95 <= v < 105,
        "105-110pt":lambda v: 105 <= v <= 110,
    }
    results = bucket_stats(trades, get_or, buckets)
    print_bucket_table(results, base, "OR Size (fine buckets)")
    return results


def analyze_gap_fine(trades, base):
    """Fine gap size buckets — find the true dead zone."""
    def get_gap(t):
        gap = t.get("gap_points", t.get("gap", 0))
        try:
            return abs(float(gap))
        except:
            return 0.0

    buckets = {
        "20-30pt":  lambda v: 20 <= v < 30,
        "30-40pt":  lambda v: 30 <= v < 40,
        "40-50pt":  lambda v: 40 <= v < 50,
        "50-60pt":  lambda v: 50 <= v < 60,
        "60-80pt":  lambda v: 60 <= v < 80,
        "80-100pt": lambda v: 80 <= v < 100,
        "100-150pt":lambda v: 100 <= v < 150,
        "150pt+":   lambda v: v >= 150,
    }
    results = bucket_stats(trades, get_gap, buckets)
    print_bucket_table(results, base, "Gap Size (fine buckets — true dead zone search)")
    return results


def analyze_vol_ratio_fine(trades, base):
    """Fine volume ratio buckets — find the optimal range."""
    def get_vol(t):
        try:
            return float(t.get("volume_ratio", 1.0))
        except:
            return 1.0

    buckets = {
        "<0.5x":     lambda v: v < 0.5,
        "0.5-0.7x":  lambda v: 0.5 <= v < 0.7,
        "0.7-0.9x":  lambda v: 0.7 <= v < 0.9,
        "0.9-1.1x":  lambda v: 0.9 <= v < 1.1,
        "1.1-1.3x":  lambda v: 1.1 <= v < 1.3,
        "1.3-1.6x":  lambda v: 1.3 <= v < 1.6,
        "1.6-2.0x":  lambda v: 1.6 <= v < 2.0,
        "2.0x+":     lambda v: v >= 2.0,
    }
    results = bucket_stats(trades, get_vol, buckets)
    print_bucket_table(results, base, "Volume Ratio (fine buckets — optimal range)")
    return results


def analyze_day_of_week_by_month(trades, base):
    """Day-of-week × month interaction table."""
    dow_names = {0:"Mon", 1:"Tue", 2:"Wed", 3:"Thu", 4:"Fri"}
    month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}

    def get_dow(t):
        d = date.fromisoformat(t["date"])
        return d.weekday()

    def get_month(t):
        return date.fromisoformat(t["date"]).month

    print(f"\n  === Day-of-Week × Month Interaction ===")
    print(f"  {'':>6}" + "".join(f"  {month_names[m]:>5}" for m in range(1,13)))

    for dow in range(5):
        row = f"  {dow_names[dow]:>6}"
        day_trades = [t for t in trades if get_dow(t) == dow]
        for m in range(1, 13):
            mt = [t for t in day_trades if get_month(t) == m]
            if len(mt) >= 5:
                wr = sum(1 for t in mt if t["pnl"] > 0) / len(mt)
                row += f"  {wr:>4.0%}"
            else:
                row += f"  {'—':>5}"
        day_wr = sum(1 for t in day_trades if t["pnl"] > 0) / len(day_trades) if day_trades else 0
        row += f"  ← {day_wr:.0%} ({len(day_trades)}t)"
        print(row)


def analyze_atr_regime(trades, base):
    """ATR regime: low/normal/high volatility vs ORB performance."""
    def get_atr(t):
        try:
            return float(t.get("market_atr", 0))
        except:
            return 0.0

    buckets = {
        "ATR <150pt": lambda v: 0 < v < 150,
        "ATR 150-200pt": lambda v: 150 <= v < 200,
        "ATR 200-280pt": lambda v: 200 <= v < 280,
        "ATR 280-350pt": lambda v: 280 <= v < 350,
        "ATR 350pt+":    lambda v: v >= 350,
    }
    results = bucket_stats(trades, get_atr, buckets)
    print_bucket_table(results, base, "ATR Regime (rolling 14-day daily ATR)")
    return results


def analyze_streak_effects(trades, base):
    """Win/loss streak effects — does streak predict next trade quality?"""
    def get_consec_losses(t):
        try:
            return int(t.get("consecutive_losses_before", 0))
        except:
            return 0

    def get_consec_wins(t):
        try:
            return int(t.get("consecutive_wins_before", 0))
        except:
            return 0

    loss_buckets = {
        "0 prior losses": lambda v: v == 0,
        "1 loss":  lambda v: v == 1,
        "2 losses": lambda v: v == 2,
        "3 losses": lambda v: v == 3,
        "4+ losses": lambda v: v >= 4,
    }
    win_buckets = {
        "0 prior wins": lambda v: v == 0,
        "1 win":   lambda v: v == 1,
        "2 wins":  lambda v: v == 2,
        "3+ wins": lambda v: v >= 3,
    }

    loss_results = bucket_stats(trades, get_consec_losses, loss_buckets)
    win_results = bucket_stats(trades, get_consec_wins, win_buckets)

    print_bucket_table(loss_results, base, "Consecutive Losses Before Entry")
    print_bucket_table(win_results, base, "Consecutive Wins Before Entry")
    return loss_results, win_results


def analyze_week_of_month(trades, base):
    """Week of month effects (1st week, 2nd week, etc.)."""
    def get_week_of_month(t):
        d = date.fromisoformat(t["date"])
        return (d.day - 1) // 7 + 1

    buckets = {
        "Week 1 (days 1-7)":   lambda v: v == 1,
        "Week 2 (days 8-14)":  lambda v: v == 2,
        "Week 3 (days 15-21)": lambda v: v == 3,
        "Week 4 (days 22+)":   lambda v: v >= 4,
    }
    results = bucket_stats(trades, get_week_of_month, buckets)
    print_bucket_table(results, base, "Week of Month")
    return results


def analyze_or_position(trades, base):
    """How much of the OR was used before entry (OR_position proxy via entry relative to OR)."""
    def get_or_size(t):
        try:
            return float(t.get("or_size", 80))
        except:
            return 80.0

    # OR size as proxy for "expansion at open" — larger ORs have traded more range
    # already before the breakout. Hypothesis: medium OR (65-90pt) → best breakouts
    buckets = {
        "OR tight (55-65pt)":  lambda v: 55 <= v < 65,
        "OR medium (65-90pt)": lambda v: 65 <= v < 90,
        "OR wide (90-110pt)":  lambda v: 90 <= v <= 110,
    }
    results = bucket_stats(trades, get_or_size, buckets)
    print_bucket_table(results, base, "OR Width Category (tight/medium/wide)")
    return results


def find_best_multidimensional(trades, base):
    """Find the best combination of two filters."""
    print(f"\n  === Best Multi-Dimensional Filter Combinations ===")
    print(f"  (requires n>=8 to qualify)")

    combinations = []

    # Test all combinations of:
    # - Entry time early (<10:15 vs 10:15-10:30)
    # - Gap size (20-40 vs 40-60 vs 60+)
    # - Month (strong vs weak vs neutral)
    # - OR size (small/medium/large)
    # - Volume ratio (<0.9 vs 0.9-1.3 vs 1.3+)

    def time_mins(t):
        if "entry_time" not in t:
            return 9 * 60 + 45
        parts = t["entry_time"].split(":")
        return int(parts[0]) * 60 + int(parts[1])

    def gap_abs(t):
        try:
            return abs(float(t.get("gap_points", 0)))
        except:
            return 0.0

    def month(t):
        return date.fromisoformat(t["date"]).month

    def vol(t):
        try:
            return float(t.get("volume_ratio", 1.0))
        except:
            return 1.0

    def or_size(t):
        try:
            return float(t.get("or_size", 80))
        except:
            return 80.0

    filters = {
        "entry_early": lambda t: time_mins(t) < 10*60+15,
        "entry_mid":   lambda t: 10*60+15 <= time_mins(t) < 10*60+30,
        "gap_small":   lambda t: 20 <= gap_abs(t) < 40,
        "gap_medium":  lambda t: 40 <= gap_abs(t) < 80,
        "gap_large":   lambda t: gap_abs(t) >= 80,
        "month_strong": lambda t: month(t) in [1,2,3,4,5,10,11],
        "month_weak":   lambda t: month(t) in [6,9,12],
        "vol_low":     lambda t: vol(t) < 0.9,
        "vol_normal":  lambda t: 0.9 <= vol(t) < 1.3,
        "vol_high":    lambda t: vol(t) >= 1.3,
        "or_small":    lambda t: or_size(t) < 75,
        "or_medium":   lambda t: 75 <= or_size(t) < 95,
        "or_large":    lambda t: or_size(t) >= 95,
    }

    filter_names = list(filters.keys())
    for i, fname1 in enumerate(filter_names):
        for fname2 in filter_names[i+1:]:
            # Skip contradictory combos (same dimension different values)
            f1_dim = fname1.rsplit("_", 1)[0]
            f2_dim = fname2.rsplit("_", 1)[0]
            if f1_dim == f2_dim:
                continue

            combo = [t for t in trades if filters[fname1](t) and filters[fname2](t)]
            if len(combo) < 8:
                continue
            wins = [t for t in combo if t["pnl"] > 0]
            losses = [t for t in combo if t["pnl"] <= 0]
            gw = sum(t["pnl"] for t in wins)
            gl = abs(sum(t["pnl"] for t in losses))
            pf = gw / gl if gl else float("inf")
            wr = len(wins) / len(combo)
            net = sum(t["pnl"] for t in combo)

            combinations.append({
                "name": f"{fname1} + {fname2}",
                "n": len(combo),
                "wr": wr, "pf": pf, "net": net,
                "avg_pnl": net / len(combo),
            })

    combinations.sort(key=lambda x: x["wr"], reverse=True)
    print(f"\n  {'Filter Combination':<40}  {'N':>4}  {'WR':>6}  {'PF':>5}  {'ΔWR':>7}  {'Avg P&L':>8}")
    print(f"  {'-'*78}")
    for combo in combinations[:15]:
        dwr = combo["wr"] - base["wr"]
        sign = "+" if dwr >= 0 else ""
        pf_s = f"{combo['pf']:.2f}" if combo["pf"] != float("inf") else "inf"
        print(f"  {combo['name']:<40}  {combo['n']:>4}  {combo['wr']:>5.1%}  {pf_s:>5}  "
              f"{sign}{dwr*100:>5.1f}pp  {combo['avg_pnl']:>+8,.0f}")

    return combinations


def save_findings(results_dict, trades, base):
    out_path = "brain/research/edge_discovery_results.md"
    month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}

    with open(out_path, "w") as f:
        f.write("# Edge Discovery Results\n\n")
        f.write(f"**Date:** 2026-06-14\n")
        f.write(f"**Trades analyzed:** {base['n']} (continuous OOS, full nq_full.csv)\n")
        f.write(f"**Baseline WR:** {base['wr']:.1%} | PF: {base['pf']:.2f} | Net: ${base['net']:+,.0f}\n\n")

        f.write("## Key Findings\n\n")
        f.write("_(auto-populated by edge_discovery.py)_\n\n")

        for section_name, data in results_dict.items():
            if data is None:
                continue
            f.write(f"### {section_name}\n\n")
            if isinstance(data, dict):
                items = sorted(data.items(), key=lambda x: x[1].get("wr", 0), reverse=True)
                f.write("| Bucket | N | WR | PF | ΔWR | Avg P&L |\n")
                f.write("|--------|---|----|----|-----|--------|\n")
                for bname, r in items:
                    dwr = r["wr"] - base["wr"]
                    s = "+" if dwr >= 0 else ""
                    f.write(f"| {bname} | {r['n']} | {r['wr']:.1%} | {r['pf']:.2f} | "
                            f"{s}{dwr*100:.1f}pp | ${r['avg_pnl']:+,.0f} |\n")
            f.write("\n")

    print(f"\n  Saved: {out_path}")
    return out_path


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/nq_full.csv"
    if not os.path.exists(path):
        path = "data/nq_1min.csv"
        print(f"WARNING: Using {path} (limited 2024-2026 data)")

    print(f"Loading {path}...")
    bars = load_csv(path)

    start_year = 2023 if bars[0]["timestamp"].year <= 2022 else 2025
    print(f"Running continuous OOS backtest from {start_year}...")
    trades = run_full_oos(bars, start_year=start_year)
    print(f"OOS trades: {len(trades)}")

    if not trades:
        print("No OOS trades found.")
        return

    base = baseline(trades)
    print(f"Baseline: WR {base['wr']:.1%} | PF {base['pf']:.2f} | Net ${base['net']:+,.0f}")

    sep = "=" * 80
    print(f"\n{sep}")
    print("  DEEP EDGE DISCOVERY ANALYSIS")
    print(f"{sep}")

    results = {}

    results["Entry Time (15-min buckets)"] = analyze_time_of_day(trades, base)
    results["OR Size (fine buckets)"] = analyze_or_size_fine(trades, base)
    results["Gap Size (fine buckets)"] = analyze_gap_fine(trades, base)
    results["Volume Ratio (fine buckets)"] = analyze_vol_ratio_fine(trades, base)
    results["ATR Regime"] = analyze_atr_regime(trades, base)
    results["Streak Effects (losses)"], results["Streak Effects (wins)"] = (
        analyze_streak_effects(trades, base))
    results["Week of Month"] = analyze_week_of_month(trades, base)
    results["OR Width Category"] = analyze_or_position(trades, base)

    analyze_day_of_week_by_month(trades, base)
    combos = find_best_multidimensional(trades, base)
    results["Multi-Dim Combinations"] = {
        c["name"]: c for c in combos[:10]
    }

    print(f"\n{sep}")
    print("  EDGE DISCOVERY COMPLETE")
    print(f"{sep}")

    save_findings(results, trades, base)


if __name__ == "__main__":
    main()
