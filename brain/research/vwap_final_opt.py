"""
brain/research/vwap_final_opt.py

VWAP Reclaim v6 — deployment-grade reoptimization + account survival analysis.
(v6 = stop 20 / 3R / ext 35 / lock 11:00 / entry 11-13h / skip Mon+May)

  S1. DOW table with v6 params on FULL data — the Monday skip came from
      2024-only data (v3); re-check. Friday already confirmed good.
  S2. Long vs short asymmetry (full data, v6)
  S3. Entry-hour buckets 11-12 vs 12-13 (full data, v6)
  S4. Stop micro-tune {18,20,22} x RR {2.75,3.0,3.25} at v6 settings
  S5. Alternative trend definition: price-vs-VWAP at 11:00 (vs close-vs-open)
  S6. ACCOUNT ANALYSIS (chosen config, 1c, own prefunded 50K):
      worst day / losing streaks / monthly distribution,
      fresh-start floor test (min equity ≤ -$2,000) from every trading day,
      payout timing: days to +$1k / +$2k / +$3k.
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


def run_day(bars, stop=20.0, rr=3.0, extend=35.0, lock=time(11, 0),
            entry_start=time(11, 0), window_end=time(13, 0),
            trend_mode="open"):
    """v6 sim returning (entry_t, dir, pnl) or None. trend_mode: open|vwap."""
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
        if trend is None and t >= lock and open930 and vwap:
            if trend_mode == "open":
                trend = "bull" if b["c"] > open930 else "bear"
            else:
                trend = "bull" if b["c"] > vwap else "bear"
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
                return (e_t, "long" if is_long else "short",
                        done * NQ_PT - COST)
            prev_above = cur_above
            continue
        if t >= window_end:
            return None
        if not was_ext and abs(close - vwap) > extend:
            was_ext = True
        if was_ext and prev_above is not None and t >= entry_start:
            cu = (not prev_above) and cur_above
            cd = prev_above and (not cur_above)
            if cu and trend == "bull":
                entry, is_long, e_t = close, True, t
                sl, tp = close - stop, close + stop * rr
            elif cd and trend == "bear":
                entry, is_long, e_t = close, False, t
                sl, tp = close + stop, close - stop * rr
        prev_above = cur_above
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, "long" if is_long else "short", pts * NQ_PT - COST)
    return None


def collect(days, years, skip_dows=(0,), skip_months=(5,), **kw):
    out = []
    for d in sorted(days):
        if d.year not in years or d.weekday() in skip_dows or d.month in skip_months:
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
    print(f"  {tag:<32} IS: N={s_i['n']:>3} WR={s_i['wr']:>4.0%} PF={s_i['pf']:>6.3f} "
          f"${s_i['net']:>+8,} | OOS: N={s_o['n']:>3} WR={s_o['wr']:>4.0%} "
          f"PF={s_o['pf']:>6.3f} ${s_o['net']:>+8,}{mark}")


if __name__ == "__main__":
    print("Loading full RTH data...", flush=True)
    days = load_days(DATA)

    # Baseline v6
    b_is  = collect(days, IS_Y)
    b_oos = collect(days, OOS_Y)
    print(f"\n{'═'*92}\n  BASELINE v6\n{'═'*92}")
    duo("v6", stats(b_is), stats(b_oos), "  ← current")

    # S1: DOW — include all days, bucket
    print(f"\n{'═'*92}\n  S1: DAY OF WEEK (v6 params, full data, Mon INCLUDED for the test)\n{'═'*92}")
    all_rows = collect(days, (2022, 2023, 2024, 2025, 2026), skip_dows=())
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    for wd in range(5):
        rows = [r for r in all_rows if r[0].weekday() == wd]
        s = stats(rows)
        flag = "  [skipped in v6]" if wd == 0 else ""
        print(f"    {names[wd]}:  N={s['n']:>3}  WR={s['wr']:>4.0%}  PF={s['pf']:>6.3f}  "
              f"Net=${s['net']:>+8,}{flag}")

    # S2: direction
    print(f"\n{'═'*92}\n  S2: LONG vs SHORT (v6, IS/OOS)\n{'═'*92}")
    for dirn in ["long", "short"]:
        duo(dirn,
            stats([r for r in b_is if r[2] == dirn]),
            stats([r for r in b_oos if r[2] == dirn]))

    # S3: entry hour
    print(f"\n{'═'*92}\n  S3: ENTRY HOUR (v6, IS/OOS)\n{'═'*92}")
    for lo, hi, tag in [(time(11, 0), time(12, 0), "11:00-12:00"),
                        (time(12, 0), time(13, 0), "12:00-13:00")]:
        duo(tag,
            stats([r for r in b_is if lo <= r[1] < hi]),
            stats([r for r in b_oos if lo <= r[1] < hi]))

    # S4: stop micro-tune
    print(f"\n{'═'*92}\n  S4: STOP x RR MICRO-TUNE\n{'═'*92}")
    for st, rr in product([18, 20, 22], [2.75, 3.0, 3.25]):
        s_i = stats(collect(days, IS_Y, stop=st, rr=rr))
        s_o = stats(collect(days, OOS_Y, stop=st, rr=rr))
        duo(f"stop={st} rr={rr}", s_i, s_o,
            "  ← v6" if (st, rr) == (20, 3.0) else "")

    # S5: trend definition
    print(f"\n{'═'*92}\n  S5: TREND DEFINITION at 11:00\n{'═'*92}")
    for mode, tag in [("open", "close vs 9:30 open (v6)"),
                      ("vwap", "close vs VWAP")]:
        s_i = stats(collect(days, IS_Y, trend_mode=mode))
        s_o = stats(collect(days, OOS_Y, trend_mode=mode))
        duo(tag, s_i, s_o, "  ← v6" if mode == "open" else "")

    # S6: account analysis at v6
    print(f"\n{'═'*92}\n  S6: ACCOUNT ANALYSIS — v6 on its own prefunded 50K (1c)\n{'═'*92}")
    rows_all = collect(days, (2022, 2023, 2024, 2025, 2026))
    by_day = {r[0]: r[3] for r in rows_all}
    trading_days = sorted(d for d in days)          # calendar of possible days
    pnl_seq = [(d, by_day.get(d, 0.0)) for d in trading_days]

    pnls = [r[3] for r in rows_all]
    worst_trade = min(pnls)
    # losing streak (trade-level)
    streak = max_streak = 0
    for p in pnls:
        streak = streak + 1 if p < 0 else 0
        max_streak = max(max_streak, streak)
    # monthly distribution
    monthly = defaultdict(float)
    for d, p in by_day.items():
        monthly[(d.year, d.month)] += p
    mvals = sorted(monthly.values())
    neg_months = sum(1 for v in mvals if v < 0)
    print(f"    trades: {len(pnls)}  (~{len(pnls)/234:.1f}/wk)   worst trade: ${worst_trade:+,.0f}")
    print(f"    max losing streak: {max_streak} trades")
    print(f"    months: {len(mvals)}  negative: {neg_months} ({neg_months/len(mvals):.0%})  "
          f"median month: ${mvals[len(mvals)//2]:+,.0f}  worst: ${mvals[0]:+,.0f}  best: ${mvals[-1]:+,.0f}")

    # fresh-start floor test
    eqs = [p for _, p in pnl_seq]
    deaths = 0
    n_starts = len(eqs)
    for i in range(n_starts):
        eq = 0.0
        for p in eqs[i:]:
            eq += p
            if eq <= -2000:
                deaths += 1
                break
    print(f"    fresh-start floor test (-$2,000): {deaths}/{n_starts} starts die ({deaths/n_starts:.0%})")

    # payout timing
    for tgt in [1000, 2000, 3000]:
        reach = []
        died = 0
        for i in range(n_starts):
            eq = 0.0
            n = 0
            hit = None
            for p in eqs[i:]:
                eq += p
                n += 1
                if eq <= -2000:
                    hit = "die"; break
                if eq >= tgt:
                    hit = "pass"; break
            if hit == "pass":
                reach.append(n)
            elif hit == "die":
                died += 1
        if reach:
            reach.sort()
            med, p90 = reach[len(reach)//2], reach[int(len(reach)*0.9)]
            print(f"    to +${tgt:,}: reach {len(reach)}/{len(reach)+died}  "
                  f"median {med} tdays  p90 {p90} tdays")

    print(f"\n{'═'*92}\n  vwap_final_opt done.\n{'═'*92}")
