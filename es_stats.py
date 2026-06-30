"""
ES Statistics Analyzer
Loads es_1min.csv and computes opening range and daily range statistics
for RTH session (9:30-16:00 ET).
"""

import csv
from datetime import datetime, time
from collections import defaultdict

RTH_START = time(9, 30)
OR_END    = time(9, 45)
RTH_END   = time(16, 0)


def load_csv(path):
    bars = []
    with open(path) as f:
        for row in csv.DictReader(f):
            bars.append({
                "timestamp": datetime.fromisoformat(row["timestamp"]),
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": float(row.get("volume", 0)),
            })
    return bars


def percentile(data, pct):
    """Simple percentile calculation without numpy."""
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n == 0:
        return 0.0
    idx = (pct / 100) * (n - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= n:
        return sorted_data[lo]
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


def main():
    path = "data/es_1min.csv"
    print(f"Loading {path}...")
    bars = load_csv(path)
    print(f"Total bars: {len(bars):,}")
    print(f"Date range: {bars[0]['timestamp'].date()} to {bars[-1]['timestamp'].date()}")
    print()

    # Group bars by date, RTH only
    days = defaultdict(list)
    for bar in bars:
        t = bar["timestamp"].time()
        if RTH_START <= t <= RTH_END:
            days[bar["timestamp"].date()].append(bar)

    print(f"RTH trading days found: {len(days)}")
    print()

    or_sizes = []
    daily_ranges = []
    session_volumes = []

    for day_date, day_bars in sorted(days.items()):
        # Opening Range bars: 9:30-9:44 (i.e., t < 9:45)
        or_bars  = [b for b in day_bars if b["timestamp"].time() < OR_END]
        all_bars = day_bars

        if len(or_bars) < 5:   # need at least 5 bars for a valid OR
            continue

        or_high = max(b["high"]  for b in or_bars)
        or_low  = min(b["low"]   for b in or_bars)
        or_size = or_high - or_low

        day_high = max(b["high"] for b in all_bars)
        day_low  = min(b["low"]  for b in all_bars)
        day_rng  = day_high - day_low

        day_vol = sum(b["volume"] for b in all_bars)

        or_sizes.append(or_size)
        daily_ranges.append(day_rng)
        session_volumes.append(day_vol)

    print("=" * 60)
    print("  OPENING RANGE STATISTICS (9:30-9:45, 15 min)")
    print("=" * 60)
    print(f"  Days analyzed: {len(or_sizes)}")
    print(f"  Mean OR size:    {sum(or_sizes)/len(or_sizes):.2f} pts")
    print(f"  Median OR size:  {percentile(or_sizes, 50):.2f} pts")
    print(f"  10th pct:        {percentile(or_sizes, 10):.2f} pts")
    print(f"  25th pct:        {percentile(or_sizes, 25):.2f} pts")
    print(f"  75th pct:        {percentile(or_sizes, 75):.2f} pts")
    print(f"  90th pct:        {percentile(or_sizes, 90):.2f} pts")
    print(f"  Min OR size:     {min(or_sizes):.2f} pts")
    print(f"  Max OR size:     {max(or_sizes):.2f} pts")
    print()

    print("=" * 60)
    print("  DAILY RANGE STATISTICS (Full RTH session)")
    print("=" * 60)
    print(f"  Mean daily range:   {sum(daily_ranges)/len(daily_ranges):.2f} pts")
    print(f"  Median daily range: {percentile(daily_ranges, 50):.2f} pts")
    print(f"  10th pct:           {percentile(daily_ranges, 10):.2f} pts")
    print(f"  25th pct:           {percentile(daily_ranges, 25):.2f} pts")
    print(f"  75th pct:           {percentile(daily_ranges, 75):.2f} pts")
    print(f"  90th pct:           {percentile(daily_ranges, 90):.2f} pts")
    print()

    print("=" * 60)
    print("  SESSION VOLUME STATISTICS")
    print("=" * 60)
    avg_vol = sum(session_volumes) / len(session_volumes)
    print(f"  Mean session volume:   {avg_vol:,.0f}")
    print(f"  Median session volume: {percentile(session_volumes, 50):,.0f}")
    print()

    print("=" * 60)
    print("  OR SIZE DISTRIBUTION")
    print("=" * 60)
    bins = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 40), (40, 60), (60, 80), (80, 999)]
    total = len(or_sizes)
    for lo, hi in bins:
        count = sum(1 for s in or_sizes if lo <= s < hi)
        pct   = count / total * 100
        label = f"<{hi}" if lo == 0 else f"{lo}-{hi}" if hi < 999 else f"{lo}+"
        print(f"  OR {label:>10} pts: {count:>4} days ({pct:.1f}%)")
    print()

    # Cumulative distribution for filter thresholds
    print("=" * 60)
    print("  CUMULATIVE OR DISTRIBUTION (for filter calibration)")
    print("=" * 60)
    thresholds = [5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40, 50, 60, 80, 100]
    for thr in thresholds:
        pct_below = sum(1 for s in or_sizes if s < thr) / total * 100
        pct_above = 100 - pct_below
        print(f"  OR < {thr:>3} pts: {pct_below:5.1f}% of days  |  OR >= {thr:>3}: {pct_above:5.1f}% of days")
    print()

    # Upper tail for max filter
    print("=" * 60)
    print("  UPPER TAIL (for max range filter)")
    print("=" * 60)
    upper_thresholds = [30, 40, 50, 60, 70, 80, 100]
    for thr in upper_thresholds:
        pct_above = sum(1 for s in or_sizes if s > thr) / total * 100
        print(f"  OR > {thr:>3} pts: {pct_above:5.1f}% of days")
    print()

    # NQ comparison notes
    print("=" * 60)
    print("  NQ vs ES COMPARISON NOTES")
    print("=" * 60)
    print("  NQ ORB_MIN_RANGE_POINTS = 55.0 (excludes small OR days)")
    print("  NQ ORB_MAX_RANGE_POINTS = 130.0 (excludes chaotic days)")
    print("  NQ ORB_FIXED_STOP_POINTS = 25.0 (+ 5.0 buffer = 30pt stop)")
    print("  NQ ORB_BREAKOUT_BUFFER_POINTS = 4.0")
    print()
    print(f"  ES median OR:   {percentile(or_sizes, 50):.2f} pts")
    print(f"  ES mean OR:     {sum(or_sizes)/len(or_sizes):.2f} pts")
    print(f"  ES 25th pct OR: {percentile(or_sizes, 25):.2f} pts")
    print(f"  ES 75th pct OR: {percentile(or_sizes, 75):.2f} pts")

    mean_or = sum(or_sizes) / len(or_sizes)
    median_or = percentile(or_sizes, 50)
    p25 = percentile(or_sizes, 25)
    p75 = percentile(or_sizes, 75)

    # Proposed ES parameters
    # NQ min filter is ~55pt, NQ mean OR is ~80-100pt. The ratio ~0.6-0.7 of mean.
    # For ES: apply similar ratio to ES mean OR.
    proposed_min = round(p25 * 0.8, 0)
    proposed_max = round(percentile(or_sizes, 90) * 1.1, 0)
    proposed_stop = round(median_or * 0.35, 0)

    print()
    print("  SUGGESTED ES PARAMETERS (preliminary):")
    print(f"    ORB_MIN_RANGE_POINTS suggestion: {proposed_min:.0f} pts")
    print(f"    ORB_MAX_RANGE_POINTS suggestion: {proposed_max:.0f} pts")
    print(f"    ORB_FIXED_STOP suggestion:       {proposed_stop:.0f} pts")
    print()


if __name__ == "__main__":
    main()
