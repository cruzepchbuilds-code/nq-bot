"""
brain/research/vwap_long_only.py

Long-only VWAP reclaim investigation (user's Analyzer: long PF 2.25 / short 0.93).

  A. Direction x YEAR at v7 — is the short decay monotone (regime) or noise?
  B. Short-rescue variant: shorts need DOUBLE trend confirm (close<9:30-open AND
     close<VWAP at 11:00 lock) — can a stricter gate save them?
  C. Long-only parameter re-tune: stop x RR, extension, lock, window end
  D. Long-only month table (both halves) — derive minimal skip set
  E. Long-only DOW
  F. Final config: year-by-year + full account card (monthly, deaths, payout)

IS 2022-24 / OOS 2025-26, strict 1c, costs $14.50/trade.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vwap_fulldata import load_days, DATA
from datetime import time
from collections import defaultdict
from itertools import product

NQ_PT, COST = 20.0, 14.50
IS_Y, OOS_Y = (2022, 2023, 2024), (2025, 2026)
ALL_Y = (2022, 2023, 2024, 2025, 2026)


def run_day(bars, stop=20.0, rr=2.75, extend=35.0, lock=time(11, 0),
            window_end=time(13, 0), longs=True, shorts=True,
            short_double=False):
    sum_pv = sum_vol = 0.0
    vwap = open930 = None
    trend = None            # "bull"/"bear" via close vs open at lock
    below_vwap_at_lock = None
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
        if trend is None and t >= lock and open930 and vwap:
            trend = "bull" if b["c"] > open930 else "bear"
            below_vwap_at_lock = b["c"] < vwap
        if vwap is None or t < time(10, 0):
            if vwap:
                prev_above = b["c"] > vwap
            continue
        close = b["c"]
        cur_above = close > vwap
        if entry is not None:
            done = None
            if is_long:
                if b["l"] <= sl:   done = sl - entry
                elif b["h"] >= tp: done = tp - entry
            else:
                if b["h"] >= sl:   done = entry - sl
                elif b["l"] <= tp: done = entry - tp
            if done is None and t >= window_end:
                done = (close - entry) if is_long else (entry - close)
            if done is not None:
                return (e_t, "long" if is_long else "short", done * NQ_PT - COST)
            prev_above = cur_above
            continue
        if t >= window_end:
            return None
        if not was_ext and abs(close - vwap) > extend:
            was_ext = True
        if was_ext and prev_above is not None and t >= time(11, 0):
            cu = (not prev_above) and cur_above
            cd = prev_above and (not cur_above)
            ok_long  = longs  and cu and trend == "bull"
            ok_short = shorts and cd and trend == "bear"
            if ok_short and short_double and not below_vwap_at_lock:
                ok_short = False
            if ok_long:
                entry, is_long, e_t = close, True, t
                sl, tp = close - stop, close + stop * rr
            elif ok_short:
                entry, is_long, e_t = close, False, t
                sl, tp = close + stop, close - stop * rr
        prev_above = cur_above
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, "long" if is_long else "short", pts * NQ_PT - COST)
    return None


def collect(days, years, skip_months=(5,), **kw):
    out = []
    for d in sorted(days):
        if d.year not in years or d.weekday() == 0 or d.month in skip_months:
            continue
        r = run_day(days[d], **kw)
        if r:
            out.append((d, *r))
    return out


def stats(rows):
    if not rows:
        return {"n": 0, "wr": 0, "pf": 0, "net": 0}
    pnls = [r[-1] for r in rows]
    w = [p for p in pnls if p > 0]
    gl = abs(sum(p for p in pnls if p <= 0))
    return {"n": len(pnls), "wr": len(w) / len(pnls),
            "pf": round(sum(w) / gl, 3) if gl else 99.0, "net": round(sum(pnls))}


def duo(tag, s_i, s_o, mark=""):
    print(f"  {tag:<36} IS: N={s_i['n']:>3} WR={s_i['wr']:>4.0%} PF={s_i['pf']:>6.3f} "
          f"${s_i['net']:>+8,} | OOS: N={s_o['n']:>3} WR={s_o['wr']:>4.0%} "
          f"PF={s_o['pf']:>6.3f} ${s_o['net']:>+8,}{mark}")


if __name__ == "__main__":
    print("Loading...", flush=True)
    days = load_days(DATA)

    # A: direction x year
    print(f"\n{'═'*94}\n  A: DIRECTION x YEAR (v7 params)\n{'═'*94}")
    rows = collect(days, ALL_Y)
    print(f"    {'Year':<6}{'Long N':>7}{'L-PF':>8}{'L-Net':>10}   {'Short N':>8}{'S-PF':>8}{'S-Net':>10}")
    for y in ALL_Y:
        L = stats([r for r in rows if r[0].year == y and r[2] == "long"])
        S = stats([r for r in rows if r[0].year == y and r[2] == "short"])
        print(f"    {y:<6}{L['n']:>7}{L['pf']:>8.3f}{L['net']:>+10,}   "
              f"{S['n']:>8}{S['pf']:>8.3f}{S['net']:>+10,}")

    # B: short rescue
    print(f"\n{'═'*94}\n  B: SHORT RESCUE — double trend confirm (close<open AND close<VWAP at lock)\n{'═'*94}")
    for tag, kw in [("shorts as-is", dict(longs=False)),
                    ("shorts double-confirm", dict(longs=False, short_double=True))]:
        duo(tag, stats(collect(days, IS_Y, **kw)), stats(collect(days, OOS_Y, **kw)))

    # C: long-only re-tune
    print(f"\n{'═'*94}\n  C: LONG-ONLY RE-TUNE\n{'═'*94}")
    print("  stop x RR:")
    for st, rr in product([18, 20, 22], [2.5, 2.75, 3.0, 3.25]):
        s_i = stats(collect(days, IS_Y, stop=st, rr=rr, shorts=False))
        s_o = stats(collect(days, OOS_Y, stop=st, rr=rr, shorts=False))
        duo(f"  stop={st} rr={rr}", s_i, s_o,
            "  ← v7 params" if (st, rr) == (20, 2.75) else "")
    print("\n  extension:")
    for ex in [25, 35, 45]:
        s_i = stats(collect(days, IS_Y, extend=ex, shorts=False))
        s_o = stats(collect(days, OOS_Y, extend=ex, shorts=False))
        duo(f"  ext={ex}", s_i, s_o, "  ← v7" if ex == 35 else "")
    print("\n  trend lock:")
    for lk in [time(10, 30), time(11, 0)]:
        s_i = stats(collect(days, IS_Y, lock=lk, shorts=False))
        s_o = stats(collect(days, OOS_Y, lock=lk, shorts=False))
        duo(f"  lock {lk.strftime('%H:%M')}", s_i, s_o, "  ← v7" if lk == time(11, 0) else "")
    print("\n  window end:")
    for we in [time(12, 30), time(13, 0), time(13, 30)]:
        s_i = stats(collect(days, IS_Y, window_end=we, shorts=False))
        s_o = stats(collect(days, OOS_Y, window_end=we, shorts=False))
        duo(f"  end {we.strftime('%H:%M')}", s_i, s_o, "  ← v7" if we == time(13, 0) else "")

    # D: long-only month table (no skip) split by halves
    print(f"\n{'═'*94}\n  D: LONG-ONLY MONTH TABLE (skip disabled; 22-24 | 25-26)\n{'═'*94}")
    rows_a = collect(days, IS_Y,  skip_months=(), shorts=False)
    rows_b = collect(days, OOS_Y, skip_months=(), shorts=False)
    mn = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for m in range(1, 13):
        A = stats([r for r in rows_a if r[0].month == m])
        B = stats([r for r in rows_b if r[0].month == m])
        both_neg = "  ← NEG BOTH" if (A["net"] < 0 and B["net"] < 0 and A["n"] + B["n"] >= 15) else ""
        print(f"    {mn[m]}:  22-24 N={A['n']:>3} PF={A['pf']:>6.3f} ${A['net']:>+7,} | "
              f"25-26 N={B['n']:>3} PF={B['pf']:>6.3f} ${B['net']:>+7,}{both_neg}")

    # E: long-only DOW
    print(f"\n{'═'*94}\n  E: LONG-ONLY DOW (full data, Mon included for test)\n{'═'*94}")
    rows_d = []
    for d in sorted(days):
        if d.month == 5:
            continue
        r = run_day(days[d], shorts=False)
        if r:
            rows_d.append((d, *r))
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    for wd in range(5):
        s = stats([r for r in rows_d if r[0].weekday() == wd])
        print(f"    {names[wd]}:  N={s['n']:>3}  WR={s['wr']:>4.0%}  PF={s['pf']:>6.3f}  ${s['net']:>+8,}"
              + ("  [currently skipped]" if wd == 0 else ""))

    print(f"\n{'═'*94}\n  vwap_long_only done — pick config, then run account card manually.\n{'═'*94}")
