"""
brain/research/meta_pattern_search.py

Systematic application of our VALIDATED meta-patterns to untouched structures.
Meta-patterns that made money: (1) trap/failure continuation, (2) directional
context (gap/trend) + structural trigger, (3) time-slot specificity.
Meta-patterns that lost: naked level breaks, late momentum, counter-trend fades.

  N1. GAP DRIFT   — gap>20pt aligned days: ride 9:31→9:44 (pre-ORB dead time,
                    monetizes our most-validated filter before our own entry)
  N2. PM REVERSE  — PM ORB stop-out → flip opposite (trap pattern on PM range)
  N3. MOC DRIVE   — 15:00 trend day (close vs VWAP + vs open) → ride into 15:50
                    (MOC imbalance flow; the one session never tested)
  N4. SWEEP REJECT— price sweeps prior-day H/L then closes back inside →
                    reverse (trap pattern at DAILY levels; breakout version
                    failed, rejection version untested)
  N5. FAILED GAP  — gap>20 day where morning ORB never triggered by 10:30 →
                    fade toward prior close (gap absorbed = trapped gappers)
  N6. PC REJECT   — rejection pattern anchored on PRIOR CLOSE instead of VWAP
                    (failed gap-fill = continuation)

All raw: no DOW/month filters. 1c, $14.50/trade. IS 2022-24 / OOS 2025-26.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vwap_fulldata import load_days, DATA
from datetime import time
from collections import defaultdict

PT, COST = 20.0, 14.50
IS_Y, OOS_Y = (2022, 2023, 2024), (2025, 2026)
ALL_Y = (2022, 2023, 2024, 2025, 2026)


def stats(rows):
    if not rows:
        return {"n": 0, "wr": 0, "pf": 0, "net": 0, "avg": 0}
    p = [r[1] for r in rows]
    w = [x for x in p if x > 0]
    gl = abs(sum(x for x in p if x <= 0))
    return {"n": len(p), "wr": len(w)/len(p),
            "pf": round(sum(w)/gl, 3) if gl else 99.0,
            "net": round(sum(p)), "avg": round(sum(p)/len(p))}


def report(name, rows):
    print(f"\n{'═'*96}\n  {name}\n{'═'*96}")
    i = stats([r for r in rows if r[0].year in IS_Y])
    o = stats([r for r in rows if r[0].year in OOS_Y])
    print(f"  IS : N={i['n']:>4}  WR={i['wr']:>4.0%}  PF={i['pf']:>6.3f}  Net=${i['net']:>+9,}  avg=${i['avg']:>+5,}")
    print(f"  OOS: N={o['n']:>4}  WR={o['wr']:>4.0%}  PF={o['pf']:>6.3f}  Net=${o['net']:>+9,}  avg=${o['avg']:>+5,}")
    for y in ALL_Y:
        s = stats([r for r in rows if r[0].year == y])
        if s["n"]:
            print(f"    {y}: N={s['n']:>4}  PF={s['pf']:>6.3f}  ${s['net']:>+8,}")
    verdict = ("← CANDIDATE" if i["pf"] >= 1.10 and o["pf"] >= 1.25 and i["n"] + o["n"] >= 60
               else "✗ no edge")
    print(f"  {verdict}")
    return verdict.startswith("←")


def manage(bars, i0, entry, is_long, sl, tp, flatten=time(15, 55)):
    """Walk bars from i0; return pnl at stop/target/flatten-close."""
    for b in bars[i0:]:
        t = b["t"]
        if t >= flatten:
            pts = (b["c"] - entry) if is_long else (entry - b["c"])
            return pts * PT - COST
        if is_long:
            if b["l"] <= sl: return (sl - entry) * PT - COST
            if b["h"] >= tp: return (tp - entry) * PT - COST
        else:
            if b["h"] >= sl: return (entry - sl) * PT - COST
            if b["l"] <= tp: return (entry - tp) * PT - COST
    pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
    return pts * PT - COST


if __name__ == "__main__":
    print("Loading...", flush=True)
    days = load_days(DATA)
    dates = sorted(days)

    prior_close, pdh, pdl = {}, {}, {}
    for i in range(1, len(dates)):
        pb = days[dates[i-1]]
        prior_close[dates[i]] = pb[-1]["c"]
        pdh[dates[i]] = max(b["h"] for b in pb)
        pdl[dates[i]] = min(b["l"] for b in pb)

    # ── N1: GAP DRIFT 9:31→9:44 ───────────────────────────────────────────────
    rows = []
    for d in dates:
        pc = prior_close.get(d)
        if pc is None:
            continue
        bars = [b for b in days[d] if time(9, 30) <= b["t"] < time(9, 45)]
        if len(bars) < 14:
            continue
        gap = bars[0]["o"] - pc
        if abs(gap) < 20:
            continue
        is_long = gap > 0
        entry = bars[0]["c"]                      # first bar close (~9:30)
        exit_ = bars[-1]["c"]                     # 9:44 close
        # 15pt protective stop intrabar
        sl = entry - 15 if is_long else entry + 15
        pnl = None
        for b in bars[1:]:
            if is_long and b["l"] <= sl:  pnl = (sl - entry) * PT - COST; break
            if not is_long and b["h"] >= sl: pnl = (entry - sl) * PT - COST; break
        if pnl is None:
            pts = (exit_ - entry) if is_long else (entry - exit_)
            pnl = pts * PT - COST
        rows.append((d, pnl))
    report("N1: GAP DRIFT 9:31-9:44  (gap>20 aligned, 15pt stop)", rows)

    # ── N2: PM ORB STOP-AND-REVERSE ───────────────────────────────────────────
    rows = []
    for d in dates:
        bars = days[d]
        or_hi = or_lo = None
        entry = sl = tp = None
        is_long = None
        for i, b in enumerate(bars):
            t = b["t"]
            if t < time(13, 0) or t >= time(15, 55):
                continue
            if t < time(13, 15):
                or_hi = b["h"] if or_hi is None else max(or_hi, b["h"])
                or_lo = b["l"] if or_lo is None else min(or_lo, b["l"])
                continue
            if or_hi is None or not (15 <= or_hi - or_lo <= 60):
                break
            if entry is not None:
                stopped = (b["l"] <= sl) if is_long else (b["h"] >= sl)
                target  = (b["h"] >= tp) if is_long else (b["l"] <= tp)
                if stopped:
                    # trap sprung → reverse at this bar close, 22pt stop, 2R
                    r_entry = b["c"]
                    r_long = not is_long
                    r_sl = r_entry - 22 if r_long else r_entry + 22
                    r_tp = r_entry + 44 if r_long else r_entry - 44
                    rows.append((d, manage(bars, i + 1, r_entry, r_long, r_sl, r_tp)))
                    break
                if target:
                    break
                continue
            if t > time(14, 0):
                break
            if b["c"] > or_hi + 2:
                entry, is_long = b["c"], True
                sl, tp = entry - 22, entry + 55
            elif b["c"] < or_lo - 2:
                entry, is_long = b["c"], False
                sl, tp = entry + 22, entry - 55
    report("N2: PM ORB STOP-AND-REVERSE  (22pt stop, 2R, flat 15:55)", rows)

    # ── N3: MOC DRIVE 15:00→15:50 ────────────────────────────────────────────
    for disp_min, tag in [(0, "any"), (40, "disp≥40pt")]:
        rows = []
        for d in dates:
            bars = days[d]
            spv = svol = 0.0
            vwap = o930 = None
            e_idx = None
            for i, b in enumerate(bars):
                t = b["t"]
                if t < time(9, 30) or t >= time(15, 55):
                    continue
                if o930 is None and t < time(9, 31):
                    o930 = b["o"]
                spv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
                svol += b["v"]
                vwap = spv / svol if svol else None
                if t >= time(15, 0):
                    e_idx = i
                    break
            if e_idx is None or vwap is None or o930 is None:
                continue
            b = bars[e_idx]
            c = b["c"]
            disp = c - o930
            if abs(disp) < disp_min:
                continue
            if c > vwap and disp > 0:
                is_long = True
            elif c < vwap and disp < 0:
                is_long = False
            else:
                continue
            sl = c - 20 if is_long else c + 20
            tp = c + 60 if is_long else c - 60          # rarely hit; mostly time exit
            rows.append((d, manage(bars, e_idx + 1, c, is_long, sl, tp, flatten=time(15, 50))))
        report(f"N3: MOC DRIVE 15:00→15:50  ({tag}, 20pt stop)", rows)

    # ── N4: PDH/PDL SWEEP-REJECT ─────────────────────────────────────────────
    rows = []
    for d in dates:
        H, L = pdh.get(d), pdl.get(d)
        if H is None:
            continue
        bars = days[d]
        swept_hi = swept_lo = False
        sweep_hi_px = sweep_lo_px = None
        done_s = done_l = False
        for i, b in enumerate(bars):
            t = b["t"]
            if t < time(10, 0) or t >= time(14, 30):
                continue
            if not swept_hi and b["h"] > H:
                swept_hi = True
                sweep_hi_px = b["h"]
            elif swept_hi and not done_s and b["c"] < H - 2:
                e = b["c"]
                sl = sweep_hi_px + 3
                risk = sl - e
                rows.append((d, manage(bars, i + 1, e, False, sl, e - 2 * risk)))
                done_s = True
            if swept_hi:
                sweep_hi_px = max(sweep_hi_px or 0, b["h"])
            if not swept_lo and b["l"] < L:
                swept_lo = True
                sweep_lo_px = b["l"]
            elif swept_lo and not done_l and b["c"] > L + 2:
                e = b["c"]
                sl = sweep_lo_px - 3
                risk = e - sl
                rows.append((d, manage(bars, i + 1, e, True, sl, e + 2 * risk)))
                done_l = True
            if swept_lo:
                sweep_lo_px = min(sweep_lo_px or 1e9, b["l"])
    report("N4: PDH/PDL SWEEP-REJECT  (close back inside, 2R)", rows)

    # ── N5: FAILED GAP FADE ──────────────────────────────────────────────────
    rows = []
    for d in dates:
        pc = prior_close.get(d)
        if pc is None:
            continue
        bars = days[d]
        or_hi = or_lo = o930 = None
        triggered = False
        e_idx = None
        for i, b in enumerate(bars):
            t = b["t"]
            if t < time(9, 30):
                continue
            if o930 is None and t < time(9, 31):
                o930 = b["o"]
            if t < time(9, 45):
                or_hi = b["h"] if or_hi is None else max(or_hi, b["h"])
                or_lo = b["l"] if or_lo is None else min(or_lo, b["l"])
                continue
            if t <= time(10, 30):
                if b["c"] > or_hi + 4 or b["c"] < or_lo - 4:
                    triggered = True
                    break
                e_idx = i
                continue
            break
        if o930 is None or or_hi is None or triggered or e_idx is None:
            continue
        gap = o930 - pc
        if abs(gap) < 20:
            continue
        b = bars[e_idx]
        is_long = gap < 0                     # fade toward prior close
        e = b["c"]
        sl = e - 25 if is_long else e + 25
        tp = e + 50 if is_long else e - 50
        rows.append((d, manage(bars, e_idx + 1, e, is_long, sl, tp)))
    report("N5: FAILED GAP FADE  (gap>20, no ORB trigger by 10:30, 25pt/2R)", rows)

    # ── N6: PRIOR-CLOSE REJECTION ────────────────────────────────────────────
    rows = []
    for d in dates:
        pc = prior_close.get(d)
        if pc is None:
            continue
        bars = days[d]
        was_ext = False
        saw = False
        cross_up = None
        prev_above = None
        for i, b in enumerate(bars):
            t = b["t"]
            if t < time(9, 30) or t >= time(15, 55):
                continue
            c = b["c"]
            above = c > pc
            if prev_above is None:
                prev_above = above
                continue
            if not was_ext and abs(c - pc) > 25:
                was_ext = True
            if was_ext and time(10, 30) <= t < time(13, 0):
                cu = (not prev_above) and above
                cd = prev_above and (not above)
                if not saw:
                    if cu:   saw, cross_up = True, True
                    elif cd: saw, cross_up = True, False
                else:
                    if (cross_up and cd) or ((not cross_up) and cu):
                        is_long = cu
                        sl = c - 20 if is_long else c + 20
                        tp = c + 60 if is_long else c - 60
                        rows.append((d, manage(bars, i + 1, c, is_long, sl, tp)))
                        break
            prev_above = above
    report("N6: PRIOR-CLOSE REJECTION  (ext 25, cross≥10:30, 20pt/3R)", rows)

    print(f"\n{'═'*96}\n  meta_pattern_search done.\n{'═'*96}")
