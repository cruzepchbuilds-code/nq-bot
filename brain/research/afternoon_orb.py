"""
brain/research/afternoon_orb.py

NQ Afternoon ORB — 13:00-13:14 ET opening range, entry 13:15-14:00.

Hypothesis: Post-lunch NQ consolidation (12:30-13:00) produces a clean
directional range. Break above/below that range has follow-through into
close. Different days than AM ORB fires — complementary, not correlated.

Tests:
  1. Baseline: OR 13:00-13:14, entry 13:15-14:00, 2R target, 27pt stop
  2. Sweep: OR range filter (20-50pt), stop size (15pt vs 27pt), RR (2R vs 3R)
  3. Correlation: on days AM ORB fired, does PM ORB add or hurt?
  4. Best param combo for OOS PF

Usage:
    python3 brain/research/afternoon_orb.py
"""
import sys
import os
from datetime import date, datetime, time, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest import load_csv

DATA_PATH = "data/nq_full.csv"

PM_OR_START = time(13, 0)
PM_OR_END   = time(13, 15)
PM_ENTRY_START = time(13, 15)
PM_ENTRY_END   = time(14, 15)
PM_EXIT_TIME   = time(15, 55)

POINT_VALUE = 20.0
COMMISSION  = 5.0   # round trip
SLIPPAGE_PT = 0.5   # 2 ticks each side × $20 = $10 per side... actually 2 ticks = 0.5pt NQ

