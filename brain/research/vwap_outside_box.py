"""
brain/research/vwap_outside_box.py

Structural (not parametric) improvement attempts on VWAP Reclaim v10.

  T1. PERCENT SCALING — NQ went ~11k (2022) → ~23k (2026); fixed 35pt/20pt is
      a big relative move in 2022, small in 2026. Scale ext/stop by price.
  T2. REJECTION FLIP — on stop-out, flip into the failed-reclaim direction
      (the validated E4 edge, OOS PF 1.58) as a recovery trade on this account.
  T3. ASYMMETRIC EXIT — 13:00 flattens only losers; winners hold to target /
      stop / 15:55. Also plain hold-to variants.
  T4. TREND STRENGTH — require |11:00 close - 9:30 open| >= X pt (skip
      coin-flip trend days).
  T5. EXTENSION RECENCY — extension extreme must be within last N minutes.
  T6. Combos of whatever wins.

Baseline v10: stop 20 / 2.75R / ext 35 / lock+entry 11:00 / entries<13:00 /
flat 13:00 / skip Mon+May / both dirs. IS 2022-24 / OOS 2025-26.
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
T1300, T1555 = time(13, 0), time(15, 55)


def run_day(bars,
            stop=20.0, rr=2.75, extend=35.0,
            pct=False, stop_pct=0.0, ext_pct=0.0,          # T1
            flip=False,                                     # T2
            exit_mode="flat13",   # flat13 | asym | hold1400 | hold1555   T3
            trend_min=0.0,                                  # T4
            recency_min=0,                                  # T5 (minutes, 0=off)
            ):
    """Returns list of (entry_t, dir, tag, pnl). tag: main|flip."""
    sum_pv = sum_vol = 0.0
    vwap = open930 = trend = None
    was_ext = False
    ext_time = None
    prev_above = None
    trades = []

    entry = sl = tp = e_t = None
    is_long = None
    tag = "main"
    flip_armed = False   # set when main trade stops out
    done_main = False
    done_flip = False

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

        if pct and open930:
            eff_stop = round(open930 * stop_pct, 2)
            eff_ext  = round(open930 * ext_pct, 2)
        else:
            eff_stop, eff_ext = stop, extend
        eff_tgt = eff_stop * rr

        if trend is None and t >= time(11, 0) and open930 and vwap:
            disp = b["c"] - open930
            if trend_min and abs(disp) < trend_min:
                trend = "none"
            else:
                trend = "bull" if disp > 0 else "bear"
        if vwap is None or t < time(10, 0):
            if vwap:
                prev_above = b["c"] > vwap
            continue

        close = b["c"]
        cur_above = close > vwap

        # manage position
        if entry is not None:
            res = None
            if is_long:
                if b["l"] <= sl:   res = sl - entry
                elif b["h"] >= tp: res = tp - entry
            else:
                if b["h"] >= sl:   res = entry - sl
                elif b["l"] <= tp: res = entry - tp
            if res is None:
                open_pnl = (close - entry) if is_long else (entry - close)
                if exit_mode == "flat13" and t >= T1300:
                    res = open_pnl
                elif exit_mode == "asym" and t >= T1300 and open_pnl <= 0:
                    res = open_pnl
                elif exit_mode == "hold1400" and t >= time(14, 0):
                    res = open_pnl
                # hold1555: session end handles it
            if res is not None:
                trades.append((e_t, "long" if is_long else "short", tag,
                               res * NQ_PT - COST))
                stopped = res < 0 and abs(res + eff_stop) < 0.01
                if tag == "main":
                    done_main = True
                    if flip and stopped and not done_flip and t < T1300:
                        # failed reclaim: flip immediately at this bar close
                        done_flip = True
                        tag = "flip"
                        is_long = not is_long
                        entry, e_t = close, t
                        sl = entry + eff_stop if not is_long else entry - eff_stop
                        tp = entry - eff_tgt  if not is_long else entry + eff_tgt
                        prev_above = cur_above
                        continue
                else:
                    done_flip = True
                entry = None
            prev_above = cur_above
            continue

        # entry logic (main only; flips happen inline above)
        if done_main or t >= T1300:
            prev_above = cur_above
            continue
        if not was_ext and abs(close - vwap) > eff_ext:
            was_ext = True
            ext_time = t
        if recency_min and was_ext and ext_time is not None:
            age = (t.hour * 60 + t.minute) - (ext_time.hour * 60 + ext_time.minute)
            if age > recency_min:
                was_ext = False          # stale extension — needs a fresh one
                ext_time = None
        if was_ext and prev_above is not None and t >= time(11, 0) and trend not in (None, "none"):
            cu = (not prev_above) and cur_above
            cd = prev_above and (not cur_above)
            if cu and trend == "bull":
                entry, is_long, e_t, tag = close, True, t, "main"
                sl, tp = close - eff_stop, close + eff_tgt
            elif cd and trend == "bear":
                entry, is_long, e_t, tag = close, False, t, "main"
                sl, tp = close + eff_stop, close - eff_tgt
        prev_above = cur_above

    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        trades.append((e_t, "long" if is_long else "short", tag, pts * NQ_PT - COST))
    return trades


def collect(days, years, **kw):
    out = []
    for d in sorted(days):
        if d.year not in years or d.weekday() == 0 or d.month == 5:
            continue
        for r in run_day(days[d], **kw):
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
    print(f"  {tag:<40} IS: N={s_i['n']:>3} WR={s_i['wr']:>4.0%} PF={s_i['pf']:>6.3f} "
          f"${s_i['net']:>+8,} | OOS: N={s_o['n']:>3} WR={s_o['wr']:>4.0%} "
          f"PF={s_o['pf']:>6.3f} ${s_o['net']:>+8,}{mark}")


if __name__ == "__main__":
    print("Loading...", flush=True)
    days = load_days(DATA)

    b_i, b_o = stats(collect(days, IS_Y)), stats(collect(days, OOS_Y))
    print(f"\n{'═'*98}\n  BASELINE v10\n{'═'*98}")
    duo("v10", b_i, b_o, "  ← current")

    # T1: percent scaling
    print(f"\n{'═'*98}\n  T1: PERCENT SCALING (ref = 9:30 open; 35pt≈0.28% in 2022, 0.15% in 2026)\n{'═'*98}")
    for sp, ep in product([0.0008, 0.0010, 0.0012], [0.0015, 0.0020, 0.0025]):
        s_i = stats(collect(days, IS_Y,  pct=True, stop_pct=sp, ext_pct=ep))
        s_o = stats(collect(days, OOS_Y, pct=True, stop_pct=sp, ext_pct=ep))
        duo(f"stop={sp:.2%} ext={ep:.2%}", s_i, s_o)

    # T2: rejection flip
    print(f"\n{'═'*98}\n  T2: REJECTION FLIP on stop-out (recovery trade, E4 edge)\n{'═'*98}")
    for yrs, lbl in [(IS_Y, "IS"), (OOS_Y, "OOS")]:
        rows = collect(days, yrs, flip=True)
        m = stats([r for r in rows if r[3] == "main"])
        f = stats([r for r in rows if r[3] == "flip"])
        a = stats(rows)
        print(f"  {lbl}: main N={m['n']} PF={m['pf']:.3f} ${m['net']:+,} | "
              f"FLIP N={f['n']} PF={f['pf']:.3f} ${f['net']:+,} | "
              f"combined PF={a['pf']:.3f} ${a['net']:+,}")

    # T3: exit modes
    print(f"\n{'═'*98}\n  T3: EXIT MODE (entries unchanged, 11:00-13:00)\n{'═'*98}")
    for em in ["flat13", "asym", "hold1400", "hold1555"]:
        s_i = stats(collect(days, IS_Y,  exit_mode=em))
        s_o = stats(collect(days, OOS_Y, exit_mode=em))
        duo(f"exit={em}", s_i, s_o, "  ← v10" if em == "flat13" else "")

    # T4: trend strength
    print(f"\n{'═'*98}\n  T4: TREND STRENGTH — require |11:00 close - open| ≥ X pt\n{'═'*98}")
    for tm in [0, 15, 25, 40]:
        s_i = stats(collect(days, IS_Y,  trend_min=tm))
        s_o = stats(collect(days, OOS_Y, trend_min=tm))
        duo(f"trend_min={tm}pt", s_i, s_o, "  ← v10" if tm == 0 else "")

    # T5: extension recency
    print(f"\n{'═'*98}\n  T5: EXTENSION RECENCY — extreme within last N minutes\n{'═'*98}")
    for rm in [0, 60, 90, 120]:
        s_i = stats(collect(days, IS_Y,  recency_min=rm))
        s_o = stats(collect(days, OOS_Y, recency_min=rm))
        duo(f"recency={rm if rm else 'off'}min", s_i, s_o, "  ← v10" if rm == 0 else "")

    print(f"\n{'═'*98}\n  vwap_outside_box done — combos run manually on winners.\n{'═'*98}")
