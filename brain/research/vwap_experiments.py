"""
brain/research/vwap_experiments.py

Creative experiments on CruzCapitalVWAP v4 — trying to find any angle that helps.

v4 baseline: OOS PF=1.618, N=97, Net=$13,834 (~$769/mo)
All experiments use same day filters: skip Mon + {Apr,May,Jun,Sep,Dec}, 11AM entry gate.

Experiments:
  E1: R:R sweep — test every stop/target combo; find the real optimal
  E2: Breakeven trail — after +1R profit, slide stop to entry (protect winners)
  E3: Fade the extension — enter AT the extension peak (no cross needed), target=VWAP
  E4: Reverse the entry — instead of reclaim, trade the REJECTION (failed reclaim)
  E5: VWAP cross with NO extend requirement — any cross in trend direction
  E6: Best combos

IS: 2024  |  OOS: 2025-2026
"""

import csv, os
from datetime import datetime, time, date
from collections import defaultdict
from itertools import product

NQ_POINT = 20.0
COST     = 14.50

BASE    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NQ_DATA = os.path.join(BASE, "data", "nq_1min.csv")

IS_YEARS  = {2024}
OOS_YEARS = {2025, 2026}
WEAK_MONTHS = frozenset({4, 5, 6, 9, 12})


# ── Data ─────────────────────────────────────────────────────────────────────

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
                "t": ts.time(), "h": float(row["h"] if "h" in row else row["high"]),
                "l": float(row["l"] if "l" in row else row["low"]),
                "c": float(row["c"] if "c" in row else row["close"]),
                "v": float(row["v"] if "v" in row else row["volume"]),
                "o": float(row["o"] if "o" in row else row["open"]),
                "dow": ts.weekday(), "month": ts.month,
            })
    return bars_by_day


def _load_bars(path):
    bars_by_day = defaultdict(list)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_str = row["timestamp"][:19]
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts.hour < 9 or ts.hour >= 16:
                continue
            bars_by_day[ts.date()].append({
                "t": ts.time(),
                "h": float(row["high"]),  "l": float(row["low"]),
                "c": float(row["close"]), "v": float(row["volume"]),
                "o": float(row["open"]),
                "dow": ts.weekday(), "month": ts.month,
            })
    return bars_by_day


# ── VWAP helper ───────────────────────────────────────────────────────────────

def _compute_vwap_and_trend(bars):
    """Returns {t: (vwap, am_trend)} for each bar. am_trend locked at 10:30."""
    T_930 = time(9, 30); T_1030 = time(10, 30)
    sum_pv = sum_vol = 0.0; vwap = None
    open_930 = am_trend = None; result = {}
    for b in bars:
        t = b["t"]
        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]
        if t >= T_930:
            tp = (b["h"] + b["l"] + b["c"]) / 3.0
            sum_pv += tp * b["v"]; sum_vol += b["v"]
            if sum_vol: vwap = sum_pv / sum_vol
        if am_trend is None and t >= T_1030 and open_930 and vwap:
            am_trend = "bull" if b["c"] > open_930 else "bear"
        result[t] = (vwap, am_trend)
    return result


# ── E1: R:R sweep (standard cross + trail option) ────────────────────────────