def run_pm_orb(bars, stop_pts=22.0, rr=2.0, or_min=15.0, or_max=60.0, buffer=2.0):
    """Run afternoon ORB on pre-grouped daily bars."""
    trades = []
    by_date = defaultdict(list)
    for b in bars:
        by_date[b["ts"].date()].append(b)

    for d, day_bars in sorted(by_date.items()):
        if d.weekday() == 0 or d.weekday() == 4:  # skip Mon/Fri
            continue

        or_hi = or_lo = None
        or_bars_done = False
        position = None  # "long" / "short"
        entry_px = None
        stop_px = target_px = None
        entry_ts = None
        trade_done = False  # one trade max per day

        for bar in sorted(day_bars, key=lambda x: x["ts"]):
            ts = bar["ts"].time()
            px_h, px_l, px_c = bar["high"], bar["low"], bar["close"]

            # Build OR
            if PM_OR_START <= ts < PM_OR_END:
                or_hi = max(or_hi, px_h) if or_hi is not None else px_h
                or_lo = min(or_lo, px_l) if or_lo is not None else px_l

            # Finalize OR
            if ts >= PM_OR_END and not or_bars_done:
                or_bars_done = True
                if or_hi is None or or_lo is None:
                    break
                or_range = or_hi - or_lo
                if not (or_min <= or_range <= or_max):
                    break

            if not or_bars_done:
                continue

            # Exit open position
            if position:
                # Check stop
                if position == "long" and px_l <= stop_px:
                    pnl = (stop_px - entry_px) * POINT_VALUE - COMMISSION
                    trades.append({"date": d, "side": "long", "pnl": pnl, "bars": (bar["ts"] - entry_ts).seconds // 60, "hit": "stop"})
                    position = None
                    continue
                if position == "short" and px_h >= stop_px:
                    pnl = (entry_px - stop_px) * POINT_VALUE - COMMISSION
                    trades.append({"date": d, "side": "short", "pnl": pnl, "bars": (bar["ts"] - entry_ts).seconds // 60, "hit": "stop"})
                    position = None
                    continue
                # Check target
                if position == "long" and px_h >= target_px:
                    pnl = (target_px - entry_px) * POINT_VALUE - COMMISSION
                    trades.append({"date": d, "side": "long", "pnl": pnl, "bars": (bar["ts"] - entry_ts).seconds // 60, "hit": "target"})
                    position = None
                    continue
                if position == "short" and px_l <= target_px:
                    pnl = (entry_px - target_px) * POINT_VALUE - COMMISSION
                    trades.append({"date": d, "side": "short", "pnl": pnl, "bars": (bar["ts"] - entry_ts).seconds // 60, "hit": "target"})
                    position = None
                    continue
                # EOD exit
                if ts >= PM_EXIT_TIME:
                    exit_px = bar["close"]
                    if position == "long":
                        pnl = (exit_px - entry_px) * POINT_VALUE - COMMISSION
                    else:
                        pnl = (entry_px - exit_px) * POINT_VALUE - COMMISSION
                    trades.append({"date": d, "side": position, "pnl": pnl, "bars": (bar["ts"] - entry_ts).seconds // 60, "hit": "eod"})
                    position = None
                    continue
                continue

            # Entry logic — one trade per day only
            if PM_ENTRY_START <= ts <= PM_ENTRY_END and position is None and not trade_done:
                if px_c > or_hi + buffer:
                    position = "long"
                    entry_px = or_hi + buffer
                    stop_px = entry_px - stop_pts
                    target_px = entry_px + stop_pts * rr
                    entry_ts = bar["ts"]
                elif px_c < or_lo - buffer:
                    position = "short"
                    entry_px = or_lo - buffer
                    stop_px = entry_px + stop_pts
                    target_px = entry_px - stop_pts * rr
                    entry_ts = bar["ts"]
                if position is not None:
                    trade_done = True

        # Handle open position at end of data
        if position and day_bars:
            exit_px = day_bars[-1]["close"]
            if position == "long":
                pnl = (exit_px - entry_px) * POINT_VALUE - COMMISSION
            else:
                pnl = (entry_px - exit_px) * POINT_VALUE - COMMISSION
            trades.append({"date": d, "side": position, "pnl": pnl, "bars": 0, "hit": "eod"})

    return trades


def pf(trades):
    wins  = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    return wins / losses if losses > 0 else float("inf")


def stats(trades, label=""):
    if not trades:
        return f"{label:30s} N=0"
    n = len(trades)
    net = sum(t["pnl"] for t in trades)
    wr = sum(1 for t in trades if t["pnl"] > 0) / n
    p = pf(trades)
    avg = net / n
    return f"{label:30s} N={n:3d}  WR={wr:.0%}  PF={p:5.2f}  Net=${net:>+8,.0f}  Avg=${avg:>+5.0f}"


def main():
    print("=" * 68)
    print("  NQ Afternoon ORB Research")
    print("  OR window: 13:00-13:14 | Entry: 13:15-14:15 | Exit: 15:55")
    print("=" * 68)

    raw = load_csv(DATA_PATH)
    bars = []
    for row in raw:
        try:
            ts = datetime.strptime(str(row["timestamp"])[:19], "%Y-%m-%d %H:%M:%S")
            bars.append({
                "ts": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low":  float(row["low"]),
                "close": float(row["close"]),
            })
        except Exception:
            continue

    print(f"\nLoaded {len(bars):,} bars")

    # Split IS/OOS
    IS_END  = date(2024, 12, 31)
    OOS_START = date(2025, 1, 1)

    is_bars  = [b for b in bars if b["ts"].date() <= IS_END]
    oos_bars = [b for b in bars if b["ts"].date() >= OOS_START]

    print(f"IS  bars: {len(is_bars):,}")
    print(f"OOS bars: {len(oos_bars):,}")

    # ── Baseline ─────────────────────────────────────────────────────────
    print("\n── Baseline: stop=22pt, RR=2.0, OR=15-60pt ─────────────────")
    is_t  = run_pm_orb(is_bars,  stop_pts=22, rr=2.0, or_min=15, or_max=60)
    oos_t = run_pm_orb(oos_bars, stop_pts=22, rr=2.0, or_min=15, or_max=60)
    print(stats(is_t,  "IS  2022-2024:"))
    print(stats(oos_t, "OOS 2025-2026:"))

    # ── Stop size sweep ───────────────────────────────────────────────────
    print("\n── Stop size sweep (OR=15-60pt, RR=2.0) ─────────────────────")
    for stop in [10, 15, 22, 27, 35]:
        t = run_pm_orb(oos_bars, stop_pts=stop, rr=2.0, or_min=15, or_max=60)
        print(stats(t, f"  stop={stop}pt:"))

    # ── RR sweep ──────────────────────────────────────────────────────────
    print("\n── RR target sweep (stop=22pt, OR=15-60pt) ──────────────────")
    for rr in [1.5, 2.0, 2.5, 3.0]:
        t = run_pm_orb(oos_bars, stop_pts=22, rr=rr, or_min=15, or_max=60)
        print(stats(t, f"  RR={rr}x:"))

    # ── OR range sweep ────────────────────────────────────────────────────
    print("\n── OR range sweep (stop=22pt, RR=2.0) ───────────────────────")
    for lo, hi in [(10,40), (15,50), (15,60), (20,60), (25,70), (15,80)]:
        t = run_pm_orb(oos_bars, stop_pts=22, rr=2.0, or_min=lo, or_max=hi)
        print(stats(t, f"  OR={lo}-{hi}pt:"))

    # ── Year by year ──────────────────────────────────────────────────────
    print("\n── Year-by-Year OOS ─────────────────────────────────────────")
    for yr in [2025, 2026]:
        yr_bars = [b for b in oos_bars if b["ts"].year == yr]
        t = run_pm_orb(yr_bars, stop_pts=22, rr=2.0, or_min=15, or_max=60)
        print(stats(t, f"  {yr}:"))

    # ── Direction breakdown ───────────────────────────────────────────────
    print("\n── Long vs Short (OOS baseline) ─────────────────────────────")
    baseline_oos = run_pm_orb(oos_bars, stop_pts=22, rr=2.0, or_min=15, or_max=60)
    longs  = [t for t in baseline_oos if t["side"] == "long"]
    shorts = [t for t in baseline_oos if t["side"] == "short"]
    print(stats(longs,  "  Long:"))
    print(stats(shorts, "  Short:"))

    # ── Hit type ──────────────────────────────────────────────────────────
    print("\n── Exit type (OOS baseline) ─────────────────────────────────")
    for hit in ["target", "stop", "eod"]:
        t = [x for x in baseline_oos if x["hit"] == hit]
        print(stats(t, f"  {hit}:"))

    # ── Best combo ───────────────────────────────────────────────────────
    print("\n── Best param search (OOS) ──────────────────────────────────")
    best_pf = 0
    best_cfg = None
    best_t = []
    for stop in [15, 22, 27]:
        for rr in [2.0, 2.5, 3.0]:
            for lo, hi in [(10,40), (15,50), (15,60), (20,60)]:
                t = run_pm_orb(oos_bars, stop_pts=stop, rr=rr, or_min=lo, or_max=hi)
                if len(t) >= 10:
                    p = pf(t)
                    if p > best_pf:
                        best_pf = p
                        best_cfg = (stop, rr, lo, hi)
                        best_t = t
    if best_cfg:
        print(f"  Best: stop={best_cfg[0]}pt RR={best_cfg[1]}x OR={best_cfg[2]}-{best_cfg[3]}pt")
        print(stats(best_t, "  Result:"))
        # IS validation
        is_best = run_pm_orb(is_bars, stop_pts=best_cfg[0], rr=best_cfg[1],
                              or_min=best_cfg[2], or_max=best_cfg[3])
        print(stats(is_best, "  IS check:"))


if __name__ == "__main__":
    main()
