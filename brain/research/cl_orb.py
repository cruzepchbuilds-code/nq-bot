"""
CL (Crude Oil Futures) Opening Range Breakout Research
=======================================================
OR window  : 9:00-9:14 ET (15 bars, bars 0-14)
Entry window: 9:15-10:00 ET
Direction  : first bar closing > OR_high + 0.10 → long
             first bar closing < OR_low  - 0.10 → short
Stop       : 0.50 fixed (=$500/contract)
Target     : 1.00 from entry  (=$1000/contract)
Flatten    : 10:00 ET hard close
Skip       : Monday (weekday==0) and Friday (weekday==4)
One trade per day

POINT_VALUE = $1000/point   commission = $4/trade
"""

import sys
sys.path.insert(0, '/Users/Cruz/Desktop/nq_bot_final-main')

from backtest import load_csv
from datetime import time, datetime
from collections import defaultdict

# ── Constants ───────────────────────────────────────────────────────────────
POINT_VALUE = 1000       # CL: $1000 per full point
COMMISSION  = 4.0        # per round-trip trade
STOP_PTS    = 0.50
TARGET_PTS  = 1.00
OR_ENTRY_OFFSET = 0.10   # breakout buffer

IS_START  = datetime(2022, 1, 1)
IS_END    = datetime(2024, 12, 31, 23, 59)
OOS_START = datetime(2025, 1, 1)
OOS_END   = datetime(2026, 6, 30, 23, 59)

OR_BUILD_START = time(9, 0)
OR_BUILD_END   = time(9, 14)   # last bar included in OR (close of 9:14)
ENTRY_START    = time(9, 15)
ENTRY_END      = time(10, 0)   # also hard flatten time
FLATTEN_TIME   = time(10, 0)

DOW_NAMES = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri'}


# ── Data load ────────────────────────────────────────────────────────────────
print("=" * 65)
print("CL Opening Range Breakout Research")
print("=" * 65)

raw = load_csv('data/cl_1min.csv')
print(f"\nData loaded: {len(raw):,} bars")
print(f"Date range : {raw[0]['timestamp']} → {raw[-1]['timestamp']}")
print("\nSample rows:")
for b in raw[100:105]:
    print(f"  {b['timestamp']}  O={b['open']}  H={b['high']}  L={b['low']}  C={b['close']}")


# ── Group bars by date ───────────────────────────────────────────────────────
def group_by_date(bars):
    days = defaultdict(list)
    for b in bars:
        days[b['timestamp'].date()].append(b)
    return days


# ── Core backtest per day ────────────────────────────────────────────────────
def run_day(day_bars, or_min_range=None, or_max_range=None):
    """
    Returns dict with trade info or None if no trade taken.
    or_min_range / or_max_range: optional CL-point filters on OR size.
    """
    # Sort by time
    day_bars = sorted(day_bars, key=lambda b: b['timestamp'])
    dt = day_bars[0]['timestamp']

    # Skip Mon and Fri
    dow = dt.weekday()
    if dow in (0, 4):
        return None

    # Build OR
    or_high = None
    or_low  = None
    for b in day_bars:
        t = b['timestamp'].time()
        if t >= OR_BUILD_START and t <= OR_BUILD_END:
            if or_high is None:
                or_high = b['high']
                or_low  = b['low']
            else:
                or_high = max(or_high, b['high'])
                or_low  = min(or_low,  b['low'])

    if or_high is None or or_low is None:
        return None   # no OR bars found

    or_range = round(or_high - or_low, 4)

    # Apply OR size filter
    if or_min_range is not None and or_range < or_min_range:
        return None
    if or_max_range is not None and or_range > or_max_range:
        return None

    # Entry scan
    long_trigger  = or_high + OR_ENTRY_OFFSET
    short_trigger = or_low  - OR_ENTRY_OFFSET

    entry_price = None
    direction   = None
    entry_time  = None

    for b in day_bars:
        t = b['timestamp'].time()
        if t < ENTRY_START:
            continue
        if t > ENTRY_END:
            break

        if direction is None:
            if b['close'] > long_trigger:
                direction   = 'long'
                entry_price = b['close']
                entry_time  = t
            elif b['close'] < short_trigger:
                direction   = 'short'
                entry_price = b['close']
                entry_time  = t
        if direction is not None:
            break   # one trade per day

    if direction is None:
        return None   # no signal

    # Simulate trade on subsequent bars
    stop_price   = entry_price - STOP_PTS if direction == 'long' else entry_price + STOP_PTS
    target_price = entry_price + TARGET_PTS if direction == 'long' else entry_price - TARGET_PTS

    exit_price  = None
    exit_reason = None

    entry_bar_idx = None
    for i, b in enumerate(day_bars):
        if b['timestamp'].time() == entry_time:
            entry_bar_idx = i
            break

    if entry_bar_idx is None:
        return None

    for b in day_bars[entry_bar_idx + 1:]:
        t = b['timestamp'].time()

        if direction == 'long':
            # Check stop first (low)
            if b['low'] <= stop_price:
                exit_price  = stop_price
                exit_reason = 'stop'
                break
            # Check target (high)
            if b['high'] >= target_price:
                exit_price  = target_price
                exit_reason = 'target'
                break
        else:  # short
            if b['high'] >= stop_price:
                exit_price  = stop_price
                exit_reason = 'stop'
                break
            if b['low'] <= target_price:
                exit_price  = target_price
                exit_reason = 'target'
                break

        # Hard flatten at 10:00
        if t >= FLATTEN_TIME:
            exit_price  = b['close']
            exit_reason = 'flatten'
            break

    if exit_price is None:
        # Never reached flatten bar — use last bar's close
        exit_price  = day_bars[-1]['close']
        exit_reason = 'flatten'

    if direction == 'long':
        pnl = (exit_price - entry_price) * POINT_VALUE - COMMISSION
    else:
        pnl = (entry_price - exit_price) * POINT_VALUE - COMMISSION

    return {
        'date'      : dt.date(),
        'dow'       : dow,
        'year'      : dt.year,
        'direction' : direction,
        'entry'     : entry_price,
        'exit'      : exit_price,
        'stop'      : stop_price,
        'target'    : target_price,
        'reason'    : exit_reason,
        'pnl'       : round(pnl, 2),
        'or_range'  : or_range,
    }


