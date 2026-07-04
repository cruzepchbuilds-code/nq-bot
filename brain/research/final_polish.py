"""
brain/research/final_polish.py

Last-mile improvement pass over the full v11 + VWAP v6 system.

  A. Cross-strategy conditioning (the rejection|no-morning gate earned +$3.1k —
     are there more?):
       A1. PM ORB conditioned on same-day morning outcome (none/win/loss)
       A2. PM ORB conditioned on same-day rejection outcome
       A3. VWAP reclaim v6 conditioned on morning-ORB-traded (separate account)
  B. Rejection day-of-week table (Friday never checked)
  C. PM re-entry after target (never tested)
  D. WEEKLY loss brake — daily DLL can't stop the multi-week grind that kills
     accounts. Halt the week at -$X: cost in net vs gain in fresh-start survival.

All strict 1c. IS 2022-24 / OOS 2025-26 where relevant.

NOTE (2026-07-03): upstream fix — portfolio_policy.run_year_morning's
regime-ATR window is now a bounded 14-day deque (was unbounded list ->
expanding mean). This script inherits the fix via import; results recorded
BEFORE this date used the buggy morning stream (composed v12 stream deltas:
710 -> 713 trading days, full-period net -0.08%, OOS 2025-26 net +2.4%) —
re-run before citing absolute numbers.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import portfolio_policy as pp
from vwap_fulldata import run_day as vwap_run_day, load_days as vwap_load_days
from backtest import load_csv
from datetime import date, datetime, time
from collections import defaultdict

DLL = 500.0


def build(strict_1c=True):
    bars = load_csv(pp.DATA)
    morning = []
    for y in pp.YEARS:
        morning.extend(pp.run_year_morning(bars, y))
    del bars
    rth = vwap_load_days(pp.DATA)          # includes "o" (needed by vwap_run_day)
    eve = pp.load_days(pp.DATA, 16, 21)
    dt = defaultdict(list)
    for t in morning:
        d = date.fromisoformat(t["date"])
        e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
        x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
        c = max(1, t.get("contracts", 1)) if strict_1c else 1
        dt[d].append(("ORB", e, x, t["pnl"] / c))
    for d in sorted(rth):
        wd, mo = d.weekday(), d.month
        if wd != 0 and mo not in (4, 5, 6, 9, 12):
            r = pp.rejection_day(rth[d])
            if r: dt[d].append(("REJ", *r))
        if wd not in (0, 4):
            r = pp.pm_day(rth[d])
            if r: dt[d].append(("PM", *r))
    for d in sorted(eve):
        if d.weekday() != 3 and d.month not in (8, 11):
            r = pp.asia_day(eve[d])
            if r: dt[d].append(("ASIA", *r))
    for d in dt:
        dt[d].sort(key=lambda x: x[1])
    return dt, rth


def pf_stats(pnls):
    if not pnls:
        return {"n": 0, "wr": 0, "pf": 0, "net": 0}
    w = [p for p in pnls if p > 0]
    gl = abs(sum(p for p in pnls if p <= 0))
    return {"n": len(pnls), "wr": len(w) / len(pnls),
            "pf": round(sum(w) / gl, 3) if gl else 99.0, "net": round(sum(pnls))}


def prow(tag, s):
    print(f"    {tag:<26} N={s['n']:>4}  WR={s['wr']:>4.0%}  PF={s['pf']:>6.3f}  Net=${s['net']:>+9,}")


if __name__ == "__main__":
    day_trades, rth = build()

    # ── A1/A2: PM conditioning ────────────────────────────────────────────────
    print(f"\n{'═'*88}\n  A1: PM ORB by same-day MORNING outcome\n{'═'*88}")
    buckets = defaultdict(list)
    for d, lst in day_trades.items():
        orb = [x[3] for x in lst if x[0] == "ORB"]
        pm  = [x[3] for x in lst if x[0] == "PM"]
        if not pm:
            continue
        key = ("no_morning" if not orb else
               "morning_win" if sum(orb) > 0 else "morning_loss")
        buckets[key].extend(pm)
    for k in ["no_morning", "morning_win", "morning_loss"]:
        prow(k, pf_stats(buckets[k]))

    print(f"\n{'═'*88}\n  A2: PM ORB by same-day REJECTION outcome\n{'═'*88}")
    buckets = defaultdict(list)
    for d, lst in day_trades.items():
        orb = [x[3] for x in lst if x[0] == "ORB"]
        rej = [x[3] for x in lst if x[0] == "REJ" and not orb]   # rej only fires no-morning
        pm  = [x[3] for x in lst if x[0] == "PM"]
        if not pm:
            continue
        key = ("no_rej" if not rej else
               "rej_win" if sum(rej) > 0 else "rej_loss")
        buckets[key].extend(pm)
    for k in ["no_rej", "rej_win", "rej_loss"]:
        prow(k, pf_stats(buckets[k]))

    # ── A3: VWAP reclaim v6 conditioned on morning-ORB day ───────────────────
    print(f"\n{'═'*88}\n  A3: VWAP RECLAIM v6 (separate acct) by morning-ORB-traded flag\n{'═'*88}")
    orb_days = {d for d, lst in day_trades.items() if any(x[0] == "ORB" for x in lst)}
    v6 = dict(stop=20.0, rr=3.0, extend=35.0, lock=time(11, 0))
    buckets = defaultdict(list)
    for d in sorted(rth):
        if d.weekday() == 0 or d.month == 5:
            continue
        for p in vwap_run_day(rth[d], **v6):
            buckets["orb_day" if d in orb_days else "no_orb_day"].append(p)
    for k in ["no_orb_day", "orb_day"]:
        prow(k, pf_stats(buckets[k]))

    # ── B: Rejection DOW ──────────────────────────────────────────────────────
    print(f"\n{'═'*88}\n  B: REJECTION by day of week (no-morning days only, as deployed)\n{'═'*88}")
    dow_b = defaultdict(list)
    for d, lst in day_trades.items():
        orb = [x for x in lst if x[0] == "ORB"]
        if orb:
            continue
        for x in lst:
            if x[0] == "REJ":
                dow_b[d.weekday()].append(x[3])
    for wd, name in [(1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri")]:
        prow(name, pf_stats(dow_b[wd]))

    # ── C: PM re-entry after target ───────────────────────────────────────────
    print(f"\n{'═'*88}\n  C: PM re-entry — second PM trade after first hits target\n{'═'*88}")

    def pm_day_reentry(bars):
        results = []
        or_hi = or_lo = None
        or_done = False
        entry = sl = tp = e_t = None
        is_long = None
        trades_done = 0
        rearm = True
        for b in bars:
            t = b["t"]
            if t < time(13, 0):
                continue
            if t >= time(15, 55):
                break
            if t < time(13, 15):
                or_hi = b["h"] if or_hi is None else max(or_hi, b["h"])
                or_lo = b["l"] if or_lo is None else min(or_lo, b["l"])
                continue
            if not or_done:
                or_done = True
                if or_hi is None or not (15 <= or_hi - or_lo <= 60):
                    return results
            if entry is not None:
                done = None
                if is_long:
                    if b["l"] <= sl:   done = (sl - entry)
                    elif b["h"] >= tp: done = (tp - entry)
                else:
                    if b["h"] >= sl:   done = (entry - sl)
                    elif b["l"] <= tp: done = (entry - tp)
                if done is not None:
                    results.append(done * 20.0 - 9.50)
                    rearm = done > 0          # re-enter only after target
                    entry = None
                continue
            if t > time(14, 30) or trades_done >= 2 or not rearm:
                continue
            if trades_done >= 1 and t <= time(14, 0):
                pass                            # re-entry window extends to 14:30
            elif trades_done >= 1:
                pass
            elif t > time(14, 0):
                continue
            if b["c"] > or_hi + 2:
                entry, is_long, e_t = b["c"], True, t
                sl, tp = entry - 22.0, entry + 55.0
                trades_done += 1
            elif b["c"] < or_lo - 2:
                entry, is_long, e_t = b["c"], False, t
                sl, tp = entry + 22.0, entry - 55.0
                trades_done += 1
        if entry is not None:
            pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
            results.append(pts * 20.0 - 9.50)
        return results

    base_is, base_oos, re_is, re_oos = [], [], [], []
    for d in sorted(rth):
        if d.weekday() in (0, 4):
            continue
        r1 = pp.pm_day(rth[d])
        r2 = pm_day_reentry(rth[d])
        tgt_is = base_is if d.year <= 2024 else base_oos
        if r1: tgt_is.append(r1[2])
        tgt_re = re_is if d.year <= 2024 else re_oos
        tgt_re.extend(r2)
    print("  baseline (1 PM trade):")
    prow("IS",  pf_stats(base_is)); prow("OOS", pf_stats(base_oos))
    print("  re-entry after target (max 2, window to 14:30):")
    prow("IS",  pf_stats(re_is));  prow("OOS", pf_stats(re_oos))

    # ── D: Weekly loss brake ──────────────────────────────────────────────────
    print(f"\n{'═'*88}\n  D: WEEKLY LOSS BRAKE — halt week when week P&L ≤ -X (v11 rules otherwise)\n{'═'*88}")

    def sim_weekbrake(dt, brake):
        daily = {}
        week_pnl = {}
        for d in sorted(dt):
            wk = d.isocalendar()[:2]
            wp = week_pnl.get(wk, 0.0)
            pnl_day = 0.0
            open_until = None
            morning_traded = any(s == "ORB" for s, _, _, _ in dt[d])
            for strat, e_t, x_t, pnl in dt[d]:
                if strat == "REJ" and morning_traded:
                    continue
                if open_until is not None and e_t < open_until:
                    continue
                if pnl_day <= -DLL:
                    continue
                if brake and wp + pnl_day <= -brake:
                    continue
                pnl_day += pnl
                open_until = x_t
            week_pnl[wk] = wp + pnl_day
            daily[d] = pnl_day
        return daily

    def floor_deaths(daily_all, years):
        """fresh-start deaths: min cum equity ≤ -2000 from each start day in years"""
        days = sorted(daily_all)
        pnls = [daily_all[d] for d in days]
        deaths = starts = 0
        for i, d in enumerate(days):
            if d.year not in years:
                continue
            starts += 1
            eq = 0.0
            for p in pnls[i:]:
                eq += p
                if eq <= -2000:
                    deaths += 1
                    break
        return deaths, starts

    for brake in [0, 2000, 1500, 1250, 1000]:
        daily = sim_weekbrake(day_trades, brake)
        net = round(sum(daily.values()))
        worst_wk = defaultdict(float)
        for d, p in daily.items():
            worst_wk[d.isocalendar()[:2]] += p
        ww = round(min(worst_wk.values()))
        d_all, s_all = floor_deaths(daily, (2022, 2023, 2024, 2025, 2026))
        tag = "none" if brake == 0 else f"-{brake}"
        print(f"    brake {tag:>6}:  Net=${net:>+10,}  worstWeek=${ww:>+7,}  "
              f"fresh-start deaths={d_all}/{s_all} ({d_all/s_all:.0%})")

    print(f"\n{'═'*88}\n  final_polish done.\n{'═'*88}")
