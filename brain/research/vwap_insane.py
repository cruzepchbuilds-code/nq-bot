"""
brain/research/vwap_insane.py

Two outside-the-box shots for the VWAP account:

  A. COUNTER-CROSS REJECTION LEG — first cross >= 11:00 is counter-trend
     (currently skipped); if it FAILS (re-cross back to trend side), enter
     WITH trend. The E4 pattern harvested from crosses we already watch.
     Adds a leg on days the account currently sits idle.

  B. MICRO-LADDER SIZING (the insane one) — replace binary 1-NQ sizing with
     MNQ micros sized as a fixed fraction of remaining floor-room:
        n_micros = clamp(risk_frac * room / (stop_pts * $2), 2, 50)
     Start ~4 micros ($80 risk on a fresh $2k room), compound up with cushion.
     Simulated under REAL Lucid Direct rules (EOD trailing->lock at start,
     20% consistency = 5x best day for payouts, 5-day min, keep $1k buffer).
     Costs: MNQ $1.75/contract RT (≈17% worse relative friction than NQ,
     modeled honestly). Compare vs fixed 1-NQ on the SAME signal stream.

Signals: VWAP v10-final (asym exit), point-based, 2024+ fresh starts.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vwap_fulldata import load_days, DATA
from datetime import time
from collections import defaultdict

IS_Y, OOS_Y, ALL_Y = (2022, 2023, 2024), (2025, 2026), (2022, 2023, 2024, 2025, 2026)
T13, T1555 = time(13, 0), time(15, 55)


def run_day_pts(bars, counter_leg=False):
    """v10-final signals returning POINT results: [(tag, pts, stop_pts)].
       counter_leg: also trade failed counter-trend first-crosses (with trend)."""
    sum_pv = sum_vol = 0.0
    vwap = open930 = trend = None
    was_ext = False
    prev_above = None
    saw_counter = False           # first cross was counter-trend
    counter_up = None
    trades = []
    entry = sl = tp = None
    is_long = None
    tag = "main"
    done = False

    for b in bars:
        t = b["t"]
        if t < time(9, 30) or t >= T1555:
            continue
        if open930 is None and t < time(9, 31):
            open930 = b["o"]
        sum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        sum_vol += b["v"]
        if sum_vol:
            vwap = sum_pv / sum_vol
        if trend is None and t >= time(11, 0) and open930 and vwap:
            trend = "bull" if b["c"] > open930 else "bear"
        if vwap is None:
            continue
        close = b["c"]
        above = close > vwap

        if entry is not None:
            res = None
            if is_long:
                if b["l"] <= sl:   res = sl - entry
                elif b["h"] >= tp: res = tp - entry
            else:
                if b["h"] >= sl:   res = entry - sl
                elif b["l"] <= tp: res = entry - tp
            if res is None and t >= T13:
                op = (close - entry) if is_long else (entry - close)
                if op <= 0:
                    res = op
            if res is not None:
                trades.append((tag, res, 20.0))
                entry = None
                done = True
            prev_above = above
            continue

        if done or t >= T13:
            prev_above = above
            continue
        if not was_ext and abs(close - vwap) > 35.0:
            was_ext = True
        if was_ext and prev_above is not None and t >= time(11, 0) and trend:
            cu = (not prev_above) and above
            cd = prev_above and (not above)
            if cu or cd:
                aligned = (cu and trend == "bull") or (cd and trend == "bear")
                if aligned and not saw_counter:
                    entry = close
                    is_long = cu
                    sl = entry - 20 if is_long else entry + 20
                    tp = entry + 55 if is_long else entry - 55
                    tag = "main"
                elif not aligned and not saw_counter:
                    saw_counter = True
                    counter_up = cu
                elif saw_counter and counter_leg:
                    # counter-attempt failed: re-cross back to trend side
                    if (counter_up and cd and trend == "bear") or \
                       ((not counter_up) and cu and trend == "bull"):
                        entry = close
                        is_long = cu
                        sl = entry - 20 if is_long else entry + 20
                        tp = entry + 55 if is_long else entry - 55
                        tag = "counter"
        prev_above = above

    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        trades.append((tag, pts, 20.0))
    return trades


def pf(v):
    w = sum(x for x in v if x > 0)
    l = abs(sum(x for x in v if x <= 0))
    return round(w / l, 3) if l else (99.0 if w else 0.0)


if __name__ == "__main__":
    print("Loading...", flush=True)
    days = load_days(DATA)
    dates = [d for d in sorted(days) if d.weekday() != 0 and d.month != 5]

    # ── A: counter-cross rejection leg ────────────────────────────────────────
    print(f"\n{'═'*92}\n  A: COUNTER-CROSS REJECTION LEG (points x $20 - $14.50, 1 NQ)\n{'═'*92}")
    legs = defaultdict(list)
    for d in dates:
        for tag, pts, _ in run_day_pts(days[d], counter_leg=True):
            legs[(tag, "IS" if d.year <= 2024 else "OOS")].append(pts * 20 - 14.50)
    for tag in ("main", "counter"):
        i, o = legs[(tag, "IS")], legs[(tag, "OOS")]
        print(f"  {tag:<9} IS: N={len(i):>3} PF={pf(i):>6} ${sum(i):>+9,.0f} | "
              f"OOS: N={len(o):>3} PF={pf(o):>6} ${sum(o):>+9,.0f}")

    # ── B: micro-ladder under real Lucid rules ────────────────────────────────
    print(f"\n{'═'*92}\n  B: MICRO-LADDER vs FIXED 1-NQ — Lucid Direct real rules, 12-mo, 2024+ starts\n{'═'*92}")
    # per-day point-trades (main leg only — keep sizing test clean)
    day_trades = {d: run_day_pts(days[d]) for d in dates}
    seq = [(d, day_trades[d]) for d in dates]
    START, KEEP = 50_000.0, 1_000.0
    MNQ_PT, MNQ_COST = 2.0, 1.75
    NQ_PT, NQ_COST = 20.0, 14.50

    def run_account(i0, mode, risk_frac=0.08, horizon=252):
        bal, peak, floor = START, START, START - 2000.0
        tp_, best, wd, last_pay = 0.0, 0.0, 0.0, -10
        first_pay = None
        max_n = 0
        for k in range(i0, min(i0 + horizon, len(seq))):
            _, trades = seq[k]
            day = 0.0
            for tag, pts, stop_pts in trades:
                room = bal + day - floor
                if room <= 0:
                    break
                if mode == "nq":
                    day += pts * NQ_PT - NQ_COST
                else:
                    n = int(risk_frac * room / (stop_pts * MNQ_PT + MNQ_COST))
                    n = max(2, min(50, n))
                    max_n = max(max_n, n)
                    day += n * (pts * MNQ_PT) - n * MNQ_COST
            bal += day
            tp_ += day
            best = max(best, day)
            if bal <= floor:
                return wd, True, first_pay, max_n, bal - START
            if tp_ >= 5 * best and tp_ > 0 and k - i0 - (last_pay if last_pay > 0 else 0) >= 5 and k - i0 >= 5:
                avail = bal - (START + KEEP)
                if avail > 0:
                    bal -= avail
                    wd += avail
                    last_pay = k - i0
                    if first_pay is None:
                        first_pay = k - i0 + 1
            peak = max(peak, bal)
            floor = max(floor, min(peak - 2000.0, START))
        return wd, False, first_pay, max_n, bal - START

    idx24 = [i for i, (d, _) in enumerate(seq) if d.year >= 2024 and len(seq) - i >= 60]
    for mode, rf, label in [("nq", 0, "fixed 1 NQ"),
                            ("micro", 0.05, "ladder 5% of room"),
                            ("micro", 0.08, "ladder 8%"),
                            ("micro", 0.12, "ladder 12%"),
                            ("micro", 0.20, "ladder 20%")]:
        res = [run_account(i, mode, rf) for i in idx24]
        n = len(res)
        died = sum(1 for r in res if r[1])
        wd_all = sorted(r[0] for r in res)
        paid = sum(1 for r in res if r[0] > 0)
        fps = sorted(r[2] for r in res if r[2])
        mx = max(r[3] for r in res)
        tot = sorted(r[0] + max(r[4], -2000) for r in res)  # extracted + resid
        print(f"  {label:<20} died={died/n:>4.0%}  paid={paid/n:>4.0%}  "
              f"wd p50=${wd_all[n//2]:>7,.0f}  p90=${wd_all[int(n*.9)]:>8,.0f}  "
              f"1stPay med={fps[len(fps)//2] if fps else -1:>3} td  maxMicros={mx}")

    print(f"\n{'═'*92}\n  vwap_insane done.\n{'═'*92}")
