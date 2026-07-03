"""
Score≥2 Morning ORB — OOS confidence score threshold comparison.
Tests thresholds 1, 2, 3 side by side on OOS 2025-01-01 to 2026-06-30.
Also prints "additive" row for score==2 exactly (trades added going threshold 3→2).

Morning ORB params (fixed):
- OR window: 9:30-9:44 (build high/low from each bar's open/high/low/close)
- Entry: 9:46 onward (skip 9:45 first bar), close > orHi+4pt long or close < orLo-4pt short
- Stop: 22+5=27pt from entry, Target: 2R (54pt)
- OR size filter: 55-110pt
- Skip Mon + Fri
- Hard flatten: 10:30
- POINT_VALUE=20, commission=5 per trade, one trade per day

Confidence score (0-4):
- +1 if OR close > prior day pivot P for longs (< P for shorts)
- +1 if OR close > prior day VWAP for longs (< for shorts)
- +1 if OR close in R1-R2 zone for longs (S2-S1 for shorts)
- +1 if current-day VWAP at 9:35 close < VWAP at 9:44 close for longs (rising) or falling for shorts
- If no prior day data available, score=4 (pass through)
"""

import sys
import os
from datetime import datetime, date, time, timedelta
from collections import defaultdict

sys.path.insert(0, '/Users/Cruz/Desktop/nq_bot_final-main')
from backtest import load_csv

# ── Constants ────────────────────────────────────────────────────────────────
POINT_VALUE  = 20
COMMISSION   = 5       # per trade (round-trip)
STOP_PT      = 27.0    # 22 base + 5 buffer
RR           = 2.0
TARGET_PT    = STOP_PT * RR   # 54pt
BUFFER       = 4.0     # breakout buffer
OR_MIN       = 55.0
OR_MAX       = 110.0

OR_START     = time(9, 30)
OR_END       = time(9, 44)    # last bar included in OR window
ENTRY_START  = time(9, 46)    # skip 9:45 bar
FLATTEN_TIME = time(10, 30)

IS_START  = date(2022, 1, 1)
IS_END    = date(2024, 12, 31)
OOS_START = date(2025, 1, 1)
OOS_END   = date(2026, 6, 30)

RTH_START = time(9, 30)
RTH_END   = time(16, 0)


# ── Helpers ──────────────────────────────────────────────────────────────────
def pf(wins, losses):
    """Profit factor = gross_win / gross_loss."""
    if losses == 0:
        return float('inf') if wins > 0 else 0.0
    return wins / losses


def compute_prior_day_vwap(bars):
    """Weighted average of bar closes by volume for RTH bars."""
    num = 0.0
    den = 0.0
    for b in bars:
        t = b['ts'].time()
        if RTH_START <= t <= RTH_END:
            vol = b['volume'] or 1
            num += b['close'] * vol
            den += vol
    return num / den if den > 0 else None


def compute_confidence_score(
    or_close, direction,
    prev_high, prev_low, prev_close,
    prev_vwap,
    vwap_935, vwap_944
):
    """Compute 0-4 confidence score. Returns 4 if prior day data unavailable."""
    if prev_high is None or prev_low is None or prev_close is None:
        return 4  # no prior day data → pass through

    P   = (prev_high + prev_low + prev_close) / 3.0
    R1  = 2 * P - prev_low
    R2  = P + (prev_high - prev_low)
    S1  = 2 * P - prev_high
    S2  = P - (prev_high - prev_low)

    score = 0

    # +1 pivot
    if direction == 'long' and or_close > P:
        score += 1
    elif direction == 'short' and or_close < P:
        score += 1

    # +1 prior-day VWAP
    if prev_vwap is not None:
        if direction == 'long' and or_close > prev_vwap:
            score += 1
        elif direction == 'short' and or_close < prev_vwap:
            score += 1

    # +1 pivot zone (R1-R2 for longs, S2-S1 for shorts)
    if direction == 'long' and R1 <= or_close <= R2:
        score += 1
    elif direction == 'short' and S2 <= or_close <= S1:
        score += 1

    # +1 VWAP slope
    if vwap_935 is not None and vwap_944 is not None:
        if direction == 'long' and vwap_944 > vwap_935:
            score += 1
        elif direction == 'short' and vwap_944 < vwap_935:
            score += 1

    return score


