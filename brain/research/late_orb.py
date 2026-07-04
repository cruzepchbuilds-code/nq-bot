"""
brain/research/late_orb.py

NQ Late Afternoon ORB — 14:30-14:44 ET opening range, entry 14:45-15:30.

Hypothesis: Late-day NQ consolidation produces a directional range whose
break has follow-through into the final hour. Tests independence from the
PM ORB (13:00 session).

Strategy params:
  OR window:    14:30–14:44 ET (15 bars)
  Entry window: 14:45–15:30
  Direction:    first bar closing > OR high + 2pt → long
                first bar closing < OR low  - 2pt → short
  Stop:         22pt fixed from entry
  Target:       2R = 44pt
  Hard flatten: 15:55
  One trade per day (trade_done flag)
  Skip Monday + Friday

POINT_VALUE = 20, commission = 5/trade (round-trip)

IS:  2022-01-01 – 2024-12-31
OOS: 2025-01-01 – 2026-06-30

Parts:
  1. OR range sweep on OOS — filters [5-25, 5-35, 10-30, 10-40, 10-50, 15-40, 15-50]
  2. DOW breakdown (baseline, no OR filter) on OOS
  3. Year-by-year (2025, 2026) on OOS baseline
  4. Independence: what % of PM ORB days (13:00 session) also had a late ORB trigger?
"""

import sys
import os
from datetime import date, datetime, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest import load_csv

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "nq_full.csv"
)

# ── Constants ────────────────────────────────────────────────────────────────
LATE_OR_START   = time(14, 30)
LATE_OR_END     = time(14, 45)   # exclusive: bars 14:30–14:44 included
LATE_ENTRY_START= time(14, 45)
LATE_ENTRY_END  = time(15, 30)
LATE_FLATTEN    = time(15, 55)

BUFFER_PT  = 2.0
STOP_PT    = 22.0
TARGET_PT  = 44.0   # 2R
POINT_VALUE= 20.0
COMMISSION = 5.0    # round-trip per trade

IS_START  = date(2022, 1,  1)
IS_END    = date(2024, 12, 31)
OOS_START = date(2025, 1,  1)
OOS_END   = date(2026, 6,  30)

# PM ORB reference (13:00 session) for independence check
PM_OR_START    = time(13,  0)
PM_OR_END      = time(13, 15)
PM_ENTRY_START = time(13, 15)
PM_ENTRY_END   = time(14, 15)
PM_FLATTEN     = time(15, 55)
PM_BUFFER_PT   = 2.0
PM_STOP_PT     = 22.0
PM_TARGET_PT   = 44.0


# ── Core: group bars by date ──────────────────────────────────────────────────
def group_by_date(bars):
    by_date = defaultdict(list)
    for b in bars:
        ts = b["timestamp"]
        # Strip timezone if present so comparisons are consistent
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
            b = dict(b, timestamp=ts)
        by_date[ts.date()].append(b)
    return {d: sorted(v, key=lambda x: x["timestamp"]) for d, v in by_date.items()}


