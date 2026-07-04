"""
brain/research/multi_probe.py

Three untested probes in one pass (loads each CSV once):

  P1. ASIA stop x RR sweep   — current 15pt/1.5R was never swept.
      Gap 30-80pt at 18:15 vs 17:00 close, skip Thu/Aug/Nov, flatten 21:00.
      Grid: stop {10,12,15,20,25} x RR {1.0,1.5,2.0,2.5,3.0} + gap-bound variants.

  P2. EUROPE OPEN ORB (NQ)   — completely untested session.
      OR 3:00-3:14 AM ET, entry 3:15-4:30, flatten 9:00 AM.
      Grid: stop {10,15,20} x RR {1.5,2.0,2.5} x OR-range {(5,40),(10,50)}.

  P3. ES VWAP RECLAIM PORT   — NQ VWAP v5 logic on ES full data.
      Extension from VWAP -> first reclaim cross after 11 AM, trend-aligned.
      Grid: stop {5,7,9} x extend {6,8,10,12} x RR {2.5,3.0}.

IS: 2022-2024  |  OOS: 2025-Jun 2026
"""

import csv, os
from datetime import datetime, time
from collections import defaultdict
from itertools import product

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NQ_PT, NQ_COST = 20.0, 14.50          # commission 4.50 + 1-tick slip x2
ES_PT, ES_COST = 50.0, 30.00          # commission 5.00 + 2-tick slip x2 (es_config)


def load(path, h_lo, h_hi):
    """Bars bucketed by date, hours [h_lo, h_hi)."""
    days = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            s = row["timestamp"][:19]
            try:
                ts = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if not (h_lo <= ts.hour < h_hi):
                continue
            days[ts.date()].append({
                "t": ts.time(), "o": float(row["open"]), "h": float(row["high"]),
                "l": float(row["low"]), "c": float(row["close"]),
                "v": float(row["volume"]),
            })
    return days


def stats(pnls):
    if not pnls:
        return {"n": 0, "wr": 0, "pf": 0, "net": 0}
    w = [p for p in pnls if p > 0]
    gl = abs(sum(p for p in pnls if p <= 0))
    return {"n": len(pnls), "wr": len(w) / len(pnls),
            "pf": round(sum(w) / gl, 3) if gl else 99.0, "net": round(sum(pnls))}


def split(trades):
    """trades = [(date, pnl)] -> IS/OOS stats"""
    return (stats([p for d, p in trades if d.year <= 2024]),
            stats([p for d, p in trades if d.year >= 2025]))


def line(tag, s_is, s_oos, mark=""):
    print(f"  {tag:<38} IS: N={s_is['n']:>4} WR={s_is['wr']:>4.0%} PF={s_is['pf']:>6.3f} "
          f"${s_is['net']:>+8,.0f}  | OOS: N={s_oos['n']:>4} WR={s_oos['wr']:>4.0%} "
          f"PF={s_oos['pf']:>6.3f} ${s_oos['net']:>+8,.0f}{mark}")


# ══ P1: ASIA sweep ════════════════════════════════════════════════════════════

def asia_day(bars, stop, rr, gap_lo, gap_hi):
    """Last close before 17:00 halt -> 18:15 gap entry -> stop/target/21:00 flatten."""
    cme = None
    entry = sl = tp = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(17, 0):          # session tail: keep updating reference close
            cme = b["c"]
            continue
        if t < time(18, 15):
            continue
        if entry is None:
            if t >= time(18, 16):        # only the 18:15 bar
                return None
            if cme is None:
                return None
            gap = b["c"] - cme
            if not (gap_lo <= abs(gap) <= gap_hi):
                return None
            entry = b["c"]
            is_long = gap > 0
            sl = entry - stop if is_long else entry + stop
            tp = entry + stop * rr if is_long else entry - stop * rr
            continue
        if t >= time(21, 0):
            pts = (b["c"] - entry) if is_long else (entry - b["c"])
            return pts * NQ_PT - NQ_COST
        if is_long:
            if b["l"] <= sl: return (sl - entry) * NQ_PT - NQ_COST
            if b["h"] >= tp: return (tp - entry) * NQ_PT - NQ_COST
        else:
            if b["h"] >= sl: return (entry - sl) * NQ_PT - NQ_COST
            if b["l"] <= tp: return (entry - tp) * NQ_PT - NQ_COST
    if entry is not None:
        last = bars[-1]["c"]
        pts = (last - entry) if is_long else (entry - last)
        return pts * NQ_PT - NQ_COST
    return None


