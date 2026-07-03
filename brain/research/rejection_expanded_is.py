"""
brain/research/rejection_expanded_is.py

E4 Rejection re-analysis with expanded data (nq_full.csv: 2022-2026).
  IS:  2022-2024  (3 years)
  OOS: 2025-2026

Original finding (nq_1min.csv, IS=2024 only):
  OOS PF=1.583, N=94  (best: stop=20, RR=2.0, ext=25)
  IS  PF=1.179, N=32  (weak — 2024 was low-vol)

Question: does the edge hold with 2022-2023 added to IS?
"""

import csv, os
from datetime import datetime, time
from collections import defaultdict
from itertools import product

NQ_POINT = 20.0
COST     = 14.50

DATA_PATH = "/Users/Cruz/Desktop/nq_bot_final-main/data/nq_full.csv"

IS_YEARS  = {2022, 2023, 2024}
OOS_YEARS = {2025, 2026}
WEAK_MONTHS = frozenset({4, 5, 6, 9, 12})


def load_bars(path):
    bars_by_day = defaultdict(list)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_str = row["timestamp"][:19]
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts.hour < 9 or ts.hour >= 16:
                continue
            bars_by_day[ts.date()].append({
                "t":     ts.time(),
                "h":     float(row["high"]),
                "l":     float(row["low"]),
                "c":     float(row["close"]),
                "v":     float(row["volume"]),
                "o":     float(row["open"]),
                "dow":   ts.weekday(),
                "month": ts.month,
            })
    return bars_by_day


def run_rejection_day(bars, min_extend=25, stop_pt=20, rr=2.0,
                      entry_start=time(11, 0), window_end=time(13, 0)):
    T_930 = time(9, 30); T_1030 = time(10, 30); T_1555 = time(15, 55)
    sum_pv = sum_vol = 0.0; vwap = None
    open_930 = am_trend = None
    was_extended = False; prev_above = None
    saw_reclaim = False; reclaim_dir = None
    traded = in_pos = False; pos_long = None
    entry_px = sl = tp = None; trades = []

    for b in bars:
        t = b["t"]
        if t >= T_1555: break
        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]
        if t >= T_930:
            sum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
            sum_vol += b["v"]
            if sum_vol: vwap = sum_pv / sum_vol
        if am_trend is None and t >= T_1030 and open_930 and vwap:
            am_trend = "bull" if b["c"] > open_930 else "bear"
        if vwap is None or t < time(10, 0):
            if vwap: prev_above = b["c"] > vwap
            continue

        close = b["c"]; curr_above = close > vwap

        if in_pos:
            if pos_long:
                if   b["l"] <= sl:  pnl_pts = sl - entry_px
                elif b["h"] >= tp:  pnl_pts = tp - entry_px
                else:
                    if t >= window_end: pnl_pts = close - entry_px
                    else: prev_above = curr_above; continue
            else:
                if   b["h"] >= sl:  pnl_pts = entry_px - sl
                elif b["l"] <= tp:  pnl_pts = entry_px - tp
                else:
                    if t >= window_end: pnl_pts = entry_px - close
                    else: prev_above = curr_above; continue
            trades.append({"pnl_usd": round(pnl_pts * NQ_POINT - COST, 2)})
            in_pos = False; prev_above = curr_above; continue

        if traded or t >= window_end:
            prev_above = curr_above; continue

        if not was_extended and abs(close - vwap) > min_extend:
            was_extended = True

        if was_extended and prev_above is not None:
            crossed_up   = (not prev_above) and curr_above
            crossed_down = prev_above and (not curr_above)

            if crossed_up   and not saw_reclaim and t >= entry_start:
                saw_reclaim = True; reclaim_dir = "up"
            elif crossed_down and not saw_reclaim and t >= entry_start:
                saw_reclaim = True; reclaim_dir = "down"
            elif saw_reclaim:
                failed_up   = reclaim_dir == "up"   and crossed_down
                failed_down = reclaim_dir == "down"  and crossed_up
                if failed_up:
                    entry_px = close; sl = close + stop_pt; tp = close - stop_pt * rr
                    pos_long = False; in_pos = True; traded = True
                elif failed_down:
                    entry_px = close; sl = close - stop_pt; tp = close + stop_pt * rr
                    pos_long = True; in_pos = True; traded = True

        prev_above = curr_above

    if in_pos and entry_px is not None:
        pnl = (bars[-1]["c"] - entry_px) if pos_long else (entry_px - bars[-1]["c"])
        trades.append({"pnl_usd": round(pnl * NQ_POINT - COST, 2)})
    return trades


def run_period(bars_by_day, years, **kw):
    results = []
    for d, bars in sorted(bars_by_day.items()):
        if d.year not in years or not bars: continue
        if bars[0]["dow"] == 0: continue          # skip Monday
        if bars[0]["month"] in WEAK_MONTHS: continue
        for t in run_rejection_day(bars, **kw):
            results.append({**t, "date": d})
    return results


def stats(trades):
    if not trades: return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0, "avg": 0}
    wins    = [t for t in trades if t["pnl_usd"] > 0]
    gross_w = sum(t["pnl_usd"] for t in wins)
    gross_l = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] < 0))
    net     = sum(t["pnl_usd"] for t in trades)
    return {
        "n":   len(trades),
        "wr":  len(wins) / len(trades),
        "pf":  round(gross_w / gross_l, 3) if gross_l else 99.0,
        "net": round(net),
        "avg": round(net / len(trades)),
    }