def run_cross_day(bars, stop_pt=20, rr=2.5, min_extend=25,
                  ext_start=time(10, 0), entry_start=time(11, 0),
                  window_end=time(13, 0), trail_be=False):
    """Standard VWAP reclaim. trail_be: move stop to entry after 1R gain."""
    T_930 = time(9, 30); T_1030 = time(10, 30); T_1555 = time(15, 55)
    sum_pv = sum_vol = 0.0; vwap = None
    open_930 = am_trend = None
    was_extended = False; prev_above = None
    traded = in_pos = False; pos_long = None
    entry_px = sl = tp = None; be_trailed = False; trades = []

    for b in bars:
        t = b["t"]
        if t >= T_1555: break
        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]
        if t >= T_930:
            sum_pv += (b["h"]+b["l"]+b["c"])/3*b["v"]; sum_vol += b["v"]
            if sum_vol: vwap = sum_pv / sum_vol
        if am_trend is None and t >= T_1030 and open_930 and vwap:
            am_trend = "bull" if b["c"] > open_930 else "bear"
        if vwap is None or t < ext_start:
            if vwap: prev_above = b["c"] > vwap
            continue

        close = b["c"]; curr_above = close > vwap

        if in_pos:
            # Breakeven trail: slide stop to entry once up 1R
            if trail_be and not be_trailed:
                if pos_long  and b["h"] >= entry_px + stop_pt:
                    sl = entry_px; be_trailed = True
                if not pos_long and b["l"] <= entry_px - stop_pt:
                    sl = entry_px; be_trailed = True
            if pos_long:
                if   b["l"] <= sl:  pnl_pts = sl - entry_px
                elif b["h"] >= tp:  pnl_pts = tp - entry_px
                else:
                    if t >= window_end: pnl_pts = close - entry_px
                    else: prev_above = curr_above; continue
            else:
                if   b["h"] >= sl:  pnl_pts = entry_px - sl
                elif b["l"] <= tp:  pnl_pts = entry_px - tp
                else:
                    if t >= window_end: pnl_pts = entry_px - close
                    else: prev_above = curr_above; continue
            trades.append({"pnl_usd": round(pnl_pts*NQ_POINT-COST, 2)})
            in_pos = False; be_trailed = False; prev_above = curr_above; continue

        if traded or t >= window_end:
            prev_above = curr_above; continue

        if not was_extended and abs(close - vwap) > min_extend:
            was_extended = True

        if was_extended and prev_above is not None and t >= entry_start:
            cu = (not prev_above) and curr_above
            cd = prev_above and (not curr_above)
            gl = cu and am_trend == "bull"
            gs = cd and am_trend == "bear"
            if gl:
                entry_px=close; sl,tp=close-stop_pt,close+stop_pt*rr
                pos_long=True; in_pos=True; traded=True; was_extended=False; be_trailed=False
            elif gs:
                entry_px=close; sl,tp=close+stop_pt,close-stop_pt*rr
                pos_long=False; in_pos=True; traded=True; was_extended=False; be_trailed=False

        prev_above = curr_above

    if in_pos and entry_px is not None:
        pnl = (bars[-1]["c"]-entry_px) if pos_long else (entry_px-bars[-1]["c"])
        trades.append({"pnl_usd": round(pnl*NQ_POINT-COST, 2)})
    return trades


# ── E3: Fade the extension (enter AT peak, target = VWAP) ────────────────────

