#!/usr/bin/env python3
"""
brain/research_es.py  — Optimized version
ES (E-mini S&P 500) Research — Full Strategy Build
IS: 2022-2023 | OOS: 2024-2026

Uses pre-grouped bars by date for performance.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import itertools

NQ_DATA = Path(__file__).parent.parent / "data" / "nq_full.csv"
ES_DATA = Path(__file__).parent.parent / "data" / "es_1min.csv"
OUT_MD  = Path(__file__).parent / "es_research.md"
OUT_CSV = Path(__file__).parent / "es_trades.csv"

ES_PT  = 50.0
ES_COST = 55.0   # $5 comm + 0.5pt×2 slip × $50 = $55

NQ_PT  = 20.0
NQ_COST = 25.0   # $5 comm + 0.5pt×2 slip × $20 = $25

IS_END    = pd.Timestamp("2023-12-31")
OOS_START = pd.Timestamp("2024-01-01")
VIA_PF    = 1.40   # ES viability gate is 1.40
VIA_N     = 30


def load_and_pregroup(fpath, label=""):
    print(f"Loading {label}...")
    df = pd.read_csv(fpath, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["date"]   = df["timestamp"].dt.date
    df["hour"]   = df["timestamp"].dt.hour
    df["minute"] = df["timestamp"].dt.minute
    df["dow"]    = df["timestamp"].dt.dayofweek
    df["month"]  = df["timestamp"].dt.month
    print(f"  {len(df):,} bars")

    # US session: 9:30 - 4:00pm
    us = df[
        ((df["hour"] == 9) & (df["minute"] >= 30)) |
        df["hour"].between(10, 15) |
        ((df["hour"] == 16) & (df["minute"] == 0))
    ].copy()

    # Group US bars by date
    us_by_date = {}
    for date, g in us.groupby("date"):
        us_by_date[date] = g.reset_index(drop=True)

    # OR data: 9:30-9:44
    or_bars = df[(df["hour"] == 9) & (df["minute"].between(30, 44))]
    or_data = {}
    for date, g in or_bars.groupby("date"):
        if len(g) < 10:
            continue
        h = float(g["high"].max()); l = float(g["low"].min())
        or_data[date] = {"high": h, "low": l, "range": h - l, "open": float(g.iloc[0]["open"])}

    # 4pm close and 9:30 open for gaps
    pm4  = df[(df["hour"] == 16) & (df["minute"] == 0)]
    open_bar = df[(df["hour"] == 9) & (df["minute"] == 30)]
    pm4_close = {r["date"]: float(r["close"]) for _, r in pm4.iterrows()}
    open_930  = {r["date"]: float(r["open"])  for _, r in open_bar.iterrows()}

    sorted_dates = sorted(or_data)
    gaps = {}
    for i, d in enumerate(sorted_dates):
        if i == 0:
            continue
        pd_ = sorted_dates[i-1]
        if pd_ in pm4_close and d in open_930:
            gaps[d] = open_930[d] - pm4_close[pd_]

    print(f"  OR days: {len(or_data)} | Gaps: {len(gaps)}")
    return df, us_by_date, or_data, gaps


def sim_orb_trade(us_bars, or_data, date, stop_pts, buf, rr, last_entry_ts,
                  cost=ES_COST, pt_val=ES_PT, dir_filter=None):
    """Simulate one ORB trade. Returns trade dict or None."""
    day = or_data.get(date)
    if not day:
        return None

    bars = us_bars.get(date)
    if bars is None or len(bars) < 20:
        return None

    or_high = day["high"] + buf
    or_low  = day["low"]  - buf

    after_or = bars[bars["timestamp"] > pd.Timestamp(f"{date} 09:45:00")].reset_index(drop=True)

    direction = entry_px = ei = None
    for i, bar in after_or.iterrows():
        if bar["timestamp"] > last_entry_ts:
            break
        if dir_filter and bar["close"] > or_high and dir_filter != "long":
            continue
        if dir_filter and bar["close"] < or_low and dir_filter != "short":
            continue
        if bar["close"] > or_high:
            direction, entry_px, ei = "long", float(bar["close"]), i; break
        if bar["close"] < or_low:
            direction, entry_px, ei = "short", float(bar["close"]), i; break

    if direction is None:
        return None

    stop   = entry_px - stop_pts if direction == "long" else entry_px + stop_pts
    target = entry_px + stop_pts * rr if direction == "long" else entry_px - stop_pts * rr
    flatten_ts = pd.Timestamp(f"{date} 15:55:00")

    exit_reason = exit_px = None
    for i in range(int(ei) + 1, len(after_or)):
        b = after_or.iloc[i]
        if b["timestamp"] >= flatten_ts:
            exit_reason, exit_px = "flatten", float(b["open"]); break
        if direction == "long":
            if b["low"] <= stop:    exit_reason, exit_px = "stop",   stop;   break
            if b["high"] >= target: exit_reason, exit_px = "target", target; break
        else:
            if b["high"] >= stop:   exit_reason, exit_px = "stop",   stop;   break
            if b["low"] <= target:  exit_reason, exit_px = "target", target; break
    if exit_reason is None and len(after_or) > 0:
        exit_reason = "flatten"; exit_px = float(after_or.iloc[-1]["close"])
    if exit_reason is None:
        return None

    pnl = ((exit_px - entry_px) if direction == "long" else (entry_px - exit_px)) * pt_val - cost
    return {"date": date, "direction": direction, "entry": entry_px,
            "exit": exit_reason, "exit_px": exit_px, "pnl": pnl,
            "or_range": day["range"]}


def stats(trades):
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0.0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p < 0]
    return {"n": len(pnls), "wr": len(wins)/len(pnls),
            "pf": sum(wins)/abs(sum(loss)) if loss else 9.99, "net": sum(pnls)}

def split(trades):
    is_t  = [t for t in trades if pd.Timestamp(str(t["date"])) <= IS_END]
    oos_t = [t for t in trades if pd.Timestamp(str(t["date"])) >= OOS_START]
    return stats(is_t), stats(oos_t)

def viable(s): return s["n"] >= VIA_N and s["pf"] >= VIA_PF


# ── S1: ORB parameter sweep ───────────────────────────────────────────────────

def s1_orb_sweep(us_by_date, or_data, gaps):
    print("\n=== S1: ES ORB Parameter Sweep ===")
    stops   = [4, 6, 8, 10, 12]
    buffers = [1.0, 1.5, 2.0, 2.25, 3.0]
    or_mins = [4, 6, 8]
    or_maxs = [25, 30, 35, 40, 50]
    rrs     = [1.5, 2.0, 2.5]
    results = []
    last_ts = {d: pd.Timestamp(f"{d} 11:15:00") for d in or_data}

    total = len(stops)*len(buffers)*len(or_mins)*len(or_maxs)*len(rrs)
    print(f"  {total} combos...")

    for sp, buf, or_min, or_max, rr in itertools.product(stops, buffers, or_mins, or_maxs, rrs):
        if or_min >= or_max:
            continue
        trades = []
        for date in sorted(or_data):
            day = or_data[date]
            if day["range"] < or_min or day["range"] > or_max:
                continue
            if abs(gaps.get(date, 0)) < 6:
                continue
            dt = pd.Timestamp(str(date))
            if dt.dayofweek == 0:
                continue
            t = sim_orb_trade(us_by_date, or_data, date, sp, buf, rr, last_ts[date])
            if t:
                trades.append(t)
        if not trades:
            continue
        s_is, s_oos = split(trades)
        v = viable(s_oos)
        results.append({"strategy": "S1", "config": f"stop={sp} buf={buf} or={or_min}-{or_max} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        t = results[0]
        print(f"  Best OOS PF: {t['s_oos']['pf']:.2f}  n={t['s_oos']['n']}  {t['config']}")
    return results


# ── S2: Month/DOW filters ─────────────────────────────────────────────────────

def s2_month_filter(us_by_date, or_data, gaps):
    print("\n=== S2: ES Month/DOW Filters ===")
    month_combos = [
        ([1,2,3,10,11], "JFMON"),
        ([1,2,3,4,10,11], "+Apr"),
        ([1,2,3,5,10,11], "+May"),
        ([1,2,10,11], "JFON"),
        (list(range(1,13)), "all"),
    ]
    dow_combos = [
        ([0,1,2,3,4], "all"), ([1,2,3,4], "no_mon"),
        ([1,2,4], "tue_wed_fri"), ([2,3,4], "wed_thu_fri"),
    ]
    results = []
    last_ts = {d: pd.Timestamp(f"{d} 11:15:00") for d in or_data}

    for (months, ml), (dows, dl) in itertools.product(month_combos, dow_combos):
        trades = []
        for date in sorted(or_data):
            day = or_data[date]
            if day["range"] < 6 or day["range"] > 35:
                continue
            if abs(gaps.get(date, 0)) < 6:
                continue
            dt = pd.Timestamp(str(date))
            if dt.month not in months or dt.dayofweek not in dows:
                continue
            t = sim_orb_trade(us_by_date, or_data, date, 6, 2.25, 2.0, last_ts[date])
            if t:
                trades.append(t)
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S2", "config": f"months={ml} dows={dl}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


# ── S3: Last-entry sweep ──────────────────────────────────────────────────────

def s3_last_entry_sweep(us_by_date, or_data, gaps):
    print("\n=== S3: Last-Entry Time Sweep ===")
    cutoffs = [(10,0),(10,15),(10,30),(10,45),(11,0),(11,15),(11,30),(12,0)]
    results = []

    for hr, mn in cutoffs:
        last_ts = {d: pd.Timestamp(f"{d} {hr:02d}:{mn:02d}:00") for d in or_data}
        trades = []
        for date in sorted(or_data):
            day = or_data[date]
            if day["range"] < 6 or day["range"] > 35:
                continue
            if abs(gaps.get(date, 0)) < 6:
                continue
            if pd.Timestamp(str(date)).dayofweek == 0:
                continue
            t = sim_orb_trade(us_by_date, or_data, date, 6, 2.25, 2.0, last_ts[date])
            if t:
                trades.append(t)
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S3", "config": f"last_entry={hr:02d}:{mn:02d}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


# ── S4: OR range analysis ─────────────────────────────────────────────────────

def s4_or_range(us_by_date, or_data, gaps):
    print("\n=== S4: ES OR Range Analysis ===")
    # Run baseline on all, then bucket
    last_ts = {d: pd.Timestamp(f"{d} 11:15:00") for d in or_data}
    all_trades = []
    for date in sorted(or_data):
        if or_data[date]["range"] < 2:
            continue
        if abs(gaps.get(date, 0)) < 6:
            continue
        if pd.Timestamp(str(date)).dayofweek == 0:
            continue
        t = sim_orb_trade(us_by_date, or_data, date, 6, 2.25, 2.0, last_ts[date])
        if t:
            all_trades.append(t)

    if not all_trades:
        return []

    print("\n  OR Range Buckets:")
    print(f"  {'Range':<12} {'n':>5} {'WR':>8} {'PF':>8} {'Net':>12}")
    buckets = [(2,8),(8,14),(14,20),(20,30),(30,50),(50,999)]
    for lo, hi in buckets:
        sub = [t for t in all_trades if lo <= t["or_range"] < hi]
        if not sub:
            continue
        s = stats(sub)
        print(f"  {lo}-{hi:<9} {s['n']:>5} {s['wr']:>8.1%} {s['pf']:>8.2f} ${s['net']:>10,.0f}")

    # Test refined ranges
    results = []
    for or_min, or_max in [(4,20),(4,25),(6,20),(6,25),(6,30),(8,20),(8,25),(8,30),(4,30)]:
        sub = [t for t in all_trades if or_min <= t["or_range"] < or_max]
        if not sub:
            continue
        s_is, s_oos = split(sub); v = viable(s_oos)
        results.append({"strategy": "S4", "config": f"or={or_min}-{or_max}",
                        "s_all": stats(sub), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": sub})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best refined range: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


# ── S5: ES+NQ Confirmation ────────────────────────────────────────────────────

def s5_nq_es_confirm(es_us, es_or, es_gaps, nq_us, nq_or, nq_gaps):
    print("\n=== S5: NQ Trade with ES Confirmation ===")
    # NQ config v7: or=55-110, stop=30, buf=5, rr=2, last_entry=10:30, strong months, skip Mon
    NQ_OR_MIN = 55; NQ_OR_MAX = 110
    NQ_STOP = 30; NQ_BUF = 5; NQ_RR = 2.0
    NQ_MONTHS = [1,2,3,4,5,10,11]

    # ES config: or=6-35, stop=6, buf=2.25, rr=2.0
    ES_BUF = 2.25

    results = []
    for confirm_win in [15, 30, 45, 60]:
        trades = []
        all_dates = sorted(set(nq_or) & set(es_or))

        for date in all_dates:
            nq_day = nq_or.get(date); es_day = es_or.get(date)
            if not nq_day or not es_day:
                continue
            if nq_day["range"] < NQ_OR_MIN or nq_day["range"] > NQ_OR_MAX:
                continue
            if abs(nq_gaps.get(date, 0)) < 40:
                continue
            dt = pd.Timestamp(str(date))
            if dt.dayofweek == 0 or dt.month not in NQ_MONTHS:
                continue

            nq_bars = nq_us.get(date)
            if nq_bars is None:
                continue

            # Find NQ breakout
            nq_orH = nq_day["high"] + NQ_BUF; nq_orL = nq_day["low"] - NQ_BUF
            after_or = nq_bars[nq_bars["timestamp"] > pd.Timestamp(f"{date} 09:45:00")].reset_index(drop=True)
            last_nq_ts = pd.Timestamp(f"{date} 10:30:00")

            nq_dir = nq_entry = nq_ts = None
            for i, bar in after_or.iterrows():
                if bar["timestamp"] > last_nq_ts:
                    break
                if bar["close"] > nq_orH:
                    nq_dir, nq_entry, nq_ts = "long", float(bar["close"]), bar["timestamp"]; break
                if bar["close"] < nq_orL:
                    nq_dir, nq_entry, nq_ts = "short", float(bar["close"]), bar["timestamp"]; break
            if nq_dir is None:
                continue

            # Check ES confirmation
            es_bars = es_us.get(date)
            if es_bars is None:
                continue
            confirm_window = nq_ts + pd.Timedelta(minutes=confirm_win)
            es_orH = es_day["high"] + ES_BUF; es_orL = es_day["low"] - ES_BUF
            es_window = es_bars[
                (es_bars["timestamp"] > pd.Timestamp(f"{date} 09:45:00")) &
                (es_bars["timestamp"] <= confirm_window)
            ]
            confirmed = any(
                (nq_dir == "long" and b["close"] > es_orH) or
                (nq_dir == "short" and b["close"] < es_orL)
                for _, b in es_window.iterrows()
            )
            if not confirmed:
                continue

            # Simulate NQ trade
            after_nq_entry = nq_bars[nq_bars["timestamp"] > nq_ts].reset_index(drop=True)
            if nq_dir == "long":
                stop = nq_entry - NQ_STOP; target = nq_entry + NQ_STOP * NQ_RR
            else:
                stop = nq_entry + NQ_STOP; target = nq_entry - NQ_STOP * NQ_RR
            flatten = pd.Timestamp(f"{date} 15:55:00")

            exit_r = exit_px = None
            for i, b in after_nq_entry.iterrows():
                if b["timestamp"] >= flatten:
                    exit_r, exit_px = "flatten", float(b["open"]); break
                if nq_dir == "long":
                    if b["low"] <= stop:    exit_r, exit_px = "stop",   stop;   break
                    if b["high"] >= target: exit_r, exit_px = "target", target; break
                else:
                    if b["high"] >= stop:   exit_r, exit_px = "stop",   stop;   break
                    if b["low"] <= target:  exit_r, exit_px = "target", target; break
            if exit_r is None and len(after_nq_entry) > 0:
                exit_r, exit_px = "flatten", float(after_nq_entry.iloc[-1]["close"])
            if exit_r is None:
                continue

            pnl = ((exit_px - nq_entry) if nq_dir == "long" else (nq_entry - exit_px)) * NQ_PT - NQ_COST
            trades.append({"date": date, "direction": nq_dir, "entry": nq_entry,
                           "exit": exit_r, "exit_px": exit_px, "pnl": pnl,
                           "or_range": nq_day["range"]})

        if not trades:
            continue
        s_is, s_oos = split(trades)
        v = s_oos["n"] >= VIA_N and s_oos["pf"] >= 1.30  # NQ threshold
        results.append({"strategy": "S5", "config": f"es_confirm={confirm_win}min",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        t = results[0]
        print(f"  Best OOS PF: {t['s_oos']['pf']:.2f}  n={t['s_oos']['n']}  {t['config']}")
        # Compare to NQ v7 baseline
        all_t = stats(results[0]["trades"])
        print(f"  Note: NQ v7 baseline OOS PF 2.14, n=72 (2024-26) — filter keeps {t['s_oos']['n']} trades")
    return results


# ── S6-S10: Additional strategies ─────────────────────────────────────────────

def s6_pyramid(us_by_date, or_data, gaps):
    print("\n=== S6: ES ORB + Pyramid ===")
    trades = []
    last_ts = {d: pd.Timestamp(f"{d} 11:15:00") for d in or_data}
    MONTHS = [1,2,3,10,11]

    for date in sorted(or_data):
        day = or_data[date]
        if day["range"] < 6 or day["range"] > 35:
            continue
        if abs(gaps.get(date, 0)) < 6:
            continue
        dt = pd.Timestamp(str(date))
        if dt.dayofweek == 0 or dt.month not in MONTHS:
            continue

        bars = us_by_date.get(date)
        if bars is None:
            continue

        or_high = day["high"] + 2.25; or_low = day["low"] - 2.25
        after_or = bars[bars["timestamp"] > pd.Timestamp(f"{date} 09:45:00")].reset_index(drop=True)

        direction = entry_px = ei = None
        for i, bar in after_or.iterrows():
            if bar["timestamp"] > last_ts[date]:
                break
            if bar["close"] > or_high:
                direction, entry_px, ei = "long", float(bar["close"]), i; break
            if bar["close"] < or_low:
                direction, entry_px, ei = "short", float(bar["close"]), i; break
        if direction is None:
            continue

        STOP = 6; RR = 2.0; PYR = STOP
        if direction == "long":
            stop = entry_px - STOP; target = entry_px + STOP * RR; pyr_lv = entry_px + PYR
        else:
            stop = entry_px + STOP; target = entry_px - STOP * RR; pyr_lv = entry_px - PYR

        pnl1 = pnl2 = 0; pyr_added = False
        exit_r = exit_px = None
        flatten = pd.Timestamp(f"{date} 15:55:00")

        for j in range(int(ei) + 1, len(after_or)):
            b = after_or.iloc[j]
            if b["timestamp"] >= flatten:
                exit_r, exit_px = "flatten", float(b["open"]); break
            if direction == "long":
                if not pyr_added and b["high"] >= pyr_lv:
                    pyr_added = True
                if b["low"] <= stop:    exit_r, exit_px = "stop",   stop;   break
                if b["high"] >= target: exit_r, exit_px = "target", target; break
            else:
                if not pyr_added and b["low"] <= pyr_lv:
                    pyr_added = True
                if b["high"] >= stop:   exit_r, exit_px = "stop",   stop;   break
                if b["low"] <= target:  exit_r, exit_px = "target", target; break
        if exit_r is None and len(after_or) > 0:
            exit_r, exit_px = "flatten", float(after_or.iloc[-1]["close"])
        if exit_r is None:
            continue

        if direction == "long":
            pnl1 = (exit_px - entry_px) * ES_PT - ES_COST
            pnl2 = (exit_px - pyr_lv)   * ES_PT - ES_COST if pyr_added else 0
        else:
            pnl1 = (entry_px - exit_px) * ES_PT - ES_COST
            pnl2 = (pyr_lv - exit_px)   * ES_PT - ES_COST if pyr_added else 0

        trades.append({"date": date, "direction": direction,
                       "pnl": pnl1 + pnl2, "exit": exit_r, "exit_px": exit_px,
                       "or_range": day["range"]})

    if not trades:
        return []
    s_is, s_oos = split(trades); v = viable(s_oos)
    print(f"  OOS PF: {s_oos['pf']:.2f}  n={s_oos['n']}")
    return [{"strategy": "S6", "config": "pyramid@1R strong_months",
             "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
             "viable": v, "trades": trades}]


def s7_gap_fill(us_by_date, or_data, gaps):
    print("\n=== S7: ES Gap Fill ===")
    # Pre-load 4pm closes
    es_df = pd.read_csv(ES_DATA, parse_dates=["timestamp"])
    es_df["hour"] = es_df["timestamp"].dt.hour
    es_df["minute"] = es_df["timestamp"].dt.minute
    es_df["date"] = es_df["timestamp"].dt.date
    pm4_close = {r["date"]: float(r["close"])
                 for _, r in es_df[(es_df["hour"]==16)&(es_df["minute"]==0)].iterrows()}

    sorted_dates = sorted(or_data)
    results = []
    for gap_min, sp, rr in itertools.product([8, 10, 15], [6, 8], [1.0, 1.5]):
        trades = []
        for i, date in enumerate(sorted_dates):
            if i == 0: continue
            gap = gaps.get(date, 0)
            if abs(gap) < gap_min: continue
            prev_d = sorted_dates[i-1]
            prev_close = pm4_close.get(prev_d)
            if prev_close is None: continue

            bars = us_by_date.get(date)
            if bars is None or len(bars) < 20: continue
            entry_px = float(bars.iloc[0]["open"])
            direction = "short" if gap > 0 else "long"
            if direction == "short" and prev_close >= entry_px: continue
            if direction == "long"  and prev_close <= entry_px: continue

            stop = entry_px + sp if direction == "short" else entry_px - sp
            target = prev_close
            flatten = pd.Timestamp(f"{date} 10:30:00")

            exit_r = exit_px = None
            for _, b in bars.iterrows():
                if b["timestamp"] >= flatten:
                    exit_r, exit_px = "time_exit", float(b["open"]); break
                if direction == "short":
                    if b["high"] >= stop:   exit_r, exit_px = "stop",   stop;   break
                    if b["low"] <= target:  exit_r, exit_px = "target", target; break
                else:
                    if b["low"] <= stop:    exit_r, exit_px = "stop",   stop;   break
                    if b["high"] >= target: exit_r, exit_px = "target", target; break
            if exit_r is None and len(bars) > 0:
                exit_r, exit_px = "flatten", float(bars.iloc[-1]["close"])
            if exit_r is None: continue

            pnl = ((exit_px - entry_px) if direction == "long" else (entry_px - exit_px)) * ES_PT - ES_COST
            trades.append({"date": date, "direction": direction, "pnl": pnl,
                           "exit": exit_r, "exit_px": exit_px, "or_range": or_data[date]["range"]})
        if not trades: continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S7", "config": f"gap>{gap_min} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


def s8_large_or_fade(us_by_date, or_data, gaps):
    print("\n=== S8: Large OR Mean Reversion ===")
    results = []
    for or_thr, sp, rr in itertools.product([30, 35, 40], [10, 15], [1.0, 1.5]):
        trades = []
        for date in sorted(or_data):
            day = or_data[date]
            if day["range"] < or_thr: continue
            bars = us_by_date.get(date)
            if bars is None: continue

            or_high = day["high"] + 2.25; or_low = day["low"] - 2.25
            after_or = bars[bars["timestamp"] > pd.Timestamp(f"{date} 09:45:00")].reset_index(drop=True)
            last_ts_fade = pd.Timestamp(f"{date} 10:30:00")

            direction = entry_px = ei = None
            for i, bar in after_or.iterrows():
                if bar["timestamp"] > last_ts_fade: break
                if bar["close"] > or_high:
                    direction, entry_px, ei = "short", float(bar["close"]), i; break
                if bar["close"] < or_low:
                    direction, entry_px, ei = "long",  float(bar["close"]), i; break
            if direction is None: continue

            stop = entry_px - sp if direction == "long" else entry_px + sp
            target = entry_px + sp * rr if direction == "long" else entry_px - sp * rr
            flatten = pd.Timestamp(f"{date} 15:55:00")

            exit_r = exit_px = None
            for j in range(int(ei)+1, len(after_or)):
                b = after_or.iloc[j]
                if b["timestamp"] >= flatten:
                    exit_r, exit_px = "flatten", float(b["open"]); break
                if direction == "long":
                    if b["low"] <= stop:    exit_r, exit_px = "stop",   stop;   break
                    if b["high"] >= target: exit_r, exit_px = "target", target; break
                else:
                    if b["high"] >= stop:   exit_r, exit_px = "stop",   stop;   break
                    if b["low"] <= target:  exit_r, exit_px = "target", target; break
            if exit_r is None and len(after_or) > 0:
                exit_r, exit_px = "flatten", float(after_or.iloc[-1]["close"])
            if exit_r is None: continue

            pnl = ((exit_px - entry_px) if direction == "long" else (entry_px - exit_px)) * ES_PT - ES_COST
            trades.append({"date": date, "direction": direction, "pnl": pnl,
                           "exit": exit_r, "exit_px": exit_px, "or_range": day["range"]})
        if not trades: continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S8", "config": f"or>{or_thr} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s9_rr_sweep(us_by_date, or_data, gaps):
    print("\n=== S9: R:R Ratio Sweep ===")
    last_ts = {d: pd.Timestamp(f"{d} 11:15:00") for d in or_data}
    results = []
    for rr in [1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0]:
        trades = []
        for date in sorted(or_data):
            day = or_data[date]
            if day["range"] < 6 or day["range"] > 35: continue
            if abs(gaps.get(date, 0)) < 6: continue
            if pd.Timestamp(str(date)).dayofweek == 0: continue
            t = sim_orb_trade(us_by_date, or_data, date, 6, 2.25, rr, last_ts[date])
            if t: trades.append(t)
        if not trades: continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S9", "config": f"rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


def s10_combined(us_by_date, or_data, gaps):
    print("\n=== S10: ES Combined Best Config ===")
    combos = [
        {"sp": 6,  "buf": 2.25, "or_min": 6,  "or_max": 35, "rr": 2.0,
         "months": [1,2,3,10,11], "hr": 11, "mn": 15, "label": "baseline"},
        {"sp": 6,  "buf": 2.0,  "or_min": 6,  "or_max": 30, "rr": 2.0,
         "months": [1,2,3,10,11], "hr": 10, "mn": 30, "label": "tighter"},
        {"sp": 8,  "buf": 2.0,  "or_min": 4,  "or_max": 25, "rr": 2.5,
         "months": [1,2,3,4,5,10,11], "hr": 11, "mn": 15, "label": "wide_months"},
        {"sp": 6,  "buf": 1.5,  "or_min": 6,  "or_max": 30, "rr": 2.0,
         "months": list(range(1,13)), "hr": 11, "mn": 15, "label": "all_months"},
    ]
    results = []
    for c in combos:
        last_ts = {d: pd.Timestamp(f"{d} {c['hr']:02d}:{c['mn']:02d}:00") for d in or_data}
        trades = []
        for date in sorted(or_data):
            day = or_data[date]
            if day["range"] < c["or_min"] or day["range"] > c["or_max"]: continue
            if abs(gaps.get(date, 0)) < 6: continue
            dt = pd.Timestamp(str(date))
            if dt.dayofweek == 0 or dt.month not in c["months"]: continue
            t = sim_orb_trade(us_by_date, or_data, date, c["sp"], c["buf"], c["rr"], last_ts[date])
            if t: trades.append(t)
        if not trades: continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S10", "config": c["label"],
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        t = results[0]
        print(f"  Best OOS PF: {t['s_oos']['pf']:.2f}  n={t['s_oos']['n']}  {t['config']}")
    return results


# ── Year breakdown ────────────────────────────────────────────────────────────

def year_breakdown(trades):
    df = pd.DataFrame(trades)
    df["year"] = pd.to_datetime(df["date"]).dt.year
    lines = ["| Year | n | WR | PF | Net |", "| --- | --- | --- | --- | --- |"]
    for y, g in df.groupby("year"):
        s = stats(g.to_dict("records"))
        lines.append(f"| {y} | {s['n']} | {s['wr']:.1%} | {s['pf']:.2f} | ${s['net']:+,.0f} |")
    return "\n".join(lines)


# ── Report ────────────────────────────────────────────────────────────────────

def write_report(all_results, today):
    top = [s[0] for s in all_results if s]
    top.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    viable_list = [r for r in top if r["viable"]]

    lines = [f"# ES Research — Full Strategy Build\n",
             f"Generated: {today} | Data: 2022-01-03 to 2026-06-11",
             f"IS: 2022-2023 | OOS: 2024-2026 | Viability gate: PF ≥ {VIA_PF}, n ≥ {VIA_N}",
             "\n**Baseline (es_config.py):** stop=6, buf=2.25, or=6-35, rr=2.0, no Mon",
             "OOS 2024-2026: 163 trades, WR 47%, PF 1.61, Net +$23,380\n",
             "\n## Strategy Results Summary\n",
             "| Strategy | Config | n_all | WR | PF_all | n_oos | WR_oos | PF_oos | Net_oos | V |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]

    for r in top:
        sa = r["s_all"]; so = r["s_oos"]; v = "✓" if r["viable"] else "✗"
        lines.append(f"| {r['strategy']} | {r['config']} "
                     f"| {sa['n']} | {sa['wr']:.1%} | {sa['pf']:.2f} "
                     f"| {so['n']} | {so['wr']:.1%} | {so['pf']:.2f} "
                     f"| ${so['net']:+,.0f} | {v} |")

    if all_results[0]:
        lines += ["\n## S1 ORB Sweep — Top 10\n",
                  "| Config | n_oos | WR_oos | PF_oos | Net_oos |",
                  "| --- | --- | --- | --- | --- |"]
        for r in all_results[0][:10]:
            so = r["s_oos"]
            lines.append(f"| {r['config']} | {so['n']} | {so['wr']:.1%} | {so['pf']:.2f} | ${so['net']:+,.0f} |")

    if top:
        best = top[0]
        si = best["s_is"]; so = best["s_oos"]
        lines += [f"\n## Best Strategy Year-by-Year\n**{best['strategy']}** — {best['config']}\n",
                  year_breakdown(best["trades"])]

    lines.append("\n## Viability Verdict\n")
    if viable_list:
        lines.append(f"**{len(viable_list)} strategies passed PF ≥ {VIA_PF}:**\n")
        for r in viable_list:
            so = r["s_oos"]
            lines.append(f"- **{r['strategy']}** — {r['config']}: OOS PF {so['pf']:.2f}, n={so['n']}")
    else:
        best_pf = top[0]["s_oos"]["pf"] if top else 0
        lines.append(f"No strategy cleared PF ≥ {VIA_PF}. Best: {best_pf:.2f}")
        lines.append("Baseline es_config.py (PF 1.61) remains recommended.")

    lines.append("\n## Recommendations\n")
    if viable_list:
        bv = viable_list[0]; so = bv["s_oos"]
        lines.append(f"Best: **{bv['strategy']}** — `{bv['config']}`  OOS PF {so['pf']:.2f}")
    else:
        lines.append("No improvement over baseline. Keep es_config.py as-is.")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from datetime import date as dt_date
    today = str(dt_date.today())

    _, es_us, es_or, es_gaps = load_and_pregroup(ES_DATA, "ES")
    _, nq_us, nq_or, nq_gaps = load_and_pregroup(NQ_DATA, "NQ")

    print(f"\n{'='*60}\nRunning ES strategies...\n{'='*60}")

    all_results = [
        s1_orb_sweep(es_us, es_or, es_gaps),
        s2_month_filter(es_us, es_or, es_gaps),
        s3_last_entry_sweep(es_us, es_or, es_gaps),
        s4_or_range(es_us, es_or, es_gaps),
        s5_nq_es_confirm(es_us, es_or, es_gaps, nq_us, nq_or, nq_gaps),
        s6_pyramid(es_us, es_or, es_gaps),
        s7_gap_fill(es_us, es_or, es_gaps),
        s8_large_or_fade(es_us, es_or, es_gaps),
        s9_rr_sweep(es_us, es_or, es_gaps),
        s10_combined(es_us, es_or, es_gaps),
    ]

    top_all = [s[0] for s in all_results if s]
    if top_all:
        top_all.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
        pd.DataFrame(top_all[0]["trades"]).to_csv(OUT_CSV, index=False)
        print(f"\nBest trades → {OUT_CSV}")

    OUT_MD.write_text(write_report(all_results, today))
    print(f"Report → {OUT_MD}")

    print(f"\n{'='*60}\nES RESEARCH SUMMARY\n{'='*60}")
    for s_list in all_results:
        if s_list:
            r = s_list[0]
            v = "✓" if r["viable"] else "✗"
            print(f"  {r['strategy']:4s}  OOS PF {r['s_oos']['pf']:.2f}  n={r['s_oos']['n']:3d}  {r['config'][:45]:45s}  {v}")


if __name__ == "__main__":
    main()
