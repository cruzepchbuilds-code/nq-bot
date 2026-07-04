"""
brain/research/v12_lab.py — part 1 of the v12 work loop.

  A. LUCID + RECLAIM MERGE: fixed floor may tolerate what Tradeify's trailing
     floor couldn't. Full v11.3 (2c REJ/PM) + VWAP reclaim as 5th strategy,
     one-position arbitration. vs baseline: monthly, worst day, deaths.
  B. TRADEIFY DAY-STOP at +$1,000: profit beyond the daily payout cap can't
     be swept today and sits exposed. Stop new entries once day >= +$1,000.
     Metric: withdrawn per account + median payout cadence.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval_boost import build_components
from tradeify_build import reclaim_day
from orchestration_final import RISK, GUARD_LUCID, GUARD_TRADEIFY, DLL
import portfolio_policy as pp
from vwap_fulldata import load_days as vwap_load
from collections import defaultdict

RISK_VWAP = 415.0
RISK2 = dict(RISK, VWAP=RISK_VWAP)


def day_pnl(lst, guard, size2=None, cum=0.0, day_stop=None, dll=DLL):
    pnl_day = 0.0
    morning_pnl = 0.0
    has_orb = any(s == "ORB" for s, _, _, _ in lst)
    open_until = None
    for strat, e_t, x_t, pnl in sorted(lst, key=lambda x: x[1]):
        if strat == "REJ" and has_orb:
            continue
        if strat == "PM" and has_orb and morning_pnl < 0:
            continue
        if open_until is not None and e_t < open_until:
            continue
        if pnl_day <= -dll:
            continue
        if day_stop is not None and pnl_day >= day_stop:
            continue
        mult = 1
        if size2 and strat in size2 and cum + pnl_day >= 1500:
            if (guard + pnl_day) >= 2 * RISK2[strat]:
                mult = 2
        if mult == 1 and (guard + pnl_day) < RISK2[strat]:
            continue
        pnl_day += pnl * mult
        if strat == "ORB":
            morning_pnl += pnl * mult
        open_until = x_t
    return pnl_day


if __name__ == "__main__":
    print("Building components...", flush=True)
    comp = build_components()
    rth = vwap_load(pp.DATA)
    comp["VWAP"] = defaultdict(list)
    for d in sorted(rth):
        if d.weekday() != 0 and d.month != 5:
            r = reclaim_day(rth[d])
            if r:
                comp["VWAP"][d].append(("VWAP", *r))
    all_days = sorted(set().union(*[set(comp[k]) for k in ("ORB3", "REJ", "PM", "ASIA", "VWAP")]))

    def trades_for(d, use):
        lst = []
        for k in use:
            src = comp["ORB3"] if k == "ORB" else comp[k]
            lst.extend(src.get(d, []))
        return lst

    # ══ A: LUCID + RECLAIM MERGE ══════════════════════════════════════════════
    print(f"\n{'═'*96}\n  A: LUCID (fixed -2k floor, 2c REJ/PM) — with vs without reclaim merged\n{'═'*96}")
    S2C = {"REJ", "PM"}
    for name, use in [("baseline v11.3 (4 strat)", ("ORB", "REJ", "PM", "ASIA")),
                      ("+ reclaim merged (5 strat)", ("ORB", "REJ", "PM", "ASIA", "VWAP"))]:
        cum = 0.0
        monthly = defaultdict(float)
        worst = 0.0
        for d in all_days:
            if d.year < 2024:
                continue
            p = day_pnl(trades_for(d, use), GUARD_LUCID, size2=S2C, cum=cum)
            cum += p
            worst = min(worst, p)
            monthly[(d.year, d.month)] += p
        deaths = starts = 0
        d24 = [d for d in all_days if d.year >= 2024]
        for i in range(len(d24)):
            starts += 1
            c = 0.0
            for d in d24[i:]:
                c += day_pnl(trades_for(d, use), GUARD_LUCID, size2=S2C, cum=c)
                if c <= -2000:
                    deaths += 1
                    break
        mv = sorted(monthly.values())
        print(f"  {name:<28} net=${cum:>+10,.0f}  avg/mo=${cum/len(monthly):>+7,.0f}  "
              f"worst day=${worst:>+7,.0f}  median mo=${mv[len(mv)//2]:>+7,.0f}  "
              f"deaths={deaths}/{starts} ({deaths/starts:.0%})")

    # ══ B: TRADEIFY DAY-STOP ═════════════════════════════════════════════════
    print(f"\n{'═'*96}\n  B: TRADEIFY daily path — stop new entries once day P&L ≥ +$X\n{'═'*96}")
    FULL = ("ORB", "REJ", "PM", "ASIA")
    for stop_at in [None, 1000, 700]:
        seq = [(d, day_pnl(trades_for(d, FULL), GUARD_TRADEIFY, day_stop=stop_at))
               for d in all_days]
        tot_w, lives, deaths, starts = [], [], 0, 0
        for i, (d0, _) in enumerate(seq):
            if d0.year < 2024:
                continue
            starts += 1
            bal, hwm, floor = 50_000.0, 50_000.0, 48_000.0
            withdrawn = 0.0
            died = False
            for d, p in seq[i:]:
                bal += p
                if bal <= floor:
                    died = True
                    break
                w = min(1000.0, bal - max(50_000.0, floor + 1500.0))
                if w > 0:
                    bal -= w
                    withdrawn += w
                hwm = max(hwm, bal)
                floor = max(floor, min(hwm - 2000.0, 50_000.0))
            tot_w.append(withdrawn)
            deaths += died
        tag = "no stop (current)" if stop_at is None else f"stop at +${stop_at:,}"
        print(f"  {tag:<20} withdrawn/acct=${sum(tot_w)/len(tot_w):>8,.0f}   "
              f"deaths={deaths}/{starts} ({deaths/starts:.0%})")

    print(f"\n{'═'*96}\n  v12_lab part 1 done.\n{'═'*96}")
