"""
brain/research/vwap_scale_up.py

Goal: increase BOTH trade count AND net profit for CruzCapitalVWAP v4.
v4 baseline: OOS (2025-2026) PF=1.618, N=97, Net=$13,834 (~$769/mo, ~5.4 trades/mo)

Strategy: find dimensions where removing constraints adds trades WITHOUT killing PF.
All results compared to v4 baseline. Want: N↑ AND Net↑ AND PF ≥ 1.40 OOS.

Dimensions:
  D1: MaxTrades/day: 2 (allow second trade after first closes)
  D2: Window end: 1:30 PM, 2:00 PM, 3:00 PM (more time for setups)
  D3: MIN_EXTEND: 15pt, 20pt (lower bar → more setups qualify)
  D4: TrendAligned=False (allow counter-trend reclaims too)
  D5: Weak months: add back Sep (9), Dec (12), or May (5) individually
  D6: Combo sweeps: stack best performers

IS: 2024  |  OOS: 2025-2026
"""

import csv, os
from datetime import datetime, time, date
from collections import defaultdict

NQ_POINT = 20.0
COST     = 14.50   # $4.50 commission + $5.00 slip × 2

BASE    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NQ_DATA = os.path.join(BASE, "data", "nq_1min.csv")

IS_YEARS  = {2024}
OOS_YEARS = {2025, 2026}
WEAK_BASE = frozenset({4, 5, 6, 9, 12})   # v4 weak set


# ── Data loading ──────────────────────────────────────────────────────────────

def load_bars(path):
    bars_by_day = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            ts_str = row["timestamp"][:19]
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts.hour < 9 or ts.hour >= 16:
                continue
            bars_by_day[ts.date()].append({
                "ts":    ts,
                "t":     ts.time(),
                "h":     float(row["high"]),
                "l":     float(row["low"]),
                "c":     float(row["close"]),
                "v":     float(row["volume"]),
                "o":     float(row["open"]),
                "dow":   ts.weekday(),   # 0=Mon, 4=Fri
                "month": ts.month,
            })
    return bars_by_day


# ── Day simulation ────────────────────────────────────────────────────────────

def run_day(bars, stop_pt=20, rr=2.5, min_extend=25,
            ext_start=time(10, 0), entry_start=time(11, 0),
            window_end=time(13, 0), max_trades=1, trend_aligned=True):
    T_930  = time(9,  30)
    T_1030 = time(10, 30)
    T_1555 = time(15, 55)

    sum_pv = sum_vol = 0.0
    vwap = open_930 = am_trend = None
    was_extended = False
    prev_above   = None

    trades_today = 0
    in_pos       = False
    pos_long     = None
    entry_px = sl = tp = entry_t = None
    trades = []

    for b in bars:
        t = b["t"]
        if t >= T_1555:
            break

        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]

        if t >= T_930:
            tp2 = (b["h"] + b["l"] + b["c"]) / 3.0
            sum_pv  += tp2 * b["v"]
            sum_vol += b["v"]
            if sum_vol > 0:
                vwap = sum_pv / sum_vol

        if am_trend is None and t >= T_1030 and open_930 and vwap:
            am_trend = "bull" if b["c"] > open_930 else "bear"

        if vwap is None or t < ext_start:
            if vwap:
                prev_above = b["c"] > vwap
            continue

        close      = b["c"]
        curr_above = close > vwap

        # Manage open position
        if in_pos:
            if pos_long:
                if   b["l"] <= sl:  pnl_pts = sl - entry_px
                elif b["h"] >= tp:  pnl_pts = tp - entry_px
                else:
                    if t >= window_end:
                        pnl_pts = close - entry_px
                    else:
                        prev_above = curr_above; continue
            else:
                if   b["h"] >= sl:  pnl_pts = entry_px - sl
                elif b["l"] <= tp:  pnl_pts = entry_px - tp
                else:
                    if t >= window_end:
                        pnl_pts = entry_px - close
                    else:
                        prev_above = curr_above; continue
            trades.append({
                "pnl_usd": round(pnl_pts * NQ_POINT - COST, 2),
                "entry_t": entry_t,
                "dir":     "long" if pos_long else "short",
            })
            in_pos = False
            prev_above = curr_above
            continue

        if trades_today >= max_trades or t >= window_end:
            prev_above = curr_above; continue

        if not was_extended and abs(close - vwap) > min_extend:
            was_extended = True

        if was_extended and prev_above is not None and t >= entry_start:
            cu = (not prev_above) and curr_above
            cd = prev_above and (not curr_above)
            gl = cu and (not trend_aligned or am_trend == "bull")
            gs = cd and (not trend_aligned or am_trend == "bear")
            if gl:
                entry_px = close; sl, tp = close - stop_pt, close + stop_pt * rr
                pos_long = True;  in_pos = True; trades_today += 1; was_extended = False; entry_t = t
            elif gs:
                entry_px = close; sl, tp = close + stop_pt, close - stop_pt * rr
                pos_long = False; in_pos = True; trades_today += 1; was_extended = False; entry_t = t

        prev_above = curr_above

    if in_pos and entry_px is not None:
        last_c  = bars[-1]["c"]
        pnl_pts = (last_c - entry_px) if pos_long else (entry_px - last_c)
        trades.append({
            "pnl_usd": round(pnl_pts * NQ_POINT - COST, 2),
            "entry_t": entry_t,
            "dir":     "long" if pos_long else "short",
        })
    return trades