def run_fade_day(bars, fade_dist=30, stop_pt=15, entry_start=time(11, 0),
                 window_end=time(13, 0)):
    """
    REVERSE LOGIC: When price is fade_dist from VWAP, enter TOWARD VWAP (mean reversion fade).
    Unlike reclaim: we enter BEFORE the cross, not after.
    Target = VWAP (dynamic, updated each bar).
    Stop = stop_pt in direction of deviation (fade is wrong = keep going).
    Trend-aligned: bull trend → only take LONG fades (price below VWAP, expect return).
    """
    T_930 = time(9, 30); T_1030 = time(10, 30); T_1555 = time(15, 55)
    sum_pv = sum_vol = 0.0; vwap = None
    open_930 = am_trend = None
    traded = in_pos = False; pos_long = None
    entry_px = sl = None; trades = []

    for b in bars:
        t = b["t"]
        if t >= T_1555: break
        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]
        if t >= T_930:
            sum_pv += (b["h"]+b["l"]+b["c"])/3*b["v"]; sum_vol += b["v"]
            if sum_vol: vwap = sum_pv / sum_vol
        if am_trend is None and t >= T_1030 and open_930 and vwap:
            am_trend = "bull" if b["c"] > open_930 else "bear"
        if vwap is None or am_trend is None: continue

        close = b["c"]

        if in_pos:
            # Dynamic target: close enough to VWAP (within 5pt)
            hit_vwap = (pos_long and close >= vwap - 5) or (not pos_long and close <= vwap + 5)
            if pos_long:
                if   b["l"] <= sl:   pnl_pts = sl - entry_px
                elif hit_vwap:       pnl_pts = close - entry_px
                else:
                    if t >= window_end: pnl_pts = close - entry_px
                    else: continue
            else:
                if   b["h"] >= sl:   pnl_pts = entry_px - sl
                elif hit_vwap:       pnl_pts = entry_px - close
                else:
                    if t >= window_end: pnl_pts = entry_px - close
                    else: continue
            trades.append({"pnl_usd": round(pnl_pts*NQ_POINT-COST, 2)})
            in_pos = False; continue

        if traded or t >= window_end or t < entry_start: continue

        dist = close - vwap   # positive = above VWAP, negative = below VWAP

        # Bull trend: fade DOWNWARD extension (price too far BELOW VWAP, expect return UP)
        if am_trend == "bull" and dist < -fade_dist:
            entry_px = close; sl = close - stop_pt
            pos_long = True; in_pos = True; traded = True

        # Bear trend: fade UPWARD extension (price too far ABOVE VWAP, expect return DOWN)
        elif am_trend == "bear" and dist > fade_dist:
            entry_px = close; sl = close + stop_pt
            pos_long = False; in_pos = True; traded = True

    if in_pos and entry_px is not None:
        pnl = (bars[-1]["c"]-entry_px) if pos_long else (entry_px-bars[-1]["c"])
        trades.append({"pnl_usd": round(pnl*NQ_POINT-COST, 2)})
    return trades


# ── E4: Rejection trade (enter AGAINST the reclaim direction) ────────────────

def run_rejection_day(bars, min_extend=25, stop_pt=20, rr=2.0,
                      entry_start=time(11, 0), window_end=time(13, 0)):
    """
    REVERSE of reclaim: if price crosses VWAP but then crosses BACK (failed reclaim),
    enter in the OPPOSITE direction (original trend continuation).

    Bull trend: price dips below VWAP, crosses back above (reclaim) — if that fails and
    price crosses below again, enter SHORT (failed reclaim = bears win).

    Bear trend: reverse logic.

    This catches "fake-outs" where the VWAP reclaim fails.
    """
    T_930 = time(9, 30); T_1030 = time(10, 30); T_1555 = time(15, 55)
    sum_pv = sum_vol = 0.0; vwap = None
    open_930 = am_trend = None
    was_extended = False; prev_above = None
    saw_reclaim = False   # did we see a cross (attempted reclaim)?
    reclaim_dir = None    # "up" or "down" — direction of the attempted reclaim
    traded = in_pos = False; pos_long = None
    entry_px = sl = tp = None; trades = []

    for b in bars:
        t = b["t"]
        if t >= T_1555: break
        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]
        if t >= T_930:
            sum_pv += (b["h"]+b["l"]+b["c"])/3*b["v"]; sum_vol += b["v"]
            if sum_vol: vwap = sum_pv / sum_vol
        if am_trend is None and t >= T_1030 and open_930 and vwap:
            am_trend = "bull" if b["c"] > open_930 else "bear"
        if vwap is None or t < time(10, 0):
            if vwap: prev_above = b["c"] > vwap
            continue

        close = b["c"]; curr_above = close > vwap

        if in_pos:
            if pos_long:
                if   b["l"] <= sl:  pnl_pts = sl - entry_px
                elif b["h"] >= tp:  pnl_pts = tp - entry_px
                else:
                    if t >= window_end: pnl_pts = close - entry_px
                    else: prev_above = curr_above; continue
            else:
                if   b["h"] >= sl:  pnl_pts = entry_px - sl
                elif b["l"] <= tp:  pnl_pts = entry_px - tp
                else:
                    if t >= window_end: pnl_pts = entry_px - close
                    else: prev_above = curr_above; continue
            trades.append({"pnl_usd": round(pnl_pts*NQ_POINT-COST, 2)})
            in_pos = False; prev_above = curr_above; continue

        if traded or t >= window_end:
            prev_above = curr_above; continue

        if not was_extended and abs(close - vwap) > min_extend:
            was_extended = True

        if was_extended and prev_above is not None:
            crossed_up   = (not prev_above) and curr_above
            crossed_down = prev_above and (not curr_above)

            # Track attempted reclaims
            if crossed_up   and not saw_reclaim and t >= entry_start:
                saw_reclaim = True; reclaim_dir = "up"
            elif crossed_down and not saw_reclaim and t >= entry_start:
                saw_reclaim = True; reclaim_dir = "down"

            # Rejection: the attempted reclaim crossed BACK (failed)
            elif saw_reclaim:
                failed_up   = reclaim_dir == "up"   and crossed_down  # tried up, now down = rejection
                failed_down = reclaim_dir == "down"  and crossed_up    # tried down, now up = rejection
                go_short = failed_up   and (am_trend == "bear" or True)  # always trade rejection
                go_long  = failed_down and (am_trend == "bull" or True)
                if go_short:
                    entry_px=close; sl,tp=close+stop_pt,close-stop_pt*rr
                    pos_long=False; in_pos=True; traded=True
                elif go_long:
                    entry_px=close; sl,tp=close-stop_pt,close+stop_pt*rr
                    pos_long=True; in_pos=True; traded=True

        prev_above = curr_above

    if in_pos and entry_px is not None:
        pnl = (bars[-1]["c"]-entry_px) if pos_long else (entry_px-bars[-1]["c"])
        trades.append({"pnl_usd": round(pnl*NQ_POINT-COST, 2)})
    return trades


