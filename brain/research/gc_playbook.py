"""
brain/research/gc_playbook.py

GOLD (GC) — first proper test, on clean volume-ranked continuous data
(data/gc_1min_v2.csv, 1.57M bars 2022-2026, $5.74 well spent).

Patterns: our two validated shapes, ATR-scaled (one scale factor, no fitting):
  ORB w/ gap filter   @ 8:20 pit open  (OR 8:20-8:34, entry to 9:45, flat 13:15)
  ORB w/ gap filter   @ 9:30 eq open   (OR 9:30-9:44, entry to 10:30, flat 15:55)
  VWAP rejection      @ pit session    (VWAP from 8:20, arm 10:00, flat 13:15)
  VWAP rejection      @ eq session     (VWAP from 9:30, arm 11:00, flat 15:55)

Metrics: standalone IS/OOS + counterbalance stats vs the NQ v12 system
(daily corr + PF on NQ-losing days). GC $100/pt, cost $24.50 RT.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import time
from counterbalance import (load_days, med_or, orb_day, rejection_day, pf,
                            NQ_REF, build_components)

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IS_Y, OOS_Y = (2022, 2023, 2024), (2025, 2026)
PT, COST = 100.0, 24.50

if __name__ == "__main__":
    print("Building NQ system daily P&L (for counterbalance metrics)...", flush=True)
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
    nq_loss = {d for d, p in nq_active.items() if p < 0}
    print(f"  NQ: {len(nq_active)} active days, {len(nq_loss)} losing")

    print("Loading GC (8-16h ET)...", flush=True)
    gc = load_days(os.path.join(BASE, "data", "gc_1min_v2.csv"), 8, 16)
    dates = sorted(gc)
    pc = {}
    for i in range(1, len(dates)):
        pc[dates[i]] = gc[dates[i-1]][-1]["c"]

    ANCHORS = {
        "pit(8:20)": dict(or_start=time(8, 20), or_end=time(8, 35),
                          entry_end=time(9, 45), flat=time(13, 15),
                          arm=time(10, 0)),
        "eq(9:30)":  dict(or_start=time(9, 30), or_end=time(9, 45),
                          entry_end=time(10, 30), flat=time(15, 55),
                          arm=time(11, 0)),
    }

    def evaluate(name, pnl_by_day):
        act = {d: p for d, p in pnl_by_day.items() if p is not None}
        if len(act) < 80:
            print(f"  {name:<26} insufficient N={len(act)}")
            return
        vis = [p for d, p in act.items() if d.year in IS_Y]
        vos = [p for d, p in act.items() if d.year in OOS_Y]
        on_loss = [p for d, p in act.items() if d in nq_loss]
        both = [(nq_pnl[d], p) for d, p in act.items() if d in nq_active]
        corr = 0.0
        if len(both) > 30:
            xs, ys = zip(*both)
            mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
            cov = sum((a-mx)*(b-my) for a, b in both)
            vx = sum((a-mx)**2 for a in xs) ** .5
            vy = sum((b-my)**2 for b in ys) ** .5
            corr = cov/(vx*vy) if vx*vy else 0.0
        star = ("  ★" if pf(vis) >= 1.05 and pf(vos) >= 1.05 else "")
        print(f"  {name:<26} IS PF={pf(vis):>5} (N={len(vis)}, ${sum(vis):>+8,.0f})  "
              f"OOS PF={pf(vos):>5} (N={len(vos)}, ${sum(vos):>+8,.0f})  "
              f"corr={corr:+.2f}  onNQloss PF={pf(on_loss):>5}{star}")
        # year table for anything alive
        if pf(vis) >= 1.0 or pf(vos) >= 1.1:
            for y in (2022, 2023, 2024, 2025, 2026):
                yr = [p for d, p in act.items() if d.year == y]
                if yr:
                    print(f"      {y}: N={len(yr):>3}  PF={pf(yr):>6}  ${sum(yr):>+8,.0f}")

    for aname, A in ANCHORS.items():
        m = med_or(gc, A["or_start"], A["or_end"])
        k = m / NQ_REF["or_med"]
        P = {key: NQ_REF[key] * k for key in
             ("min_or", "max_or", "gap", "brk_buf", "stop", "rej_ext", "rej_stop")}
        print(f"\n── anchor {aname}: median OR {m:.2f} → scale {k:.4f} "
              f"(stop {P['stop']:.2f}pt = ${P['stop']*PT:,.0f}/contract)")
        orb_p, rej_p = {}, {}
        for d in dates:
            if d.weekday() == 0:
                continue
            orb_p[d] = orb_day(gc[d], pc.get(d), P, PT, COST,
                               A["or_start"], A["or_end"], A["entry_end"], A["flat"])
            rej_p[d] = rejection_day(gc[d], P, PT, COST,
                                     A["or_start"], A["flat"], A["arm"])
        evaluate(f"GC ORB {aname}", orb_p)
        evaluate(f"GC rejection {aname}", rej_p)

    print(f"\n{'═'*96}\n  gc_playbook done.\n{'═'*96}")
