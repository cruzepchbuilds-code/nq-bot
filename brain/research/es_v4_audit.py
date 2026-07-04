"""
brain/research/es_v4_audit.py

Forensic audit of CruzCapitalES.cs "v4" (Feb/Mar/Nov seasonal ES ORB,
claimed OOS 2024-26 PF 2.17, $24,645/90 trades).

Suspicion: the month list was adjusted USING OOS results (header admits
"dropped Apr: lost money all 3 OOS years") → OOS contamination.

Tests:
  A. Faithful re-simulation of the exact ruleset, year-by-year 2022-2026
  B. THE DECISIVE TEST — month-selection stability: rank ALL months by PF
     in 2022-23 (the supposed IS) vs 2024-26. If Feb/Mar/Nov weren't the
     IS winners, the selection was fit on OOS = mirage.
  C. Concentration: top-5 trades' share of the claimed profit.

Exact rules replicated: months {2,3,11}, skip Mon, OR 9:30-9:44 range 5-30,
entry 9:45-10:14 close beyond OR±1, score>=60 (time+gap+vol+range+strong),
stop 9pt eff, RR 2.5, 1 trade/day, flatten 15:55. ES $50/pt, $17 RT cost.
"""

import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, time
from collections import defaultdict, deque

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PT, COST = 50.0, 17.0
STOP_EFF, RR = 9.0, 2.5
STRONG = {2, 11}


def load(path):
    days = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            s = row["timestamp"][:19]
            try:
                ts = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if not (9 <= ts.hour < 16):
                continue
            days[ts.date()].append({
                "t": ts.time(), "o": float(row["open"]), "h": float(row["high"]),
                "l": float(row["low"]), "c": float(row["close"]), "v": float(row["volume"]),
            })
    return days


def run_day(bars, prev_close, or_vol_hist, month):
    orh = orl = None
    or_vol = 0.0
    entry = sl = tp = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t >= time(15, 55):
            break
        if t < time(9, 30):
            continue
        if t < time(9, 45):
            orh = b["h"] if orh is None else max(orh, b["h"])
            orl = b["l"] if orl is None else min(orl, b["l"])
            or_vol += b["v"]
            continue
        if orh is None:
            return None, or_vol
        rng = orh - orl
        if not (5 <= rng <= 30):
            return None, or_vol
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (sl - entry) * PT - COST, or_vol
                if b["h"] >= tp: return (tp - entry) * PT - COST, or_vol
            else:
                if b["h"] >= sl: return (entry - sl) * PT - COST, or_vol
                if b["l"] <= tp: return (entry - tp) * PT - COST, or_vol
            continue
        if t >= time(10, 15):
            continue
        close = b["c"]
        go_long = close > orh + 1.0
        go_short = close < orl - 1.0
        if not (go_long or go_short):
            continue
        # score
        score = 0
        if   t < time(9, 50):  score += 20
        elif t < time(10, 0):  score += 15
        else:                  score += 10
        gap = b["o"] - prev_close if prev_close else 0
        if   go_long and gap > 5:    score += 25
        elif go_short and gap < -5:  score += 25
        else:                        score += 15
        if or_vol_hist:
            avg = sum(or_vol_hist) / len(or_vol_hist)
            r = or_vol / avg if avg else 1
            score += 25 if 0.7 <= r <= 1.5 else (15 if r >= 0.5 else 5)
        else:
            score += 15
        if   10 <= rng <= 20: score += 20
        elif 20 < rng <= 30:  score += 15
        elif 5 < rng < 10:    score += 10
        else:                 score += 5
        if month in STRONG:
            score += 10
        if score < 60:
            continue
        entry = close
        is_long = go_long
        sl = entry - STOP_EFF if is_long else entry + STOP_EFF
        tp = entry + STOP_EFF * RR if is_long else entry - STOP_EFF * RR
    if entry is not None:
        last = bars[-1]["c"]
        pts = (last - entry) if is_long else (entry - last)
        return pts * PT - COST, or_vol
    return None, or_vol


