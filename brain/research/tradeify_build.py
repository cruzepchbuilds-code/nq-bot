"""
brain/research/tradeify_build.py

Optimal build for Tradeify 50K funded (daily-payout) accounts.

Account rules (from pricing page; lock behavior needs verification):
  DAILY path: DLL $1,000 | trailing max DD $2,000 EOD | payout daily, cap $1,000
  FLEX  path: no DLL     | trailing max DD $2,000 EOD | payout /5 days, cap $3,000
  Start balance 50,000. Payouts only from balance > start (assumed).

Two trailing interpretations simulated:
  LOCK:  floor = min(EOD-HWM - 2000, start)  → floor stops rising at breakeven
  PURE:  floor = EOD-HWM - 2000              → floor trails forever (harsher)
  (HWM assumed on EOD balance AFTER withdrawals — conservative.)

Withdrawal policy: each EOD, withdraw min(cap, bal - max(start, floor + BUFFER)).
BUFFER grid: how much cushion above the floor to keep before sweeping.

Stacks:
  S1 = v11.2 (ORB + REJ + PM + ASIA, strict 1c, all v11 rules)
  S2 = v11.2 + VWAP reclaim (asym exit) merged on the SAME account —
       chronological one-position arbitration resolves REJ/reclaim collisions.

Metric: total withdrawn per start, deaths, time to first payout.
Fresh starts: every trading day, split 2024+ (current regime) vs all.

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
from vwap_fulldata import load_days as vwap_load
from backtest import load_csv
from datetime import date, datetime, time
from collections import defaultdict

DLL_INTERNAL = 500.0
START = 50_000.0
TRAIL = 2_000.0


# ── VWAP reclaim (v10-final asym) day sim emitting entry/exit times ──────────

def reclaim_day(bars):
    """v10-final: 20/2.75R, ext35, lock 11:00, entries 11-13, asym 13:00 exit.
       Returns (entry_t, exit_t, pnl) or None."""
    sum_pv = sum_vol = 0.0
    vwap = open930 = trend = None
    was_ext = False
    prev_above = None
    entry = sl = tp = e_t = None
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
        if trend is None and t >= time(11, 0) and open930 and vwap:
            trend = "bull" if b["c"] > open930 else "bear"
        if vwap is None or t < time(10, 0):
            if vwap:
                prev_above = b["c"] > vwap
            continue
        close = b["c"]
        cur_above = close > vwap
        if entry is not None:
            res = None
            if is_long:
                if b["l"] <= sl:   res = sl - entry
                elif b["h"] >= tp: res = tp - entry
            else:
                if b["h"] >= sl:   res = entry - sl
                elif b["l"] <= tp: res = entry - tp
            if res is None and t >= time(13, 0):
                op = (close - entry) if is_long else (entry - close)
                if op <= 0:
                    res = op
            if res is not None:
                return (e_t, t, res * 20.0 - 14.50)
            prev_above = cur_above
            continue
        if t >= time(13, 0):
            return None
        if not was_ext and abs(close - vwap) > 35.0:
            was_ext = True
        if was_ext and prev_above is not None and t >= time(11, 0):
            cu = (not prev_above) and cur_above
            cd = prev_above and (not cur_above)
            if cu and trend == "bull":
                entry, is_long, e_t = close, True, t
                sl, tp = close - 20.0, close + 55.0
            elif cd and trend == "bear":
                entry, is_long, e_t = close, False, t
                sl, tp = close + 20.0, close - 55.0
        prev_above = cur_above
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, time(15, 55), pts * 20.0 - 14.50)
    return None


def build_stacks():
    bars = load_csv(pp.DATA)
    morning = []
    for y in pp.YEARS:
        morning.extend(pp.run_year_morning(bars, y))
    del bars
    rth = vwap_load(pp.DATA)
    eve = pp.load_days(pp.DATA, 16, 21)

    s1 = defaultdict(list)
    for t in morning:
        d = date.fromisoformat(t["date"])
        e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
        x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
        c = max(1, t.get("contracts", 1))
        s1[d].append(("ORB", e, x, t["pnl"] / c))
    for d in sorted(rth):
        wd, mo = d.weekday(), d.month
        if wd != 0 and mo not in (4, 5, 6, 9, 12):
            r = pp.rejection_day(rth[d])
            if r: s1[d].append(("REJ", *r))
        if wd not in (0, 4):
            r = pp.pm_day(rth[d])
            if r: s1[d].append(("PM", *r))
    for d in sorted(eve):
        if d.weekday() != 3 and d.month not in (8, 11):
            r = pp.asia_day(eve[d])
            if r: s1[d].append(("ASIA", *r))

    s2 = defaultdict(list, {d: list(v) for d, v in s1.items()})
    for d in sorted(rth):
        if d.weekday() != 0 and d.month != 5:
            r = reclaim_day(rth[d])
            if r: s2[d].append(("VWAP", *r))

    for dd in (s1, s2):
        for d in dd:
            dd[d].sort(key=lambda x: x[1])
    return s1, s2


def daily_pnl(day_trades):
    """v11.2 rules → {date: pnl}. PM gated on morning loss; REJ on no-morning;
       one position at a time; internal DLL 500."""
    out = {}
    for d in sorted(day_trades):
        pnl_day = 0.0
        morning_pnl = 0.0
        morning_traded = any(s == "ORB" for s, _, _, _ in day_trades[d])
        open_until = None
        for strat, e_t, x_t, pnl in day_trades[d]:
            if strat == "REJ" and morning_traded:
                continue
            if strat == "PM" and morning_traded and morning_pnl < 0:
                continue
            if open_until is not None and e_t < open_until:
                continue
            if pnl_day <= -DLL_INTERNAL:
                continue
            pnl_day += pnl
            if strat == "ORB":
                morning_pnl += pnl
            open_until = x_t
        out[d] = pnl_day
    return out


def run_account(pnl_seq, path="daily", lock=True, buffer=1000.0):
    """pnl_seq: [(date, pnl)] from a fresh start. Returns (withdrawn, died, days_to_first_payout)."""
    bal = START
    hwm = START
    floor = START - TRAIL
    withdrawn = 0.0
    first_pay = None
    cap_daily = 1000.0
    flex_acc = 0       # day counter for flex 5-day cycle
    for i, (d, p) in enumerate(pnl_seq):
        bal += p
        # EOD death check BEFORE withdrawal
        if bal <= floor:
            return withdrawn, True, first_pay
        # withdrawal
        can_pay = (path == "daily") or (flex_acc % 5 == 4)
        cap = cap_daily if path == "daily" else 3000.0
        if can_pay:
            avail = bal - max(START, floor + buffer)
            w = min(cap, avail)
            if w > 0:
                bal -= w
                withdrawn += w
                if first_pay is None:
                    first_pay = i + 1
        flex_acc += 1
        # EOD trailing update (post-withdrawal balance, conservative)
        hwm = max(hwm, bal)
        f = hwm - TRAIL
        if lock:
            f = min(f, START)
        floor = max(floor, f)
    return withdrawn, False, first_pay


def evaluate(daily, label):
    days = sorted(daily)
    seq = [(d, daily[d]) for d in days]
    print(f"\n  ── {label} ──")
    print(f"  {'path':<7}{'rule':<6}{'buffer':>7} | {'2024+ starts':^36} | {'all starts':^28}")
    print(f"  {'':<20} | {'wdrawn/st':>10} {'death%':>7} {'1stPay':>7} {'$/mo':>8} | {'wdrawn/st':>10} {'death%':>7}")
    print(f"  {'─'*100}")
    for path in ["daily", "flex"]:
        for lock in [True, False]:
            for buf in [600, 1000, 1500]:
                res_recent, res_all = [], []
                for i, (d0, _) in enumerate(seq):
                    r = run_account(seq[i:], path=path, lock=lock, buffer=buf)
                    res_all.append(r)
                    if d0.year >= 2024:
                        res_recent.append((r, len(seq) - i))
                wr = [r for r, _ in res_recent]
                w_avg = sum(x[0] for x in wr) / len(wr)
                deaths = sum(1 for x in wr if x[1]) / len(wr)
                fps = [x[2] for x in wr if x[2]]
                fp = sorted(fps)[len(fps)//2] if fps else -1
                # $/mo: withdrawn per start / months available from that start
                permo = []
                for (w, died, _), ndays in res_recent:
                    months = max(1, ndays / 21)
                    permo.append(w / months)
                pm = sum(permo) / len(permo)
                wa = sum(x[0] for x in res_all) / len(res_all)
                da = sum(1 for x in res_all if x[1]) / len(res_all)
                print(f"  {path:<7}{'lock' if lock else 'pure':<6}{buf:>7,} | "
                      f"{w_avg:>10,.0f} {deaths:>6.0%} {fp:>7} {pm:>8,.0f} | "
                      f"{wa:>10,.0f} {da:>6.0%}")


if __name__ == "__main__":
    print("Building stacks (morning engine + all day sims)...", flush=True)
    s1, s2 = build_stacks()

    d1 = daily_pnl(s1)
    d2 = daily_pnl(s2)

    n1 = sum(v != 0 for v in d1.values())
    n2 = sum(v != 0 for v in d2.values())
    print(f"  S1 v11.2:        {sum(d1.values()):>+10,.0f} over {len(d1)} days ({n1} active)")
    print(f"  S2 +VWAP merged: {sum(d2.values()):>+10,.0f} over {len(d2)} days ({n2} active)")

    # worst day check vs Tradeify DLL $1,000 (daily path)
    w1, w2 = min(d1.values()), min(d2.values())
    print(f"  worst day: S1 ${w1:+,.0f}   S2 ${w2:+,.0f}   (Tradeify Daily DLL = $1,000)")

    print(f"\n{'═'*104}")
    print(f"  TRADEIFY 50K FUNDED — withdrawal-policy grid (start $50k, trailing $2k EOD)")
    print(f"{'═'*104}")
    evaluate(d1, "S1: v11.2 stack")
    evaluate(d2, "S2: v11.2 + VWAP reclaim merged (one account)")

    print(f"\n{'═'*104}\n  tradeify_build done.\n{'═'*104}")