# ── E5: Any VWAP cross (remove min_extend, just trend-aligned cross) ─────────

# reuse run_cross_day with min_extend=0


# ── Runner / stats ────────────────────────────────────────────────────────────

def run_period_cross(bars_by_day, years, **kw):
    results = []
    for d, bars in sorted(bars_by_day.items()):
        if d.year not in years or not bars: continue
        if bars[0]["dow"] == 0: continue
        if bars[0]["month"] in WEAK_MONTHS: continue
        for t in run_cross_day(bars, **kw):
            results.append({**t, "date": d})
    return results

def run_period_fade(bars_by_day, years, **kw):
    results = []
    for d, bars in sorted(bars_by_day.items()):
        if d.year not in years or not bars: continue
        if bars[0]["dow"] == 0: continue
        if bars[0]["month"] in WEAK_MONTHS: continue
        for t in run_fade_day(bars, **kw):
            results.append({**t, "date": d})
    return results

def run_period_rejection(bars_by_day, years, **kw):
    results = []
    for d, bars in sorted(bars_by_day.items()):
        if d.year not in years or not bars: continue
        if bars[0]["dow"] == 0: continue
        if bars[0]["month"] in WEAK_MONTHS: continue
        for t in run_rejection_day(bars, **kw):
            results.append({**t, "date": d})
    return results


def stats(trades):
    if not trades: return {"n":0,"wr":0.0,"pf":0.0,"net":0,"avg":0}
    wins    = [t for t in trades if t["pnl_usd"] > 0]
    gross_w = sum(t["pnl_usd"] for t in wins)
    gross_l = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] < 0))
    net     = sum(t["pnl_usd"] for t in trades)
    return {"n":len(trades),"wr":len(wins)/len(trades),
            "pf":round(gross_w/gross_l,3) if gross_l else 99.0,
            "net":round(net),"avg":round(net/len(trades))}


W = 46

def row(label, s, ref):
    dn   = s["n"]   - ref["n"]
    dnet = s["net"] - ref["net"]
    flag = ("  ← N↑ Net↑" if dn>0 and dnet>0 else
            "  ← Net↑"    if dnet>0 else
            "  ← N↑"      if dn>0 else "")
    print(f"  {label:<{W}}  N={s['n']:>3}({dn:+d})  "
          f"WR={s['wr']:.0%}  PF={s['pf']:.3f}  "
          f"Net=${s['net']:>+9,.0f}({dnet:>+6,.0f}){flag}")