def run_asia(days):
    print(f"\n{'═'*100}")
    print(f"  P1: ASIA GAP stop x RR sweep  (gap at 18:15 vs 17:00 close, skip Thu/Aug/Nov, flat 21:00)")
    print(f"{'═'*100}")
    results = []
    for stop, rr in product([10, 12, 15, 20, 25], [1.0, 1.5, 2.0, 2.5, 3.0]):
        trades = []
        for d in sorted(days):
            if d.weekday() == 3 or d.month in (8, 11):   # Thu / Aug / Nov
                continue
            p = asia_day(days[d], stop, rr, 30, 80)
            if p is not None:
                trades.append((d, p))
        results.append((stop, rr, *split(trades)))
    results.sort(key=lambda x: x[3]["net"], reverse=True)
    for stop, rr, s_is, s_oos in results[:12]:
        mark = "  ← current" if (stop, rr) == (15, 1.5) else ""
        line(f"stop={stop} rr={rr} tgt={stop*rr:.0f}pt", s_is, s_oos, mark)
    cur = [r for r in results if (r[0], r[1]) == (15, 1.5)][0]
    if (15, 1.5) not in [(r[0], r[1]) for r in results[:12]]:
        line("stop=15 rr=1.5 (current)", cur[2], cur[3], "  ← current")

    print(f"\n  Gap-bound variants at current 15pt/1.5R:")
    for glo, ghi in [(30, 80), (25, 90), (40, 80), (30, 100), (20, 80)]:
        trades = []
        for d in sorted(days):
            if d.weekday() == 3 or d.month in (8, 11):
                continue
            p = asia_day(days[d], 15, 1.5, glo, ghi)
            if p is not None:
                trades.append((d, p))
        s_is, s_oos = split(trades)
        mark = "  ← current" if (glo, ghi) == (30, 80) else ""
        line(f"gap {glo}-{ghi}pt", s_is, s_oos, mark)


# ══ P2: EUROPE OPEN ORB ═══════════════════════════════════════════════════════

def europe_day(bars, stop, rr, or_lo, or_hi):
    orh = orl = None
    entry = sl = tp = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(3, 0):
            continue
        if t < time(3, 15):
            orh = b["h"] if orh is None else max(orh, b["h"])
            orl = b["l"] if orl is None else min(orl, b["l"])
            continue
        if orh is None or not (or_lo <= orh - orl <= or_hi):
            return None
        if entry is not None:
            if t >= time(9, 0):
                pts = (b["c"] - entry) if is_long else (entry - b["c"])
                return pts * NQ_PT - NQ_COST
            if is_long:
                if b["l"] <= sl: return (sl - entry) * NQ_PT - NQ_COST
                if b["h"] >= tp: return (tp - entry) * NQ_PT - NQ_COST
            else:
                if b["h"] >= sl: return (entry - sl) * NQ_PT - NQ_COST
                if b["l"] <= tp: return (entry - tp) * NQ_PT - NQ_COST
            continue
        if t > time(4, 30):
            return None
        if b["c"] > orh + 2:
            entry, is_long = b["c"], True
            sl, tp = entry - stop, entry + stop * rr
        elif b["c"] < orl - 2:
            entry, is_long = b["c"], False
            sl, tp = entry + stop, entry - stop * rr
    return None


def run_europe(days):
    print(f"\n{'═'*100}")
    print(f"  P2: EUROPE OPEN ORB (NQ)  — OR 3:00-3:14 ET, entry 3:15-4:30, flatten 9:00")
    print(f"{'═'*100}")
    results = []
    for stop, rr, (olo, ohi) in product([10, 15, 20], [1.5, 2.0, 2.5], [(5, 40), (10, 50)]):
        trades = []
        for d in sorted(days):
            if d.weekday() == 0:  # skip Monday for symmetry with US
                continue
            p = europe_day(days[d], stop, rr, olo, ohi)
            if p is not None:
                trades.append((d, p))
        results.append((stop, rr, olo, ohi, *split(trades)))
    results.sort(key=lambda x: x[5]["net"], reverse=True)
    for stop, rr, olo, ohi, s_is, s_oos in results[:12]:
        line(f"stop={stop} rr={rr} OR {olo}-{ohi}pt", s_is, s_oos)


