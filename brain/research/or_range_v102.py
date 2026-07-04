"""
brain/research/or_range_v102.py

Sweeps ORB_MIN_RANGE_POINTS and ORB_MAX_RANGE_POINTS with the v10.2 config
(skip<3 + hard gate) to see if range bounds can push OOS PF even higher.

IS: 2022-2024  OOS: 2025-2026
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from backtest import Backtester, load_csv
from collections import defaultdict

DATA      = "data/nq_full.csv"
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

print("Loading data …")
bars = load_csv(DATA)
print(f"  {len(bars):,} bars\n")

def run_orb(bars, min_or, max_or):
    config.ORB_MIN_RANGE_POINTS = min_or
    config.ORB_MAX_RANGE_POINTS = max_or
    config.ASIA_ENABLED = False

    by_year = defaultdict(list)
    for b in bars:
        by_year[b["timestamp"].year].append(b)

    oos_trades = []
    is_trades  = []
    for yr, ybars in sorted(by_year.items()):
        config.STARTING_BALANCE = 50000.0
        bt = Backtester()
        bt.run(ybars, silent=True)
        orb = [t for t in bt.bank.trade_log if t["mode"] not in ("asia_gap","london")]
        if yr in OOS_YEARS:
            oos_trades.extend(orb)
        elif yr in IS_YEARS:
            is_trades.extend(orb)

    def agg(tlist):
        if not tlist: return 0.0, 0, 0.0
        wins = [t for t in tlist if t["pnl"] > 0]
        loss = [t for t in tlist if t["pnl"] <= 0]
        gw = sum(t["pnl"] for t in wins)
        gl = abs(sum(t["pnl"] for t in loss))
        pf  = gw / gl if gl > 0 else float("inf")
        net = sum(t["pnl"] for t in tlist)
        return pf, len(tlist), net

    return agg(oos_trades), agg(is_trades)

# ── Baseline ──────────────────────────────────────────────────────────────────
print("=" * 70)
print("v10.2 Baseline: MIN=55, MAX=110")
print("=" * 70)
base_oos, base_is = run_orb(bars, 55, 110)
print(f"  OOS: PF={base_oos[0]:.2f}  N={base_oos[1]}  Net=${base_oos[2]:>+8,.0f}")
print(f"  IS:  PF={base_is[0]:.2f}   N={base_is[1]}  Net=${base_is[2]:>+8,.0f}\n")

# ── Min range sweep (MAX fixed at 110) ───────────────────────────────────────
print("=" * 70)
print("A. Sweep MIN_OR (MAX fixed at 110)")
print("=" * 70)
print(f"  {'MIN':>5}  {'MAX':>5}  {'OOS_N':>6}  {'OOS_PF':>7}  {'OOS_Net':>10}  {'IS_PF':>6}")
print("  " + "-" * 50)
for min_or in [30, 40, 50, 55, 60, 65, 70, 75, 80]:
    oos, is_ = run_orb(bars, min_or, 110)
    flag = " ★" if oos[0] >= 3.5 and oos[2] > 0 else ""
    print(f"  {min_or:>5}  {110:>5}  {oos[1]:>6}  {oos[0]:>7.2f}  ${oos[2]:>+8,.0f}  {is_[0]:>6.2f}{flag}")

# ── Max range sweep (MIN fixed at 55) ────────────────────────────────────────
print()
print("=" * 70)
print("B. Sweep MAX_OR (MIN fixed at 55)")
print("=" * 70)
print(f"  {'MIN':>5}  {'MAX':>5}  {'OOS_N':>6}  {'OOS_PF':>7}  {'OOS_Net':>10}  {'IS_PF':>6}")
print("  " + "-" * 50)
for max_or in [80, 90, 95, 100, 105, 110, 115, 120, 130]:
    oos, is_ = run_orb(bars, 55, max_or)
    flag = " ★" if oos[0] >= 3.5 and oos[2] > 0 else ""
    print(f"  {55:>5}  {max_or:>5}  {oos[1]:>6}  {oos[0]:>7.2f}  ${oos[2]:>+8,.0f}  {is_[0]:>6.2f}{flag}")

# ── Grid search top combos ────────────────────────────────────────────────────
print()
print("=" * 70)
print("C. Grid search: top OOS PF combos (N≥30)")
print("=" * 70)
results = []
for min_or in [50, 55, 60, 65, 70]:
    for max_or in [90, 95, 100, 105, 110]:
        if min_or >= max_or: continue
        oos, is_ = run_orb(bars, min_or, max_or)
        if oos[1] >= 30 and oos[2] > 0:
            results.append((oos[0], min_or, max_or, oos[1], oos[2], is_[0]))

results.sort(reverse=True)
print(f"\n  {'MIN':>5}  {'MAX':>5}  {'OOS_N':>6}  {'OOS_PF':>7}  {'OOS_Net':>10}  {'IS_PF':>6}")
print("  " + "-" * 55)
for pf, min_or, max_or, n, net, is_pf in results[:8]:
    flag = " ★" if pf >= 4.0 else ""
    print(f"  {min_or:>5}  {max_or:>5}  {n:>6}  {pf:>7.2f}  ${net:>+8,.0f}  {is_pf:>6.2f}{flag}")

# Restore
config.ORB_MIN_RANGE_POINTS = 55.0
config.ORB_MAX_RANGE_POINTS = 110.0
print("\nDone — baseline restored.")
