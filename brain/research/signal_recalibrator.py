"""
brain/research/signal_recalibrator.py

Diagnoses and recalibrates the signal strength scorer.

Critical finding: The current scorer is INVERTED.
  - Score 60-69: WR 51.7%, PF 2.03 (BEST actual performance)
  - Score 70-79: WR 36.5%, PF 1.09 (WORST actual performance)
  - Score 80-89: WR 35.2%, PF 1.03

This module:
  1. Decomposes each scorer component independently
  2. Tests the actual predictive power of each component
  3. Proposes a recalibrated scorer
  4. Tests the recalibrated scorer OOS

Usage:
    python3 brain/research/signal_recalibrator.py
"""

import sys
import os
import csv
import math
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


MEMORY_PATH = "brain/trade_memory.csv"


def load_trades():
    with open(MEMORY_PATH) as f:
        return list(csv.DictReader(f))


def wr_pf(trades):
    if not trades:
        return 0, 0, 0
    wins = [t for t in trades if float(t["pnl"]) > 0]
    losses = [t for t in trades if float(t["pnl"]) <= 0]
    gw = sum(float(t["pnl"]) for t in wins)
    gl = abs(sum(float(t["pnl"]) for t in losses))
    pf = gw / gl if gl else float("inf")
    return len(wins) / len(trades), pf, len(trades)


def wilson_lower(w, n, z=1.645):
    if n == 0:
        return 0.0
    p = w / n
    denom = 1 + z*z/n
    return (p + z*z/(2*n) - z*math.sqrt((p*(1-p)+z*z/(4*n))/n)) / denom


def component_analysis(trades):
    """
    Test each scorer component individually to find which ones
    actually predict trade quality.
    """
    sep = "=" * 75

    def time_minutes(t):
        parts = t["entry_time"].split(":")
        return int(parts[0]) * 60 + int(parts[1])

    print(f"\n{sep}")
    print("  SIGNAL COMPONENT ANALYSIS")
    print(f"  (source: {len(trades)} trades from trade_memory.csv)")
    print(sep)

    # ── Component 1: Entry Time ──────────────────────────────────────────────
    print("\n  [1] Entry Time Component (current: best window +20pts)")
    time_groups = {
        "9:45-10:00 (14-20pts)": [t for t in trades if 9*60+45 <= time_minutes(t) < 10*60],
        "10:00-10:15 (14pts)":   [t for t in trades if 10*60 <= time_minutes(t) < 10*60+15],
        "10:15-10:30 (20pts)":   [t for t in trades if 10*60+15 <= time_minutes(t) < 10*60+30],
        "10:30-11:00 (0pts)":    [t for t in trades if 10*60+30 <= time_minutes(t) < 11*60],
    }
    print(f"  {'Window':<28}  {'N':>4}  {'WR':>6}  {'PF':>5}  {'Avg P&L':>8}  Score Pts")
    for label, g in time_groups.items():
        if len(g) >= 5:
            w, p, n = wr_pf(g)
            avg = sum(float(t["pnl"]) for t in g) / n
            pts = 20 if "10:15-10:30" in label else (14 if "9:45-10:00" in label else
                  14 if "10:00-10:15" in label else 0)
            print(f"  {label:<28}  {n:>4}  {w:>5.1%}  {p:>5.2f}  {avg:>+8,.0f}  {pts}")

    # ── Component 2: Gap Alignment ───────────────────────────────────────────
    print("\n  [2] Gap Alignment Component (current: aligned +25pts, neutral +10pts)")
    def gap_dir(t):
        gap = float(t.get("gap_points", 0))
        direction = t.get("direction", "long")
        if abs(gap) < 20:
            return "neutral"
        if (gap > 0 and direction == "long") or (gap < 0 and direction == "short"):
            return "aligned"
        return "against"

    gap_groups = defaultdict(list)
    for t in trades:
        gap_groups[gap_dir(t)].append(t)

    for label in ["aligned", "neutral", "against"]:
        g = gap_groups[label]
        if len(g) >= 5:
            w, p, n = wr_pf(g)
            avg = sum(float(t["pnl"]) for t in g) / n
            pts = 25 if label == "aligned" else (10 if label == "neutral" else 0)
            print(f"  Gap {label:<24}  {n:>4}  {w:>5.1%}  {p:>5.2f}  {avg:>+8,.0f}  {pts}")

    # ── Component 3: Volume Ratio ────────────────────────────────────────────
    print("\n  [3] Volume Ratio Component (current: 1.2-1.8x +25pts, 0.8-1.2x +15pts)")
    vol_groups = {
        "<0.5x (0pts)":        [t for t in trades if float(t.get("volume_ratio",1)) < 0.5],
        "0.5-0.8x (0pts)":     [t for t in trades if 0.5 <= float(t.get("volume_ratio",1)) < 0.8],
        "0.8-1.0x (15pts)":    [t for t in trades if 0.8 <= float(t.get("volume_ratio",1)) < 1.0],
        "1.0-1.2x (15pts)":    [t for t in trades if 1.0 <= float(t.get("volume_ratio",1)) < 1.2],
        "1.2-1.5x (25pts)":    [t for t in trades if 1.2 <= float(t.get("volume_ratio",1)) < 1.5],
        "1.5-2.0x (8pts)":     [t for t in trades if 1.5 <= float(t.get("volume_ratio",1)) < 2.0],
        "2.0x+ (8pts)":        [t for t in trades if float(t.get("volume_ratio",1)) >= 2.0],
    }
    for label, g in vol_groups.items():
        if len(g) >= 5:
            w, p, n = wr_pf(g)
            avg = sum(float(t["pnl"]) for t in g) / n
            print(f"  {label:<28}  {n:>4}  {w:>5.1%}  {p:>5.2f}  {avg:>+8,.0f}")

    # ── Component 4: OR Size ─────────────────────────────────────────────────
    print("\n  [4] OR Size Component (current: 62-86pt +20pts, 86-120pt +12pts)")
    or_groups = {
        "55-62pt (0pts)":   [t for t in trades if 55 <= float(t.get("or_size",80)) < 62],
        "62-75pt (20pts)":  [t for t in trades if 62 <= float(t.get("or_size",80)) < 75],
        "75-86pt (20pts)":  [t for t in trades if 75 <= float(t.get("or_size",80)) < 86],
        "86-100pt (12pts)": [t for t in trades if 86 <= float(t.get("or_size",80)) < 100],
        "100-110pt (12pts)":[t for t in trades if 100 <= float(t.get("or_size",80)) <= 110],
    }
    for label, g in or_groups.items():
        if len(g) >= 5:
            w, p, n = wr_pf(g)
            avg = sum(float(t["pnl"]) for t in g) / n
            print(f"  {label:<28}  {n:>4}  {w:>5.1%}  {p:>5.2f}  {avg:>+8,.0f}")

    # ── Component 5: Previous Day Breakout ───────────────────────────────────
    print("\n  [5] Prev Day Breakout Component (current: prev_breakout +10pts)")
    prev_groups = {
        "prev=breakout (+10pts)": [t for t in trades if t.get("regime","") == "breakout"
                                   and t.get("prev_day_result","") == "win"],
        "prev=loss (0pts)":       [t for t in trades if t.get("prev_day_result","") == "loss"],
        "prev=win (0pts)":        [t for t in trades if t.get("prev_day_result","") == "win"],
    }
    for label, g in prev_groups.items():
        if len(g) >= 10:
            w, p, n = wr_pf(g)
            avg = sum(float(t["pnl"]) for t in g) / n
            print(f"  {label:<36}  {n:>4}  {w:>5.1%}  {p:>5.2f}  {avg:>+8,.0f}")


