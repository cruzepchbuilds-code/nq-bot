"""
brain/research/eval_boost.py

Maximize P(pass Tradeify 50K eval): +$3,000 before trailing -$2,000 EOD,
40% consistency (best day <= 40% of total => total >= best_day/0.4).

Baseline (full v11.2 funded config): 48% pass, median 10 td (2024+ starts).

Levers tested (policy-level, applied to the SAME underlying trades):
  stack:      full | noASIA | ORB+REJ | morning2R variants (2R caps the big
              single trade at ~$1,086 so consistency binds less)
  maxLosses:  stop day after N losing trades (cuts stacked-loss days)
  greenLock:  stop NEW entries once day P&L >= +$X (bank the green day —
              also caps best-day so the 40% rule never forces overshoot)
  dll:        internal daily halt 500 (v11) vs 400

Reports pass% + median/p90 days for 2024+ starts and all starts.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import portfolio_policy as pp
from vwap_fulldata import load_days as vwap_load
from backtest import load_csv
from datetime import date, datetime
from collections import defaultdict


def build_components():
    """Returns per-day trade lists per component, morning in BOTH configs."""
    comp = {"ORB3": defaultdict(list), "ORB2R": defaultdict(list),
            "REJ": defaultdict(list), "PM": defaultdict(list),
            "ASIA": defaultdict(list)}

    for eval_mode, key in [(False, "ORB3"), (True, "ORB2R")]:
        config.EVAL_MODE = eval_mode
        bars = load_csv(pp.DATA)
        morning = []
        for y in pp.YEARS:
            morning.extend(pp.run_year_morning(bars, y))
        del bars
        for t in morning:
            d = date.fromisoformat(t["date"])
            e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
            x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
            c = max(1, t.get("contracts", 1))
            comp[key][d].append(("ORB", e, x, t["pnl"] / c))
    config.EVAL_MODE = False

    rth = vwap_load(pp.DATA)
    eve = pp.load_days(pp.DATA, 16, 21)
    for d in sorted(rth):
        wd, mo = d.weekday(), d.month
        if wd != 0 and mo not in (4, 5, 6, 9, 12):
            r = pp.rejection_day(rth[d])
            if r: comp["REJ"][d].append(("REJ", *r))
        if wd not in (0, 4):
            r = pp.pm_day(rth[d])
            if r: comp["PM"][d].append(("PM", *r))
    for d in sorted(eve):
        if d.weekday() != 3 and d.month not in (8, 11):
            r = pp.asia_day(eve[d])
            if r: comp["ASIA"][d].append(("ASIA", *r))
    return comp


def make_daily(comp, morning_key, use, max_losses=99, green_lock=None, dll=500.0):
    """v11.2 rules + eval policy levers -> {date: day_pnl}."""
    all_days = set()
    for k in use:
        src = comp[morning_key] if k == "ORB" else comp[k]
        all_days |= set(src.keys())
    out = {}
    for d in sorted(all_days):
        lst = []
        for k in use:
            src = comp[morning_key] if k == "ORB" else comp[k]
            lst.extend(src.get(d, []))
        lst.sort(key=lambda x: x[1])
        pnl_day = 0.0
        morning_pnl = 0.0
        losses = 0
        morning_traded = any(s == "ORB" for s, _, _, _ in lst)
        open_until = None
        for strat, e_t, x_t, pnl in lst:
            if strat == "REJ" and morning_traded:
                continue
            if strat == "PM" and morning_traded and morning_pnl < 0:
                continue
            if open_until is not None and e_t < open_until:
                continue
            if pnl_day <= -dll:
                continue
            if losses >= max_losses:
                continue
            if green_lock is not None and pnl_day >= green_lock:
                continue
            pnl_day += pnl
            if strat == "ORB":
                morning_pnl += pnl
            if pnl < 0:
                losses += 1
            open_until = x_t
        out[d] = pnl_day
    return out


def eval_sim(daily, years):
    days = sorted(daily)
    seq = [(d, daily[d]) for d in days]
    passed, died = [], 0
    for i, (d0, _) in enumerate(seq):
        if d0.year not in years:
            continue
        cum = hwm = best = 0.0
        floor = -2000.0
        hit = None
        n = 0
        for _, p in seq[i:]:
            cum += p
            n += 1
            best = max(best, p)
            if cum <= floor:
                hit = "die"; break
            if cum >= max(3000.0, best / 0.40):
                hit = "pass"; break
            hwm = max(hwm, cum)
            floor = max(floor, hwm - 2000.0)
        if hit == "pass":
            passed.append(n)
        elif hit == "die":
            died += 1
    tot = len(passed) + died
    if tot == 0:
        return 0, -1, -1, 0
    passed.sort()
    med = passed[len(passed)//2] if passed else -1
    p90 = passed[int(len(passed)*0.9)] if passed else -1
    return len(passed)/tot, med, p90, tot


if __name__ == "__main__":
    print("Building components (two morning configs — ~8 min)...", flush=True)
    comp = build_components()

    FULL = ("ORB", "REJ", "PM", "ASIA")
    variants = [
        ("BASELINE full 3R",                "ORB3",  FULL, 99, None, 500),
        ("full 3R, maxLoss=1",              "ORB3",  FULL, 1,  None, 500),
        ("full 3R, maxLoss=2",              "ORB3",  FULL, 2,  None, 500),
        ("full 3R, greenLock +600",         "ORB3",  FULL, 99, 600,  500),
        ("full 3R, greenLock +1100",        "ORB3",  FULL, 99, 1100, 500),
        ("full 3R, DLL 400",                "ORB3",  FULL, 99, None, 400),
        ("full 3R, no ASIA",                "ORB3",  ("ORB", "REJ", "PM"), 99, None, 500),
        ("ORB3+REJ only",                   "ORB3",  ("ORB", "REJ"), 99, None, 500),
        ("morning 2R + rest",               "ORB2R", FULL, 99, None, 500),
        ("2R + greenLock +1100",            "ORB2R", FULL, 99, 1100, 500),
        ("2R + greenLock +600",             "ORB2R", FULL, 99, 600,  500),
        ("2R + maxLoss=1 + lock600",        "ORB2R", FULL, 1,  600,  500),
        ("3R + maxLoss=1 + lock1100",       "ORB3",  FULL, 1,  1100, 500),
        ("3R + maxLoss=1 + lock600",        "ORB3",  FULL, 1,  600,  500),
    ]

    print(f"\n{'═'*100}")
    print(f"  EVAL PASS-RATE GRID — target $3,000 | trailing $2,000 EOD | consistency 40%")
    print(f"{'═'*100}")
    print(f"  {'variant':<30} {'2024+ pass':>10} {'med':>5} {'p90':>5}   {'all pass':>9} {'med':>5}")
    print(f"  {'─'*76}")
    for name, mk, use, ml, gl, dll in variants:
        daily = make_daily(comp, mk, use, max_losses=ml, green_lock=gl, dll=dll)
        pr, med, p90, _ = eval_sim(daily, (2024, 2025, 2026))
        pa, meda, _, _ = eval_sim(daily, (2022, 2023, 2024, 2025, 2026))
        print(f"  {name:<30} {pr:>10.0%} {med:>5} {p90:>5}   {pa:>9.0%} {meda:>5}")

    print(f"\n{'═'*100}\n  eval_boost done.\n{'═'*100}")