# ── Single-day Late ORB simulation ────────────────────────────────────────────
def sim_late_orb_day(day_bars, or_min=0.0, or_max=9999.0):
    """
    Returns a dict with keys:
      triggered (bool), direction, entry_px, exit_px, pnl_pts, pnl_usd, result
    or None if no trade.
    """
    or_hi = None
    or_lo = None
    position     = None
    entry_px     = None
    stop_px      = None
    target_px    = None
    trade_done   = False
    triggered    = False

    for bar in day_bars:
        ts  = bar["timestamp"].time()
        cls = bar["close"]
        hi  = bar["high"]
        lo  = bar["low"]

        # ── Build OR (14:30–14:44 inclusive) ─────────────────────────────────
        if LATE_OR_START <= ts < LATE_OR_END:
            or_hi = hi  if (or_hi is None or hi  > or_hi) else or_hi
            or_lo = lo  if (or_lo is None or lo  < or_lo) else or_lo
            continue

        # Skip if OR not formed
        if or_hi is None or or_lo is None:
            continue

        # ── Entry window: 14:45–15:30 ─────────────────────────────────────────
        if LATE_ENTRY_START <= ts <= LATE_ENTRY_END and not trade_done:
            if position is None:
                or_range = or_hi - or_lo

                # OR range filter
                if or_range < or_min or or_range > or_max:
                    continue

                # Long trigger
                if cls > or_hi + BUFFER_PT:
                    position  = "long"
                    entry_px  = cls
                    stop_px   = entry_px - STOP_PT
                    target_px = entry_px + TARGET_PT
                    triggered = True
                    trade_done = True

                # Short trigger
                elif cls < or_lo - BUFFER_PT:
                    position  = "short"
                    entry_px  = cls
                    stop_px   = entry_px + STOP_PT
                    target_px = entry_px - TARGET_PT
                    triggered = True
                    trade_done = True

        # ── Manage open position ──────────────────────────────────────────────
        if position is not None:
            if position == "long":
                # Check stop/target during bar (use hi/lo for intra-bar)
                if lo <= stop_px:
                    exit_px = stop_px
                    pnl_pts = exit_px - entry_px
                    pnl_usd = pnl_pts * POINT_VALUE - COMMISSION
                    return dict(triggered=True, direction="long", entry_px=entry_px,
                                exit_px=exit_px, pnl_pts=pnl_pts, pnl_usd=pnl_usd,
                                result="stop", or_range=or_hi - or_lo)
                if hi >= target_px:
                    exit_px = target_px
                    pnl_pts = exit_px - entry_px
                    pnl_usd = pnl_pts * POINT_VALUE - COMMISSION
                    return dict(triggered=True, direction="long", entry_px=entry_px,
                                exit_px=exit_px, pnl_pts=pnl_pts, pnl_usd=pnl_usd,
                                result="target", or_range=or_hi - or_lo)

            elif position == "short":
                if hi >= stop_px:
                    exit_px = stop_px
                    pnl_pts = entry_px - exit_px
                    pnl_usd = pnl_pts * POINT_VALUE - COMMISSION
                    return dict(triggered=True, direction="short", entry_px=entry_px,
                                exit_px=exit_px, pnl_pts=pnl_pts, pnl_usd=pnl_usd,
                                result="stop", or_range=or_hi - or_lo)
                if lo <= target_px:
                    exit_px = target_px
                    pnl_pts = entry_px - exit_px
                    pnl_usd = pnl_pts * POINT_VALUE - COMMISSION
                    return dict(triggered=True, direction="short", entry_px=entry_px,
                                exit_px=exit_px, pnl_pts=pnl_pts, pnl_usd=pnl_usd,
                                result="target", or_range=or_hi - or_lo)

            # Hard flatten at 15:55
            if ts >= LATE_FLATTEN:
                if position == "long":
                    exit_px = cls
                    pnl_pts = exit_px - entry_px
                elif position == "short":
                    exit_px = cls
                    pnl_pts = entry_px - exit_px
                pnl_usd = pnl_pts * POINT_VALUE - COMMISSION
                return dict(triggered=True, direction=position, entry_px=entry_px,
                            exit_px=exit_px, pnl_pts=pnl_pts, pnl_usd=pnl_usd,
                            result="flatten", or_range=or_hi - or_lo)

    return None  # no trade


# ── Summary helpers ────────────────────────────────────────────────────────────
def summarize(trades):
    if not trades:
        return dict(N=0, wins=0, WR=0.0, PF=0.0, net_usd=0.0, avg_usd=0.0)
    N    = len(trades)
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    gross_win  = sum(t["pnl_usd"] for t in trades if t["pnl_usd"] > 0)
    gross_loss = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] < 0))
    net_usd    = sum(t["pnl_usd"] for t in trades)
    pf = gross_win / gross_loss if gross_loss else float("inf")
    return dict(N=N, wins=wins, WR=100*wins/N, PF=pf,
                net_usd=net_usd, avg_usd=net_usd/N)


