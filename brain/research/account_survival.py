"""
brain/research/account_survival.py

"How many accounts would it fail?" — rolling-start survival analysis for v11.

For EVERY trading day 2022-2026 as a fresh-account start:
  EVAL stage   (eval config: morning 2R 1c, no Asia, DLL 500, REJ/PM on):
      P(reach +$3,000 before cum equity ≤ -$2,000), and days to pass.
  FUNDED stage (funded config, strict 1c):
      P(cum equity never ≤ -$2,000 through data end), RampMode ON and OFF.

Right-censoring: late starts with little remaining data are reported separately
(unresolved ≠ survived).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import portfolio_policy as pp          # funded config: Fri ON, pyramid off, EVAL_MODE False
import config
from backtest import load_csv
from datetime import date, datetime
from collections import defaultdict

DLL       = 500.0
RAMP_GATE = 800.0
FLOOR     = -2000.0
EVAL_TGT  = 3000.0


def build_day_trades(eval_mode):
    """Returns {date: [(strat, e_t, x_t, pnl_1c)]}. eval_mode: morning 2R, no Asia."""
    config.EVAL_MODE = eval_mode
    bars = load_csv(pp.DATA)
    morning = []
    for y in pp.YEARS:
        morning.extend(pp.run_year_morning(bars, y))
    del bars
    rth = pp.load_days(pp.DATA, 9, 16)
    eve = pp.load_days(pp.DATA, 16, 21) if not eval_mode else {}

    dt = defaultdict(list)
    for t in morning:
        d = date.fromisoformat(t["date"])
        e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
        x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
        c = max(1, t.get("contracts", 1))
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
    return dt


def daily_pnl_series(day_trades, ramp):
    """One pass: daily P&L under v11 rules given a lifetime offset is 0 at series
    start — NOTE: ramp interacts with start point, so for ramp=True we must
    re-simulate per start. For ramp=False the daily series is start-independent."""
    lifetime = 0.0
    out = []
    for d in sorted(day_trades):
        pnl_day = 0.0
        open_until = None
        morning_traded = any(s == "ORB" for s, _, _, _ in day_trades[d])
        for strat, e_t, x_t, pnl in day_trades[d]:
            if strat == "REJ" and morning_traded:
                continue
            if strat in ("REJ", "PM", "ASIA") and ramp and lifetime < RAMP_GATE:
                continue
            if open_until is not None and e_t < open_until:
                continue
            if pnl_day <= -DLL:
                continue
            pnl_day += pnl
            lifetime += pnl
            open_until = x_t
        out.append((d, pnl_day))
    return out


def sim_from(day_trades_sorted_dates, day_trades, start_idx, ramp, target=None):
    """Simulate fresh account from start index. Returns (outcome, days_used):
       outcome: 'pass' (hit target), 'die' (hit floor), 'end' (data ended)."""
    lifetime = 0.0
    eq = 0.0
    n = 0
    for d in day_trades_sorted_dates[start_idx:]:
        pnl_day = 0.0
        open_until = None
        lst = day_trades[d]
        morning_traded = any(s == "ORB" for s, _, _, _ in lst)
        for strat, e_t, x_t, pnl in lst:
            if strat == "REJ" and morning_traded:
                continue
            if strat in ("REJ", "PM", "ASIA") and ramp and lifetime < RAMP_GATE:
                continue
            if open_until is not None and e_t < open_until:
                continue
            if pnl_day <= -DLL:
                continue
            pnl_day += pnl
            lifetime += pnl
            open_until = x_t
        eq += pnl_day
        n += 1
        if eq <= FLOOR:
            return "die", n
        if target is not None and eq >= target:
            return "pass", n
    return "end", n


def rolling(day_trades, ramp, target, label):
    dates = sorted(day_trades)
    res = defaultdict(list)          # start_year -> outcomes
    days_used = []
    for i in range(len(dates)):
        out, n = sim_from(dates, day_trades, i, ramp, target)
        res[dates[i].year].append(out)
        if out == "pass":
            days_used.append(n)
    print(f"\n  ── {label} ──")
    tot = {"pass": 0, "die": 0, "end": 0}
    for y in sorted(res):
        outs = res[y]
        p = outs.count("pass"); d_ = outs.count("die"); e = outs.count("end")
        tot["pass"] += p; tot["die"] += d_; tot["end"] += e
        resolved = p + d_
        rate = f"{p/resolved:.0%}" if resolved else "  n/a"
        print(f"  {y}: starts={len(outs):>4}  pass={p:>4}  die={d_:>4}  unresolved={e:>3}  "
              f"pass-rate(resolved)={rate}")
    resolved = tot["pass"] + tot["die"]
    print(f"  ALL: starts={sum(len(v) for v in res.values()):>4}  pass={tot['pass']:>4}  "
          f"die={tot['die']:>4}  unresolved={tot['end']:>3}  "
          f"pass-rate={tot['pass']/resolved:.0%}" if resolved else "")
    if days_used:
        days_used.sort()
        med = days_used[len(days_used)//2]
        print(f"  median trading days to outcome (passes): {med}")
    return tot


if __name__ == "__main__":
    print("Building EVAL trade set (morning 2R 1c, no Asia)...", flush=True)
    dt_eval = build_day_trades(eval_mode=True)
    print("Building FUNDED trade set (morning 3R, strict 1c)...", flush=True)
    dt_fund = build_day_trades(eval_mode=False)

    print(f"\n{'═'*92}")
    print(f"  ROLLING-START ACCOUNT SURVIVAL — every trading day 2022-2026 as a fresh start")
    print(f"  Floor -$2,000 | eval target +$3,000 | v11 rules (DLL 500, REJ conditioning, 1 pos)")
    print(f"{'═'*92}")

    # EVAL stage: reach +3k before -2k (ramp irrelevant question — test both)
    e_on  = rolling(dt_eval, ramp=True,  target=EVAL_TGT, label="EVAL stage, RampMode ON")
    e_off = rolling(dt_eval, ramp=False, target=EVAL_TGT, label="EVAL stage, RampMode OFF")

    # FUNDED stage: survive to end of data (no target — 'end' counts as survived,
    # but report separately those with <120 trading days of data as censored)
    print(f"\n  (FUNDED stage: 'pass' impossible — survival = 'end' reached without dying)")
    f_on  = rolling(dt_fund, ramp=True,  target=None, label="FUNDED stage, RampMode ON")
    f_off = rolling(dt_fund, ramp=False, target=None, label="FUNDED stage, RampMode OFF")

    # Headline math
    print(f"\n{'═'*92}")
    print(f"  HEADLINE (RampMode ON):")
    ev_res = e_on["pass"] + e_on["die"]
    p_eval = e_on["pass"] / ev_res if ev_res else 0
    fu_res = f_on["end"] + f_on["die"]      # end = survived to data end
    p_fund = f_on["end"] / fu_res if fu_res else 0
    p_all  = p_eval * p_fund
    print(f"  P(pass eval)          ≈ {p_eval:.0%}")
    print(f"  P(funded survives)    ≈ {p_fund:.0%}   (to data end; long-run floor risk decays with cushion)")
    print(f"  P(end-to-end)         ≈ {p_all:.0%}")
    if p_all > 0:
        print(f"  Expected attempts per surviving funded account: {1/p_all:.1f}")
    print(f"{'═'*92}")
