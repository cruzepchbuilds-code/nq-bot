"""
brain/research/vwap_revamp.py

Structural revamp candidates for VWAP Reclaim v10-final
(base: stop 20 / 2.75R / ext 35 / lock+entry 11:00 / asym 13:00 exit /
 skip Mon+May / both dirs). IS 22-24 / OOS 25-26.

  R1. ADAPTIVE EXTENSION — arm when |close-vwap| > k x same-day stdev of the
      spread (min 20 samples, 15pt floor). Vol-aware version of fixed 35pt.
  R2. RETEST LIMIT ENTRY — after the cross signal, wait for price to trade
      back THROUGH vwap level (1 tick trade-through) within 5 bars; enter at
      the level. Better price on retests; runners are missed. Conservative.
  R3. STRUCTURAL TARGET — target = day extreme at entry (long: day high) if
      >= 25pt away, else standard 55pt. Trend reaches for the level.
  R4. FlipOnStop year-by-year — is the flip monotone-strengthening like the
      rejection edge (would justify default ON in current regime)?
  R5. Combos of anything that wins.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vwap_fulldata import load_days, DATA
from datetime import time
from collections import defaultdict

NQ_PT, COST = 20.0, 14.50
IS_Y, OOS_Y, ALL_Y = (2022, 2023, 2024), (2025, 2026), (2022, 2023, 2024, 2025, 2026)
T13, T1555 = time(13, 0), time(15, 55)


def run_day(bars, ext_mode="fixed", ext_k=2.0, entry_mode="market",
            tgt_mode="fixed", flip=False):
    sum_pv = sum_vol = 0.0
    vwap = open930 = trend = None
    day_hi = -1e18
    day_lo = 1e18
    n_s = 0
    mean_s = m2_s = 0.0            # Welford on spread
    was_ext = False
    prev_above = None
    trades = []

    entry = sl = tp = None
    is_long = None
    tag = "main"
    done_main = done_flip = False
    pend = None                    # (level, dir, bars_left) retest order

    for b in bars:
        t = b["t"]
        if t < time(9, 30) or t >= T1555:
            continue
        if open930 is None and t < time(9, 31):
            open930 = b["o"]
        day_hi = max(day_hi, b["h"])
        day_lo = min(day_lo, b["l"])
        sum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        sum_vol += b["v"]
        if sum_vol:
            vwap = sum_pv / sum_vol
        if trend is None and t >= time(11, 0) and open930 and vwap:
            trend = "bull" if b["c"] > open930 else "bear"
        if vwap is None:
            continue
        close = b["c"]
        spread = close - vwap
        if t >= time(10, 0):
            n_s += 1
            d0 = spread - mean_s
            mean_s += d0 / n_s
            m2_s += d0 * (spread - mean_s)
        above = close > vwap

        # manage open position
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
                trades.append((tag, res * NQ_PT - COST))
                stopped = res < 0 and abs(res + (sl - entry if not is_long else entry - sl)) < 0.01
                full_stop = res < 0 and abs(abs(res) - abs(entry - sl)) < 0.01
                if tag == "main":
                    done_main = True
                    if flip and full_stop and not done_flip and t < time(12, 58):
                        done_flip = True
                        tag = "flip"
                        is_long = not is_long
                        entry = close
                        sl = entry + 20.0 if not is_long else entry - 20.0
                        tp = entry - 55.0 if not is_long else entry + 55.0
                        prev_above = above
                        continue
                else:
                    done_flip = True
                entry = None
            prev_above = above
            continue

        # pending retest order
        if pend is not None:
            level, dirn, left = pend
            filled = (b["l"] <= level - 0.25) if dirn == "long" else (b["h"] >= level + 0.25)
            if filled:
                entry = level
                is_long = dirn == "long"
                sl = entry - 20.0 if is_long else entry + 20.0
                if tgt_mode == "struct":
                    ext_tgt = (day_hi - entry) if is_long else (entry - day_lo)
                    dist = ext_tgt if ext_tgt >= 25 else 55.0
                else:
                    dist = 55.0
                tp = entry + dist if is_long else entry - dist
                tag = "main"
                pend = None
                prev_above = above
                continue
            left -= 1
            pend = None if left <= 0 else (level, dirn, left)

        if done_main or t >= T13:
            prev_above = above
            continue

        # extension arming
        if ext_mode == "fixed":
            if not was_ext and abs(spread) > 35.0:
                was_ext = True
        else:
            if n_s >= 20 and m2_s > 0:
                sd = (m2_s / n_s) ** 0.5
                thr = max(15.0, ext_k * sd)
                if not was_ext and abs(spread) > thr:
                    was_ext = True

        if was_ext and prev_above is not None and t >= time(11, 0) and trend:
            cu = (not prev_above) and above
            cd = prev_above and (not above)
            sig = None
            if cu and trend == "bull":
                sig = "long"
            elif cd and trend == "bear":
                sig = "short"
            if sig and pend is None:
                if entry_mode == "market":
                    entry = close
                    is_long = sig == "long"
                    sl = entry - 20.0 if is_long else entry + 20.0
                    if tgt_mode == "struct":
                        ext_tgt = (day_hi - entry) if is_long else (entry - day_lo)
                        dist = ext_tgt if ext_tgt >= 25 else 55.0
                    else:
                        dist = 55.0
                    tp = entry + dist if is_long else entry - dist
                    tag = "main"
                else:
                    pend = (vwap, sig, 5)
        prev_above = above

    if entry is not None:
        last = bars[-1]["c"]
        pts = (last - entry) if is_long else (entry - last)
        trades.append((tag, pts * NQ_PT - COST))
    return trades


def collect(days, years, **kw):
    out = []
    for d in sorted(days):
        if d.year not in years or d.weekday() == 0 or d.month == 5:
            continue
        for tag, p in run_day(days[d], **kw):
            out.append((d, tag, p))
    return out


def stats(rows):
    if not rows:
        return {"n": 0, "pf": 0, "net": 0, "wr": 0}
    v = [p for *_, p in rows]
    w = [p for p in v if p > 0]
    gl = abs(sum(p for p in v if p <= 0))
    return {"n": len(v), "wr": len(w)/len(v),
            "pf": round(sum(w)/gl, 3) if gl else 99.0, "net": round(sum(v))}


def duo(tag, s_i, s_o, mark=""):
    print(f"  {tag:<36} IS: N={s_i['n']:>3} PF={s_i['pf']:>6.3f} ${s_i['net']:>+8,} | "
          f"OOS: N={s_o['n']:>3} PF={s_o['pf']:>6.3f} ${s_o['net']:>+8,}{mark}")


if __name__ == "__main__":
    print("Loading...", flush=True)
    days = load_days(DATA)

    b_i = stats(collect(days, IS_Y))
    b_o = stats(collect(days, OOS_Y))
    print(f"\n{'═'*92}\n  BASELINE v10-final (asym exit)\n{'═'*92}")
    duo("baseline", b_i, b_o, "  ← current")

    print(f"\n{'═'*92}\n  R1: ADAPTIVE EXTENSION (k x same-day spread stdev, 15pt floor)\n{'═'*92}")
    for k in [1.5, 2.0, 2.5]:
        duo(f"ext=stdev k={k}",
            stats(collect(days, IS_Y, ext_mode="stdev", ext_k=k)),
            stats(collect(days, OOS_Y, ext_mode="stdev", ext_k=k)))

    print(f"\n{'═'*92}\n  R2: RETEST LIMIT ENTRY (fill at vwap level, 5-bar window, trade-through)\n{'═'*92}")
    duo("retest entry",
        stats(collect(days, IS_Y, entry_mode="retest")),
        stats(collect(days, OOS_Y, entry_mode="retest")))

    print(f"\n{'═'*92}\n  R3: STRUCTURAL TARGET (day extreme if ≥25pt, else 55pt)\n{'═'*92}")
    duo("struct target",
        stats(collect(days, IS_Y, tgt_mode="struct")),
        stats(collect(days, OOS_Y, tgt_mode="struct")))

    print(f"\n{'═'*92}\n  R4: FlipOnStop — year by year (is it monotone like rejection?)\n{'═'*92}")
    for y in ALL_Y:
        rows = collect(days, (y,), flip=True)
        f = stats([r for r in rows if r[1] == "flip"])
        a = stats(rows)
        print(f"    {y}: flips N={f['n']:>3} PF={f['pf']:>6.3f} ${f['net']:>+7,} | combined ${a['net']:>+8,}")

    print(f"\n{'═'*92}\n  vwap_revamp done — combos after review.\n{'═'*92}")
