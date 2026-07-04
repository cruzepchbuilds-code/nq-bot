"""
brain/research/vwap_fulldata.py

VWAP Reclaim (CruzCapitalVWAP v5) — FIRST test on 2022-2023 data.
Everything so far used nq_1min.csv (2024+). nq_full.csv covers 2022-2026.

v5 config: stop 20 / RR 3.0 (60pt) / extend 25 / track from 10:00 /
entry 11:00-13:00 / trend-aligned (10:30 lock vs 9:30 open) / max 1/day /
skip Mon + weak months {4,5,6,9,12}.

Sections:
  S1. v5 baseline year-by-year 2022-2026 (does the edge exist pre-2024?)
  S2. Month table on full data (is the weak-month set right for 2022-23?)
  S3. Sweeps on full data, IS 2022-24 / OOS 2025-26:
      trend-lock time {10:00, 10:30, 11:00}
      stop {15, 20, 25} x RR {2.5, 3.0, 3.5}
      extend {15, 25, 35}
      window end {12:30, 13:00}
  S4. Re-entry: allow a second reclaim trade after the first hits TARGET
"""

import csv, os
from datetime import datetime, time
from collections import defaultdict
from itertools import product

NQ_PT, COST = 20.0, 14.50
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data", "nq_full.csv")
WEAK = {4, 5, 6, 9, 12}


def load_days(path):
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


def run_day(bars, stop=20.0, rr=3.0, extend=25.0, lock=time(10, 30),
            entry_start=time(11, 0), window_end=time(13, 0), max_trades=1):
    """v5 reclaim with sweepable knobs. Returns list of pnl."""
    sum_pv = sum_vol = 0.0
    vwap = open930 = trend = None
    was_ext = False
    prev_above = None
    trades = []
    n_trades = 0
    entry = sl = tp = None
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
        if trend is None and t >= lock and open930 and vwap:
            trend = "bull" if b["c"] > open930 else "bear"
        if vwap is None or t < time(10, 0):
            if vwap:
                prev_above = b["c"] > vwap
            continue

        close = b["c"]
        cur_above = close > vwap

        if entry is not None:
            done = None
            if is_long:
                if b["l"] <= sl:   done = (sl - entry)
                elif b["h"] >= tp: done = (tp - entry)
            else:
                if b["h"] >= sl:   done = (entry - sl)
                elif b["l"] <= tp: done = (entry - tp)
            if done is None and t >= window_end:
                done = (close - entry) if is_long else (entry - close)
            if done is not None:
                hit_target = (done > 0 and abs(done) >= stop * rr - 0.01)
                trades.append(done * NQ_PT - COST)
                entry = None
                # re-arm only after a target hit (for max_trades=2 test)
                if not hit_target:
                    n_trades = max_trades
            prev_above = cur_above
            continue

        if t >= window_end or n_trades >= max_trades:
            prev_above = cur_above
            continue
        if not was_ext and abs(close - vwap) > extend:
            was_ext = True
        if was_ext and prev_above is not None and t >= entry_start:
            cu = (not prev_above) and cur_above
            cd = prev_above and (not cur_above)
            if cu and trend == "bull":
                entry, is_long = close, True
                sl, tp = close - stop, close + stop * rr
                n_trades += 1; was_ext = False
            elif cd and trend == "bear":
                entry, is_long = close, False
                sl, tp = close + stop, close - stop * rr
                n_trades += 1; was_ext = False
        prev_above = cur_above

    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        trades.append(pts * NQ_PT - COST)
    return trades


def run_period(days, years, months_skip=WEAK, **kw):
    out = []
    for d in sorted(days):
        if d.year not in years:
            continue
        if d.weekday() == 0 or d.month in months_skip:
            continue
        for p in run_day(days[d], **kw):
            out.append((d, p))
    return out


def stats(rows):
    if not rows:
        return {"n": 0, "wr": 0, "pf": 0, "net": 0}
    pnls = [p for _, p in rows]
    w = [p for p in pnls if p > 0]
    gl = abs(sum(p for p in pnls if p <= 0))
    return {"n": len(pnls), "wr": len(w) / len(pnls),
            "pf": round(sum(w) / gl, 3) if gl else 99.0, "net": round(sum(pnls))}