# ── Main backtest ─────────────────────────────────────────────────────────────
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

    # Group bars by date
    by_date = defaultdict(list)
    for b in bars:
        by_date[b['ts'].date()].append(b)

    all_dates = sorted(by_date.keys())

    # ── Per-threshold result accumulators ─────────────────────────────────────
    # We collect trades with their score so we can slice by threshold
    # Each trade: (score, pnl, is_win)
    oos_trades = []

    # Prior-day tracking
    prev_high  = None
    prev_low   = None
    prev_close = None
    prev_vwap  = None
    prev_rth_bars = []

    for d in all_dates:
        day_bars = sorted(by_date[d], key=lambda b: b['ts'])
        dow = d.weekday()  # 0=Mon, 4=Fri

        # Determine period
        in_oos = OOS_START <= d <= OOS_END

        # Always process for prior-day tracking even in IS
        rth_bars = [b for b in day_bars
                    if RTH_START <= b['ts'].time() <= RTH_END]

        # ── Skip Mon/Fri ──────────────────────────────────────────────────────
        if dow in (0, 4):
            # Still update prior day
            if rth_bars:
                prev_high  = max(b['high'] for b in rth_bars)
                prev_low   = min(b['low']  for b in rth_bars)
                prev_close = rth_bars[-1]['close']
                prev_vwap  = compute_prior_day_vwap(rth_bars)
            continue

        # ── Build OR (9:30-9:44) ──────────────────────────────────────────────
        or_bars = [b for b in day_bars
                   if OR_START <= b['ts'].time() <= OR_END]
        if len(or_bars) < 5:
            # Not enough OR bars
            if rth_bars:
                prev_high  = max(b['high'] for b in rth_bars)
                prev_low   = min(b['low']  for b in rth_bars)
                prev_close = rth_bars[-1]['close']
                prev_vwap  = compute_prior_day_vwap(rth_bars)
            continue

        or_high = max(b['high'] for b in or_bars)
        or_low  = min(b['low']  for b in or_bars)
        or_size = or_high - or_low
        or_close = or_bars[-1]['close']  # 9:44 bar close

        # OR size filter
        if not (OR_MIN <= or_size <= OR_MAX):
            if rth_bars:
                prev_high  = max(b['high'] for b in rth_bars)
                prev_low   = min(b['low']  for b in rth_bars)
                prev_close = rth_bars[-1]['close']
                prev_vwap  = compute_prior_day_vwap(rth_bars)
            continue

        # ── VWAP tracking for slope (9:35 and 9:44 snapshots) ────────────────
        vwap_num = 0.0
        vwap_den = 0.0
        vwap_935 = None
        vwap_944 = None

        for b in or_bars:
            typical = (b['high'] + b['low'] + b['close']) / 3.0
            vol = b['volume'] or 1
            vwap_num += typical * vol
            vwap_den += vol
            t = b['ts'].time()
            if t == time(9, 35):
                vwap_935 = vwap_num / vwap_den if vwap_den > 0 else None
            if t == time(9, 44):
                vwap_944 = vwap_num / vwap_den if vwap_den > 0 else None

        # ── Entry bars (9:46 onward, flatten at 10:30) ────────────────────────
        entry_bars = [b for b in day_bars
                      if ENTRY_START <= b['ts'].time() < FLATTEN_TIME]

        trade_taken = False
        trade_score = None
        trade_pnl   = None
        trade_dir   = None

        for b in entry_bars:
            if trade_taken:
                break

            c = b['close']

            # Long signal
            if c > or_high + BUFFER:
                direction = 'long'
                entry_price = c
                stop_price  = entry_price - STOP_PT
                target_price = entry_price + TARGET_PT

                score = compute_confidence_score(
                    or_close=or_close, direction=direction,
                    prev_high=prev_high, prev_low=prev_low, prev_close=prev_close,
                    prev_vwap=prev_vwap,
                    vwap_935=vwap_935, vwap_944=vwap_944
                )

                # Simulate trade outcome: scan forward for target/stop hit
                future_bars = [fb for fb in day_bars
                               if fb['ts'] > b['ts'] and fb['ts'].time() <= FLATTEN_TIME]

                pnl = None
                for fb in future_bars:
                    # target hit (high touches target)
                    if fb['high'] >= target_price:
                        pnl = TARGET_PT * POINT_VALUE - COMMISSION
                        break
                    # stop hit (low touches stop)
                    if fb['low'] <= stop_price:
                        pnl = -STOP_PT * POINT_VALUE - COMMISSION
                        break
                if pnl is None:
                    # flatten at 10:30 close
                    flatten_bars = [fb for fb in day_bars
                                    if fb['ts'].time() >= FLATTEN_TIME]
                    if flatten_bars:
                        exit_price = flatten_bars[0]['open']  # open of 10:30 bar
                    else:
                        exit_price = future_bars[-1]['close'] if future_bars else entry_price
                    pnl = (exit_price - entry_price) * POINT_VALUE - COMMISSION

                trade_taken  = True
                trade_score  = score
                trade_pnl    = pnl
                trade_dir    = direction

            # Short signal (only if no long taken)
            elif c < or_low - BUFFER and not trade_taken:
                direction = 'short'
                entry_price = c
                stop_price  = entry_price + STOP_PT
                target_price = entry_price - TARGET_PT

                score = compute_confidence_score(
                    or_close=or_close, direction=direction,
                    prev_high=prev_high, prev_low=prev_low, prev_close=prev_close,
                    prev_vwap=prev_vwap,
                    vwap_935=vwap_935, vwap_944=vwap_944
                )

                future_bars = [fb for fb in day_bars
                               if fb['ts'] > b['ts'] and fb['ts'].time() <= FLATTEN_TIME]

                pnl = None
                for fb in future_bars:
                    if fb['low'] <= target_price:
                        pnl = TARGET_PT * POINT_VALUE - COMMISSION
                        break
                    if fb['high'] >= stop_price:
                        pnl = -STOP_PT * POINT_VALUE - COMMISSION
                        break
                if pnl is None:
                    flatten_bars = [fb for fb in day_bars
                                    if fb['ts'].time() >= FLATTEN_TIME]
                    if flatten_bars:
                        exit_price = flatten_bars[0]['open']
                    else:
                        exit_price = future_bars[-1]['close'] if future_bars else entry_price
                    pnl = (entry_price - exit_price) * POINT_VALUE - COMMISSION

                trade_taken  = True
                trade_score  = score
                trade_pnl    = pnl
                trade_dir    = direction

        if in_oos and trade_taken:
            oos_trades.append((trade_score, trade_pnl))

        # Update prior-day stats
        if rth_bars:
            prev_high  = max(b['high'] for b in rth_bars)
            prev_low   = min(b['low']  for b in rth_bars)
            prev_close = rth_bars[-1]['close']
            prev_vwap  = compute_prior_day_vwap(rth_bars)

    # ── Print results ─────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  SCORE THRESHOLD TEST — Morning ORB  (OOS: 2025-01-01 to 2026-06-30)")
    print("=" * 70)
    print(f"  OR: 9:30-9:44  |  Entry: 9:46+  |  Stop: {STOP_PT}pt  |  "
          f"Target: {TARGET_PT}pt  |  Buffer: {BUFFER}pt")
    print(f"  OR size: {OR_MIN}-{OR_MAX}pt  |  Skip Mon+Fri  |  "
          f"Flatten: 10:30  |  Pts = ${POINT_VALUE}/pt")
    print()
    print(f"  {'Threshold':<12} {'N':>5} {'WR%':>7} {'PF':>7} {'Net$':>10} {'Avg$/trade':>12}")
    print(f"  {'-'*12} {'-'*5} {'-'*7} {'-'*7} {'-'*10} {'-'*12}")

    for threshold in [1, 2, 3]:
        subset = [(s, p) for s, p in oos_trades if s >= threshold]
        N = len(subset)
        if N == 0:
            print(f"  {'≥'+str(threshold):<12} {'0':>5}")
            continue
        wins   = [p for _, p in subset if p > 0]
        losses = [p for _, p in subset if p <= 0]
        gross_win  = sum(wins)
        gross_loss = abs(sum(losses))
        net    = sum(p for _, p in subset)
        wr     = len(wins) / N * 100
        _pf    = pf(gross_win, gross_loss)
        avg    = net / N
        label  = f"≥{threshold}"
        print(f"  {label:<12} {N:>5} {wr:>7.1f} {_pf:>7.3f} {net:>10,.0f} {avg:>12.0f}")

    # Additive row: score == 2 exactly (new trades added by going threshold 3 → 2)
    print()
    additive = [(s, p) for s, p in oos_trades if s == 2]
    N2 = len(additive)
    if N2 > 0:
        wins2  = [p for _, p in additive if p > 0]
        losses2 = [p for _, p in additive if p <= 0]
        gw2 = sum(wins2)
        gl2 = abs(sum(losses2))
        net2 = sum(p for _, p in additive)
        wr2  = len(wins2) / N2 * 100
        pf2  = pf(gw2, gl2)
        avg2 = net2 / N2
        print(f"  Additive (score==2 exactly, threshold 3→2):")
        print(f"  {'==2':<12} {N2:>5} {wr2:>7.1f} {pf2:>7.3f} {net2:>10,.0f} {avg2:>12.0f}")
    else:
        print("  Additive (score==2 exactly): no trades")

    # Score distribution
    print()
    print("  Score distribution (OOS all qualifying trades):")
    for sc in range(5):
        cnt = sum(1 for s, _ in oos_trades if s == sc)
        print(f"    score={sc}: {cnt} trades")

    print()
    total_all = sum(p for _, p in oos_trades)
    print(f"  Total OOS trades (all scores): {len(oos_trades)}, Net$: ${total_all:,.0f}")


if __name__ == '__main__':
    run()