def yearly_breakdown(bars_by_day, year, **kw):
    trades = run_period(bars_by_day, {year}, **kw)
    return stats(trades)


if __name__ == "__main__":
    print(f"\n{'='*88}")
    print(f"  E4 Rejection — Expanded IS (2022-2024) vs OOS (2025-2026)")
    print(f"  Data: nq_full.csv")
    print(f"{'='*88}")

    print("  Loading nq_full.csv...", end=" ", flush=True)
    bars = load_bars(DATA_PATH)
    total_bars = sum(len(v) for v in bars.values())
    print(f"{total_bars:,} bars across {len(bars)} trading days")

    # ── 1. Year-by-year breakdown at best params (stop=20, RR=2.0, ext=25) ──────
    BEST_KW = dict(min_extend=25, stop_pt=20, rr=2.0,
                   entry_start=time(11, 0), window_end=time(13, 0))

    print(f"\n  Best params (stop=20, RR=2.0, ext=25) — year-by-year:")
    print(f"  {'Year':<6}  {'N':>4}  {'WR':>5}  {'PF':>7}  {'Net $':>10}  {'Avg $/tr':>9}")
    print(f"  {'─'*56}")
    for yr in [2022, 2023, 2024, 2025, 2026]:
        s = yearly_breakdown(bars, yr, **BEST_KW)
        label = "  ← OOS" if yr >= 2025 else ""
        print(f"  {yr:<6}  {s['n']:>4}  {s['wr']:>5.0%}  {s['pf']:>7.3f}  "
              f"${s['net']:>+9,.0f}  ${s['avg']:>+8,.0f}{label}")

    # ── 2. Aggregate IS vs OOS ────────────────────────────────────────────────
    print(f"\n  Aggregate (stop=20, RR=2.0, ext=25):")
    s_is  = stats(run_period(bars, IS_YEARS,  **BEST_KW))
    s_oos = stats(run_period(bars, OOS_YEARS, **BEST_KW))
    print(f"  IS  2022-2024:  N={s_is['n']:>3}  WR={s_is['wr']:.0%}  "
          f"PF={s_is['pf']:.3f}  Net=${s_is['net']:>+9,.0f}  Avg=${s_is['avg']:>+7,.0f}/tr")
    print(f"  OOS 2025-2026:  N={s_oos['n']:>3}  WR={s_oos['wr']:.0%}  "
          f"PF={s_oos['pf']:.3f}  Net=${s_oos['net']:>+9,.0f}  Avg=${s_oos['avg']:>+7,.0f}/tr")

    # ── 3. Full param sweep ───────────────────────────────────────────────────
    print(f"\n  Full param sweep (sorted by OOS PF):")
    print(f"  {'Stop':>5}  {'RR':>5}  {'Ext':>4}  "
          f"{'IS-N':>5}  {'IS-PF':>6}  {'IS-Net':>10}  "
          f"{'OOS-N':>5}  {'OOS-PF':>6}  {'OOS-Net':>10}")
    print(f"  {'─'*82}")

    sweep_results = []
    for sp, rr, ext in product([15, 20, 25], [1.5, 2.0, 2.5, 3.0], [15, 20, 25, 30]):
        kw = dict(min_extend=ext, stop_pt=sp, rr=rr,
                  entry_start=time(11, 0), window_end=time(13, 0))
        s_is  = stats(run_period(bars, IS_YEARS,  **kw))
        s_oos = stats(run_period(bars, OOS_YEARS, **kw))
        sweep_results.append((sp, rr, ext, s_is, s_oos))

    sweep_results.sort(key=lambda x: x[4]["pf"], reverse=True)
    for sp, rr, ext, s_is, s_oos in sweep_results[:25]:
        star = " *" if sp == 20 and rr == 2.0 and ext == 25 else ""
        print(f"  {sp:>5}  {rr:>5.1f}  {ext:>4}  "
              f"{s_is['n']:>5}  {s_is['pf']:>6.3f}  ${s_is['net']:>+9,.0f}  "
              f"{s_oos['n']:>5}  {s_oos['pf']:>6.3f}  ${s_oos['net']:>+9,.0f}{star}")

    # ── 4. N-sufficient configs (OOS N>=50 for robustness) ───────────────────
    print(f"\n  Configs with OOS N>=50 (sorted by OOS PF):")
    print(f"  {'Stop':>5}  {'RR':>5}  {'Ext':>4}  "
          f"{'IS-N':>5}  {'IS-PF':>6}  {'IS-Net':>10}  "
          f"{'OOS-N':>5}  {'OOS-PF':>6}  {'OOS-Net':>10}")
    print(f"  {'─'*82}")
    robust = [(sp,rr,ext,s_is,s_oos) for sp,rr,ext,s_is,s_oos in sweep_results
              if s_oos["n"] >= 50]
    for sp, rr, ext, s_is, s_oos in robust[:15]:
        star = " *" if sp == 20 and rr == 2.0 and ext == 25 else ""
        print(f"  {sp:>5}  {rr:>5.1f}  {ext:>4}  "
              f"{s_is['n']:>5}  {s_is['pf']:>6.3f}  ${s_is['net']:>+9,.0f}  "
              f"{s_oos['n']:>5}  {s_oos['pf']:>6.3f}  ${s_oos['net']:>+9,.0f}{star}")

    print(f"\n{'='*88}")
    print(f"  DONE")
    print(f"{'='*88}\n")
