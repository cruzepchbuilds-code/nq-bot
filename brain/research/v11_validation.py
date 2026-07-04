"""
brain/research/v11_validation.py

Final validation of the EXACT v11 configuration (all rules applied together):
  - morning ORB (gap+regime+vol+score, Fridays ON, funded 3R)
  - VWAP rejection 11-13h, ONLY on days morning didn't trade
  - PM ORB 22/2.5R (skip Mon+Fri)
  - Asia 25/3R (skip Thu/Aug/Nov)
  - one position at a time (entry blocked while another is open)
  - internal DLL $500 (entry blocked once dailyPnL <= -500)
  - RampMode variant: REJ/PM/Asia only after cumulative P&L >= +$800

Reports: year-by-year net/N/WR/PF, per-strategy contribution, worst days,
max EOD drawdown, $/week, and RampMode-from-zero comparison (full period and
OOS-2025 fresh start).

NOTE (2026-07-03): upstream fix — portfolio_policy.run_year_morning's
regime-ATR window is now a bounded 14-day deque (was unbounded list ->
expanding mean). This script inherits the fix via import; results recorded
BEFORE this date used the buggy morning stream (composed v12 stream deltas:
710 -> 713 trading days, full-period net -0.08%, OOS 2025-26 net +2.4%) —
re-run before citing absolute numbers.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import portfolio_policy as pp     # reuses generators; sets config (Fri ON, pyramid off, funded)
from backtest import load_csv
from datetime import date, datetime
from collections import defaultdict

DLL       = 500.0
RAMP_GATE = 800.0


def build_day_trades():
    print("Loading full bars for morning engine...", flush=True)
    bars = load_csv(pp.DATA)
    print("Generating morning trades...", flush=True)
    morning = []
    for y in pp.YEARS:
        morning.extend(pp.run_year_morning(bars, y))
    del bars
    print(f"  {len(morning)} morning trades", flush=True)

    print("Generating rejection/PM/Asia trades...", flush=True)
    rth = pp.load_days(pp.DATA, 9, 16)
    eve = pp.load_days(pp.DATA, 16, 21)

    day_trades = defaultdict(list)
    for t in morning:
        d = date.fromisoformat(t["date"])
        e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
        x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
        day_trades[d].append(("ORB", e, x, t["pnl"]))
    for d in sorted(rth):
        wd, mo = d.weekday(), d.month
        if wd != 0 and mo not in (4, 5, 6, 9, 12):
            r = pp.rejection_day(rth[d])
            if r:
                day_trades[d].append(("REJ", *r))
        if wd not in (0, 4):
            r = pp.pm_day(rth[d])
            if r:
                day_trades[d].append(("PM", *r))
    for d in sorted(eve):
        if d.weekday() != 3 and d.month not in (8, 11):
            r = pp.asia_day(eve[d])
            if r:
                day_trades[d].append(("ASIA", *r))
    for d in day_trades:
        day_trades[d].sort(key=lambda x: x[1])
    return day_trades


def simulate_v11(day_trades, ramp=False, start_lifetime=0.0):
    """Exact v11 rules. Returns list of (date, strat, pnl) + daily dict."""
    lifetime = start_lifetime
    taken = []
    daily = {}
    for d in sorted(day_trades):
        pnl_day = 0.0
        open_until = None
        morning_traded = any(s == "ORB" for s, _, _, _ in day_trades[d])
        for strat, e_t, x_t, pnl in day_trades[d]:
            if strat == "REJ" and morning_traded:
                continue                          # v11 conditioning rule
            if strat in ("REJ", "PM", "ASIA") and ramp and lifetime < RAMP_GATE:
                continue                          # RampMode lock
            if open_until is not None and e_t < open_until:
                continue                          # one position at a time
            if pnl_day <= -DLL:
                continue                          # internal DLL halt
            pnl_day += pnl
            lifetime += pnl
            open_until = x_t
            taken.append((d, strat, pnl))
        daily[d] = pnl_day
    return taken, daily


def stats(rows):
    if not rows:
        return {"n": 0, "wr": 0, "pf": 0, "net": 0}
    pnls = [p for _, _, p in rows]
    w = [p for p in pnls if p > 0]
    gl = abs(sum(p for p in pnls if p <= 0))
    return {"n": len(pnls), "wr": len(w) / len(pnls),
            "pf": round(sum(w) / gl, 3) if gl else 99.0, "net": round(sum(pnls))}


def curve_metrics(daily):
    days = sorted(daily)
    eq = peak = dd = 0.0
    worst = floor = 0.0
    for d in days:
        p = daily[d]
        worst = min(worst, p)
        eq += p
        floor = min(floor, eq)          # min equity vs fresh-account start
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    weeks = max(1, (days[-1] - days[0]).days / 7) if days else 1
    return {"worst": round(worst), "dd": round(dd), "floor": round(floor),
            "wk": round(sum(daily.values()) / weeks)}


if __name__ == "__main__":
    day_trades = build_day_trades()

    print(f"\n{'═'*100}")
    print(f"  v11 EXACT-CONFIG VALIDATION — 2022-2026, 1c base (engine sizes 2c on strong score days)")
    print(f"{'═'*100}")

    taken, daily = simulate_v11(day_trades, ramp=False)

    # Year-by-year
    print(f"\n  ── Year by year (no ramp) ──")
    print(f"  {'Year':<6} {'N':>4} {'WR':>5} {'PF':>7} {'Net $':>10} {'$/wk':>7} {'WorstDay':>9} {'MaxDD':>8}")
    print(f"  {'─'*66}")
    for y in [2022, 2023, 2024, 2025, 2026]:
        rows = [r for r in taken if r[0].year == y]
        dsub = {d: p for d, p in daily.items() if d.year == y}
        if not dsub:
            continue
        s, c = stats(rows), curve_metrics(dsub)
        print(f"  {y:<6} {s['n']:>4} {s['wr']:>5.0%} {s['pf']:>7.3f} {s['net']:>+10,} "
              f"{c['wk']:>+7,} {c['worst']:>+9,} {-c['dd']:>+8,}")

    s_all, c_all = stats(taken), curve_metrics(daily)
    print(f"  {'─'*66}")
    print(f"  {'ALL':<6} {s_all['n']:>4} {s_all['wr']:>5.0%} {s_all['pf']:>7.3f} "
          f"{s_all['net']:>+10,} {c_all['wk']:>+7,} {c_all['worst']:>+9,} {-c_all['dd']:>+8,}")

    # IS/OOS split
    print(f"\n  ── IS vs OOS ──")
    for label, yrs in [("IS  2022-2024", (2022, 2023, 2024)), ("OOS 2025-2026", (2025, 2026))]:
        rows = [r for r in taken if r[0].year in yrs]
        dsub = {d: p for d, p in daily.items() if d.year in yrs}
        s, c = stats(rows), curve_metrics(dsub)
        print(f"  {label:<14} N={s['n']:>4}  WR={s['wr']:.0%}  PF={s['pf']:.3f}  "
              f"Net=${s['net']:>+10,}  $/wk={c['wk']:>+6,}  worst={c['worst']:>+7,}  DD={-c['dd']:>+8,}")

    # Per-strategy contribution
    print(f"\n  ── Per-strategy (no ramp, full period) ──")
    for st in ["ORB", "REJ", "PM", "ASIA"]:
        rows = [r for r in taken if r[1] == st]
        s = stats(rows)
        oos = stats([r for r in rows if r[0].year >= 2025])
        print(f"  {st:<5} N={s['n']:>4}  PF={s['pf']:>7.3f}  Net=${s['net']:>+9,}   "
              f"| OOS: N={oos['n']:>4}  PF={oos['pf']:>7.3f}  Net=${oos['net']:>+9,}")

    # Worst-day distribution
    pnls = sorted(daily.values())
    print(f"\n  ── Worst 8 days (full period) ──")
    for p in pnls[:8]:
        print(f"    ${p:>+8,.0f}")

    # Ramp variants
    print(f"\n  ── RampMode comparison ──")
    for label, dt, start in [
        ("Full 2022-26, ramp OFF", day_trades, 0),
        ("Full 2022-26, ramp ON ", day_trades, 0),
        ("Fresh acct @2025, OFF ", {d: v for d, v in day_trades.items() if d.year >= 2025}, 0),
        ("Fresh acct @2025, ON  ", {d: v for d, v in day_trades.items() if d.year >= 2025}, 0),
    ]:
        ramp = "ON" in label
        t2, d2 = simulate_v11(dt, ramp=ramp)
        s2, c2 = stats(t2), curve_metrics(d2)
        print(f"  {label}  N={s2['n']:>4}  Net=${s2['net']:>+10,}  $/wk={c2['wk']:>+6,}  "
              f"worst={c2['worst']:>+7,}  DD={-c2['dd']:>+8,}  minEq={c2['floor']:>+7,}")

    # Fresh-account survival: min equity from start, per start-year (ramp ON vs OFF)
    print(f"\n  ── Fresh-account survival (min equity vs start; Lucid floor -$2,000) ──")
    for sy in [2022, 2023, 2024, 2025, 2026]:
        dt = {d: v for d, v in day_trades.items() if d.year >= sy}
        if not dt:
            continue
        _, d_off = simulate_v11(dt, ramp=False)
        _, d_on  = simulate_v11(dt, ramp=True)
        c_off, c_on = curve_metrics(d_off), curve_metrics(d_on)
        def verdict(c):
            return "DIES" if c["floor"] <= -2000 else "survives"
        print(f"  start {sy}:  rampOFF minEq={c_off['floor']:>+7,} ({verdict(c_off)})   "
              f"rampON minEq={c_on['floor']:>+7,} ({verdict(c_on)})")

    print(f"\n{'═'*100}\n  v11_validation done.\n{'═'*100}")