# ── Period runner ─────────────────────────────────────────────────────────────

def run_period(bars_by_day, years, weak_months=WEAK_BASE, skip_fri=False, **day_kw):
    results = []
    for d, bars in sorted(bars_by_day.items()):
        if d.year not in years or not bars:
            continue
        dow   = bars[0]["dow"]
        month = bars[0]["month"]
        if dow == 0:
            continue
        if skip_fri and dow == 4:
            continue
        if month in weak_months:
            continue
        for t in run_day(bars, **day_kw):
            results.append({**t, "date": d, "dow": dow, "month": month})
    return results


# ── Stats / display ───────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0, "avg": 0}
    wins    = [t for t in trades if t["pnl_usd"] > 0]
    gross_w = sum(t["pnl_usd"] for t in wins)
    gross_l = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] < 0))
    net     = sum(t["pnl_usd"] for t in trades)
    return {
        "n":   len(trades),
        "wr":  len(wins) / len(trades),
        "pf":  round(gross_w / gross_l, 3) if gross_l else 99.0,
        "net": round(net),
        "avg": round(net / len(trades)),
    }


V4_BASE_PARAMS = dict(
    stop_pt=20, rr=2.5, min_extend=25,
    ext_start=time(10, 0), entry_start=time(11, 0),
    window_end=time(13, 0), max_trades=1, trend_aligned=True,
)

W = 46

def row(label, s, ref_s):
    dn  = s["n"]   - ref_s["n"]
    dnet= s["net"] - ref_s["net"]
    both_better = dn > 0 and dnet > 0
    flag = "  ← N↑ Net↑" if both_better else (
           "  ← N↑ Net↓" if dn > 0 else (
           "  ← N↓ Net↑" if dnet > 0 else ""))
    print(f"  {label:<{W}}  N={s['n']:>3} ({dn:+d})  "
          f"WR={s['wr']:.0%}  PF={s['pf']:.3f}  "
          f"Net=${s['net']:>+9,.0f} ({dnet:+,.0f})"
          f"{flag}")