# ── Helper stats ─────────────────────────────────────────────────────────────
def stats(trades, label=""):
    if not trades:
        return {'label': label, 'N': 0, 'WR': 0, 'PF': 0, 'Net': 0, 'Avg': 0}
    n    = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    loss = [t for t in trades if t['pnl'] <= 0]
    gross_win  = sum(t['pnl'] for t in wins)
    gross_loss = abs(sum(t['pnl'] for t in loss))
    pf   = round(gross_win / gross_loss, 2) if gross_loss > 0 else 9.99
    net  = round(sum(t['pnl'] for t in trades), 0)
    avg  = round(net / n, 0)
    wr   = round(100 * len(wins) / n, 1)
    return {'label': label, 'N': n, 'WR': wr, 'PF': pf, 'Net': net, 'Avg': avg}


def print_stats_table(rows, title):
    print(f"\n{title}")
    print(f"{'Label':<25} {'N':>5} {'WR%':>6} {'PF':>6} {'Net$':>10} {'Avg$/tr':>9}")
    print("-" * 65)
    for r in rows:
        print(f"{r['label']:<25} {r['N']:>5} {r['WR']:>5.1f}% {r['PF']:>6.2f} {r['Net']:>10,.0f} {r['Avg']:>9,.0f}")


# ── Split data ───────────────────────────────────────────────────────────────
all_days = group_by_date(raw)

is_days  = {d: v for d, v in all_days.items()
            if IS_START.date()  <= d <= IS_END.date()}
oos_days = {d: v for d, v in all_days.items()
            if OOS_START.date() <= d <= OOS_END.date()}

print(f"\nIS  days: {len(is_days)}   OOS days: {len(oos_days)}")


# ── Baseline runs ─────────────────────────────────────────────────────────────
def run_all(day_dict, or_min=None, or_max=None):
    trades = []
    for d, bars in sorted(day_dict.items()):
        result = run_day(bars, or_min, or_max)
        if result:
            trades.append(result)
    return trades

is_baseline  = run_all(is_days)
oos_baseline = run_all(oos_days)


# ════════════════════════════════════════════════════════════════════════════
# PART 1 — OR range filter sweep (OOS)
# ════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("PART 1 — OR Range Filter Sweep (OOS 2025-2026)")
print("=" * 65)

filters = [
    ("0.20-0.80",    0.20, 0.80),
    ("0.30-1.00",    0.30, 1.00),
    ("0.30-1.50",    0.30, 1.50),
    ("0.50-2.00",    0.50, 2.00),
    ("0.50-3.00",    0.50, 3.00),
    ("baseline-no-filter", None, None),
]

sweep_rows = []
for label, mn, mx in filters:
    trades = run_all(oos_days, mn, mx)
    sweep_rows.append(stats(trades, label))