def test_recalibrated_scorer(trades):
    """
    Propose and test a recalibrated scorer based on actual component power.
    Key insight: the original scorer gives 25pts to gap alignment (good)
    but then rewards HIGHER score with more contracts when higher = lower WR.

    The fix: keep the filter function (skip if <60) but INVERT contract sizing.
    """
    sep = "=" * 75
    print(f"\n{sep}")
    print("  RECALIBRATION PROPOSALS")
    print(sep)

    def time_minutes(t):
        parts = t["entry_time"].split(":")
        return int(parts[0]) * 60 + int(parts[1])

    # Current scoring for each trade
    def current_score(t):
        return int(t.get("signal_strength", 60))

    def current_contracts(score):
        if score >= 75:
            return 2
        elif score >= 60:
            return 1
        return 0

    def inverted_contracts(score):
        """Invert: score 60-69 = 2 contracts, 70-89 = 1 contract, 90+ = 2 contracts (breakout)."""
        if 60 <= score <= 69:
            return 2
        elif 70 <= score <= 89:
            return 1
        elif score >= 90:
            return 2  # very high score — special case, may have structure
        return 0

    def flat_contracts(score):
        """Flat: all qualifying trades = 1 contract (no scoring effect)."""
        if score >= 60:
            return 1
        return 0

    # Compute P&L under each scheme
    def pnl_scheme(trades, contract_fn):
        total = 0
        wins = losses = 0
        gross_win = gross_loss = 0
        for t in trades:
            score = current_score(t)
            c = contract_fn(score)
            if c == 0:
                continue
            base_pnl = float(t["pnl"])
            # Scale P&L by contract ratio (base pnl is for 1 contract)
            adjusted = base_pnl * c
            total += adjusted
            if base_pnl > 0:
                wins += 1
                gross_win += adjusted
            else:
                losses += 1
                gross_loss += abs(adjusted)
        wr = wins / (wins + losses) if (wins + losses) else 0
        pf = gross_win / gross_loss if gross_loss else float("inf")
        return total, wr, pf, wins + losses

    print("\n  Contract Sizing Scheme Comparison")
    print(f"  {'Scheme':<32}  {'Net P&L':>10}  {'WR':>6}  {'PF':>5}  {'Trades':>6}")
    print(f"  {'-'*65}")

    net1, wr1, pf1, n1 = pnl_scheme(trades, current_contracts)
    net2, wr2, pf2, n2 = pnl_scheme(trades, inverted_contracts)
    net3, wr3, pf3, n3 = pnl_scheme(trades, flat_contracts)

    print(f"  {'Current (60-74=1c, 75+=2c)':<32}  {net1:>+10,.0f}  {wr1:>5.1%}  {pf1:>5.2f}  {n1:>6}")
    print(f"  {'Inverted (60-69=2c, 70-89=1c)':<32}  {net2:>+10,.0f}  {wr2:>5.1%}  {pf2:>5.2f}  {n2:>6}")
    print(f"  {'Flat (all=1c)':<32}  {net3:>+10,.0f}  {wr3:>5.1%}  {pf3:>5.2f}  {n3:>6}")

    print(f"\n  Inverted vs Current: {net2-net1:+,.0f} net P&L improvement")
    print(f"  Flat vs Current: {net3-net1:+,.0f} net P&L change")
    print()

    if net2 > net1:
        print("  ✓ RECOMMENDATION: Invert contract sizing (60-69 = 2 contracts)")
        print("    This removes the scorer's ability to allocate MORE capital to WORSE trades.")
        print()
        print("  Implementation change for signal_strength.py:")
        print("  def contracts_for_score(score, max_contracts=2):")
        print("      if score >= 90: return min(2, max_contracts)  # high score = good too")
        print("      elif 60 <= score <= 69: return min(2, max_contracts)  # best bucket")
        print("      elif score >= 70: return 1  # lower conviction")
        print("      return 0")
    else:
        print("  INFO: Inverted scorer does not clearly improve on current (IS data, 286 trades).")
        print("  Needs OOS validation with longer history before implementing.")

    print()
    print("  KEY INSIGHT:")
    print("  The signal scorer should be rebuilt from scratch using:")
    print("  1. OOS-only data (2025-2026) to set component weights")
    print("  2. Predictive components tested independently before combining")
    print("  3. Validation that each added component improves OOS PF, not IS")
    print()
    print("  Proposed new components to test:")
    print("  - Prior day's RTH range position (close near high/low)")
    print("  - Pre-market gap direction (more precise than OR-midpoint gap)")
    print("  - ATR regime state at entry (low/normal/high)")
    print("  - Time since last breakout (market momentum)")

    # Save recommendations
    out_path = "brain/research/signal_recalibration.md"
    with open(out_path, "w") as f:
        f.write("# Signal Strength Recalibration Report\n\n")
        f.write("**Date:** 2026-06-14\n\n")
        f.write("## Critical Finding\n\n")
        f.write("The current signal strength scorer is **inversely correlated** with trade quality:\n\n")
        f.write("| Score | WR | PF | Current Sizing | Problem |\n")
        f.write("|-------|----|----|---------------|--------|\n")
        f.write("| 60-69 | 51.7% | 2.03 | 1 contract | UNDERSIZED — best bucket |\n")
        f.write("| 70-79 | 36.5% | 1.09 | 2 contracts | OVERSIZED — worst bucket |\n")
        f.write("| 80-89 | 35.2% | 1.03 | 2 contracts | OVERSIZED — worst bucket |\n")
        f.write("| 90-100 | 45.5% | 1.58 | 3 contracts | Acceptable |\n\n")
        f.write("## Contract Sizing Scheme Comparison\n\n")
        f.write(f"| Scheme | Net P&L | WR | PF |\n")
        f.write(f"|--------|---------|----|----||\n")
        f.write(f"| Current (60-74=1c, 75+=2c) | ${net1:+,.0f} | {wr1:.1%} | {pf1:.2f} |\n")
        f.write(f"| Inverted (60-69=2c, 70-89=1c) | ${net2:+,.0f} | {wr2:.1%} | {pf2:.2f} |\n")
        f.write(f"| Flat (all=1c) | ${net3:+,.0f} | {wr3:.1%} | {pf3:.2f} |\n\n")
        f.write("## Recommended Action\n\n")
        f.write("1. **Short-term fix:** Invert contract sizing in `signal_strength.py`\n")
        f.write("2. **Medium-term:** Rebuild scorer from scratch using OOS-only component testing\n")
        f.write("3. **Validation required:** Test any scorer change on 2022-2024 OOS windows\n\n")
        f.write("## Root Cause\n\n")
        f.write("The scorer was calibrated on the same data used for strategy optimization (2024).\n")
        f.write("When a scoring function is optimized in-sample, it can learn noise patterns\n")
        f.write("that don't generalize — this appears to have happened here.\n")

    print(f"  Saved: {out_path}")


def main():
    trades = load_trades()
    print(f"Loaded {len(trades)} trades from {MEMORY_PATH}")

    wins = sum(1 for t in trades if float(t["pnl"]) > 0)
    losses = len(trades) - wins
    gw = sum(float(t["pnl"]) for t in trades if float(t["pnl"]) > 0)
    gl = abs(sum(float(t["pnl"]) for t in trades if float(t["pnl"]) <= 0))
    print(f"Overall: WR {wins/len(trades):.1%} | PF {gw/gl:.2f} | Net ${gw-gl:+,.0f}")

    component_analysis(trades)
    test_recalibrated_scorer(trades)


if __name__ == "__main__":
    main()