# ══ P3: ES VWAP RECLAIM ═══════════════════════════════════════════════════════

def es_vwap_day(bars, stop, rr, extend):
    sum_pv = sum_vol = 0.0
    vwap = open930 = trend = None
    was_ext = False
    prev_above = None
    entry = sl = tp = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(9, 30) or t >= time(15, 55):
            continue
        if open930 is None and t < time(9, 31):
            open930 = b["o"]
        sum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        sum_vol += b["v"]
        if sum_vol:
            vwap = sum_pv / sum_vol
        if trend is None and t >= time(10, 30) and open930 and vwap:
            trend = "bull" if b["c"] > open930 else "bear"
        if vwap is None or t < time(10, 0):
            if vwap:
                prev_above = b["c"] > vwap
            continue
        close = b["c"]
        cur_above = close > vwap
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (sl - entry) * ES_PT - ES_COST
                if b["h"] >= tp: return (tp - entry) * ES_PT - ES_COST
            else:
                if b["h"] >= sl: return (entry - sl) * ES_PT - ES_COST
                if b["l"] <= tp: return (entry - tp) * ES_PT - ES_COST
            if t >= time(13, 0):
                pts = (close - entry) if is_long else (entry - close)
                return pts * ES_PT - ES_COST
            prev_above = cur_above
            continue
        if t >= time(13, 0):
            return None
        if not was_ext and abs(close - vwap) > extend:
            was_ext = True
        if was_ext and prev_above is not None and t >= time(11, 0):
            cu = (not prev_above) and cur_above
            cd = prev_above and (not cur_above)
            if cu and trend == "bull":
                entry, is_long = close, True
                sl, tp = close - stop, close + stop * rr
            elif cd and trend == "bear":
                entry, is_long = close, False
                sl, tp = close + stop, close - stop * rr
        prev_above = cur_above
    if entry is not None:
        last = bars[-1]["c"]
        pts = (last - entry) if is_long else (entry - last)
        return pts * ES_PT - ES_COST
    return None


def run_es_vwap(days):
    print(f"\n{'═'*100}")
    print(f"  P3: ES VWAP RECLAIM  — NQ v5 logic ported (11AM gate, trend-aligned, 13:00 window end)")
    print(f"  (skip Mon; NQ weak months {{4,5,6,9,12}} NOT applied — ES has its own seasonality)")
    print(f"{'═'*100}")
    results = []
    for stop, extend, rr in product([5, 7, 9], [6, 8, 10, 12], [2.5, 3.0]):
        trades = []
        for d in sorted(days):
            if d.weekday() == 0:
                continue
            p = es_vwap_day(days[d], stop, rr, extend)
            if p is not None:
                trades.append((d, p))
        results.append((stop, extend, rr, *split(trades)))
    results.sort(key=lambda x: x[4]["net"], reverse=True)
    for stop, extend, rr, s_is, s_oos in results[:14]:
        line(f"stop={stop} ext={extend} rr={rr}", s_is, s_oos)


if __name__ == "__main__":
    import sys
    only = sys.argv[1] if len(sys.argv) > 1 else None

    if only in (None, "asia"):
        print("Loading NQ 16-21h bars (16h tail = pre-halt reference close)...", flush=True)
        nq_eve = load(os.path.join(BASE, "data", "nq_full.csv"), 16, 21)
        run_asia(nq_eve)
        del nq_eve

    if only in (None, "europe"):
        print("\nLoading NQ overnight bars (3-9h)...", flush=True)
        nq_on = load(os.path.join(BASE, "data", "nq_full.csv"), 3, 10)
        run_europe(nq_on)
        del nq_on

    if only in (None, "es"):
        print("\nLoading ES RTH bars...", flush=True)
        es = load(os.path.join(BASE, "data", "es_1min.csv"), 9, 16)
        run_es_vwap(es)

    print(f"\n{'═'*100}\n  multi_probe done.\n{'═'*100}")