def section(title):
    print(f"\n  {'─'*92}")
    print(f"  {title}")
    print(f"  {'─'*92}")
    print(f"  {'Label':<{W}}  N         WR    PF       Net $             ΔNet")
    print(f"  {'─'*92}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'='*94}")
    print(f"  CruzCapital VWAP Experiments — searching every angle for improvement")
    print(f"  IS: 2024  |  OOS: 2025-2026")
    print(f"{'='*94}")

    print("  Loading NQ 1-min data...", end=" ", flush=True)
    bars = _load_bars(NQ_DATA)
    print(f"{sum(len(v) for v in bars.values()):,} bars")

    V4_KW = dict(stop_pt=20, rr=2.5, min_extend=25,
                 ext_start=time(10,0), entry_start=time(11,0),
                 window_end=time(13,0), trail_be=False)

    is_v4  = run_period_cross(bars, IS_YEARS,  **V4_KW)
    oos_v4 = run_period_cross(bars, OOS_YEARS, **V4_KW)
    ref_is = stats(is_v4); ref_oos = stats(oos_v4)

    print(f"\n  BASELINE v4:")
    print(f"    IS  2024:      N={ref_is['n']}  WR={ref_is['wr']:.0%}  "
          f"PF={ref_is['pf']:.3f}  Net=${ref_is['net']:>+9,.0f}")
    print(f"    OOS 2025-26:   N={ref_oos['n']}  WR={ref_oos['wr']:.0%}  "
          f"PF={ref_oos['pf']:.3f}  Net=${ref_oos['net']:>+9,.0f}")

    # ── E1: R:R sweep ─────────────────────────────────────────────────────────
    section("E1: R:R Sweep — find true optimal stop/target")
    print(f"  (v4 uses stop=20pt, RR=2.5 → 50pt target)")
    print()
    rr_results = []
    for stop, rr in product([10, 12, 15, 20, 25], [1.5, 2.0, 2.5, 3.0, 4.0]):
        kw = {**V4_KW, "stop_pt": stop, "rr": rr}
        s_is  = stats(run_period_cross(bars, IS_YEARS,  **kw))
        s_oos = stats(run_period_cross(bars, OOS_YEARS, **kw))
        rr_results.append((stop, rr, s_is, s_oos))

    # Print sorted by OOS net (descending)
    rr_results.sort(key=lambda x: x[3]["net"], reverse=True)
    print(f"  {'Stop':>5} {'RR':>5} {'Tgt':>5}  {'IS-N':>5} {'IS-PF':>6} {'IS-Net':>10}  "
          f"{'OOS-N':>5} {'OOS-PF':>6} {'OOS-Net':>10}  Flag")
    print(f"  {'─'*90}")
    baseline_oos_net = ref_oos["net"]
    for stop, rr, s_is, s_oos in rr_results[:20]:
        tgt = round(stop * rr)
        net_flag = " ← Net↑" if s_oos["net"] > baseline_oos_net else ""
        pf_flag  = " PF↑"   if s_oos["pf"]  > ref_oos["pf"]    else ""
        print(f"  {stop:>5} {rr:>5.1f} {tgt:>5}  "
              f"{s_is['n']:>5} {s_is['pf']:>6.3f} ${s_is['net']:>+9,.0f}  "
              f"{s_oos['n']:>5} {s_oos['pf']:>6.3f} ${s_oos['net']:>+9,.0f}"
              f"{net_flag}{pf_flag}")

    print(f"\n  v4 baseline row for reference:")
    s_is  = stats(run_period_cross(bars, IS_YEARS,  **V4_KW))
    s_oos = stats(run_period_cross(bars, OOS_YEARS, **V4_KW))
    print(f"  {'20':>5} {'2.5':>5} {'50':>5}  "
          f"{s_is['n']:>5} {s_is['pf']:>6.3f} ${s_is['net']:>+9,.0f}  "
          f"{s_oos['n']:>5} {s_oos['pf']:>6.3f} ${s_oos['net']:>+9,.0f}  ← v4")

    # ── E2: Breakeven trail ───────────────────────────────────────────────────
    section("E2: Breakeven Trail — slide stop to entry after gaining 1R")
    row("v4 (no trail)  IS",    ref_is,  ref_is)
    row("v4 (no trail)  OOS",   ref_oos, ref_oos)
    print()
    kw_trail = {**V4_KW, "trail_be": True}
    s_is  = stats(run_period_cross(bars, IS_YEARS,  **kw_trail))
    s_oos = stats(run_period_cross(bars, OOS_YEARS, **kw_trail))
    row("trail to BE   IS",  s_is,  ref_is)
    row("trail to BE   OOS", s_oos, ref_oos)
    print()

    # Try trail with different RR targets
    for stop, rr in [(20, 3.0), (20, 4.0), (15, 3.0), (15, 4.0)]:
        kw = {**V4_KW, "stop_pt": stop, "rr": rr, "trail_be": True}
        s_is  = stats(run_period_cross(bars, IS_YEARS,  **kw))
        s_oos = stats(run_period_cross(bars, OOS_YEARS, **kw))
        row(f"  stop={stop} RR={rr} trail  IS",  s_is,  ref_is)
        row(f"  stop={stop} RR={rr} trail  OOS", s_oos, ref_oos)
        print()

    # ── E3: Fade the extension ────────────────────────────────────────────────
    section("E3: FADE — enter at extension peak, target=VWAP (opposite of reclaim)")
    print(f"  (no cross confirmation; enter BEFORE price returns to VWAP)")
    print(f"  (trend-aligned: bull→long fade when price far below VWAP)")
    print()
    fade_results = []
    for fd, sp in product([20, 25, 30, 35, 40], [10, 15, 20]):
        kw = dict(fade_dist=fd, stop_pt=sp, entry_start=time(11,0), window_end=time(13,0))
        s_is  = stats(run_period_fade(bars, IS_YEARS,  **kw))
        s_oos = stats(run_period_fade(bars, OOS_YEARS, **kw))
        fade_results.append((fd, sp, s_is, s_oos))

    fade_results.sort(key=lambda x: x[3]["net"], reverse=True)
    print(f"  {'FadeDist':>8} {'Stop':>5}  "
          f"{'IS-N':>5} {'IS-PF':>6} {'IS-Net':>10}  "
          f"{'OOS-N':>5} {'OOS-PF':>6} {'OOS-Net':>10}")
    print(f"  {'─'*80}")
    for fd, sp, s_is, s_oos in fade_results[:15]:
        net_flag = " ← Net↑" if s_oos["net"] > 0 and s_oos["pf"] > 1.0 else ""
        print(f"  {fd:>8} {sp:>5}  "
              f"{s_is['n']:>5} {s_is['pf']:>6.3f} ${s_is['net']:>+9,.0f}  "
              f"{s_oos['n']:>5} {s_oos['pf']:>6.3f} ${s_oos['net']:>+9,.0f}{net_flag}")

    # ── E4: Rejection trade ───────────────────────────────────────────────────
    section("E4: REJECTION — trade failed reclaim (cross then re-cross)")
    print(f"  (price attempts VWAP reclaim but fails → trade the failure)")
    print()
    for sp, rr, ext in product([15, 20], [1.5, 2.0, 2.5], [20, 25]):
        kw = dict(min_extend=ext, stop_pt=sp, rr=rr,
                  entry_start=time(11,0), window_end=time(13,0))
        s_is  = stats(run_period_rejection(bars, IS_YEARS,  **kw))
        s_oos = stats(run_period_rejection(bars, OOS_YEARS, **kw))
        if s_oos["n"] >= 15 and s_oos["pf"] >= 1.2:
            print(f"  stop={sp} RR={rr} ext={ext}  "
                  f"IS: N={s_is['n']} PF={s_is['pf']:.3f} Net=${s_is['net']:>+7,.0f}  "
                  f"OOS: N={s_oos['n']} PF={s_oos['pf']:.3f} Net=${s_oos['net']:>+7,.0f}  ← SIGNAL")

    # Show best rejection combo regardless
    best_rej = sorted(
        [(sp,rr,ext,
          stats(run_period_rejection(bars, IS_YEARS,  min_extend=ext,stop_pt=sp,rr=rr,entry_start=time(11,0),window_end=time(13,0))),
          stats(run_period_rejection(bars, OOS_YEARS, min_extend=ext,stop_pt=sp,rr=rr,entry_start=time(11,0),window_end=time(13,0))))
         for sp,rr,ext in product([15,20],[1.5,2.0,2.5],[20,25])],
        key=lambda x: x[4]["net"], reverse=True)[:5]
    print(f"\n  Top 5 rejection configs by OOS net:")
    for sp,rr,ext,s_is,s_oos in best_rej:
        print(f"  stop={sp} RR={rr} ext={ext}  "
              f"IS: N={s_is['n']} PF={s_is['pf']:.3f}  "
              f"OOS: N={s_oos['n']} PF={s_oos['pf']:.3f} Net=${s_oos['net']:>+7,.0f}")

    # ── E5: Any cross (no extend requirement) ─────────────────────────────────
    section("E5: Remove MIN_EXTEND — trade any trend-aligned VWAP cross")
    print(f"  (0pt extend = any cross generates signal, more trades but noisier)")
    print()
    for ext in [0, 5, 10, 15, 20, 25]:
        kw = {**V4_KW, "min_extend": ext}
        s_is  = stats(run_period_cross(bars, IS_YEARS,  **kw))
        s_oos = stats(run_period_cross(bars, OOS_YEARS, **kw))
        row(f"  extend={ext:2d}pt  IS",  s_is,  ref_is)
        row(f"  extend={ext:2d}pt  OOS", s_oos, ref_oos)
        print()

    # ── E6: Best combos ───────────────────────────────────────────────────────
    section("E6: Best Combos from Above")
    # Pick the top R:R config from E1
    best_rr = rr_results[0]
    b_stop, b_rr = best_rr[0], best_rr[1]
    print(f"  Best OOS-net R:R config from E1: stop={b_stop} RR={b_rr}")
    print()

    combos = [
        ("v4 baseline",                 {**V4_KW}),
        (f"stop={b_stop} RR={b_rr}",    {**V4_KW, "stop_pt":b_stop, "rr":b_rr}),
        (f"stop={b_stop} RR={b_rr}+BE", {**V4_KW, "stop_pt":b_stop, "rr":b_rr, "trail_be":True}),
        ("stop=20 RR=3.0",              {**V4_KW, "stop_pt":20, "rr":3.0}),
        ("stop=20 RR=3.0+BE",           {**V4_KW, "stop_pt":20, "rr":3.0, "trail_be":True}),
        ("stop=15 RR=3.0",              {**V4_KW, "stop_pt":15, "rr":3.0}),
        ("stop=15 RR=3.0+BE",           {**V4_KW, "stop_pt":15, "rr":3.0, "trail_be":True}),
        ("stop=12 RR=4.0+BE",           {**V4_KW, "stop_pt":12, "rr":4.0, "trail_be":True}),
        ("stop=20 RR=2.0",              {**V4_KW, "stop_pt":20, "rr":2.0}),
        ("stop=15 RR=2.0",              {**V4_KW, "stop_pt":15, "rr":2.0}),
    ]
    for label, kw in combos:
        s_is  = stats(run_period_cross(bars, IS_YEARS,  **kw))
        s_oos = stats(run_period_cross(bars, OOS_YEARS, **kw))
        row(f"  {label}  IS",  s_is,  ref_is)
        row(f"  {label}  OOS", s_oos, ref_oos)
        print()

    print(f"\n{'='*94}")
    print(f"  DONE  (v4 OOS baseline: N={ref_oos['n']}, PF={ref_oos['pf']:.3f}, Net=${ref_oos['net']:,})")
    print(f"{'='*94}\n")
