"""
PM ORB Parameter Sweep — OOS 2025-01-01 to 2026-06-30
Tests OR range size limits and entry window end times.
Fixed: Tue-Thu only, stop=22pt, RR=2.0, buffer=2pt, OR window 13:00-13:14

OR range variations tested:
  10-40, 10-50, 10-60, 15-50 (baseline), 15-60, 20-50

Entry window variations (OR always 13:00-13:14):
  13:15-14:00, 13:15-14:15 (baseline), 13:15-14:30, 13:15-14:45

All combos printed, sorted by PF. Baseline marked with *.
"""

import sys
import os
from datetime import datetime, date, time, timedelta
from collections import defaultdict
from itertools import product

sys.path.insert(0, '/Users/Cruz/Desktop/nq_bot_final-main')
from backtest import load_csv

# ── Constants ─────────────────────────────────────────────────────────────────
POINT_VALUE = 20
COMMISSION  = 5      # per trade round-trip
STOP_PT     = 22.0
RR          = 2.0
TARGET_PT   = STOP_PT * RR   # 44pt
BUFFER      = 2.0

OR_START    = time(13, 0)
OR_END      = time(13, 14)   # last bar included in OR
ENTRY_START = time(13, 15)   # first possible entry bar

OOS_START   = date(2025, 1, 1)
OOS_END     = date(2026, 6, 30)

# Baseline params
BASELINE_OR_MIN    = 15.0
BASELINE_OR_MAX    = 50.0
BASELINE_ENTRY_END = time(14, 15)

# Sweep axes
OR_RANGES = [
    (10, 40),
    (10, 50),
    (10, 60),
    (15, 50),   # baseline
    (15, 60),
    (20, 50),
]

ENTRY_ENDS = [
    time(14, 0),
    time(14, 15),   # baseline
    time(14, 30),
    time(14, 45),
]

# We'll use EOD as hard flatten (after entry window no new entries, but exits allowed)
HARD_FLATTEN = time(15, 55)  # near EOD for PM session


def pf(wins, losses):
    if losses == 0:
        return float('inf') if wins > 0 else 0.0
    return wins / losses


def simulate_trade(entry_price, direction, day_bars, entry_ts):
    """Simulate one trade: scan forward bars for target/stop/flatten."""
    if direction == 'long':
        stop_price   = entry_price - STOP_PT
        target_price = entry_price + TARGET_PT
    else:
        stop_price   = entry_price + STOP_PT
        target_price = entry_price - TARGET_PT

    future = [b for b in day_bars if b['ts'] > entry_ts
              and b['ts'].time() <= HARD_FLATTEN]

    for fb in future:
        if direction == 'long':
            if fb['high'] >= target_price:
                return TARGET_PT * POINT_VALUE - COMMISSION
            if fb['low'] <= stop_price:
                return -STOP_PT * POINT_VALUE - COMMISSION
        else:
            if fb['low'] <= target_price:
                return TARGET_PT * POINT_VALUE - COMMISSION
            if fb['high'] >= stop_price:
                return -STOP_PT * POINT_VALUE - COMMISSION

    # Flatten at end of session
    flatten_bars = [b for b in day_bars if b['ts'].time() >= HARD_FLATTEN]
    if flatten_bars:
        exit_price = flatten_bars[0]['open']
    elif future:
        exit_price = future[-1]['close']
    else:
        return -COMMISSION  # no bars, just pay commission

    if direction == 'long':
        return (exit_price - entry_price) * POINT_VALUE - COMMISSION
    else:
        return (entry_price - exit_price) * POINT_VALUE - COMMISSION


