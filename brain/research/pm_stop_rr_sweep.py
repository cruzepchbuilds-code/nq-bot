"""
brain/research/pm_stop_rr_sweep.py

PM ORB stop x RR sweep — the one dimension pm_sweep.py never tested
(it held stop=22 / RR=2.0 fixed and swept OR ranges + windows).

Fixed (v10.3 settings): OR 13:00-13:14, entry 13:15-14:00, OR range 15-60pt,
buffer 2pt, skip Mon+Fri, 1 trade/day, flatten 15:55.

Sweep: stop {16, 18, 20, 22, 26, 30} x RR {1.5, 2.0, 2.5, 3.0}
IS: 2022-2024  |  OOS: 2025-Jun 2026
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import csv
from datetime import datetime, time, date
from collections import defaultdict
from itertools import product

POINT   = 20.0
COST    = 9.50          # commission + slippage round-trip (matches pm_sweep.py)
BUF     = 2.0
MIN_OR  = 15.0
MAX_OR  = 60.0
FLATTEN = time(15, 55)

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data", "nq_full.csv")


def load_days(path):
    """RTH bars 13:00-15:55 bucketed by date (that's all PM ORB needs)."""
    days = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            ts_str = row["timestamp"][:19]
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts.hour < 13 or ts.hour >= 16:
                continue
            days[ts.date()].append({
                "t": ts.time(),
                "h": float(row["high"]), "l": float(row["low"]),
                "c": float(row["close"]),
            })
    return days


def run_day(bars, stop_pt, rr):
    """One PM ORB day. Returns pnl_usd or None if no trade."""
    or_hi = or_lo = None
    or_done = False
    entry = sl = tp = None
    is_long = None

    for b in bars:
        t = b["t"]
        if t >= FLATTEN:
            break

        # OR build 13:00-13:14
        if t < time(13, 15):
            or_hi = b["h"] if or_hi is None else max(or_hi, b["h"])
            or_lo = b["l"] if or_lo is None else min(or_lo, b["l"])
            continue

        if not or_done:
            or_done = True
            if or_hi is None or not (MIN_OR <= or_hi - or_lo <= MAX_OR):
                return None

        # Manage open position
        if entry is not None:
            if is_long:
                if b["l"] <= sl:  return (sl - entry) * POINT - COST
                if b["h"] >= tp:  return (tp - entry) * POINT - COST
            else:
                if b["h"] >= sl:  return (entry - sl) * POINT - COST
                if b["l"] <= tp:  return (entry - tp) * POINT - COST
            continue

        # Entry window 13:15-14:00
        if t > time(14, 0):
            continue
        if b["c"] > or_hi + BUF:
            entry = b["c"]; sl = entry - stop_pt; tp = entry + stop_pt * rr
            is_long = True
        elif b["c"] < or_lo - BUF:
            entry = b["c"]; sl = entry + stop_pt; tp = entry - stop_pt * rr
            is_long = False

    if entry is not None:  # flatten at 15:55
        last = bars[-1]["c"] if bars else entry
        pts = (last - entry) if is_long else (entry - last)
        return pts * POINT - COST
    return None


def stats(pnls):
    if not pnls:
        return {"n": 0, "wr": 0, "pf": 0, "net": 0}
    wins = [p for p in pnls if p > 0]
    gw = sum(wins)
    gl = abs(sum(p for p in pnls if p <= 0))
    return {"n": len(pnls), "wr": len(wins) / len(pnls),
            "pf": round(gw / gl, 3) if gl else 99.0, "net": round(sum(pnls))}


if __name__ == "__main__":
    print(f"\n{'='*88}")
    print(f"  PM ORB stop x RR sweep  (OR 15-60pt, 13:15-14:00 entry, Tue-Thu)")
    print(f"  IS: 2022-2024  |  OOS: 2025-Jun 2026")
    print(f"{'='*88}\n")

    print("  Loading...", end=" ", flush=True)
    days = load_days(DATA)
    print(f"{len(days)} days\n")

    results = []
    for stop_pt, rr in product([16, 18, 20, 22, 26, 30], [1.5, 2.0, 2.5, 3.0]):
        is_pnls, oos_pnls = [], []
        for d in sorted(days):
            if d.weekday() in (0, 4):        # skip Mon + Fri
                continue
            pnl = run_day(days[d], stop_pt, rr)
            if pnl is None:
                continue
            (is_pnls if d.year <= 2024 else oos_pnls).append(pnl)
        results.append((stop_pt, rr, stats(is_pnls), stats(oos_pnls)))

    results.sort(key=lambda x: x[3]["net"], reverse=True)
    print(f"  {'Stop':>5} {'RR':>4} {'Tgt':>4}   {'IS-N':>5} {'IS-WR':>6} {'IS-PF':>6} {'IS-Net':>9}   "
          f"{'OOS-N':>5} {'OOS-WR':>6} {'OOS-PF':>6} {'OOS-Net':>9}")
    print(f"  {'─'*86}")
    for stop_pt, rr, s_is, s_oos in results:
        mark = "  ← v10.3" if (stop_pt, rr) == (22, 2.0) else ""
        print(f"  {stop_pt:>5} {rr:>4.1f} {stop_pt*rr:>4.0f}   "
              f"{s_is['n']:>5} {s_is['wr']:>6.0%} {s_is['pf']:>6.3f} ${s_is['net']:>+8,.0f}   "
              f"{s_oos['n']:>5} {s_oos['wr']:>6.0%} {s_oos['pf']:>6.3f} ${s_oos['net']:>+8,.0f}{mark}")
    print()
