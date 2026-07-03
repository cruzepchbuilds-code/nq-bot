"""
brain/research/daily_frequency.py

Daily Frequency Research: find strategy combinations that reach 15-20 trades/month
while maintaining blended PF > 1.5.

Current ORB fires ~4/month.  Need 11-16 more from:
  S1: 5-min ORB add-on  (9:30-9:34 mini-OR)
  S2: ES ORB with ES-native params + month filter
  S3: OR Midpoint Fade on range-bound days (no breakout by 10:30)
  S4: Failed 5-min ORB reversal

IS:  [2022, 2023, 2024]
OOS: [2025, 2026]

All trades use 1 contract for fair comparison.
NQ: $20/pt, stop 18-27pt (per strategy), ES: $50/pt, stop 9pt

Usage:  python3 brain/research/daily_frequency.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import csv
from datetime import datetime, date, time, timedelta
from collections import defaultdict

# ── constants ────────────────────────────────────────────────────────────────

NQ_DATA  = "data/nq_full.csv"
ES_DATA  = "data/es_1min.csv"

IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

SKIP_MONDAYS = True

# NQ ORB baseline (from existing bot, ~4/month on OOS)
NQ_OR_START   = time(9, 30)
NQ_OR_END_MIN = 44          # last bar of 15-min OR (9:44 close)
NQ_OR_END_MAX = 9           # 9:45 is the first breakout bar

NQ_BREAKOUT_BUFFER = 4.0    # close must exceed OR edge by 4pt
NQ_MIN_OR   = 55.0
NQ_MAX_OR   = 110.0
NQ_STOP     = 27.0          # effective stop (22pt fixed + 5pt buffer)
NQ_TARGET_R = 3.0           # 3R = 81pt
NQ_PV       = 20.0          # $20 / point
NQ_COMM     = 5.00          # round-trip commission per contract

# ES ORB params (native calibration)
ES_BREAKOUT_BUFFER = 1.0
ES_MIN_OR   = 6.0
ES_MAX_OR   = 20.0
ES_STOP     = 9.0           # 1pt beyond OR edge + 8pt fixed
ES_TARGET_R = 2.0           # 2R = 18pt
ES_PV       = 50.0          # $50 / point
ES_COMM     = 5.00
ES_SKIP_MONTHS = {8, 11}    # August and November

# 5-min ORB (S1)
MINI_OR_BARS  = 5           # 9:30-9:34 (bars at :30 :31 :32 :33 :34)
MINI_BUFFER   = 2.0
MINI_STOP     = 18.0
MINI_TARGET_R = 2.0         # 2R = 36pt

# OR Midpoint Fade (S3)
FADE_STOP   = 12.0
FADE_ENTRY_WINDOW_START = time(10, 30)
FADE_ENTRY_WINDOW_END   = time(12,  0)

# Failed reversal (S4)
REV_STOP     = 18.0
REV_TARGET_R = 2.0          # 2R = 36pt
REV_LOOKBACK_BARS = 10      # within 10 bars after 5-min ORB fires


# ── data loading ──────────────────────────────────────────────────────────────

def load_csv(path):
    """Load CSV into list of bar dicts with parsed timestamp."""
    bars = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            bars.append({
                "timestamp": ts,
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": float(row["volume"]),
            })
    return bars


def group_by_date(bars):
    """Group bars by date, return OrderedDict[date -> list[bar]]."""
    d = defaultdict(list)
    for b in bars:
        d[b["timestamp"].date()].append(b)
    return dict(sorted(d.items()))


def rth_bars(day_bars):
    """Return only RTH bars (9:30-15:59)."""
    return [b for b in day_bars
            if (b["timestamp"].hour, b["timestamp"].minute) >= (9, 30)
            and b["timestamp"].hour < 16]


def is_valid_day(d, skip_mondays=True):
    """Skip weekends and optionally Mondays."""
    if d.weekday() in (5, 6):
        return False
    if skip_mondays and d.weekday() == 0:
        return False
    return True


# ── pivot / VWAP helpers ─────────────────────────────────────────────────────

def compute_pivots_and_vwap(by_date):
    """
    For each date, compute prior-day RTH pivot (P,R1,R2,S1,S2) and prior VWAP.
    Returns {date: {P, R1, R2, S1, S2, vwap}}
    """
    result = {}
    sorted_dates = sorted(by_date.keys())
    for i in range(1, len(sorted_dates)):
        curr_d = sorted_dates[i]
        prev_d = sorted_dates[i - 1]
        prev_rth = rth_bars(by_date[prev_d])
        if not prev_rth:
            continue
        H = max(b["high"]  for b in prev_rth)
        L = min(b["low"]   for b in prev_rth)
        C = prev_rth[-1]["close"]
        P  = (H + L + C) / 3.0
        R1 = 2 * P - L
        R2 = P + (H - L)
        S1 = 2 * P - H
        S2 = P - (H - L)
        # VWAP of prior day
        cum_pv = sum(((b["high"]+b["low"]+b["close"])/3.0)*b["volume"] for b in prev_rth)
        cum_v  = sum(b["volume"] for b in prev_rth)
        vwap   = cum_pv / cum_v if cum_v else C
        result[curr_d] = {"P": P, "R1": R1, "R2": R2, "S1": S1, "S2": S2, "vwap": vwap}
    return result


def intraday_vwap(bars_up_to):
    """Running VWAP from first bar through bars_up_to list."""
    cum_pv = cum_v = 0.0
    for b in bars_up_to:
        typ = (b["high"] + b["low"] + b["close"]) / 3.0
        cum_pv += typ * b["volume"]
        cum_v  += b["volume"]
    return cum_pv / cum_v if cum_v else None


def confidence_score_nq(or_close, direction, pv):
    """Return confidence score 0-4 for an NQ ORB entry."""
    score = 0
    # 1. Pivot aligned (or_close vs prior P)
    if direction == "long"  and or_close >= pv["P"]:
        score += 1
    elif direction == "short" and or_close <  pv["P"]:
        score += 1
    # 2. VWAP aligned (or_close vs prior VWAP)
    if direction == "long"  and or_close >= pv["vwap"]:
        score += 1
    elif direction == "short" and or_close <  pv["vwap"]:
        score += 1
    # 3. HOT zone (R1<=orClose<=R2 for long, S2<=orClose<=S1 for short)
    if direction == "long"  and pv["R1"] <= or_close <= pv["R2"]:
        score += 1
    elif direction == "short" and pv["S2"] <= or_close <= pv["S1"]:
        score += 1
    return score


# ── PnL calculator ───────────────────────────────────────────────────────────

def calc_pnl(direction, entry, exit_price, stop, target, pv_dollar, comm):
    """
    Simulate a single trade exit: target or stop.
    Returns (pnl, outcome) where outcome is 'win' or 'loss'.
    """
    if direction == "long":
        if exit_price >= target:
            pnl = (target - entry) * pv_dollar - comm
            return pnl, "win"
        else:
            pnl = (stop - entry) * pv_dollar - comm
            return pnl, "loss"
    else:
        if exit_price <= target:
            pnl = (entry - target) * pv_dollar - comm
            return pnl, "win"
        else:
            pnl = (entry - stop) * pv_dollar - comm
            return pnl, "loss"


def sim_trade(direction, entry_price, stop_dist, rr, future_bars, pv_dollar, comm):
    """
    Walk future_bars to see if target or stop hits first (bar high/low).
    Returns dict with pnl, win, exit details.
    """
    if direction == "long":
        stop   = entry_price - stop_dist
        target = entry_price + stop_dist * rr
        for b in future_bars:
            if b["low"]  <= stop:
                pnl = (stop   - entry_price) * pv_dollar - comm
                return {"pnl": pnl, "win": False, "dir": direction}
            if b["high"] >= target:
                pnl = (target - entry_price) * pv_dollar - comm
                return {"pnl": pnl, "win": True,  "dir": direction}
    else:
        stop   = entry_price + stop_dist
        target = entry_price - stop_dist * rr
        for b in future_bars:
            if b["high"] >= stop:
                pnl = (stop   - entry_price) * pv_dollar - comm
                return {"pnl": pnl, "win": False, "dir": direction}
            if b["low"]  <= target:
                pnl = (target - entry_price) * pv_dollar - comm
                return {"pnl": pnl, "win": True,  "dir": direction}
    # EOD — no fill → flat (treat as loss at stop for conservatism)
    if direction == "long":
        pnl = (stop - entry_price) * pv_dollar - comm
    else:
        pnl = (stop - entry_price) * pv_dollar - comm  # stop is above entry for short
    return {"pnl": pnl, "win": False, "dir": direction}


# ── stats helper ─────────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0.0, "avg": 0.0, "per_month": 0.0}
    n    = len(trades)
    wins = [t for t in trades if t["win"]]
    gross_w = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_l = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    net  = sum(t["pnl"] for t in trades)
    # estimate months from date range
    dates = sorted(set(t["date"] for t in trades))
    if len(dates) >= 2:
        d0 = date.fromisoformat(dates[0])
        d1 = date.fromisoformat(dates[-1])
        months = max((d1 - d0).days / 30.44, 1.0)
    else:
        months = 1.0
    return {
        "n":         n,
        "wr":        len(wins) / n,
        "pf":        round(gross_w / gross_l, 3) if gross_l else 99.0,
        "net":       round(net, 0),
        "avg":       round(net / n, 0),
        "per_month": round(n / months, 1),
    }


def print_stats(label, s, indent=4):
    pad = " " * indent
    print(f"{pad}{label:<38}  N={s['n']:>4}  WR={s['wr']:.1%}  PF={s['pf']:.3f}"
          f"  Net=${s['net']:>+9,.0f}  Avg=${s['avg']:>+6,.0f}  /mo={s['per_month']:.1f}")


# ════════════════════════════════════════════════════════════════════════════
# BASELINE NQ 15-min ORB  (reproduced from scratch for correlation tracking)
# ════════════════════════════════════════════════════════════════════════════

def run_nq_orb_baseline(by_date, pv_data, years):
    """
    Replicate the existing 15-min NQ ORB logic so we can track which days fire
    and compare with other strategies.

    Returns list of trade dicts + dict {date: direction} for correlation.
    """
    trades  = []
    fired   = {}   # date -> direction for correlation

    for d, day_bars in by_date.items():
        if d.year not in years:
            continue
        if not is_valid_day(d):
            continue
        pv = pv_data.get(d)
        if pv is None:
            continue

        rth = rth_bars(day_bars)
        if not rth:
            continue

        # Build OR (9:30-9:44) — first 15 bars
        or_bars = [b for b in rth
                   if b["timestamp"].hour == 9 and b["timestamp"].minute <= 44]
        if len(or_bars) < 10:      # need at least ~10 bars
            continue

        or_hi = max(b["high"]  for b in or_bars)
        or_lo = min(b["low"]   for b in or_bars)
        or_sz = or_hi - or_lo

        if not (NQ_MIN_OR <= or_sz <= NQ_MAX_OR):
            continue

        or_close = or_bars[-1]["close"]

        # Scan breakout bars 9:45 → LAST_ENTRY (10:30)
        scan_bars = [b for b in rth
                     if (b["timestamp"].hour, b["timestamp"].minute) > (9, 44)
                     and (b["timestamp"].hour, b["timestamp"].minute) <= (10, 30)]
        if not scan_bars:
            continue

        entry_bar = None
        direction = None
        for b in scan_bars:
            if b["close"] > or_hi + NQ_BREAKOUT_BUFFER:
                direction = "long"
                entry_bar = b
                break
            elif b["close"] < or_lo - NQ_BREAKOUT_BUFFER:
                direction = "short"
                entry_bar = b
                break

        if entry_bar is None:
            continue

        entry_price = entry_bar["close"]
        # Future bars after entry
        entry_idx = rth.index(entry_bar)
        future    = rth[entry_idx + 1:]

        trade = sim_trade(direction, entry_price, NQ_STOP, NQ_TARGET_R, future, NQ_PV, NQ_COMM)
        trade["date"] = d.isoformat()
        trade["or_close"] = or_close
        trade["or_sz"]    = or_sz
        trades.append(trade)
        fired[d] = direction

    return trades, fired


# ════════════════════════════════════════════════════════════════════════════
# STRATEGY 1: 5-minute ORB add-on  (9:30-9:34 mini-OR)
# ════════════════════════════════════════════════════════════════════════════

def run_s1_mini_orb(by_date, pv_data, years, nq_orb_fired):
    """
    5-min ORB as ADD-ON.
    Mini-OR = 9:30-9:34 bars.  Breakout fires from 9:35 → 9:44 (before 15-min OR closes).
    Stop: 18pt, Target: 2R (36pt).

    Also tracks:
      - Correlation: % of days both fire same direction
      - Additive days: % of S1 days where no 15-min ORB fires
    """
    trades      = []
    fired       = {}   # date -> direction
    additive_n  = 0    # days where S1 fires but 15-min ORB does NOT

    for d, day_bars in by_date.items():
        if d.year not in years:
            continue
        if not is_valid_day(d):
            continue
        pv = pv_data.get(d)
        if pv is None:
            continue

        rth = rth_bars(day_bars)
        if not rth:
            continue

        # Mini-OR: bars 9:30-9:34
        mini_bars = [b for b in rth
                     if b["timestamp"].hour == 9
                     and 30 <= b["timestamp"].minute <= 34]
        if len(mini_bars) < 5:
            continue

        mini_hi = max(b["high"]  for b in mini_bars)
        mini_lo = min(b["low"]   for b in mini_bars)

        # Scan for breakout 9:35 → 9:44
        scan = [b for b in rth
                if b["timestamp"].hour == 9
                and 35 <= b["timestamp"].minute <= 44]
        if not scan:
            continue

        entry_bar = None
        direction = None
        for b in scan:
            if b["close"] > mini_hi + MINI_BUFFER:
                direction = "long"
                entry_bar = b
                break
            elif b["close"] < mini_lo - MINI_BUFFER:
                direction = "short"
                entry_bar = b
                break

        if entry_bar is None:
            continue

        entry_price = entry_bar["close"]
        entry_idx   = rth.index(entry_bar)
        future      = rth[entry_idx + 1:]

        trade = sim_trade(direction, entry_price, MINI_STOP, MINI_TARGET_R, future, NQ_PV, NQ_COMM)
        trade["date"]      = d.isoformat()
        trade["direction"] = direction
        trades.append(trade)
        fired[d] = direction

        # Additive: S1 fires but 15-min ORB did NOT
        if d not in nq_orb_fired:
            additive_n += 1

    return trades, fired, additive_n


def correlation_s1_vs_15min(s1_fired, orb_fired):
    """
    On days where BOTH S1 and 15-min ORB fired, what % are same direction?
    Also: % of S1 days where 15-min ORB also fires.
    """
    both_days   = [d for d in s1_fired if d in orb_fired]
    if not both_days:
        return {"both": 0, "same_dir_pct": 0.0, "s1_overlap_pct": 0.0}
    same_dir = sum(1 for d in both_days if s1_fired[d] == orb_fired[d])
    return {
        "both":          len(both_days),
        "same_dir_pct":  same_dir / len(both_days),
        "s1_overlap_pct": len(both_days) / len(s1_fired) if s1_fired else 0.0,
    }


# ════════════════════════════════════════════════════════════════════════════
# STRATEGY 2: ES ORB with ES-native params + month filter
# ════════════════════════════════════════════════════════════════════════════

def run_s2_es_orb(by_date_es, pv_data_es, years, nq_orb_fired):
    """
    ES ORB:
      OR = 9:30-9:44, buffer = 1pt, stop = 9pt eff, target = 2R (18pt)
      OR size: 6-20pt
      Skip August (8) and November (11)
    """
    trades  = []
    fired   = {}

    for d, day_bars in by_date_es.items():
        if d.year not in years:
            continue
        if not is_valid_day(d):
            continue
        if d.month in ES_SKIP_MONTHS:
            continue
        pv = pv_data_es.get(d)
        if pv is None:
            continue

        rth = rth_bars(day_bars)
        if not rth:
            continue

        # OR: 9:30-9:44
        or_bars = [b for b in rth
                   if b["timestamp"].hour == 9 and b["timestamp"].minute <= 44]
        if len(or_bars) < 10:
            continue

        or_hi = max(b["high"]  for b in or_bars)
        or_lo = min(b["low"]   for b in or_bars)
        or_sz = or_hi - or_lo

        if not (ES_MIN_OR <= or_sz <= ES_MAX_OR):
            continue

        or_close = or_bars[-1]["close"]

        # Scan 9:45 → 10:30
        scan = [b for b in rth
                if (b["timestamp"].hour, b["timestamp"].minute) > (9, 44)
                and (b["timestamp"].hour, b["timestamp"].minute) <= (10, 30)]
        if not scan:
            continue

        entry_bar = None
        direction = None
        for b in scan:
            if b["close"] > or_hi + ES_BREAKOUT_BUFFER:
                direction = "long"
                entry_bar = b
                break
            elif b["close"] < or_lo - ES_BREAKOUT_BUFFER:
                direction = "short"
                entry_bar = b
                break

        if entry_bar is None:
            continue

        entry_price = entry_bar["close"]
        entry_idx   = rth.index(entry_bar)
        future      = rth[entry_idx + 1:]

        trade = sim_trade(direction, entry_price, ES_STOP, ES_TARGET_R, future, ES_PV, ES_COMM)
        trade["date"]      = d.isoformat()
        trade["direction"] = direction
        trades.append(trade)
        fired[d] = direction

    return trades, fired


def correlation_es_vs_nq(es_fired, nq_fired):
    """What % of ES trade days also had same-direction NQ ORB?"""
    both = [d for d in es_fired if d in nq_fired]
    if not both:
        return {"both": 0, "same_dir_pct": 0.0}
    same = sum(1 for d in both if es_fired[d] == nq_fired[d])
    return {"both": len(both), "same_dir_pct": same / len(both)}


# ════════════════════════════════════════════════════════════════════════════
# STRATEGY 3: OR Midpoint Fade (range-bound days)
# ════════════════════════════════════════════════════════════════════════════

def run_s3_fade(by_date, pv_data, years, nq_orb_fired):
    """
    On days with valid OR (55-110pt) but NO NQ ORB breakout by 10:30:
    Enter fade at OR edge ±2pt during 10:30-12:00 window.
    Target = OR midpoint. Stop = 12pt.
    """
    trades       = []
    range_bound  = 0   # days with valid OR but no breakout

    for d, day_bars in by_date.items():
        if d.year not in years:
            continue
        if not is_valid_day(d):
            continue

        rth = rth_bars(day_bars)
        if not rth:
            continue

        # Build OR
        or_bars = [b for b in rth
                   if b["timestamp"].hour == 9 and b["timestamp"].minute <= 44]
        if len(or_bars) < 10:
            continue

        or_hi = max(b["high"]  for b in or_bars)
        or_lo = min(b["low"]   for b in or_bars)
        or_sz = or_hi - or_lo
        or_mid = (or_hi + or_lo) / 2.0

        if not (NQ_MIN_OR <= or_sz <= NQ_MAX_OR):
            continue

        # Skip if 15-min ORB fired (not range-bound)
        if d in nq_orb_fired:
            continue

        range_bound += 1

        # Scan 10:30-12:00 for fade entry
        scan = [b for b in rth
                if (10, 30) <= (b["timestamp"].hour, b["timestamp"].minute) <= (12, 0)]
        if not scan:
            continue

        entry_bar = None
        direction = None
        for b in scan:
            # Long fade: price touches orLo+2pt from below (a dip to near orLo)
            if b["low"] <= or_lo + 2.0 and b["close"] >= or_lo:
                direction = "long"
                entry_bar = b
                break
            # Short fade: price touches orHi-2pt from above
            elif b["high"] >= or_hi - 2.0 and b["close"] <= or_hi:
                direction = "short"
                entry_bar = b
                break

        if entry_bar is None:
            continue

        entry_price = entry_bar["close"]
        entry_idx   = rth.index(entry_bar)
        future      = rth[entry_idx + 1:]

        # Target is OR midpoint (variable)
        if direction == "long":
            target_dist = or_mid - entry_price
        else:
            target_dist = entry_price - or_mid

        # Only take if target is at least 1R (stops being too cramped otherwise)
        if target_dist < FADE_STOP * 0.8:
            continue

        # Walk future bars
        stop_price   = (entry_price - FADE_STOP) if direction == "long" else (entry_price + FADE_STOP)
        target_price = or_mid

        pnl  = None
        win  = False
        for b in future:
            if direction == "long":
                if b["low"] <= stop_price:
                    pnl = (stop_price   - entry_price) * NQ_PV - NQ_COMM
                    break
                if b["high"] >= target_price:
                    pnl = (target_price - entry_price) * NQ_PV - NQ_COMM
                    win = True
                    break
            else:
                if b["high"] >= stop_price:
                    pnl = (stop_price   - entry_price) * NQ_PV - NQ_COMM
                    break
                if b["low"] <= target_price:
                    pnl = (target_price - entry_price) * NQ_PV - NQ_COMM
                    win = True
                    break

        if pnl is None:
            # EOD — no fill, treat as flat / partial (conservative: stop)
            pnl = (stop_price - entry_price) * NQ_PV - NQ_COMM if direction == "long" \
                  else (stop_price - entry_price) * NQ_PV - NQ_COMM

        trades.append({"pnl": pnl, "win": win, "dir": direction, "date": d.isoformat()})

    return trades, range_bound


# ════════════════════════════════════════════════════════════════════════════
# STRATEGY 4: Failed 5-min ORB Reversal
# ════════════════════════════════════════════════════════════════════════════

def run_s4_reversal(by_date, pv_data, years):
    """
    On days where the 5-min OR breaks one direction THEN reverses through the
    full 5-min range within the next 10 bars (by 9:44):
      Enter in the reversal direction. Stop 18pt, target 2R (36pt).
    """
    trades = []

    for d, day_bars in by_date.items():
        if d.year not in years:
            continue
        if not is_valid_day(d):
            continue

        rth = rth_bars(day_bars)
        if not rth:
            continue

        # Mini-OR
        mini_bars = [b for b in rth
                     if b["timestamp"].hour == 9
                     and 30 <= b["timestamp"].minute <= 34]
        if len(mini_bars) < 5:
            continue

        mini_hi = max(b["high"]  for b in mini_bars)
        mini_lo = min(b["low"]   for b in mini_bars)

        # Look for initial 5-min breakout (first qualifying bar 9:35-9:44)
        initial_scan = [b for b in rth
                        if b["timestamp"].hour == 9
                        and 35 <= b["timestamp"].minute <= 44]
        if not initial_scan:
            continue

        initial_bar  = None
        initial_dir  = None
        initial_idx  = None

        for b in initial_scan:
            if b["close"] > mini_hi + MINI_BUFFER:
                initial_dir = "long"
                initial_bar = b
                initial_idx = rth.index(b)
                break
            elif b["close"] < mini_lo - MINI_BUFFER:
                initial_dir = "short"
                initial_bar = b
                initial_idx = rth.index(b)
                break

        if initial_bar is None:
            continue

        # Look for reversal within next REV_LOOKBACK_BARS bars, up to 9:44
        lookback = rth[initial_idx + 1: initial_idx + 1 + REV_LOOKBACK_BARS]
        # Also cap at 9:44
        lookback = [b for b in lookback
                    if (b["timestamp"].hour, b["timestamp"].minute) <= (9, 44)]

        rev_bar  = None
        rev_dir  = None

        for b in lookback:
            if initial_dir == "long" and b["close"] < mini_lo - MINI_BUFFER:
                rev_dir = "short"
                rev_bar = b
                break
            elif initial_dir == "short" and b["close"] > mini_hi + MINI_BUFFER:
                rev_dir = "long"
                rev_bar = b
                break

        if rev_bar is None:
            continue

        entry_price = rev_bar["close"]
        rev_idx     = rth.index(rev_bar)
        future      = rth[rev_idx + 1:]

        trade = sim_trade(rev_dir, entry_price, REV_STOP, REV_TARGET_R, future, NQ_PV, NQ_COMM)
        trade["date"] = d.isoformat()
        trades.append(trade)

    return trades


# ════════════════════════════════════════════════════════════════════════════
# YEAR-BY-YEAR breakdown helper
# ════════════════════════════════════════════════════════════════════════════

def by_year_stats(trades, years):
    out = {}
    for y in years:
        yt = [t for t in trades if t["date"][:4] == str(y)]
        out[y] = stats(yt)
    return out


def print_year_table(year_stats, years):
    print(f"    {'Year':<6} {'N':>4}  {'WR':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'/mo':>5}")
    print(f"    {'─'*56}")
    for y in years:
        s = year_stats.get(y, {"n":0,"wr":0,"pf":0,"net":0,"avg":0,"per_month":0})
        print(f"    {y:<6} {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
              f"${s['net']:>+9,.0f}  ${s['avg']:>+6,.0f}  {s['per_month']:>5.1f}")


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    W = 78
    SEP = "=" * W

    print(f"\n{SEP}")
    print(f"  CruzCapital NQ Bot — Daily Frequency Research")
    print(f"  Goal: 15-20 trades/month, blended PF > 1.5")
    print(f"  IS: {IS_YEARS}  |  OOS: {OOS_YEARS}")
    print(f"{SEP}")

    # ── Load data ─────────────────────────────────────────────────────────────
    print(f"\n  Loading NQ data ({NQ_DATA})...", end=" ", flush=True)
    nq_bars   = load_csv(NQ_DATA)
    by_date_nq = group_by_date(nq_bars)
    print(f"{len(nq_bars):,} bars, {len(by_date_nq)} days")

    print(f"  Loading ES data ({ES_DATA})...", end=" ", flush=True)
    es_bars   = load_csv(ES_DATA)
    by_date_es = group_by_date(es_bars)
    print(f"{len(es_bars):,} bars, {len(by_date_es)} days")

    # ── Pivot/VWAP data ───────────────────────────────────────────────────────
    print(f"  Computing NQ pivots/VWAP...", end=" ", flush=True)
    pv_nq = compute_pivots_and_vwap(by_date_nq)
    print(f"{len(pv_nq)} sessions")

    print(f"  Computing ES pivots/VWAP...", end=" ", flush=True)
    pv_es = compute_pivots_and_vwap(by_date_es)
    print(f"{len(pv_es)} sessions")

    # ══════════════════════════════════════════════════════════════════════════
    # BASELINE: NQ 15-min ORB
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print(f"  BASELINE: NQ 15-min ORB  (existing strategy)")
    print(f"{SEP}")

    is_orb,  is_fired  = run_nq_orb_baseline(by_date_nq, pv_nq, IS_YEARS)
    oos_orb, oos_fired = run_nq_orb_baseline(by_date_nq, pv_nq, OOS_YEARS)

    s_is  = stats(is_orb)
    s_oos = stats(oos_orb)

    print(f"\n  IS  [{', '.join(str(y) for y in IS_YEARS)}]:")
    print_stats("NQ ORB baseline", s_is)
    ys = by_year_stats(is_orb, IS_YEARS)
    print_year_table(ys, IS_YEARS)

    print(f"\n  OOS [{', '.join(str(y) for y in OOS_YEARS)}]:")
    print_stats("NQ ORB baseline", s_oos)
    ys_oos = by_year_stats(oos_orb, OOS_YEARS)
    print_year_table(ys_oos, OOS_YEARS)

    # ══════════════════════════════════════════════════════════════════════════
    # STRATEGY 1: 5-min ORB add-on
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print(f"  STRATEGY 1: 5-min ORB Add-On  (9:30-9:34 mini-OR)")
    print(f"  Buffer: {MINI_BUFFER}pt  |  Stop: {MINI_STOP}pt  |  Target: {MINI_TARGET_R}R ({MINI_STOP*MINI_TARGET_R:.0f}pt)")
    print(f"  Fires BEFORE 15-min OR closes — separate early entry")
    print(f"{SEP}")

    is_s1,  s1_is_fired,  s1_is_add  = run_s1_mini_orb(by_date_nq, pv_nq, IS_YEARS,  is_fired)
    oos_s1, s1_oos_fired, s1_oos_add = run_s1_mini_orb(by_date_nq, pv_nq, OOS_YEARS, oos_fired)

    s1_s_is  = stats(is_s1)
    s1_s_oos = stats(oos_s1)

    print(f"\n  IS  [{', '.join(str(y) for y in IS_YEARS)}]:")
    print_stats("S1 5-min ORB", s1_s_is)
    ys1 = by_year_stats(is_s1, IS_YEARS)
    print_year_table(ys1, IS_YEARS)

    print(f"\n  OOS [{', '.join(str(y) for y in OOS_YEARS)}]:")
    print_stats("S1 5-min ORB", s1_s_oos)
    ys1_oos = by_year_stats(oos_s1, OOS_YEARS)
    print_year_table(ys1_oos, OOS_YEARS)

    # Correlation with 15-min ORB
    corr_is  = correlation_s1_vs_15min(s1_is_fired,  is_fired)
    corr_oos = correlation_s1_vs_15min(s1_oos_fired, oos_fired)

    print(f"\n  Correlation with 15-min ORB:")
    print(f"    IS:  both fired on {corr_is['both']} days"
          f"  |  same direction: {corr_is['same_dir_pct']:.1%}"
          f"  |  S1 overlap (% of S1 days w/ 15-min ORB also): {corr_is['s1_overlap_pct']:.1%}")
    print(f"    OOS: both fired on {corr_oos['both']} days"
          f"  |  same direction: {corr_oos['same_dir_pct']:.1%}"
          f"  |  S1 overlap (% of S1 days w/ 15-min ORB also): {corr_oos['s1_overlap_pct']:.1%}")

    s1_is_tot  = len(s1_is_fired)
    s1_oos_tot = len(s1_oos_fired)
    print(f"\n  Pure additive days (S1 fires, 15-min does NOT):")
    print(f"    IS:  {s1_is_add}/{s1_is_tot} S1 days = {s1_is_add/s1_is_tot:.1%}" if s1_is_tot else "    IS: 0 days")
    print(f"    OOS: {s1_oos_add}/{s1_oos_tot} S1 days = {s1_oos_add/s1_oos_tot:.1%}" if s1_oos_tot else "    OOS: 0 days")

    # ══════════════════════════════════════════════════════════════════════════
    # STRATEGY 2: ES ORB with ES-native params
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print(f"  STRATEGY 2: ES ORB (native params + month filter)")
    print(f"  Buffer: {ES_BREAKOUT_BUFFER}pt  |  Stop: {ES_STOP}pt (${ES_STOP*ES_PV:.0f})  |  Target: {ES_TARGET_R}R ({ES_STOP*ES_TARGET_R:.0f}pt, ${ES_STOP*ES_TARGET_R*ES_PV:.0f})")
    print(f"  OR size: {ES_MIN_OR}-{ES_MAX_OR}pt  |  Skip months: {sorted(ES_SKIP_MONTHS)}")
    print(f"{SEP}")

    is_s2,  s2_is_fired  = run_s2_es_orb(by_date_es, pv_es, IS_YEARS,  is_fired)
    oos_s2, s2_oos_fired = run_s2_es_orb(by_date_es, pv_es, OOS_YEARS, oos_fired)

    s2_s_is  = stats(is_s2)
    s2_s_oos = stats(oos_s2)

    print(f"\n  IS  [{', '.join(str(y) for y in IS_YEARS)}]:")
    print_stats("S2 ES ORB", s2_s_is)
    ys2 = by_year_stats(is_s2, IS_YEARS)
    print_year_table(ys2, IS_YEARS)

    print(f"\n  OOS [{', '.join(str(y) for y in OOS_YEARS)}]:")
    print_stats("S2 ES ORB", s2_s_oos)
    ys2_oos = by_year_stats(oos_s2, OOS_YEARS)
    print_year_table(ys2_oos, OOS_YEARS)

    # Correlation ES vs NQ
    corr2_oos = correlation_es_vs_nq(s2_oos_fired, oos_fired)
    print(f"\n  Correlation with NQ ORB (OOS):")
    print(f"    Both fired same day: {corr2_oos['both']}  |  Same direction: {corr2_oos['same_dir_pct']:.1%}")

    # ══════════════════════════════════════════════════════════════════════════
    # STRATEGY 3: OR Midpoint Fade
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print(f"  STRATEGY 3: OR Midpoint Fade  (range-bound days, no ORB by 10:30)")
    print(f"  Entry: 10:30-12:00, near OR edge  |  Stop: {FADE_STOP}pt  |  Target: OR midpoint")
    print(f"  NQ OR size: {NQ_MIN_OR}-{NQ_MAX_OR}pt")
    print(f"{SEP}")

    is_s3,  s3_is_rb  = run_s3_fade(by_date_nq, pv_nq, IS_YEARS,  is_fired)
    oos_s3, s3_oos_rb = run_s3_fade(by_date_nq, pv_nq, OOS_YEARS, oos_fired)

    s3_s_is  = stats(is_s3)
    s3_s_oos = stats(oos_s3)

    print(f"\n  IS  [{', '.join(str(y) for y in IS_YEARS)}]:")
    print(f"    Range-bound days (valid OR, no 15-min breakout): {s3_is_rb}")
    print_stats("S3 Fade", s3_s_is)
    ys3 = by_year_stats(is_s3, IS_YEARS)
    print_year_table(ys3, IS_YEARS)

    print(f"\n  OOS [{', '.join(str(y) for y in OOS_YEARS)}]:")
    print(f"    Range-bound days (valid OR, no 15-min breakout): {s3_oos_rb}")
    print_stats("S3 Fade", s3_s_oos)
    ys3_oos = by_year_stats(oos_s3, OOS_YEARS)
    print_year_table(ys3_oos, OOS_YEARS)

    # ══════════════════════════════════════════════════════════════════════════
    # STRATEGY 4: Failed 5-min ORB Reversal
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print(f"  STRATEGY 4: Failed 5-min ORB Reversal")
    print(f"  5-min ORB fires, then reverses through full mini-range within {REV_LOOKBACK_BARS} bars")
    print(f"  Stop: {REV_STOP}pt  |  Target: {REV_TARGET_R}R ({REV_STOP*REV_TARGET_R:.0f}pt)")
    print(f"{SEP}")

    is_s4  = run_s4_reversal(by_date_nq, pv_nq, IS_YEARS)
    oos_s4 = run_s4_reversal(by_date_nq, pv_nq, OOS_YEARS)

    s4_s_is  = stats(is_s4)
    s4_s_oos = stats(oos_s4)

    print(f"\n  IS  [{', '.join(str(y) for y in IS_YEARS)}]:")
    print_stats("S4 Reversal", s4_s_is)
    ys4 = by_year_stats(is_s4, IS_YEARS)
    print_year_table(ys4, IS_YEARS)

    print(f"\n  OOS [{', '.join(str(y) for y in OOS_YEARS)}]:")
    print_stats("S4 Reversal", s4_s_oos)
    ys4_oos = by_year_stats(oos_s4, OOS_YEARS)
    print_year_table(ys4_oos, OOS_YEARS)

    # ══════════════════════════════════════════════════════════════════════════
    # COMBINED SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print(f"  COMBINED SUMMARY  (OOS only: {OOS_YEARS})")
    print(f"{SEP}")

    def combined_stats(trade_lists):
        all_t = []
        for tl in trade_lists:
            all_t.extend(tl)
        return stats(all_t)

    # All four options
    combos = [
        ("Baseline NQ ORB",               [oos_orb]),
        ("+ S1 (5-min ORB)",              [oos_orb, oos_s1]),
        ("+ S2 (ES ORB)",                 [oos_orb, oos_s2]),
        ("+ S3 (Fade)",                   [oos_orb, oos_s3]),
        ("+ S4 (Reversal)",               [oos_orb, oos_s4]),
        ("+ S1 + S2",                     [oos_orb, oos_s1, oos_s2]),
        ("+ S1 + S3",                     [oos_orb, oos_s1, oos_s3]),
        ("+ S1 + S4",                     [oos_orb, oos_s1, oos_s4]),
        ("+ S2 + S3",                     [oos_orb, oos_s2, oos_s3]),
        ("+ S2 + S4",                     [oos_orb, oos_s2, oos_s4]),
        ("+ S3 + S4",                     [oos_orb, oos_s3, oos_s4]),
        ("+ S1 + S2 + S3",               [oos_orb, oos_s1, oos_s2, oos_s3]),
        ("+ S1 + S2 + S4",               [oos_orb, oos_s1, oos_s2, oos_s4]),
        ("+ S1 + S3 + S4",               [oos_orb, oos_s1, oos_s3, oos_s4]),
        ("+ S2 + S3 + S4",               [oos_orb, oos_s2, oos_s3, oos_s4]),
        ("+ S1 + S2 + S3 + S4 (ALL)",    [oos_orb, oos_s1, oos_s2, oos_s3, oos_s4]),
    ]

    TARGET_LO = 15.0
    TARGET_HI = 20.0
    TARGET_PF = 1.5

    print(f"\n  {'Combination':<38}  {'N':>5}  {'PF':>5}  {'/mo':>5}  {'Net $':>10}  Status")
    print(f"  {'─'*82}")

    best_combo = None
    best_score = -999.0

    for name, trade_lists in combos:
        cs = combined_stats(trade_lists)
        in_range  = TARGET_LO <= cs["per_month"] <= TARGET_HI
        pf_ok     = cs["pf"] >= TARGET_PF
        status = ""
        if in_range and pf_ok:
            status = "  *** GOAL MET ***"
        elif cs["per_month"] < TARGET_LO and pf_ok:
            status = "  (need more trades)"
        elif cs["per_month"] > TARGET_HI and pf_ok:
            status = "  (over limit — ok)"
        elif not pf_ok:
            status = f"  (PF too low: {cs['pf']:.3f})"

        # Score: prefer closest to 17.5 trades/month with PF bonus
        mid = (TARGET_LO + TARGET_HI) / 2.0
        dist = abs(cs["per_month"] - mid)
        score = cs["pf"] * 10 - dist
        if pf_ok and cs["per_month"] >= TARGET_LO and (best_combo is None or score > best_score):
            best_combo = (name, cs)
            best_score = score

        print(f"  {name:<38}  {cs['n']:>5}  {cs['pf']:>5.3f}  {cs['per_month']:>5.1f}  "
              f"${cs['net']:>+9,.0f}{status}")

    # ── Individual OOS contributions ─────────────────────────────────────────
    print(f"\n  Individual OOS strategy contributions:")
    print(f"  {'Strategy':<38}  {'N':>5}  {'PF':>5}  {'/mo':>5}  {'Net $':>10}")
    print(f"  {'─'*70}")
    rows = [
        ("NQ ORB baseline",   s_oos),
        ("S1: 5-min ORB",     s1_s_oos),
        ("S2: ES ORB",        s2_s_oos),
        ("S3: OR Fade",       s3_s_oos),
        ("S4: Reversal",      s4_s_oos),
    ]
    for name, s in rows:
        print(f"  {name:<38}  {s['n']:>5}  {s['pf']:>5.3f}  {s['per_month']:>5.1f}  ${s['net']:>+9,.0f}")

    # ── Recommendation ────────────────────────────────────────────────────────
    print(f"\n  {'─'*78}")
    print(f"  GOAL: {TARGET_LO:.0f}-{TARGET_HI:.0f} trades/month, blended PF > {TARGET_PF}")
    if best_combo:
        nm, cs = best_combo
        print(f"\n  BEST COMBINATION: {nm}")
        print(f"    Trades/month: {cs['per_month']:.1f}  |  PF: {cs['pf']:.3f}  |  OOS Net: ${cs['net']:>+,.0f}")
        print(f"    {'*** GOAL MET ***' if TARGET_LO <= cs['per_month'] <= TARGET_HI and cs['pf'] >= TARGET_PF else '(closest match)'}")
    else:
        print(f"\n  No combination achieved the goal. Closest options above.")

    print(f"\n{SEP}")
    print(f"  Done.")
    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