sweep_rows.sort(key=lambda r: r['PF'], reverse=True)
print_stats_table(sweep_rows, "OOS OR-range filter sweep (sorted by PF)")


# ════════════════════════════════════════════════════════════════════════════
# PART 2 — DOW Breakdown (OOS baseline)
# ════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("PART 2 — Day-of-Week Breakdown (OOS baseline, Mon/Fri skipped)")
print("=" * 65)

dow_groups = defaultdict(list)
for t in oos_baseline:
    dow_groups[t['dow']].append(t)

dow_rows = []
for dow in sorted(dow_groups.keys()):
    name = DOW_NAMES[dow]
    dow_rows.append(stats(dow_groups[dow], name))

print_stats_table(dow_rows, "OOS DOW breakdown")


# ════════════════════════════════════════════════════════════════════════════
# PART 3 — Year-by-Year OOS
# ════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("PART 3 — Year-by-Year OOS")
print("=" * 65)

year_groups = defaultdict(list)
for t in oos_baseline:
    year_groups[t['year']].append(t)

year_rows = []
for yr in sorted(year_groups.keys()):
    year_rows.append(stats(year_groups[yr], str(yr)))

print_stats_table(year_rows, "OOS year-by-year")


# ════════════════════════════════════════════════════════════════════════════
# PART 4 — IS vs OOS for best filter
# ════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("PART 4 — IS vs OOS Comparison (best OR filter + baseline)")
print("=" * 65)

# Best filter = top PF from sweep
best = sweep_rows[0]
best_label = best['label']
# Find params for that label
best_mn, best_mx = None, None
for label, mn, mx in filters:
    if label == best_label:
        best_mn, best_mx = mn, mx
        break

is_best    = run_all(is_days,  best_mn, best_mx)
oos_best   = run_all(oos_days, best_mn, best_mx)

compare_rows = [
    stats(is_baseline,  f"IS  baseline-no-filter"),
    stats(oos_baseline, f"OOS baseline-no-filter"),
    stats(is_best,      f"IS  {best_label}"),
    stats(oos_best,     f"OOS {best_label}"),
]
print_stats_table(compare_rows, "IS vs OOS comparison")


# ════════════════════════════════════════════════════════════════════════════
# VERDICT
# ════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("VERDICT")
print("=" * 65)

oos_s = stats(oos_baseline)
is_s  = stats(is_baseline)

print(f"\nIS  baseline : N={is_s['N']}  WR={is_s['WR']}%  PF={is_s['PF']}  Net=${is_s['Net']:,.0f}  Avg=${is_s['Avg']:,.0f}")
print(f"OOS baseline : N={oos_s['N']}  WR={oos_s['WR']}%  PF={oos_s['PF']}  Net=${oos_s['Net']:,.0f}  Avg=${oos_s['Avg']:,.0f}")
print()

if oos_s['PF'] >= 1.3:
    verdict = "STRONG EDGE — OOS PF >= 1.30. CL ORB appears tradeable."
elif oos_s['PF'] >= 1.1:
    verdict = "MARGINAL EDGE — OOS PF 1.10-1.30. More conditions/filters needed."
else:
    verdict = "NO EDGE — OOS PF < 1.10. CL raw ORB not tradeable as defined."

print(f"  {verdict}")
print()

# OR range distribution for context
ranges = [t['or_range'] for t in oos_baseline]
if ranges:
    ranges.sort()
    p25 = ranges[len(ranges)//4]
    p50 = ranges[len(ranges)//2]
    p75 = ranges[3*len(ranges)//4]
    print(f"OOS OR-range distribution (points):  p25={p25:.2f}  p50={p50:.2f}  p75={p75:.2f}  min={min(ranges):.2f}  max={max(ranges):.2f}")

# Reason breakdown
reasons = defaultdict(int)
for t in oos_baseline:
    reasons[t['reason']] += 1
print(f"OOS exit reasons: {dict(reasons)}")

# Direction breakdown
longs  = [t for t in oos_baseline if t['direction'] == 'long']
shorts = [t for t in oos_baseline if t['direction'] == 'short']
ls = stats(longs, 'long')
ss2 = stats(shorts, 'short')
print(f"\nOOS long trades :  N={ls['N']}  WR={ls['WR']}%  PF={ls['PF']}  Net=${ls['Net']:,.0f}")
print(f"OOS short trades:  N={ss2['N']}  WR={ss2['WR']}%  PF={ss2['PF']}  Net=${ss2['Net']:,.0f}")
print()
print("Done.")
