"""Quick DOW breakdown for PM ORB to guide Friday/Monday skip decisions."""
import sys, os
from datetime import date, datetime, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest import load_csv

DATA_PATH = "data/nq_full.csv"
PM_OR_START, PM_OR_END = time(13, 0), time(13, 15)
PM_ENTRY_START, PM_ENTRY_END = time(13, 15), time(14, 15)
PM_EXIT_TIME = time(15, 55)
STOP, RR, OR_MIN, OR_MAX, BUF = 22.0, 2.0, 15.0, 50.0, 2.0
POINT_VALUE, COMMISSION = 20.0, 5.0

raw = load_csv(DATA_PATH)
bars = []
for row in raw:
    try:
        ts = datetime.strptime(str(row["timestamp"])[:19], "%Y-%m-%d %H:%M:%S")
        bars.append({"ts": ts, "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"])})
    except Exception:
        continue

by_date = defaultdict(list)
for b in bars:
    if b["ts"].date() >= date(2025, 1, 1):
        by_date[b["ts"].date()].append(b)

trades_by_dow = defaultdict(list)
for d, day_bars in sorted(by_date.items()):
    or_hi = or_lo = None
    or_done = False
    trade_done = False
    position = None
    entry_px = stop_px = target_px = None

    for bar in sorted(day_bars, key=lambda x: x["ts"]):
        ts = bar["ts"].time()
        h, l, c = bar["high"], bar["low"], bar["close"]

        if PM_OR_START <= ts < PM_OR_END:
            or_hi = max(or_hi, h) if or_hi is not None else h
            or_lo = min(or_lo, l) if or_lo is not None else l

        if ts >= PM_OR_END and not or_done:
            or_done = True
            if or_hi is None or or_lo is None or not (OR_MIN <= or_hi - or_lo <= OR_MAX):
                break

        if not or_done:
            continue

        if position:
            if position == "long" and l <= stop_px:
                trades_by_dow[d.weekday()].append((stop_px - entry_px) * POINT_VALUE - COMMISSION)
                position = None; continue
            if position == "short" and h >= stop_px:
                trades_by_dow[d.weekday()].append((entry_px - stop_px) * POINT_VALUE - COMMISSION)
                position = None; continue
            if position == "long" and h >= target_px:
                trades_by_dow[d.weekday()].append((target_px - entry_px) * POINT_VALUE - COMMISSION)
                position = None; continue
            if position == "short" and l <= target_px:
                trades_by_dow[d.weekday()].append((entry_px - target_px) * POINT_VALUE - COMMISSION)
                position = None; continue
            if ts >= PM_EXIT_TIME:
                ep = bar["close"]
                pnl = ((ep - entry_px) if position == "long" else (entry_px - ep)) * POINT_VALUE - COMMISSION
                trades_by_dow[d.weekday()].append(pnl)
                position = None; continue
            continue

        if PM_ENTRY_START <= ts <= PM_ENTRY_END and not trade_done:
            if c > or_hi + BUF:
                position = "long"; entry_px = or_hi + BUF; stop_px = entry_px - STOP; target_px = entry_px + STOP * RR; trade_done = True
            elif c < or_lo - BUF:
                position = "short"; entry_px = or_lo - BUF; stop_px = entry_px + STOP; target_px = entry_px - STOP * RR; trade_done = True

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri"]
print(f"\n{'Day':<6} {'N':>4} {'WR%':>6} {'PF':>6} {'Net $':>10} {'Avg $':>8}")
print("-" * 45)
for i in range(5):
    t = trades_by_dow.get(i, [])
    if not t:
        print(f"{DOW[i]:<6} {'0':>4}")
        continue
    wins = sum(1 for x in t if x > 0)
    gross_w = sum(x for x in t if x > 0)
    gross_l = abs(sum(x for x in t if x <= 0))
    wr = wins / len(t) * 100
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    net = sum(t)
    avg = net / len(t)
    print(f"{DOW[i]:<6} {len(t):>4} {wr:>5.0f}% {pf:>6.2f} {net:>+10,.0f} {avg:>+8,.0f}")
