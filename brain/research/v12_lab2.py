"""
brain/research/v12_lab2.py — part 2 of the v12 work loop.

  C. AUTO KILL-SWITCH: disable a component when its rolling-N-trade PF drops
     below X; keep computing virtual trades; re-enable when virtual rolling
     PF recovers above Y. Does automated component gating beat always-on?
     (Equity-curve trading usually loses — this needs proof either way.)
     Grid: N in {30, 40}, off-threshold {0.8, 0.9}, on-threshold {1.1}.

  D. ±1-MINUTE ROBUSTNESS: shift REJ/PM/ASIA/VWAP window boundaries by ±1
     bar-minute (loader-level timestamp shift) and confirm P&L stability.
     (Morning ORB already validated against real NT8 stamping: Analyzer 285
     trades vs sim 282.)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval_boost import build_components
import portfolio_policy as pp
from vwap_fulldata import load_days as vwap_load
from datetime import datetime, timedelta, time as dtime
from collections import defaultdict, deque

DLL = 500.0
GUARD = 1150.0
RISK = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}


def day_trades_merged(comp, d):
    lst = []
    for k in ("ORB3", "REJ", "PM", "ASIA"):
        key = "ORB" if k == "ORB3" else k
        for t in comp[k].get(d, []):
            lst.append(t)
    return sorted(lst, key=lambda x: x[1])


def run_killswitch(comp, all_days, window, off_pf, on_pf):
    """Full-period run with per-component rolling-PF gating."""
    hist = {k: deque(maxlen=window) for k in ("ORB", "REJ", "PM", "ASIA")}
    enabled = {k: True for k in hist}

    def rolling_pf(k):
        if len(hist[k]) < 15:
            return None
        w = sum(p for p in hist[k] if p > 0)
        l = abs(sum(p for p in hist[k] if p <= 0))
        return (w / l) if l else 99.0

    net = 0.0
    blocked_pnl = 0.0
    for d in all_days:
        lst = day_trades_merged(comp, d)
        pnl_day = 0.0
        morning_pnl = 0.0
        has_orb = any(s == "ORB" for s, _, _, _ in lst)
        open_until = None
        for strat, e_t, x_t, pnl in lst:
            if strat == "REJ" and has_orb:
                continue
            if strat == "PM" and has_orb and morning_pnl < 0:
                continue
            if open_until is not None and e_t < open_until:
                continue
            if pnl_day <= -DLL:
                continue
            if (GUARD + pnl_day) < RISK[strat]:
                continue
            # virtual trade always recorded for gating state
            hist[strat].append(pnl)
            pf = rolling_pf(strat)
            if enabled[strat] and pf is not None and pf < off_pf:
                enabled[strat] = False
            elif not enabled[strat] and pf is not None and pf >= on_pf:
                enabled[strat] = True
            if not enabled[strat]:
                blocked_pnl += pnl
                continue
            pnl_day += pnl
            if strat == "ORB":
                morning_pnl += pnl
            open_until = x_t
        net += pnl_day
    return net, blocked_pnl


if __name__ == "__main__":
    print("Building components...", flush=True)
    comp = build_components()
    all_days = sorted(set().union(*[set(comp[k]) for k in ("ORB3", "REJ", "PM", "ASIA")]))

    # ══ C: AUTO KILL-SWITCH ═══════════════════════════════════════════════════
    print(f"\n{'═'*92}\n  C: AUTO KILL-SWITCH — rolling-PF component gating (full period 2022-2026)\n{'═'*92}")
    base_net, _ = run_killswitch(comp, all_days, 40, -1, -1)   # thresholds impossible → always on
    print(f"  always-on baseline           net=${base_net:>+10,.0f}")
    for window, off in [(30, 0.8), (30, 0.9), (40, 0.8), (40, 0.9)]:
        net, blocked = run_killswitch(comp, all_days, window, off, 1.1)
        d = net - base_net
        print(f"  N={window} off<{off} on≥1.1        net=${net:>+10,.0f}  Δ=${d:>+8,.0f}  "
              f"(P&L while gated: ${blocked:>+8,.0f})")

    # ══ D: ±1-MINUTE ROBUSTNESS (REJ/PM/ASIA/VWAP standalone sims) ════════════
    print(f"\n{'═'*92}\n  D: ±1-MINUTE TIMING ROBUSTNESS (standalone components)\n{'═'*92}")

    def shifted_days(shift_min):
        """Reload RTH+eve bars with timestamps shifted by shift_min minutes."""
        rth = vwap_load(pp.DATA)
        out = {}
        for d, bars in rth.items():
            nb = []
            for b in bars:
                t0 = datetime(2000, 1, 1, b["t"].hour, b["t"].minute) + timedelta(minutes=shift_min)
                nb.append({**b, "t": t0.time()})
            out[d] = nb
        return out

    from tradeify_build import reclaim_day

    for shift in [-1, 0, 1]:
        rth_s = shifted_days(shift)
        eve = pp.load_days(pp.DATA, 16, 21)
        if shift != 0:
            eve2 = {}
            for d, bars in eve.items():
                nb = []
                for b in bars:
                    t0 = datetime(2000, 1, 1, b["t"].hour, b["t"].minute) + timedelta(minutes=shift)
                    nb.append({**b, "t": t0.time()})
                eve2[d] = nb
            eve = eve2
        tot = {"REJ": 0.0, "PM": 0.0, "ASIA": 0.0, "VWAP": 0.0}
        cnt = {k: 0 for k in tot}
        for d in sorted(rth_s):
            wd, mo = d.weekday(), d.month
            if wd != 0 and mo not in (4, 5, 6, 9, 12):
                r = pp.rejection_day(rth_s[d])
                if r:
                    tot["REJ"] += r[2]; cnt["REJ"] += 1
            if wd not in (0, 4):
                r = pp.pm_day(rth_s[d])
                if r:
                    tot["PM"] += r[2]; cnt["PM"] += 1
            if wd != 0 and mo != 5:
                r = reclaim_day(rth_s[d])
                if r:
                    tot["VWAP"] += r[2]; cnt["VWAP"] += 1
        for d in sorted(eve):
            if d.weekday() != 3 and d.month not in (8, 11):
                r = pp.asia_day(eve[d])
                if r:
                    tot["ASIA"] += r[2]; cnt["ASIA"] += 1
        lbl = f"{shift:+d} min" if shift else " 0 min (baseline)"
        print(f"  {lbl:<18} REJ ${tot['REJ']:>+8,.0f}/{cnt['REJ']}   PM ${tot['PM']:>+8,.0f}/{cnt['PM']}   "
              f"ASIA ${tot['ASIA']:>+8,.0f}/{cnt['ASIA']}   VWAP ${tot['VWAP']:>+8,.0f}/{cnt['VWAP']}")

    print(f"\n{'═'*92}\n  v12_lab2 done.\n{'═'*92}")
