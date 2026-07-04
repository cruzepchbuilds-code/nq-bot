"""
brain/research/dow_analysis.py

Day-of-week analysis for US ORB with v10.2 config (skip<3 + hard gate).
Tests whether the existing SKIP_MONDAYS=True is still optimal, and whether
any other days should be skipped.

IS: 2022-2024  OOS: 2025-2026
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from backtest import Backtester, load_csv
from collections import defaultdict
from datetime import time as dtime, datetime

DATA      = "data/nq_full.csv"
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]
DAYS      = ["Mon", "Tue", "Wed", "Thu", "Fri"]

print("Loading data …")
bars = load_csv(DATA)
print(f"  {len(bars):,} bars\n")

# Run full backtest (fresh bankroll per year, Asia on, current config)
config.ASIA_ENABLED = False  # US ORB only for clean analysis
by_year = defaultdict(list)
for b in bars:
    by_year[b["timestamp"].year].append(b)

all_trades = []
for yr, ybars in sorted(by_year.items()):
    config.STARTING_BALANCE = 50000.0
    bt = Backtester()
    bt.run(ybars, silent=True)
    for t in bt.bank.trade_log:
        if t["mode"] not in ("asia_gap", "london"):
            dow = datetime.strptime(t["date"], "%Y-%m-%d").weekday()  # 0=Mon, 4=Fri
            all_trades.append({**t, "year": yr, "dow": dow})

print(f"Total US ORB trades: {len(all_trades)}\n")

def pf_from_trades(tlist):
    if not tlist: return 0.0, 0, 0, 0.0
    wins = [t for t in tlist if t["pnl"] > 0]
    loss = [t for t in tlist if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in loss))
    pf  = gw / gl if gl > 0 else float("inf")
    net = sum(t["pnl"] for t in tlist)
    return pf, len(wins), len(tlist), net

# ── A. DOW breakdown ─────────────────────────────────────────────────────────
print("=" * 65)
print("A. Day-of-Week Performance")
print("=" * 65)

for split, years in [("IS", IS_YEARS), ("OOS", OOS_YEARS)]:
    subset = [t for t in all_trades if t["year"] in years]
    pf_all, wins_all, n_all, net_all = pf_from_trades(subset)
    print(f"\n  {split} (N={n_all}, PF={pf_all:.2f}, Net=${net_all:>+8,.0f}):")
    print(f"  {'DOW':<8}  {'N':>4}  {'WR':>5}  {'PF':>6}  {'Net':>10}  {'$/trade':>9}")
    print("  " + "-" * 55)
    for di, day in enumerate(DAYS):
        day_t = [t for t in subset if t["dow"] == di]
        pf, wins, n, net = pf_from_trades(day_t)
        wr = wins / n if n else 0
        avg = net / n if n else 0
        flag = " ★SKIP" if pf < 1.0 else (" ← good" if pf >= 3.0 else "")
        print(f"  {day:<8}  {n:>4}  {wr:.0%}  {pf:>6.2f}  ${net:>+8,.0f}  ${avg:>+7,.0f}{flag}")

# ── B. Skip combos ───────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("B. Baseline vs. Skip-Monday vs. Other Skip Combos (OOS)")
print("=" * 65)

oos = [t for t in all_trades if t["year"] in OOS_YEARS]
iss = [t for t in all_trades if t["year"] in IS_YEARS]

skip_tests = [
    ("No skip (trade all days)",   set()),
    ("Skip Mon (current)",         {0}),
    ("Skip Mon+Fri",               {0, 4}),
    ("Skip Mon+Thu",               {0, 3}),
    ("Skip Fri only",              {4}),
    ("Skip Mon+Tue",               {0, 1}),
]

print(f"\n  {'Config':<30}  {'OOS N':>5}  {'OOS PF':>7}  {'OOS Net':>10}  {'IS N':>5}  {'IS PF':>6}")
print("  " + "-" * 70)
for lbl, skip_days in skip_tests:
    oos_sub = [t for t in oos if t["dow"] not in skip_days]
    is_sub  = [t for t in iss if t["dow"] not in skip_days]
    pf_o, wins_o, n_o, net_o = pf_from_trades(oos_sub)
    pf_i, wins_i, n_i, net_i = pf_from_trades(is_sub)
    star = " ★" if pf_o >= 3.0 else ""
    print(f"  {lbl:<30}  {n_o:>5}  {pf_o:>7.2f}  ${net_o:>+8,.0f}  {n_i:>5}  {pf_i:>6.2f}{star}")

# ── C. Current config note ────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("C. Current SKIP_MONDAYS setting in live config")
print("=" * 65)
print(f"\n  config.SKIP_MONDAYS = {config.SKIP_MONDAYS}")
print("  NOTE: backtest.py applies SKIP_MONDAYS inside run(). The trades above")
print("  include or exclude Mondays depending on that setting.\n")
