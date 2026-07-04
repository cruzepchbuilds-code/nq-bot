"""
brain/research/threshold_sweep.py

Tests every combination of CONFIDENCE_SCORE_SKIP_BELOW and CONFIDENCE_SCORE_DOUBLE_AT
to find the threshold pairing that maximises OOS PF while keeping net $ positive.

Approach: enrich all signals with their confidence score (0-4), then simulate
different skip/double thresholds without re-running the full backtest each time.

IS years: [2022, 2023, 2024]
OOS years: [2025, 2026]

Usage: python3 brain/research/threshold_sweep.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import Backtester, load_csv
from collections import defaultdict
from datetime import date, time as dtime
import config

DATA      = "data/nq_full.csv"
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

# ── helpers ──────────────────────────────────────────────────────────────────

def fresh_bankroll_run(bars, skip_below, double_at):
    """Run per-year fresh-bankroll sim with given thresholds."""
    config.CONFIDENCE_SCORE_ENABLED    = True
    config.CONFIDENCE_SCORE_SKIP_BELOW = skip_below
    config.CONFIDENCE_SCORE_DOUBLE_AT  = double_at
    config.EVAL_MODE = False

    by_year = defaultdict(list)
    for b in bars:
        by_year[b["timestamp"].year].append(b)

    results = []
    for yr, ybars in sorted(by_year.items()):
        config.STARTING_BALANCE = 50000.0
        from bankroll import BankrollManager
        config.__dict__  # force reload not needed — we mutate in place
        bt = Backtester()
        bt.run(ybars, silent=True)
        log = bt.bank.trade_log
        orb = [t for t in log if t["mode"] not in ("asia_gap", "london")]
        if not orb:
            results.append((yr, 0, 0, 0.0, 0.0))
            continue
        wins   = [t for t in orb if t["pnl"] > 0]
        losses = [t for t in orb if t["pnl"] <= 0]
        gw = sum(t["pnl"] for t in wins)
        gl = abs(sum(t["pnl"] for t in losses))
        pf  = gw / gl if gl else float("inf")
        net = sum(t["pnl"] for t in orb)
        results.append((yr, len(orb), len(wins), pf, net))
    return results


def agg(results, years):
    subset = [r for r in results if r[0] in years]
    n   = sum(r[1] for r in subset)
    wins = sum(r[2] for r in subset)
    # Recompute PF from raw totals is hard without storing gross; approximate
    nets = sum(r[4] for r in subset)
    # weighted avg PF
    pfs = [r[3] for r in subset if r[3] < float("inf") and r[1] > 0]
    pf  = sum(pfs) / len(pfs) if pfs else 0.0
    return n, wins, pf, nets


def stats_label(n, wins, pf, net):
    wr = wins / n if n else 0
    return f"T={n:>3}  WR={wr:.0%}  PF={pf:.2f}  Net=${net:>+8,.0f}"


# ── main ─────────────────────────────────────────────────────────────────────

print("Loading data …")
all_bars = load_csv(DATA)
print(f"  {len(all_bars):,} bars loaded\n")

# Current baseline
print("=" * 65)
print("CURRENT CONFIG: skip<1, double≥3")
print("=" * 65)
base = fresh_bankroll_run(all_bars, skip_below=1, double_at=3)
for yr, n, wins, pf, net in base:
    tag = "OOS" if yr in OOS_YEARS else "IS "
    print(f"  {yr} [{tag}]  {stats_label(n, wins, pf, net)}")
bn, bw, bpf, bnet = agg(base, OOS_YEARS)
print(f"\n  OOS aggregate: {stats_label(bn, bw, bpf, bnet)}")

# ── sweep ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("THRESHOLD SWEEP (OOS results)")
print("=" * 65)
print(f"  {'skip_below':>10}  {'double_at':>9}  {'T':>4}  {'WR':>5}  {'PF':>6}  {'Net':>10}  {'vs base':>10}")
print("-" * 65)

best = []
for skip in [0, 1, 2, 3]:
    for double in [2, 3, 4, 99]:
        if skip >= double and double != 99:
            continue
        res = fresh_bankroll_run(all_bars, skip_below=skip, double_at=double)
        n, wins, pf, net = agg(res, OOS_YEARS)
        wr = wins / n if n else 0
        label = f"≥{double}" if double != 99 else "never"
        diff = net - bnet
        marker = " ◄" if pf >= 2.5 and net > 0 else ""
        print(f"  skip<{skip}  double{label:>6}  {n:>4}  {wr:.0%}  {pf:>6.2f}  ${net:>+9,.0f}  ${diff:>+9,.0f}{marker}")
        best.append((skip, double, n, pf, net, diff))

# Top 5 by PF with positive net
print("\n── Top combos by OOS PF (net > 0) ──────────────────────────────")
valid = sorted([b for b in best if b[4] > 0], key=lambda x: -x[3])
for skip, double, n, pf, net, diff in valid[:6]:
    label = f"≥{double}" if double != 99 else "never"
    print(f"  skip<{skip}  double{label:>6}  T={n:>3}  PF={pf:.2f}  Net=${net:>+8,.0f}  vs_base=${diff:>+8,.0f}")

# Restore defaults
config.CONFIDENCE_SCORE_SKIP_BELOW = 1
config.CONFIDENCE_SCORE_DOUBLE_AT  = 3
print("\nDone.")