def section(title):
    print(f"\n  {'─'*86}")
    print(f"  {title}")
    print(f"  {'─'*86}")
    print(f"  {'Label':<{W}}  N       WR    PF       Net $          ΔNet")
    print(f"  {'─'*86}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'='*90}")
    print(f"  CruzCapital VWAP Scale-Up Research  —  want: N↑ AND Net↑, OOS PF ≥ 1.40")
    print(f"  IS: 2024  |  OOS: 2025-2026")
    print(f"{'='*90}")

    print("  Loading NQ 1-min data...", end=" ", flush=True)
    bars_by_day = load_bars(NQ_DATA)
    print(f"{sum(len(v) for v in bars_by_day.values()):,} bars")

    # ── Baseline ──────────────────────────────────────────────────────────────
    is_v4  = run_period(bars_by_day, IS_YEARS,  **V4_BASE_PARAMS)
    oos_v4 = run_period(bars_by_day, OOS_YEARS, **V4_BASE_PARAMS)
    s_is   = stats(is_v4)
    s_oos  = stats(oos_v4)

    print(f"\n  BASELINE v4")
    print(f"  IS  2024:      N={s_is['n']}  WR={s_is['wr']:.0%}  PF={s_is['pf']:.3f}  "
          f"Net=${s_is['net']:>+9,.0f}  Avg=${s_is['avg']:>+5}")
    print(f"  OOS 2025-26:   N={s_oos['n']}  WR={s_oos['wr']:.0%}  PF={s_oos['pf']:.3f}  "
          f"Net=${s_oos['net']:>+9,.0f}  Avg=${s_oos['avg']:>+5}")

    # ── D1: MaxTrades per day ─────────────────────────────────────────────────
    section("D1: MaxTrades per Day (allow second trade after first closes)")
    row("v4 baseline (max=1) IS",  s_is,  s_is)
    row("v4 baseline (max=1) OOS", s_oos, s_oos)
    print()
    for mt in [2, 3]:
        kw = {**V4_BASE_PARAMS, "max_trades": mt}
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  **kw))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, **kw))
        row(f"  max={mt}  IS",  r_is,  s_is)
        row(f"  max={mt}  OOS", r_oos, s_oos)
        print()

    # ── D2: Window end ────────────────────────────────────────────────────────
    section("D2: Extend Window End (hold and enter later in the day)")
    row("v4 baseline (end=13:00) IS",  s_is,  s_is)
    row("v4 baseline (end=13:00) OOS", s_oos, s_oos)
    print()
    for end_h, end_m in [(13, 30), (14, 0), (15, 0)]:
        kw = {**V4_BASE_PARAMS, "window_end": time(end_h, end_m)}
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  **kw))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, **kw))
        lbl   = f"{end_h:02d}:{end_m:02d}"
        row(f"  end={lbl}  IS",  r_is,  s_is)
        row(f"  end={lbl}  OOS", r_oos, s_oos)
        print()

    # ── D3: MIN_EXTEND threshold ──────────────────────────────────────────────
    section("D3: MIN_EXTEND — lower bar means more setups qualify")
    row("v4 baseline (extend=25) IS",  s_is,  s_is)
    row("v4 baseline (extend=25) OOS", s_oos, s_oos)
    print()
    for ext in [10, 15, 20, 25, 30]:
        kw = {**V4_BASE_PARAMS, "min_extend": ext}
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  **kw))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, **kw))
        row(f"  extend={ext:2d}pt  IS",  r_is,  s_is)
        row(f"  extend={ext:2d}pt  OOS", r_oos, s_oos)
        print()

    # ── D4: Trend-aligned off ─────────────────────────────────────────────────
    section("D4: TrendAligned — allow counter-trend reclaims")
    row("v4 baseline (aligned=True) IS",  s_is,  s_is)
    row("v4 baseline (aligned=True) OOS", s_oos, s_oos)
    print()
    kw = {**V4_BASE_PARAMS, "trend_aligned": False}
    r_is  = stats(run_period(bars_by_day, IS_YEARS,  **kw))
    r_oos = stats(run_period(bars_by_day, OOS_YEARS, **kw))
    row("  TrendAligned=False  IS",  r_is,  s_is)
    row("  TrendAligned=False  OOS", r_oos, s_oos)

    # Also test: long-only or short-only
    print()
    for dir_label, aligned_months, dir_filter in [
        ("long-only", True, "long"),
        ("short-only", True, "short"),
    ]:
        # We need to filter trades post-hoc since direction is determined at runtime
        # Use trend_aligned=True but filter result by direction
        all_is  = run_period(bars_by_day, IS_YEARS,  **V4_BASE_PARAMS)
        all_oos = run_period(bars_by_day, OOS_YEARS, **V4_BASE_PARAMS)
        r_is  = stats([t for t in all_is  if t["dir"] == dir_filter])
        r_oos = stats([t for t in all_oos if t["dir"] == dir_filter])
        row(f"  {dir_label}  IS",  r_is,  s_is)
        row(f"  {dir_label}  OOS", r_oos, s_oos)
    print()

    # ── D5: Weak month relaxation ─────────────────────────────────────────────
    section("D5: Add Back Individual Weak Months")
    row("v4 baseline (skip {4,5,6,9,12}) IS",  s_is,  s_is)
    row("v4 baseline (skip {4,5,6,9,12}) OOS", s_oos, s_oos)
    print()
    month_names = {4:"Apr",5:"May",6:"Jun",9:"Sep",12:"Dec"}
    for m, name in month_names.items():
        new_weak = WEAK_BASE - {m}
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  weak_months=new_weak, **V4_BASE_PARAMS))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, weak_months=new_weak, **V4_BASE_PARAMS))
        row(f"  add back {name}  IS",  r_is,  s_is)
        row(f"  add back {name}  OOS", r_oos, s_oos)
        # Show the month-only trades for context
        all_is2  = run_period(bars_by_day, IS_YEARS,  weak_months=new_weak, **V4_BASE_PARAMS)
        all_oos2 = run_period(bars_by_day, OOS_YEARS, weak_months=new_weak, **V4_BASE_PARAMS)
        m_is  = [t for t in all_is2  if t["month"] == m]
        m_oos = [t for t in all_oos2 if t["month"] == m]
        s_mi  = stats(m_is);  s_mo = stats(m_oos)
        print(f"    → {name} IS alone:   N={s_mi['n']}  PF={s_mi['pf']:.3f}  "
              f"Net=${s_mi['net']:>+7,.0f}  Avg=${s_mi['avg']:>+5}")
        print(f"    → {name} OOS alone:  N={s_mo['n']}  PF={s_mo['pf']:.3f}  "
              f"Net=${s_mo['net']:>+7,.0f}  Avg=${s_mo['avg']:>+5}")
        print()

    # ── D6: Combo sweeps ──────────────────────────────────────────────────────
    section("D6: Combo — Stack Best Individual Wins")

    # Define combos to test (label, overrides)
    combos = [
        ("baseline", {}),
        ("max=2", {"max_trades": 2}),
        ("end=14:00", {"window_end": time(14, 0)}),
        ("end=15:00", {"window_end": time(15, 0)}),
        ("extend=20", {"min_extend": 20}),
        ("max=2+end=14:00", {"max_trades": 2, "window_end": time(14, 0)}),
        ("max=2+end=15:00", {"max_trades": 2, "window_end": time(15, 0)}),
        ("max=2+extend=20", {"max_trades": 2, "min_extend": 20}),
        ("max=2+end=14:00+extend=20", {"max_trades": 2, "window_end": time(14, 0), "min_extend": 20}),
        ("max=2+end=14:00+extend=15", {"max_trades": 2, "window_end": time(14, 0), "min_extend": 15}),
        ("max=2+end=15:00+extend=20", {"max_trades": 2, "window_end": time(15, 0), "min_extend": 20}),
    ]

    for label, overrides in combos:
        kw = {**V4_BASE_PARAMS, **overrides}
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  **kw))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, **kw))
        row(f"  {label}  IS",  r_is,  s_is)
        row(f"  {label}  OOS", r_oos, s_oos)
        print()

    # ── D7: Best combo with weak month relaxation ─────────────────────────────
    section("D7: Best Combo + Weak Month Adjustments")
    best_kw = {**V4_BASE_PARAMS, "max_trades": 2, "window_end": time(14, 0)}

    # Try adding back Sep (9) and Dec (12) which historically have some edge
    for weak_label, new_weak in [
        ("skip {4,5,6,9,12} (v4)",       WEAK_BASE),
        ("skip {4,5,6,12} (+Sep back)",   WEAK_BASE - {9}),
        ("skip {4,5,6,9} (+Dec back)",    WEAK_BASE - {12}),
        ("skip {4,5,6} (+Sep+Dec back)",  WEAK_BASE - {9, 12}),
        ("skip {4,6,9,12} (+May back)",   WEAK_BASE - {5}),
        ("skip {4,6,9,12} (+May+Dec)",    WEAK_BASE - {5, 12}),
    ]:
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  weak_months=new_weak, **best_kw))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, weak_months=new_weak, **best_kw))
        row(f"  {weak_label}  IS",  r_is,  s_is)
        row(f"  {weak_label}  OOS", r_oos, s_oos)
        print()

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"  VERDICT  (target: OOS PF ≥ 1.40, N↑, Net↑ vs baseline N={s_oos['n']}, Net=${s_oos['net']:,})")
    print(f"{'='*90}\n")