# ── PM ORB (13:00) day simulation — for independence check ───────────────────
def sim_pm_orb_day(day_bars):
    """Returns True if a PM ORB (13:00) trade was triggered, else False."""
    or_hi = None
    or_lo = None
    triggered = False
    trade_done = False

    for bar in day_bars:
        ts  = bar["timestamp"].time()
        cls = bar["close"]

        if PM_OR_START <= ts < PM_OR_END:
            hi = bar["high"]; lo = bar["low"]
            or_hi = hi if (or_hi is None or hi > or_hi) else or_hi
            or_lo = lo if (or_lo is None or lo < or_lo) else or_lo
            continue

        if or_hi is None or or_lo is None:
            continue

        if PM_ENTRY_START <= ts <= PM_ENTRY_END and not trade_done:
            if cls > or_hi + PM_BUFFER_PT:
                triggered = True; trade_done = True
            elif cls < or_lo - PM_BUFFER_PT:
                triggered = True; trade_done = True

    return triggered


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Loading data …")
    raw = load_csv(DATA_PATH)
    # load_csv returns bars with 'timestamp' key as datetime
    by_date_all = group_by_date(raw)

    # Split IS / OOS
    is_dates  = [d for d in sorted(by_date_all) if IS_START  <= d <= IS_END]
    oos_dates = [d for d in sorted(by_date_all) if OOS_START <= d <= OOS_END]

    SKIP_DOW = {0, 4}  # Monday=0, Friday=4

    # ── Collect IS & OOS baseline trades (no OR filter) ────────────────────────
    def collect_trades(dates, or_min=0.0, or_max=9999.0):
        trades = []
        for d in dates:
            if d.weekday() in SKIP_DOW:
                continue
            result = sim_late_orb_day(by_date_all[d], or_min=or_min, or_max=or_max)
            if result:
                result["date"] = d
                trades.append(result)
        return trades

    is_trades  = collect_trades(is_dates)
    oos_trades = collect_trades(oos_dates)

    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "═"*65)
    print("  LATE AFTERNOON ORB  |  14:30-14:44 OR  |  NQ Futures")
    print("═"*65)

    s_is  = summarize(is_trades)
    s_oos = summarize(oos_trades)

    print(f"\n{'Period':<8}  {'N':>5}  {'WR%':>6}  {'PF':>6}  {'Net$':>10}  {'Avg$/trade':>10}")
    print("-"*55)
    print(f"{'IS':.<8}  {s_is['N']:>5}  {s_is['WR']:>6.1f}  {s_is['PF']:>6.2f}  "
          f"${s_is['net_usd']:>9,.0f}  ${s_is['avg_usd']:>9,.0f}")
    print(f"{'OOS':.<8}  {s_oos['N']:>5}  {s_oos['WR']:>6.1f}  {s_oos['PF']:>6.2f}  "
          f"${s_oos['net_usd']:>9,.0f}  ${s_oos['avg_usd']:>9,.0f}")

    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "─"*65)
    print("  PART 1 — OR Range Sweep (OOS only, sorted by PF)")
    print("─"*65)
    print(f"  {'Filter':>10}  {'N':>5}  {'WR%':>6}  {'PF':>6}  {'Net$':>10}")
    print("  " + "-"*50)

    filters = [
        (5,  25),
        (5,  35),
        (10, 30),
        (10, 40),
        (10, 50),
        (15, 40),
        (15, 50),
    ]
    sweep_results = []
    for lo, hi in filters:
        trades = collect_trades(oos_dates, or_min=lo, or_max=hi)
        s = summarize(trades)
        sweep_results.append((lo, hi, s))

    sweep_results.sort(key=lambda x: x[2]["PF"], reverse=True)
    for lo, hi, s in sweep_results:
        label = f"{lo}-{hi}pt"
        print(f"  {label:>10}  {s['N']:>5}  {s['WR']:>6.1f}  {s['PF']:>6.2f}  "
              f"${s['net_usd']:>9,.0f}")

    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "─"*65)
    print("  PART 2 — DOW Breakdown, OOS Baseline (no OR filter)")
    print("─"*65)
    DOW_NAMES = {1: "Tue", 2: "Wed", 3: "Thu"}  # Mon/Fri skipped
    dow_trades = defaultdict(list)
    for t in oos_trades:
        dow_trades[t["date"].weekday()].append(t)

    print(f"  {'Day':<5}  {'N':>5}  {'WR%':>6}  {'PF':>6}  {'Net$':>10}")
    print("  " + "-"*40)
    for dow in [1, 2, 3]:
        s = summarize(dow_trades[dow])
        print(f"  {DOW_NAMES[dow]:<5}  {s['N']:>5}  {s['WR']:>6.1f}  {s['PF']:>6.2f}  "
              f"${s['net_usd']:>9,.0f}")

    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "─"*65)
    print("  PART 3 — Year-by-Year, OOS Baseline")
    print("─"*65)
    year_trades = defaultdict(list)
    for t in oos_trades:
        year_trades[t["date"].year].append(t)

    print(f"  {'Year':<6}  {'N':>5}  {'WR%':>6}  {'PF':>6}  {'Net$':>10}")
    print("  " + "-"*42)
    for yr in sorted(year_trades):
        s = summarize(year_trades[yr])
        print(f"  {yr:<6}  {s['N']:>5}  {s['WR']:>6.1f}  {s['PF']:>6.2f}  "
              f"${s['net_usd']:>9,.0f}")

    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "─"*65)
    print("  PART 4 — Independence: PM ORB (13:00) vs Late ORB (14:30)")
    print("─"*65)

    # Identify OOS days where PM ORB triggered
    pm_trigger_dates   = set()
    late_trigger_dates = set()

    for d in oos_dates:
        if d.weekday() in SKIP_DOW:
            continue
        if sim_pm_orb_day(by_date_all[d]):
            pm_trigger_dates.add(d)

    for t in oos_trades:
        late_trigger_dates.add(t["date"])

    both      = pm_trigger_dates & late_trigger_dates
    pm_only   = pm_trigger_dates - late_trigger_dates
    late_only = late_trigger_dates - pm_trigger_dates
    neither_count = sum(
        1 for d in oos_dates
        if d.weekday() not in SKIP_DOW
        and d not in pm_trigger_dates
        and d not in late_trigger_dates
    )
    total_oos_eligible = sum(1 for d in oos_dates if d.weekday() not in SKIP_DOW)

    pct_pm_also_late = 100 * len(both) / len(pm_trigger_dates) if pm_trigger_dates else 0
    pct_late_also_pm = 100 * len(both) / len(late_trigger_dates) if late_trigger_dates else 0
    pct_overlap_total = 100 * len(both) / total_oos_eligible if total_oos_eligible else 0

    print(f"  Total eligible OOS days (Tue/Wed/Thu): {total_oos_eligible}")
    print(f"  PM ORB (13:00) triggered:              {len(pm_trigger_dates)}")
    print(f"  Late ORB (14:30) triggered:            {len(late_trigger_dates)}")
    print(f"  BOTH triggered same day:               {len(both)}")
    print(f"  PM only:                               {len(pm_only)}")
    print(f"  Late only:                             {len(late_only)}")
    print(f"  Neither:                               {neither_count}")
    print()
    print(f"  Of PM ORB days,  how many also had Late ORB? {pct_pm_also_late:.1f}%")
    print(f"  Of Late ORB days, how many also had PM ORB?  {pct_late_also_pm:.1f}%")
    print(f"  Overlap as % of all eligible days:           {pct_overlap_total:.1f}%")

    if pct_pm_also_late < 40:
        verdict = "HIGH independence — strategies fire on very different days."
    elif pct_pm_also_late < 60:
        verdict = "MODERATE independence — some overlap, additive but correlated."
    else:
        verdict = "LOW independence — strategies co-fire often, likely redundant."
    print(f"\n  Independence verdict: {verdict}")

    # ── Final verdict ──────────────────────────────────────────────────────────
    print("\n" + "═"*65)
    print("  EDGE VERDICT")
    print("═"*65)
    if s_oos["N"] == 0:
        print("  No OOS trades — strategy never triggered. No edge to assess.")
    else:
        print(f"  OOS:  N={s_oos['N']}  WR={s_oos['WR']:.1f}%  PF={s_oos['PF']:.2f}"
              f"  Net=${s_oos['net_usd']:,.0f}  Avg=${s_oos['avg_usd']:,.0f}/trade")
        print()
        if s_oos["PF"] >= 1.5 and s_oos["net_usd"] > 0:
            print("  STRONG EDGE — OOS PF ≥ 1.5, profitable. Strategy viable.")
        elif s_oos["PF"] >= 1.2 and s_oos["net_usd"] > 0:
            print("  MARGINAL EDGE — OOS PF 1.2-1.5. Filter optimization recommended.")
        elif s_oos["PF"] >= 1.0:
            print("  BREAKEVEN — OOS PF ~1.0. Real edge unclear after slippage/costs.")
        else:
            print("  NO EDGE — OOS PF < 1.0. Strategy loses money out-of-sample.")
    print()


if __name__ == "__main__":
    main()