def run():
    print("Loading data...")
    bars = load_csv('/Users/Cruz/Desktop/nq_bot_final-main/data/nq_full.csv')
    print(f"  {len(bars)} bars loaded")

    # Normalize: load_csv uses 'timestamp' key; alias to 'ts' for convenience
    # Strip tzinfo if present so all datetimes are naive (CSV is US/Eastern local time)
    for b in bars:
        ts = b['timestamp']
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        b['ts'] = ts

    # Group by date, filter OOS only
    by_date = defaultdict(list)
    for b in bars:
        d = b['ts'].date()
        if OOS_START <= d <= OOS_END:
            by_date[d].append(b)

    # Filter Tue-Thu
    trade_dates = sorted(d for d in by_date if d.weekday() in (1, 2, 3))

    print(f"  OOS trade dates (Tue-Thu): {len(trade_dates)}")
    print()

    # ── Run all combos ────────────────────────────────────────────────────────
    results = []

    for (or_min, or_max), entry_end in product(OR_RANGES, ENTRY_ENDS):
        is_baseline = (
            or_min == BASELINE_OR_MIN and
            or_max == BASELINE_OR_MAX and
            entry_end == BASELINE_ENTRY_END
        )

        trades = []

        for d in trade_dates:
            day_bars = sorted(by_date[d], key=lambda b: b['ts'])

            # Build OR (13:00-13:14)
            or_bars = [b for b in day_bars
                       if OR_START <= b['ts'].time() <= OR_END]
            if len(or_bars) < 5:
                continue

            or_high = max(b['high'] for b in or_bars)
            or_low  = min(b['low']  for b in or_bars)
            or_size = or_high - or_low

            if not (or_min <= or_size <= or_max):
                continue

            # Entry bars (13:15 to entry_end)
            entry_bars = [b for b in day_bars
                          if ENTRY_START <= b['ts'].time() <= entry_end]

            trade_done = False
            for b in entry_bars:
                if trade_done:
                    break
                c = b['close']

                if c > or_high + BUFFER:
                    pnl = simulate_trade(c, 'long', day_bars, b['ts'])
                    trades.append(pnl)
                    trade_done = True
                elif c < or_low - BUFFER:
                    pnl = simulate_trade(c, 'short', day_bars, b['ts'])
                    trades.append(pnl)
                    trade_done = True

        N = len(trades)
        if N == 0:
            results.append({
                'or_min': or_min, 'or_max': or_max,
                'entry_end': entry_end, 'N': 0,
                'wr': 0.0, 'pf': 0.0, 'net': 0.0,
                'baseline': is_baseline,
            })
            continue

        wins   = [p for p in trades if p > 0]
        losses = [p for p in trades if p <= 0]
        gw     = sum(wins)
        gl     = abs(sum(losses))
        net    = sum(trades)
        wr     = len(wins) / N * 100
        _pf    = pf(gw, gl)

        results.append({
            'or_min': or_min, 'or_max': or_max,
            'entry_end': entry_end, 'N': N,
            'wr': wr, 'pf': _pf, 'net': net,
            'baseline': is_baseline,
        })

    # Sort by PF descending
    results.sort(key=lambda r: r['pf'], reverse=True)

    # ── Print results ─────────────────────────────────────────────────────────
    print("=" * 80)
    print("  PM ORB PARAMETER SWEEP — OOS 2025-01-01 to 2026-06-30  (Tue-Thu)")
    print("=" * 80)
    print(f"  Fixed: OR 13:00-13:14, Entry start 13:15, Stop={STOP_PT}pt, "
          f"Target={TARGET_PT}pt, Buffer={BUFFER}pt")
    print()
    print(f"  {'OR Range':<12} {'Entry Window':<22} {'N':>5} {'WR%':>7} "
          f"{'PF':>7} {'Net$':>10}  {'*'}")
    print(f"  {'-'*12} {'-'*22} {'-'*5} {'-'*7} {'-'*7} {'-'*10}  {'-'}")

    for r in results:
        baseline_mark = '*' if r['baseline'] else ''
        or_label     = f"{r['or_min']:.0f}-{r['or_max']:.0f}pt"
        entry_label  = f"13:15-{r['entry_end'].strftime('%H:%M')}"
        if r['N'] == 0:
            print(f"  {or_label:<12} {entry_label:<22} {'0':>5}  (no trades)")
            continue
        print(f"  {or_label:<12} {entry_label:<22} {r['N']:>5} {r['wr']:>7.1f} "
              f"{r['pf']:>7.3f} {r['net']:>10,.0f}  {baseline_mark}")

    # Baseline summary
    baseline = next((r for r in results if r['baseline']), None)
    if baseline:
        print()
        print(f"  Baseline (15-50pt, 13:15-14:15): "
              f"N={baseline['N']}, WR={baseline['wr']:.1f}%, "
              f"PF={baseline['pf']:.3f}, Net=${baseline['net']:,.0f}")

    print()

    # Also show grouped by OR range and by entry window
    print("  --- By OR Range (all entry windows pooled) ---")
    for (or_min, or_max) in OR_RANGES:
        subset = [r for r in results
                  if r['or_min'] == or_min and r['or_max'] == or_max]
        total_trades = [r for r in subset if r['N'] > 0]
        if not total_trades:
            continue
        # Average PF across entry windows
        avg_pf = sum(r['pf'] for r in total_trades) / len(total_trades)
        best_pf = max(r['pf'] for r in total_trades)
        print(f"    OR {or_min:.0f}-{or_max:.0f}pt: avg_PF={avg_pf:.3f}, best_PF={best_pf:.3f}")

    print()
    print("  --- By Entry Window (all OR ranges pooled) ---")
    for entry_end in ENTRY_ENDS:
        subset = [r for r in results if r['entry_end'] == entry_end and r['N'] > 0]
        if not subset:
            continue
        avg_pf = sum(r['pf'] for r in subset) / len(subset)
        best_pf = max(r['pf'] for r in subset)
        print(f"    Entry 13:15-{entry_end.strftime('%H:%M')}: avg_PF={avg_pf:.3f}, best_PF={best_pf:.3f}")


if __name__ == '__main__':
    run()
