"""
brain/research/counterbalance.py

Hunt for a COUNTERBALANCE: an instrument+strategy whose wins cluster on the
days the v12 NQ system loses. That's what smooths the trailing-floor deaths —
standalone PF alone is not the bar.

Candidates (our two most robust pattern shapes, ATR-scaled to each market —
one principled scale factor per instrument, no per-market fitting):
  ES / RTY:  morning ORB w/ gap filter (9:30-9:44 OR)  +  VWAP rejection 11-13h
  CL:        pit-session ORB (9:00-9:14 OR)            +  VWAP rejection 10:30-13h
  NQ-internal: OR-extreme fade on no-breakout days (trades our $0 days)

Metrics per candidate:
  standalone IS/OOS PF | daily-P&L corr vs NQ system (active days)
  | PF and avg$ on NQ-LOSS days vs NQ-WIN days
Counterbalance bar: standalone PF >= 1.05 (both halves) AND loss-day PF >= 1.25
AND corr <= +0.10.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import csv
from datetime import datetime, time
from collections import defaultdict
from eval_boost import build_components

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IS_Y, OOS_Y = (2022, 2023, 2024), (2025, 2026)

SPEC = {
    "ES":  dict(file="es_1min.csv",  pt=50.0,   cost=17.0,  rth=(9, 16), or_start=time(9, 30),
                or_end=time(9, 45),  entry_end=time(10, 30), flat=time(15, 55)),
    "RTY": dict(file="rty_1min.csv", pt=50.0,   cost=14.5,  rth=(9, 16), or_start=time(9, 30),
                or_end=time(9, 45),  entry_end=time(10, 30), flat=time(15, 55)),
    "CL":  dict(file="cl_1min.csv",  pt=1000.0, cost=24.5,  rth=(9, 15), or_start=time(9, 0),
                or_end=time(9, 15),  entry_end=time(10, 30), flat=time(14, 15)),
}
# NQ reference params (points) — scaled per instrument by median first-15-min OR
NQ_REF = dict(or_med=70.0, min_or=55, max_or=110, gap=20, brk_buf=4, stop=27,
              rr=3.0, rej_ext=25, rej_stop=20, rej_rr=3.0)


def load_days(path, h_lo, h_hi):
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


def med_or(days, or_start, or_end):
    vals = []
    for d in sorted(days):
        b = [x for x in days[d] if or_start <= x["t"] < or_end]
        if len(b) >= 10:
            vals.append(max(x["h"] for x in b) - min(x["l"] for x in b))
    vals.sort()
    return vals[len(vals)//2]


def orb_day(bars, prior_close, P, pt, cost, or_start, or_end, entry_end, flat):
    """Gap-filtered ORB, scaled params P."""
    orh = orl = None
    o_open = None
    entry = sl = tp = None
    is_long = None
    for i, b in enumerate(bars):
        t = b["t"]
        if t >= flat:
            break
        if t < or_start:
            continue
        if o_open is None:
            o_open = b["o"]
        if t < or_end:
            orh = b["h"] if orh is None else max(orh, b["h"])
            orl = b["l"] if orl is None else min(orl, b["l"])
            continue
        if orh is None or not (P["min_or"] <= orh - orl <= P["max_or"]):
            return None
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (sl - entry) * pt - cost
                if b["h"] >= tp: return (tp - entry) * pt - cost
            else:
                if b["h"] >= sl: return (entry - sl) * pt - cost
                if b["l"] <= tp: return (entry - tp) * pt - cost
            continue
        if t > entry_end or prior_close is None:
            continue
        gap = (orh + orl) / 2 - prior_close
        if b["c"] > orh + P["brk_buf"] and gap > P["gap"]:
            entry, is_long = b["c"], True
            sl, tp = entry - P["stop"], entry + P["stop"] * NQ_REF["rr"]
        elif b["c"] < orl - P["brk_buf"] and gap < -P["gap"]:
            entry, is_long = b["c"], False
            sl, tp = entry + P["stop"], entry - P["stop"] * NQ_REF["rr"]
    if entry is not None:
        last = bars[-1]["c"]
        pts = (last - entry) if is_long else (entry - last)
        return pts * pt - cost
    return None


def rejection_day(bars, P, pt, cost, sess_start, flat, arm_time):
    sum_pv = sum_vol = 0.0
    vwap = None
    was_ext = saw = False
    up = prev_above = None
    entry = sl = tp = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < sess_start or t >= flat:
            continue
        sum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        sum_vol += b["v"]
        vwap = sum_pv / sum_vol if sum_vol else None
        if vwap is None:
            continue
        c = b["c"]
        above = c > vwap
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (sl - entry) * pt - cost
                if b["h"] >= tp: return (tp - entry) * pt - cost
            else:
                if b["h"] >= sl: return (entry - sl) * pt - cost
                if b["l"] <= tp: return (entry - tp) * pt - cost
            if t >= time(13, 0):
                pts = (c - entry) if is_long else (entry - c)
                return pts * pt - cost
            prev_above = above
            continue
        if prev_above is None:
            prev_above = above
            continue
        if not was_ext and abs(c - vwap) > P["rej_ext"]:
            was_ext = True
        if was_ext and arm_time <= t < time(13, 0):
            cu = (not prev_above) and above
            cd = prev_above and (not above)
            if not saw:
                if cu:   saw, up = True, True
                elif cd: saw, up = True, False
            else:
                if (up and cd) or ((not up) and cu):
                    is_long = cu
                    entry = c
                    sl = c - P["rej_stop"] if is_long else c + P["rej_stop"]
                    tp = (c + P["rej_stop"] * NQ_REF["rej_rr"] if is_long
                          else c - P["rej_stop"] * NQ_REF["rej_rr"])
        prev_above = above
    if entry is not None:
        last = bars[-1]["c"]
        pts = (last - entry) if is_long else (entry - last)
        return pts * pt - cost
    return None


def nq_fade_day(bars):
    """NQ-internal: no-breakout day -> fade OR-extreme touches to OR mid, 11:00-14:00.
       stop 20, target = OR mid. One trade."""
    orh = orl = None
    for b in bars:
        t = b["t"]
        if t < time(9, 30):
            continue
        if t < time(9, 45):
            orh = b["h"] if orh is None else max(orh, b["h"])
            orl = b["l"] if orl is None else min(orl, b["l"])
            continue
        break
    if orh is None or orh - orl < 40:
        return None
    mid = (orh + orl) / 2
    entry = sl = tp = None
    is_long = None
    for i, b in enumerate(bars):
        t = b["t"]
        if t >= time(15, 55):
            break
        # disqualify: any breakout close before 11:00
        if time(9, 45) <= t < time(11, 0):
            if b["c"] > orh + 4 or b["c"] < orl - 4:
                return None
            continue
        if t < time(11, 0):
            continue
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (sl - entry) * 20.0 - 14.5
                if b["h"] >= tp: return (tp - entry) * 20.0 - 14.5
            else:
                if b["h"] >= sl: return (entry - sl) * 20.0 - 14.5
                if b["l"] <= tp: return (entry - tp) * 20.0 - 14.5
            continue
        if t > time(14, 0):
            break
        if b["h"] >= orh - 2 and b["c"] < orh:
            entry, is_long = b["c"], False
            sl, tp = entry + 20.0, mid
        elif b["l"] <= orl + 2 and b["c"] > orl:
            entry, is_long = b["c"], True
            sl, tp = entry - 20.0, mid
    if entry is not None:
        last = bars[-1]["c"]
        pts = (last - entry) if is_long else (entry - last)
        return pts * 20.0 - 14.5
    return None


def pf(v):
    w = sum(x for x in v if x > 0)
    l = abs(sum(x for x in v if x <= 0))
    return round(w / l, 3) if l else (99.0 if w else 0.0)


if __name__ == "__main__":
    print("Building NQ system daily P&L...", flush=True)
    comp = build_components()
    RISK = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}

    def nq_day(d):
        lst = []
        for k in ("ORB3", "REJ", "PM", "ASIA"):
            key = "ORB" if k == "ORB3" else k
            lst.extend((key, *t[1:]) for t in comp[k].get(d, []))
        lst.sort(key=lambda x: x[1])
        pnl_day = morning = 0.0
        has_orb = any(s == "ORB" for s, *_ in lst)
        open_until = None
        for strat, e_t, x_t, p in lst:
            if strat == "REJ" and has_orb: continue
            if strat == "PM" and has_orb and morning < 0: continue
            if open_until is not None and e_t < open_until: continue
            if pnl_day <= -500: continue
            if (1150 + pnl_day) < RISK[strat]: continue
            pnl_day += p
            if strat == "ORB": morning += p
            open_until = x_t
        return pnl_day

    nq_days_all = sorted(set().union(*[set(comp[k]) for k in ("ORB3", "REJ", "PM", "ASIA")]))
    nq_pnl = {d: nq_day(d) for d in nq_days_all}
    nq_active = {d: p for d, p in nq_pnl.items() if p != 0}
    nq_loss_days = {d for d, p in nq_active.items() if p < 0}
    nq_win_days = {d for d, p in nq_active.items() if p > 0}
    print(f"  NQ system: {len(nq_active)} active days, {len(nq_loss_days)} losing "
          f"(avg loss ${sum(nq_pnl[d] for d in nq_loss_days)/max(1,len(nq_loss_days)):,.0f})")

    results = []

    def evaluate(name, pnl_by_day):
        act = {d: p for d, p in pnl_by_day.items() if p is not None}
        if len(act) < 100:
            print(f"  {name:<24} insufficient N={len(act)}")
            return
        vis = [p for d, p in act.items() if d.year in IS_Y]
        vos = [p for d, p in act.items() if d.year in OOS_Y]
        on_loss = [p for d, p in act.items() if d in nq_loss_days]
        on_win = [p for d, p in act.items() if d in nq_win_days]
        # daily corr on days both active
        both = [(nq_pnl[d], p) for d, p in act.items() if d in nq_active]
        corr = 0.0
        if len(both) > 30:
            xs, ys = zip(*both)
            mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
            cov = sum((a-mx)*(b-my) for a, b in both)
            vx = sum((a-mx)**2 for a in xs) ** 0.5
            vy = sum((b-my)**2 for b in ys) ** 0.5
            corr = cov / (vx*vy) if vx*vy else 0.0
        bar = (pf(vis) >= 1.05 and pf(vos) >= 1.05 and pf(on_loss) >= 1.25 and corr <= 0.10)
        flag = "  ★ COUNTERBALANCE" if bar else ""
        print(f"  {name:<24} IS PF={pf(vis):>5} (N={len(vis)})  OOS PF={pf(vos):>5} (N={len(vos)})  "
              f"corr={corr:+.2f}  onNQloss PF={pf(on_loss):>5} avg=${(sum(on_loss)/len(on_loss)) if on_loss else 0:>+6,.0f} "
              f"(N={len(on_loss)}){flag}")
        results.append((name, bar))

    for sym, S in SPEC.items():
        print(f"\nLoading {sym}...", flush=True)
        days = load_days(os.path.join(BASE, "data", S["file"]), *S["rth"])
        m = med_or(days, S["or_start"], S["or_end"])
        k = m / NQ_REF["or_med"]
        P = {key: NQ_REF[key] * k for key in
             ("min_or", "max_or", "gap", "brk_buf", "stop", "rej_ext", "rej_stop")}
        print(f"  median OR {m:.2f} → scale {k:.4f}")
        dates = sorted(days)
        pc = {}
        for i in range(1, len(dates)):
            pc[dates[i]] = days[dates[i-1]][-1]["c"]

        orb_p, rej_p = {}, {}
        for d in dates:
            if d.weekday() == 0:
                continue
            orb_p[d] = orb_day(days[d], pc.get(d), P, S["pt"], S["cost"],
                               S["or_start"], S["or_end"], S["entry_end"], S["flat"])
            arm = time(11, 0) if sym != "CL" else time(10, 30)
            rej_p[d] = rejection_day(days[d], P, S["pt"], S["cost"],
                                     S["or_start"], S["flat"], arm)
        evaluate(f"{sym} ORB(gap)", orb_p)
        evaluate(f"{sym} rejection", rej_p)

    # NQ-internal fade (uses NQ data via comp? need raw bars — reload)
    print(f"\nLoading NQ for internal fade...", flush=True)
    import portfolio_policy as pp
    from vwap_fulldata import load_days as vload
    nqd = vload(pp.DATA)
    fade_p = {}
    for d in sorted(nqd):
        if d.weekday() == 0:
            continue
        fade_p[d] = nq_fade_day(nqd[d])
    evaluate("NQ OR-fade (no-BO days)", fade_p)

    print(f"\n{'═'*96}")
    stars = [n for n, b in results if b]
    print(f"  COUNTERBALANCE CANDIDATES: {stars if stars else 'NONE — nothing met the bar'}")
    print(f"{'═'*96}\n  counterbalance done.\n{'═'*96}")