def pf(v):
    w = sum(x for x in v if x > 0)
    l = abs(sum(x for x in v if x <= 0))
    return round(w / l, 2) if l else (99.0 if w else 0.0)


if __name__ == "__main__":
    print("Loading ES...", flush=True)
    days = load(os.path.join(BASE, "data", "es_1min.csv"))
    dates = sorted(days)
    pc = {}
    for i in range(1, len(dates)):
        pc[dates[i]] = days[dates[i-1]][-1]["c"]

    # run ALL months (for the stability test), volume history maintained globally
    hist = deque(maxlen=20)
    trades = []                     # (date, month, pnl)
    for d in dates:
        if d.weekday() == 0:
            hist_pnl, orv = None, None
            r, orv = run_day(days[d], pc.get(d), hist, d.month)   # still track vol
            if orv: hist.append(orv)
            continue
        r, orv = run_day(days[d], pc.get(d), hist, d.month)
        if orv:
            hist.append(orv)
        if r is not None:
            trades.append((d, d.month, r))

    # A: faithful v4 (months 2/3/11 only), year-by-year
    v4 = [(d, m, p) for d, m, p in trades if m in (2, 3, 11)]
    print(f"\n{'═'*84}\n  A: FAITHFUL v4 RE-SIM (Feb/Mar/Nov only) — year by year\n{'═'*84}")
    for y in (2022, 2023, 2024, 2025, 2026):
        yr = [p for d, m, p in v4 if d.year == y]
        if yr:
            print(f"    {y}: N={len(yr):>3}  PF={pf(yr):>6}  ${sum(yr):>+9,.0f}")
    oos = [p for d, m, p in v4 if d.year >= 2024]
    iss = [p for d, m, p in v4 if d.year <= 2023]
    print(f"    IS 22-23:  N={len(iss)}  PF={pf(iss)}  ${sum(iss):+,.0f}")
    print(f"    OOS 24-26: N={len(oos)}  PF={pf(oos)}  ${sum(oos):+,.0f}   (claimed: N=90 PF 2.17 +$24,645)")

    # C: concentration
    top5 = sorted((p for _, _, p in v4 if p > 0), reverse=True)[:5]
    net = sum(p for _, _, p in v4)
    print(f"    concentration: top-5 wins = ${sum(top5):,.0f} = {sum(top5)/net:.0%} of total net" if net > 0 else "")

    # B: month stability — every month's PF in 22-23 vs 24-26
    print(f"\n{'═'*84}\n  B: MONTH-SELECTION STABILITY — all months, IS vs OOS  (v4 trades {{2,3,11}})\n{'═'*84}")
    mn = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    rank_is, rank_oos = [], []
    for m in range(1, 13):
        a = [p for d, mm, p in trades if mm == m and d.year <= 2023]
        b = [p for d, mm, p in trades if mm == m and d.year >= 2024]
        rank_is.append((pf(a), mn[m]))
        rank_oos.append((pf(b), mn[m]))
        tag = "  ← v4 trades this" if m in (2, 3, 11) else ""
        print(f"    {mn[m]}:  IS 22-23 PF={pf(a):>6} (N={len(a):>3}, ${sum(a):>+8,.0f})   "
              f"OOS 24-26 PF={pf(b):>6} (N={len(b):>3}, ${sum(b):>+8,.0f}){tag}")
    rank_is.sort(reverse=True)
    rank_oos.sort(reverse=True)
    print(f"\n    IS 22-23 top-3 months:  {[x[1] for x in rank_is[:3]]}")
    print(f"    OOS 24-26 top-3 months: {[x[1] for x in rank_oos[:3]]}")
    print(f"    v4 selected:            ['Feb', 'Mar', 'Nov']")
    print(f"\n{'═'*84}\n  es_v4_audit done.\n{'═'*84}")
