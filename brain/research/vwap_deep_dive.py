"""
brain/research/vwap_deep_dive.py

Targeted deep-dive on CruzCapitalVWAP v3 — improve OOS PF from 1.17.

v3 Baseline params:
  stop=20pt, RR=2.5, extend=25pt, window=13:00, max_trades=1, trend_aligned=True
  skip Mon + weak months {Apr=4, May=5, Jun=6, Sep=9, Dec=12}
  IS 2024: PF 1.60  |  OOS 2025-2026: PF 1.17

Hypotheses:
  H0: Baseline (confirm we match v3)
  H1: Skip Fridays — same issue as NQ ORB?
  H2: Entry time-of-day — are 10-11 AM crosses better quality?
  H3: Extension-at-cross — prev bar still far from VWAP (not stale signal)?
  H4: Cross strength — close must be X pt past VWAP (genuine reclaim)?
  H5: Monthly breakdown — is current weak-month set correct?
  H6: Combo — stack best individual filters

IS: 2024  |  OOS: 2025-2026
"""

import csv, os
from datetime import datetime, time, date
from collections import defaultdict

NQ_POINT   = 20.0
COMMISSION = 4.50
SLIP       = 5.0  # 1 tick NQ
COST       = COMMISSION + SLIP * 2  # $14.50 round-trip

BASE    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NQ_DATA = os.path.join(BASE, "data", "nq_1min.csv")

IS_YEARS   = {2024}
OOS_YEARS  = {2025, 2026}
WEAK_MON   = {4, 5, 6, 9, 12}   # v3 C# set (Apr/May added from deep-dive)


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

def run_day(bars, stop_pt=20, rr=2.5, min_extend=25, window_end=time(13, 0),
            max_trades=1, trend_aligned=True, min_prev_dist=0, min_cross_dist=0):
    """
    Simulate one trading day of VWAP reclaim.
    Returns list of trade dicts: {pnl_usd, entry_t, dir}

    min_prev_dist: at time of VWAP cross, previous bar must be ≥ X pt from VWAP.
                   0 = disabled (same as v3 — only checks 'was ever extended').
    min_cross_dist: close must be ≥ X pt past VWAP after crossing (genuine reclaim).
                    0 = disabled.
    """
    T_930   = time(9,  30)
    T_1030  = time(10, 30)
    T_ENTRY = time(10, 0)
    T_1555  = time(15, 55)

    sum_pv = sum_vol = 0.0
    vwap     = None
    open_930 = am_trend = None

    was_extended = False
    prev_above   = None
    prev_dist    = 0.0  # abs(prev_bar_close - vwap_at_that_bar)

    trades_today = 0
    in_pos       = False
    pos_long     = None
    entry_px = sl = tp = entry_t = None
    trades = []

    for b in bars:
        t = b["t"]
        if t >= T_1555:
            break

        # Capture 9:30 open
        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]

        # Session VWAP from 9:30
        if t >= T_930:
            tp_price = (b["h"] + b["l"] + b["c"]) / 3.0
            sum_pv  += tp_price * b["v"]
            sum_vol += b["v"]
            if sum_vol > 0:
                vwap = sum_pv / sum_vol

        # Lock AM trend at 10:30: close vs 9:30 open
        if am_trend is None and t >= T_1030 and open_930 and vwap:
            am_trend = "bull" if b["c"] > open_930 else "bear"

        if vwap is None or t < T_ENTRY:
            if vwap:
                prev_above = b["c"] > vwap
                prev_dist  = abs(b["c"] - vwap)
            continue

        close      = b["c"]
        curr_dist  = abs(close - vwap)
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
                        prev_above = curr_above
                        prev_dist  = curr_dist
                        continue
            else:
                if   b["h"] >= sl:  pnl_pts = entry_px - sl
                elif b["l"] <= tp:  pnl_pts = entry_px - tp
                else:
                    if t >= window_end:
                        pnl_pts = entry_px - close
                    else:
                        prev_above = curr_above
                        prev_dist  = curr_dist
                        continue
            trades.append({
                "pnl_usd": round(pnl_pts * NQ_POINT - COST, 2),
                "entry_t": entry_t,
                "dir":     "long" if pos_long else "short",
            })
            in_pos = False
            prev_above = curr_above
            prev_dist  = curr_dist
            continue

        # Window / max-trades gate
        if trades_today >= max_trades or t >= window_end:
            prev_above = curr_above
            prev_dist  = curr_dist
            continue

        # Track extension from VWAP (only need to happen once)
        if not was_extended and curr_dist > min_extend:
            was_extended = True

        # Check for VWAP cross
        if was_extended and prev_above is not None:
            crossed_up   = (not prev_above) and curr_above
            crossed_down =  prev_above and (not curr_above)

            # H3: Extension-at-cross — previous bar must still be far from VWAP
            if min_prev_dist > 0 and prev_dist < min_prev_dist:
                crossed_up = crossed_down = False

            # H4: Cross strength — close must be meaningfully past VWAP
            if crossed_up   and min_cross_dist > 0 and (close - vwap) < min_cross_dist:
                crossed_up = False
            if crossed_down and min_cross_dist > 0 and (vwap - close) < min_cross_dist:
                crossed_down = False

            # Trend-alignment gate
            go_long  = crossed_up   and (not trend_aligned or am_trend == "bull")
            go_short = crossed_down and (not trend_aligned or am_trend == "bear")

            if go_long:
                entry_px = close
                sl, tp   = close - stop_pt, close + stop_pt * rr
                pos_long = True
                in_pos = True; trades_today += 1; was_extended = False
                entry_t = t
            elif go_short:
                entry_px = close
                sl, tp   = close + stop_pt, close - stop_pt * rr
                pos_long = False
                in_pos = True; trades_today += 1; was_extended = False
                entry_t = t

        prev_above = curr_above
        prev_dist  = curr_dist

    # Force-close any open position at session end
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