def line(tag, s_is, s_oos, mark=""):
    print(f"  {tag:<34} IS: N={s_is['n']:>3} WR={s_is['wr']:>4.0%} PF={s_is['pf']:>6.3f} "
          f"${s_is['net']:>+8,}  | OOS: N={s_oos['n']:>3} WR={s_oos['wr']:>4.0%} "
          f"PF={s_oos['pf']:>6.3f} ${s_oos['net']:>+8,}{mark}")


if __name__ == "__main__":
    print("Loading nq_full RTH bars...", flush=True)
    days = load_days(DATA)
    print(f"  {len(days)} days\n")

    IS_Y, OOS_Y = (2022, 2023, 2024), (2025, 2026)

    # S1: baseline year-by-year
    print(f"{'═'*96}")
    print(f"  S1: v5 BASELINE on full data (stop 20 / 3R / ext 25 / lock 10:30 / 11-13h)")
    print(f"{'═'*96}")
    for y in [2022, 2023, 2024, 2025, 2026]:
        s = stats(run_period(days, (y,)))
        print(f"  {y}:  N={s['n']:>3}  WR={s['wr']:>4.0%}  PF={s['pf']:>6.3f}  Net=${s['net']:>+8,}")
    s_is  = stats(run_period(days, IS_Y))
    s_oos = stats(run_period(days, OOS_Y))
    line("v5 aggregate", s_is, s_oos, "  ← baseline")
    base_oos_net = s_oos["net"]

    # S2: month table (no month skip) — validate WEAK set on 2022-26
    print(f"\n{'═'*96}")
    print(f"  S2: MONTH TABLE, full data, month-skip disabled")
    print(f"{'═'*96}")
    all_rows = run_period(days, (2022, 2023, 2024, 2025, 2026), months_skip=set())
    mn = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for m in range(1, 13):
        rows = [r for r in all_rows if r[0].month == m]
        s = stats(rows)
        flag = "  [skipped in v5]" if m in WEAK else ""
        print(f"  {mn[m]}:  N={s['n']:>3}  WR={s['wr']:>4.0%}  PF={s['pf']:>6.3f}  "
              f"Net=${s['net']:>+8,}{flag}")

    # S3: sweeps
    print(f"\n{'═'*96}")
    print(f"  S3: SWEEPS on full data (IS 2022-24 / OOS 2025-26)")
    print(f"{'═'*96}")

    print(f"\n  Trend-lock time:")
    for lk in [time(10, 0), time(10, 30), time(11, 0)]:
        s_i = stats(run_period(days, IS_Y, lock=lk))
        s_o = stats(run_period(days, OOS_Y, lock=lk))
        line(f"  lock {lk.strftime('%H:%M')}", s_i, s_o,
             "  ← v5" if lk == time(10, 30) else "")

    print(f"\n  Stop x RR:")
    for st, rr in product([15, 20, 25], [2.5, 3.0, 3.5]):
        s_i = stats(run_period(days, IS_Y, stop=st, rr=rr))
        s_o = stats(run_period(days, OOS_Y, stop=st, rr=rr))
        line(f"  stop={st} rr={rr}", s_i, s_o,
             "  ← v5" if (st, rr) == (20, 3.0) else "")

    print(f"\n  Extension threshold:")
    for ex in [15, 25, 35]:
        s_i = stats(run_period(days, IS_Y, extend=ex))
        s_o = stats(run_period(days, OOS_Y, extend=ex))
        line(f"  extend={ex}", s_i, s_o, "  ← v5" if ex == 25 else "")

    print(f"\n  Window end:")
    for we in [time(12, 30), time(13, 0)]:
        s_i = stats(run_period(days, IS_Y, window_end=we))
        s_o = stats(run_period(days, OOS_Y, window_end=we))
        line(f"  end {we.strftime('%H:%M')}", s_i, s_o,
             "  ← v5" if we == time(13, 0) else "")

    # S4: re-entry after target
    print(f"\n{'═'*96}")
    print(f"  S4: RE-ENTRY — second reclaim allowed after first hits target")
    print(f"{'═'*96}")
    for mt in [1, 2]:
        s_i = stats(run_period(days, IS_Y, max_trades=mt))
        s_o = stats(run_period(days, OOS_Y, max_trades=mt))
        line(f"  max_trades={mt}", s_i, s_o, "  ← v5" if mt == 1 else "")

    print(f"\n{'═'*96}\n  vwap_fulldata done.\n{'═'*96}")
