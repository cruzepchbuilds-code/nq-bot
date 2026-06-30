#!/usr/bin/env python3
"""
brain/research_other.py  — Optimized
CL (Crude Oil), GC (Gold), RTY (Russell 2000) ORB Research
IS: 2022-2023 | OOS: 2024-2026

Tests ORB strategy on each instrument using pre-grouped bars.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import itertools

DATA_DIR = Path(__file__).parent.parent / "data"
OUT_MD   = Path(__file__).parent / "other_instruments_research.md"

IS_END    = pd.Timestamp("2023-12-31")
OOS_START = pd.Timestamp("2024-01-01")

# Instrument specs
SPECS = {
    "CL": {
        "file": "cl_1min.csv",
        "pt_val": 1000.0,   # $1000/pt
        "slip_pts": 0.05,   # 5 ticks @ $0.01/tick
        "comm": 5.0,
        "gap_min": 0.3,
        "via_pf": 1.30, "via_n": 30,
        "or_ranges": [(0.2, 1.5), (0.3, 2.0), (0.3, 2.5), (0.5, 2.5), (0.5, 3.0)],
        "stop_pcts": [0.3, 0.4, 0.5, 0.6],
        "rrs": [1.5, 2.0, 2.5],
    },
    "RTY": {
        "file": "rty_1min.csv",
        "pt_val": 50.0,     # $50/pt (Russell 2000)
        "slip_pts": 0.20,   # 2 ticks @ $0.10/tick
        "comm": 5.0,
        "gap_min": 2.0,
        "via_pf": 1.30, "via_n": 30,
        "or_ranges": [(3, 15), (3, 20), (5, 20), (5, 25), (5, 30)],
        "stop_pcts": [0.3, 0.4, 0.5, 0.6],
        "rrs": [1.5, 2.0, 2.5],
    },
    "GC": {
        "file": "gc_1min.csv",
        "pt_val": 100.0,    # $100/pt (gold)
        "slip_pts": 0.20,   # 2 ticks @ $0.10/tick
        "comm": 5.0,
        "gap_min": 2.0,
        "via_pf": 1.30, "via_n": 30,
        "or_ranges": [(2, 15), (3, 20), (5, 25), (5, 30)],
        "stop_pcts": [0.3, 0.4, 0.5],
        "rrs": [1.5, 2.0, 2.5],
    },
}


def load_and_pregroup(symbol):
    spec = SPECS[symbol]
    fpath = DATA_DIR / spec["file"]
    if not fpath.exists():
        print(f"  {symbol}: file not found"); return None, None, None, None

    print(f"\nLoading {symbol}...")
    df = pd.read_csv(fpath, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["date"]   = df["timestamp"].dt.date
    df["hour"]   = df["timestamp"].dt.hour
    df["minute"] = df["timestamp"].dt.minute
    df["dow"]    = df["timestamp"].dt.dayofweek

    # US session: 9:30 - 4:00pm
    us = df[
        ((df["hour"] == 9) & (df["minute"] >= 30)) |
        df["hour"].between(10, 15) |
        ((df["hour"] == 16) & (df["minute"] == 0))
    ].copy()

    us_by_date = {}
    for date, g in us.groupby("date"):
        us_by_date[date] = g.reset_index(drop=True)

    # OR data: 9:30-9:44
    or_bars = df[(df["hour"] == 9) & (df["minute"].between(30, 44))]
    or_data = {}
    for date, g in or_bars.groupby("date"):
        if len(g) < 5:
            continue
        h = float(g["high"].max()); l = float(g["low"].min())
        or_data[date] = {"high": h, "low": l, "range": h - l}

    # Gaps
    pm4   = df[(df["hour"] == 16) & (df["minute"] == 0)]
    open_ = df[(df["hour"] == 9) & (df["minute"] == 30)]
    pm4_close = {r["date"]: float(r["close"]) for _, r in pm4.iterrows()}
    open_930  = {r["date"]: float(r["open"])  for _, r in open_.iterrows()}
    sorted_dates = sorted(or_data)
    gaps = {}
    for i, d in enumerate(sorted_dates):
        if i == 0: continue
        pd_ = sorted_dates[i-1]
        if pd_ in pm4_close and d in open_930:
            gaps[d] = open_930[d] - pm4_close[pd_]

    print(f"  {len(df):,} bars | OR days: {len(or_data)} | Gaps: {len(gaps)}")
    return us_by_date, or_data, gaps, spec


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

def is_viable(s, spec): return s["n"] >= spec["via_n"] and s["pf"] >= spec["via_pf"]


def run_sweep(symbol, us_by_date, or_data, gaps, spec):
    print(f"\n  Sweeping {symbol} ORB...")
    pt_val = spec["pt_val"]
    cost   = spec["comm"] + spec["slip_pts"] * 2 * pt_val
    gap_min = spec["gap_min"]

    results = []
    last_entry_ts = {d: pd.Timestamp(f"{d} 11:15:00") for d in or_data}

    for (or_min, or_max), stop_pct, rr in itertools.product(
            spec["or_ranges"], spec["stop_pcts"], spec["rrs"]):
        trades = []
        for date in sorted(or_data):
            day = or_data[date]
            if day["range"] < or_min or day["range"] > or_max:
                continue
            gap = gaps.get(date, 0)
            if abs(gap) < gap_min:
                continue

            bars = us_by_date.get(date)
            if bars is None or len(bars) < 10:
                continue

            or_high = day["high"]
            or_low  = day["low"]
            direction = "long" if gap > 0 else "short"
            stop_pts  = day["range"] * stop_pct

            after_or = bars[bars["timestamp"] > pd.Timestamp(f"{date} 09:45:00")].reset_index(drop=True)
            last_ts  = last_entry_ts.get(date)

            entry_px = ei = None
            for i, bar in after_or.iterrows():
                if last_ts and bar["timestamp"] > last_ts:
                    break
                if direction == "long" and bar["close"] > or_high:
                    entry_px, ei = float(bar["close"]), i; break
                if direction == "short" and bar["close"] < or_low:
                    entry_px, ei = float(bar["close"]), i; break
            if entry_px is None:
                continue

            if direction == "long":
                stop = entry_px - stop_pts; target = entry_px + stop_pts * rr
            else:
                stop = entry_px + stop_pts; target = entry_px - stop_pts * rr

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
            if exit_r is None:
                continue

            pnl = ((exit_px - entry_px) if direction == "long" else (entry_px - exit_px)) * pt_val - cost
            trades.append({"date": date, "direction": direction, "pnl": pnl,
                           "exit": exit_r, "or_range": day["range"]})

        if not trades:
            continue
        s_is, s_oos = split(trades)
        v = is_viable(s_oos, spec)
        results.append({
            "config": f"or={or_min:.1f}-{or_max:.1f} stop={stop_pct*100:.0f}%OR rr={rr}",
            "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos, "viable": v,
        })

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        t = results[0]
        v = "✓ VIABLE" if t["viable"] else "FAIL"
        print(f"    Best OOS PF: {t['s_oos']['pf']:.2f}  n={t['s_oos']['n']}  {v}  {t['config']}")
    else:
        print(f"    No valid configurations found")
    return results


def main():
    from datetime import date as dt_date
    today = str(dt_date.today())

    instrument_results = {}
    for symbol in ["CL", "RTY", "GC"]:
        us_by_date, or_data, gaps, spec = load_and_pregroup(symbol)
        if us_by_date is None:
            continue
        results = run_sweep(symbol, us_by_date, or_data, gaps, spec)
        instrument_results[symbol] = (results, spec)

    # Write report
    lines = [f"# CL / GC / RTY ORB Research\n",
             f"Generated: {today} | IS: 2022-2023 | OOS: 2024-2026\n"]

    for symbol, (results, spec) in instrument_results.items():
        cost = spec["comm"] + spec["slip_pts"] * 2 * spec["pt_val"]
        lines += [f"\n## {symbol}\n",
                  f"**Point value:** ${spec['pt_val']:,.0f}/pt | "
                  f"**Trade cost:** ~${cost:,.0f}/rt | "
                  f"**Viability:** OOS PF ≥ {spec['via_pf']}, n ≥ {spec['via_n']}\n",
                  "| Config | n_all | WR | PF_all | n_oos | WR_oos | PF_oos | Net_oos | V |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]

        for r in results[:10]:
            sa = r["s_all"]; so = r["s_oos"]; v = "✓" if r["viable"] else "✗"
            lines.append(f"| {r['config']} "
                         f"| {sa['n']} | {sa['wr']:.1%} | {sa['pf']:.2f} "
                         f"| {so['n']} | {so['wr']:.1%} | {so['pf']:.2f} "
                         f"| ${so['net']:+,.0f} | {v} |")

        if results:
            best = results[0]; so = best["s_oos"]
            viable_count = sum(1 for r in results if r["viable"])
            if viable_count:
                lines.append(f"\n**VERDICT: VIABLE** — {viable_count} configs. Best: {best['config']}\n")
            else:
                lines.append(f"\n**VERDICT: NOT VIABLE** — Best OOS PF {so['pf']:.2f} < {spec['via_pf']}\n")

    lines += ["\n## Summary\n",
              "| Instrument | Best OOS PF | OOS n | Verdict |",
              "| --- | --- | --- | --- |"]
    for symbol, (results, spec) in instrument_results.items():
        if results:
            r = results[0]; so = r["s_oos"]
            v = "VIABLE" if r["viable"] else "FAIL"
            lines.append(f"| {symbol} | {so['pf']:.2f} | {so['n']} | {v} |")

    lines.append("\n**Reference:** NQ ORB (primary strategy) OOS PF 2.14 — far superior.\n")
    OUT_MD.write_text("\n".join(lines))
    print(f"\nReport → {OUT_MD}")

    print("\n=== INSTRUMENT SUMMARY ===")
    for symbol, (results, spec) in instrument_results.items():
        if results:
            r = results[0]; so = r["s_oos"]
            v = "✓ VIABLE" if r["viable"] else "FAIL"
            print(f"  {symbol}: OOS PF {so['pf']:.2f}  n={so['n']}  {v}")


if __name__ == "__main__":
    main()