def run_period(bars_by_day, years, skip_fri=False, **day_kw):
    """Run all days in `years`, applying day-level filters before simulation."""
    results = []
    for d, bars in sorted(bars_by_day.items()):
        if d.year not in years or not bars:
            continue
        dow   = bars[0]["dow"]
        month = bars[0]["month"]
        if dow == 0:                       # always skip Monday (v3)
            continue
        if skip_fri and dow == 4:
            continue
        if month in WEAK_MON:              # skip v3 weak months
            continue
        for t in run_day(bars, **day_kw):
            results.append({**t, "date": d, "dow": dow, "month": month})
    return results


# ── Stats / display helpers ───────────────────────────────────────────────────

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


W = 34

def row(label, s, base_pf):
    dpf  = s["pf"] - base_pf
    flag = ("  ← BETTER" if dpf > 0.10 else
            "  ← WORSE"  if dpf < -0.10 else "")
    print(f"  {label:<{W}}  N={s['n']:>3}  WR={s['wr']:.0%}  PF={s['pf']:.3f}  "
          f"Net=${s['net']:>+9,.0f}  ΔPF={dpf:+.3f}{flag}")


def section(title):
    print(f"\n  {'─'*76}")
    print(f"  {title}")
    print(f"  {'─'*76}")
    print(f"  {'Label':<{W}}  {'N':>3}  {'WR':>4}  {'PF':>6}  {'Net $':>10}  {'ΔPF':>6}")
    print(f"  {'─'*76}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'='*78}")
    print(f"  CruzCapital VWAP Deep Dive  —  targeting OOS PF improvement from 1.17")
    print(f"  IS: 2024  |  OOS: 2025-2026")
    print(f"{'='*78}")

    print("  Loading NQ 1-min data...", end=" ", flush=True)
    bars_by_day = load_bars(NQ_DATA)
    n_days = sum(1 for d, bars in bars_by_day.items() if bars)
    print(f"{sum(len(v) for v in bars_by_day.values()):,} bars | {n_days} trading days")

    # ── Baseline parameters (v3 C#) ───────────────────────────────────────────
    BASE_DAY = dict(stop_pt=20, rr=2.5, min_extend=25, window_end=time(13, 0),
                    max_trades=1, trend_aligned=True, min_prev_dist=0, min_cross_dist=0)

    is_base  = run_period(bars_by_day, IS_YEARS,  skip_fri=False, **BASE_DAY)
    oos_base = run_period(bars_by_day, OOS_YEARS, skip_fri=False, **BASE_DAY)
    all_base = is_base + oos_base

    s_is  = stats(is_base)
    s_oos = stats(oos_base)
    s_all = stats(all_base)
    base_oos_pf = s_oos["pf"]

    section("H0: BASELINE (reproducing v3)")
    row("IS  2024",        s_is,  s_oos["pf"])
    row("OOS 2025-2026",   s_oos, s_oos["pf"])
    row("Combined",        s_all, s_oos["pf"])

    # ── H1: Friday skip ──────────────────────────────────────────────────────
    is_fri_skip  = run_period(bars_by_day, IS_YEARS,  skip_fri=True, **BASE_DAY)
    oos_fri_skip = run_period(bars_by_day, OOS_YEARS, skip_fri=True, **BASE_DAY)

    # Isolate Friday trades from baseline
    fri_is  = [t for t in is_base  if t["dow"] == 4]
    fri_oos = [t for t in oos_base if t["dow"] == 4]

    section("H1: Skip Fridays")
    row("IS  baseline (Fri included)", s_is,              base_oos_pf)
    row("IS  skip Fri",                stats(is_fri_skip), base_oos_pf)
    row("IS  Fri trades only",         stats(fri_is),      base_oos_pf)
    print()
    row("OOS baseline (Fri included)", s_oos,              base_oos_pf)
    row("OOS skip Fri",                stats(oos_fri_skip), base_oos_pf)
    row("OOS Fri trades only",         stats(fri_oos),      base_oos_pf)

    # ── H2: Entry time-of-day ─────────────────────────────────────────────────
    section("H2: Entry Time-of-Day Breakdown (IS+OOS combined)")
    row("Combined baseline", s_all, base_oos_pf)
    print()
    for sh, eh in [(10, 11), (11, 12), (12, 13)]:
        bucket = [t for t in all_base if t["entry_t"] and
                  time(sh, 0) <= t["entry_t"] < time(eh, 0)]
        row(f"  {sh:02d}:00-{eh:02d}:00 only", stats(bucket), base_oos_pf)

    # Same breakdown but OOS only
    print()
    print(f"  OOS breakdown:")
    for sh, eh in [(10, 11), (11, 12), (12, 13)]:
        bucket = [t for t in oos_base if t["entry_t"] and
                  time(sh, 0) <= t["entry_t"] < time(eh, 0)]
        row(f"  OOS {sh:02d}:00-{eh:02d}:00", stats(bucket), base_oos_pf)

    # ── H3: Extension-at-cross ────────────────────────────────────────────────
    section("H3: Extension-at-Cross — prev bar ≥ X pt from VWAP at entry")
    print(f"  (0=baseline: only checks 'was ever extended by 25pt')")
    print()
    for prev_d in [0, 10, 15, 20, 25]:
        kw = {**BASE_DAY, "min_prev_dist": prev_d}
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  skip_fri=False, **kw))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, skip_fri=False, **kw))
        row(f"  prev_dist≥{prev_d:2d}pt  IS",  r_is,  base_oos_pf)
        row(f"  prev_dist≥{prev_d:2d}pt  OOS", r_oos, base_oos_pf)
        print()

    # ── H4: Cross-strength filter ─────────────────────────────────────────────
    section("H4: Cross Strength — close ≥ X pt past VWAP after cross")
    print(f"  (0=baseline: any tick past VWAP counts)")
    print()
    for cd in [0, 3, 6, 10]:
        kw = {**BASE_DAY, "min_cross_dist": cd}
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  skip_fri=False, **kw))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, skip_fri=False, **kw))
        row(f"  cross≥{cd:2d}pt  IS",  r_is,  base_oos_pf)
        row(f"  cross≥{cd:2d}pt  OOS", r_oos, base_oos_pf)
        print()

    # ── H5: Monthly breakdown ─────────────────────────────────────────────────
    section("H5: Monthly Performance (IS+OOS combined)")
    months = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
              7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    for m in range(1, 13):
        bucket_is  = [t for t in is_base  if t["month"] == m]
        bucket_oos = [t for t in oos_base if t["month"] == m]
        note = " [WEAK-skipped]" if m in WEAK_MON else ""
        if not bucket_is and not bucket_oos:
            continue
        row(f"  {months[m]} IS{note}",  stats(bucket_is),  base_oos_pf)
        row(f"  {months[m]} OOS{note}", stats(bucket_oos), base_oos_pf)
        if m < 12:
            print()

    # ── H6: Combo ─────────────────────────────────────────────────────────────
    section("H6: Combo — Stack Best Individual Filters")
    combos = [
        (0,   0,  False, "baseline"),
        (0,   0,  True,  "skip_fri"),
        (10,  0,  False, "prev≥10"),
        (0,   5,  False, "cross≥5"),
        (10,  5,  False, "prev≥10+cross≥5"),
        (15,  5,  False, "prev≥15+cross≥5"),
        (10,  5,  True,  "prev≥10+cross≥5+skip_fri"),
        (15,  5,  True,  "prev≥15+cross≥5+skip_fri"),
    ]
    for prev_d, cd, skip_fri_flag, label in combos:
        kw = {**BASE_DAY, "min_prev_dist": prev_d, "min_cross_dist": cd}
        r_is  = stats(run_period(bars_by_day, IS_YEARS,  skip_fri=skip_fri_flag, **kw))
        r_oos = stats(run_period(bars_by_day, OOS_YEARS, skip_fri=skip_fri_flag, **kw))
        row(f"  {label}  IS",  r_is,  base_oos_pf)
        row(f"  {label}  OOS", r_oos, base_oos_pf)
        print()

    # ── Verdict ───────────────────────────────────────────────────────────────
    print(f"\n{'='*78}")
    print(f"  VERDICT")
    print(f"{'='*78}")
    print(f"  Baseline OOS PF: {base_oos_pf:.3f}  (N={s_oos['n']}, Net=${s_oos['net']:+,.0f})")
    print(f"  Target:  OOS PF ≥ 1.30 with N ≥ 20")
    print(f"  (See BETTER/WORSE annotations above)")
    print()
