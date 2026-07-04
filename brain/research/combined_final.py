"""
brain/research/combined_final.py

Combines the three best findings:
  1. Skip<3 gate (from threshold_sweep.py: OOS PF 3.31)
  2. Hard gate — skip 9:45 first-bar entries (from entry_time_score.py)
  3. Breakout distance ≤ threshold (to test)
  4. OR close position alignment (to test)

Tests all meaningful combinations to find the best implementable config.

IS years: [2022, 2023, 2024]
OOS years: [2025, 2026]
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from datetime import time as dtime, timedelta
import config

DATA = "data/nq_full.csv"
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

ORB_MIN    = 55.0
ORB_MAX    = 110.0
ORB_BUFFER = 4.0
STOP_PTS   = 22.0
STOP_BUF   = 5.0
RR         = 3.0

# ── confidence score ──────────────────────────────────────────────────────────
def compute_score(pivot, vwap_open, or_hi, or_lo, or_close, direction, slope):
    """4-component confidence score. Returns 0-4."""
    sc = 0
    mid = (or_hi + or_lo) / 2
    r1 = pivot + (pivot - or_lo)
    s1 = pivot - (or_hi - pivot)
    r2 = pivot + 2 * (pivot - or_lo)
    s2 = pivot - 2 * (or_hi - pivot)

    if direction == "long":
        if or_close > pivot:   sc += 1
        if or_close > vwap_open: sc += 1
        if r1 <= or_close <= r2: sc += 1
        if slope > 0:           sc += 1
    else:
        if or_close < pivot:   sc += 1
        if or_close < vwap_open: sc += 1
        if s2 <= or_close <= s1: sc += 1
        if slope < 0:           sc += 1
    return sc

# ── load data ─────────────────────────────────────────────────────────────────
print("Loading data...")
df = pd.read_csv(DATA)
df["timestamp"] = pd.to_datetime(df["timestamp"].astype(str).str[:19])
df = df.sort_values("timestamp").reset_index(drop=True)
df["date"] = df["timestamp"].dt.date
df["time"] = df["timestamp"].dt.time
print(f"  {len(df):,} bars\n")

# ── daily signals with full metadata ─────────────────────────────────────────
print("Computing pivots and VWAP...")
signals = []

prev_pivot = None
prev_vwap  = None

for day, grp in df.groupby("date"):
    grp = grp.sort_values("timestamp").reset_index(drop=True)
    yr  = day.year

    # Compute today's RTH pivot (prior day high/low/close — use yesterday's values)
    # We'll use the CURRENT day's RTH close + yesterday's pivot from prev iteration
    rth = grp[(grp["time"] >= dtime(9,30)) & (grp["time"] <= dtime(16,0))]

    # OR window
    or_bars = grp[(grp["time"] >= dtime(9,30)) & (grp["time"] <= dtime(9,44))]
    if len(or_bars) < 5 or prev_pivot is None or prev_vwap is None:
        # Compute pivot for next day
        if len(rth) > 0:
            prev_pivot = (rth["high"].max() + rth["low"].min() + rth["close"].iloc[-1]) / 3
            # VWAP: price-volume weighted average over RTH
            pv = ((rth["high"] + rth["low"] + rth["close"]) / 3) * rth["volume"]
            prev_vwap = pv.sum() / rth["volume"].sum() if rth["volume"].sum() > 0 else rth["close"].mean()
        continue

    or_hi  = or_bars["high"].max()
    or_lo  = or_bars["low"].min()
    or_rng = or_hi - or_lo
    if or_rng < ORB_MIN or or_rng > ORB_MAX:
        if len(rth) > 0:
            prev_pivot = (rth["high"].max() + rth["low"].min() + rth["close"].iloc[-1]) / 3
            pv = ((rth["high"] + rth["low"] + rth["close"]) / 3) * rth["volume"]
            prev_vwap = pv.sum() / rth["volume"].sum() if rth["volume"].sum() > 0 else rth["close"].mean()
        continue

    # VWAP slope from 9:35 to 9:44
    vwap_bars = []
    cum_pv = 0; cum_v = 0
    for _, b in or_bars.iterrows():
        tp = (b["high"] + b["low"] + b["close"]) / 3
        cum_pv += tp * b["volume"]
        cum_v  += b["volume"]
        vwap_bars.append((b["time"], cum_pv / cum_v if cum_v > 0 else tp))
    vwap_935 = next((v for t,v in vwap_bars if t >= dtime(9,35)), vwap_bars[0][1])
    vwap_944 = vwap_bars[-1][1]
    slope = vwap_944 - vwap_935

    or_close = or_bars.iloc[-1]["close"]
    or_pos   = (or_close - or_lo) / or_rng if or_rng > 0 else 0.5

    # Entry bars 9:45 → 10:30
    entry_bars = grp[(grp["time"] >= dtime(9,45)) & (grp["time"] <= dtime(10,30))]

    for _, bar in entry_bars.iterrows():
        ts = bar["time"]
        c  = bar["close"]
        h  = bar["high"]
        lo_b = bar["low"]
        o  = bar["open"]

        direction = None
        edge = None
        if c > or_hi + ORB_BUFFER:
            direction = "long"
            edge = or_hi
        elif c < or_lo - ORB_BUFFER:
            direction = "short"
            edge = or_lo
        if direction is None:
            continue

        score = compute_score(prev_pivot, prev_vwap, or_hi, or_lo, or_close, direction, slope)
        bdist = abs(c - edge)
        body_range = h - lo_b
        body_pct = abs(c - o) / body_range if body_range > 0 else 0.5

        # Simulate
        if direction == "long":
            entry  = c
            stop   = entry - STOP_PTS - STOP_BUF
            target = entry + (STOP_PTS + STOP_BUF) * RR
        else:
            entry  = c
            stop   = entry + STOP_PTS + STOP_BUF
            target = entry - (STOP_PTS + STOP_BUF) * RR

        future = grp[grp["time"] > ts]
        outcome = None
        for _, fb in future.iterrows():
            if direction == "long":
                if fb["low"] <= stop:   outcome = "loss"; break
                if fb["high"] >= target: outcome = "win"; break
            else:
                if fb["high"] >= stop:   outcome = "loss"; break
                if fb["low"] <= target:  outcome = "win"; break

        if outcome is None:
            continue

        pnl_pts = (STOP_PTS + STOP_BUF) * RR if outcome == "win" else -(STOP_PTS + STOP_BUF)
        pnl_usd = pnl_pts * 20

        signals.append({
            "date": day, "year": yr, "time": ts,
            "direction": direction, "outcome": outcome,
            "pnl": pnl_usd, "score": score,
            "bdist": bdist, "or_pos": or_pos, "body_pct": body_pct,
        })
        break  # one trade per day

    # Update pivot/vwap for next day
    if len(rth) > 0:
        prev_pivot = (rth["high"].max() + rth["low"].min() + rth["close"].iloc[-1]) / 3
        pv = ((rth["high"] + rth["low"] + rth["close"]) / 3) * rth["volume"]
        prev_vwap = pv.sum() / rth["volume"].sum() if rth["volume"].sum() > 0 else rth["close"].mean()

print(f"  {len(signals)} signals computed\n")
sig = pd.DataFrame(signals)
sig["is_oos"] = sig["year"].isin(OOS_YEARS)

# ── helper ────────────────────────────────────────────────────────────────────
def stats(df_sub, label, base_n=None):
    n = len(df_sub)
    if n == 0:
        return f"  {label:<45}  N=  0  —"
    gw = df_sub[df_sub["pnl"] > 0]["pnl"].sum()
    gl = abs(df_sub[df_sub["pnl"] <= 0]["pnl"].sum())
    pf  = gw / gl if gl > 0 else float("inf")
    wr  = (df_sub["pnl"] > 0).mean()
    net = df_sub["pnl"].sum()
    tag = " [N<15]" if n < 15 else ""
    star = " ★" if pf >= 3.0 else ""
    dpf = ""
    if base_n is not None:
        pct = n / base_n
        dpf = f"  ({pct:.0%} of base)"
    return f"  {label:<45}  N={n:>3}  WR={wr:.0%}  PF={pf:.2f}  Net=${net:>+8,.0f}{star}{tag}{dpf}"

print("=" * 80)
print("COMBINED FILTER TEST — OOS 2025-2026")
print("=" * 80)

oos = sig[sig["is_oos"]]
iss = sig[~sig["is_oos"]]
n_oos = len(oos)
n_is  = len(iss)
base_pf_oos = oos[oos["pnl"]>0]["pnl"].sum() / abs(oos[oos["pnl"]<=0]["pnl"].sum())
base_pf_is  = iss[iss["pnl"]>0]["pnl"].sum() / abs(iss[iss["pnl"]<=0]["pnl"].sum())

# ── filter functions ──────────────────────────────────────────────────────────
def f_skip3(d):      return d[d["score"] >= 3]
def f_gate(d):       return d[d["time"] >= dtime(9,46)]
def f_hot(d):        return d[(d["time"] >= dtime(9,46)) & (d["time"] <= dtime(9,59))]
def f_bdist(d, t):   return d[d["bdist"] <= t]
def f_aligned(d):
    return pd.concat([
        d[(d["direction"]=="long")  & (d["or_pos"] > 0.5)],
        d[(d["direction"]=="short") & (d["or_pos"] < 0.5)],
    ])
def f_body(d, t):    return d[d["body_pct"] >= t]

print(f"\n  Base OOS: N={n_oos}, PF={base_pf_oos:.2f}")
print(f"  Base IS:  N={n_is}, PF={base_pf_is:.2f}\n")

# Single filters
tests = []
for lbl, fn in [
    ("Baseline", lambda d: d),
    ("Skip<3 (score≥3 only)", f_skip3),
    ("Hard gate (skip 9:45)", f_gate),
    ("Hot window (9:46-9:59)", f_hot),
    ("Breakout dist ≤ 10pt",  lambda d: f_bdist(d, 10)),
    ("Breakout dist ≤ 15pt",  lambda d: f_bdist(d, 15)),
    ("Breakout dist ≤ 20pt",  lambda d: f_bdist(d, 20)),
    ("OR pos aligned",        f_aligned),
    ("Body ≥ 50%",            lambda d: f_body(d, 0.5)),
]:
    sub_oos = fn(oos)
    sub_is  = fn(iss)
    tests.append((lbl, sub_oos, sub_is))

print("── Single filters ───────────────────────────────────────────────────────────")
print(f"  {'Filter':<45}  {'N':>3}  {'WR':>4}  {'PF':>6}  {'Net':>10}")
for lbl, sub, _ in tests:
    print(stats(sub, lbl, n_oos))

print("\n── IS validation (same filters) ────────────────────────────────────────────")
for lbl, _, sub in tests:
    print(stats(sub, lbl, n_is))

# ── Combinations with skip<3 ─────────────────────────────────────────────────
print("\n── Combinations with skip<3 ─────────────────────────────────────────────────")
print("OOS:")
s3 = f_skip3(oos)
combos = [
    ("skip<3 baseline",             s3),
    ("skip<3 + hard gate",          f_gate(s3)),
    ("skip<3 + hot window",         f_hot(s3)),
    ("skip<3 + bdist ≤ 10",         f_bdist(s3, 10)),
    ("skip<3 + bdist ≤ 15",         f_bdist(s3, 15)),
    ("skip<3 + or_pos aligned",     f_aligned(s3)),
    ("skip<3 + body ≥ 50%",         f_body(s3, 0.5)),
    ("skip<3 + gate + bdist ≤ 15",  f_bdist(f_gate(s3), 15)),
    ("skip<3 + gate + or_pos",      f_aligned(f_gate(s3))),
    ("skip<3 + gate + body ≥ 50%",  f_body(f_gate(s3), 0.5)),
    ("skip<3 + hot + bdist ≤ 15",   f_bdist(f_hot(s3), 15)),
    ("skip<3 + hot + or_pos",       f_aligned(f_hot(s3))),
    ("skip<3 + all (gate+bdist+pos)", f_aligned(f_bdist(f_gate(s3), 15))),
]
for lbl, sub in combos:
    print(stats(sub, lbl, n_oos))

print("\nIS (same combos for overfitting check):")
s3_is = f_skip3(iss)
combos_is = [
    ("skip<3 baseline",             s3_is),
    ("skip<3 + hard gate",          f_gate(s3_is)),
    ("skip<3 + hot window",         f_hot(s3_is)),
    ("skip<3 + bdist ≤ 10",         f_bdist(s3_is, 10)),
    ("skip<3 + bdist ≤ 15",         f_bdist(s3_is, 15)),
    ("skip<3 + or_pos aligned",     f_aligned(s3_is)),
    ("skip<3 + body ≥ 50%",         f_body(s3_is, 0.5)),
    ("skip<3 + gate + bdist ≤ 15",  f_bdist(f_gate(s3_is), 15)),
    ("skip<3 + gate + or_pos",      f_aligned(f_gate(s3_is))),
    ("skip<3 + gate + body ≥ 50%",  f_body(f_gate(s3_is), 0.5)),
    ("skip<3 + hot + bdist ≤ 15",   f_bdist(f_hot(s3_is), 15)),
    ("skip<3 + hot + or_pos",       f_aligned(f_hot(s3_is))),
    ("skip<3 + all (gate+bdist+pos)", f_aligned(f_bdist(f_gate(s3_is), 15))),
]
for lbl, sub in combos_is:
    print(stats(sub, lbl, n_is))

# ── Dollar projection with sizing ────────────────────────────────────────────
print("\n── Dollar projection (skip<3, 1c flat — per year OOS avg) ─────────────────")
best_combos = [
    ("skip<3 baseline",            f_skip3(oos)),
    ("skip<3 + hard gate",         f_gate(f_skip3(oos))),
    ("skip<3 + hot window",        f_hot(f_skip3(oos))),
    ("skip<3 + gate + or_pos",     f_aligned(f_gate(f_skip3(oos)))),
    ("skip<3 + gate + bdist≤15",   f_bdist(f_gate(f_skip3(oos)), 15)),
]
n_oos_years = len(OOS_YEARS)
for lbl, sub in best_combos:
    n = len(sub)
    net = sub["pnl"].sum()
    gw  = sub[sub["pnl"] > 0]["pnl"].sum()
    gl  = abs(sub[sub["pnl"] <= 0]["pnl"].sum())
    pf  = gw / gl if gl > 0 else float("inf")
    wr  = (sub["pnl"] > 0).mean()
    net_yr = net / n_oos_years
    n_yr   = n / n_oos_years
    print(f"  {lbl:<45}  PF={pf:.2f}  ${net_yr:>+8,.0f}/yr  {n_yr:.0f} trades/yr")

print("\n══ FINAL VERDICT ═══════════════════════════════════════════════════════════")
# Find best config by OOS PF with N≥15 and positive net
all_results = [
    ("skip<3 only",                f_skip3(oos),                           f_skip3(iss)),
    ("skip<3 + hard gate",         f_gate(f_skip3(oos)),                   f_gate(f_skip3(iss))),
    ("skip<3 + hot window",        f_hot(f_skip3(oos)),                    f_hot(f_skip3(iss))),
    ("skip<3 + gate + or_pos",     f_aligned(f_gate(f_skip3(oos))),        f_aligned(f_gate(f_skip3(iss)))),
    ("skip<3 + gate + bdist≤15",   f_bdist(f_gate(f_skip3(oos)), 15),      f_bdist(f_gate(f_skip3(iss)), 15)),
    ("skip<3 + all filters",       f_aligned(f_bdist(f_gate(f_skip3(oos)),15)), f_aligned(f_bdist(f_gate(f_skip3(iss)),15))),
]

print(f"\n  {'Config':<45}  {'OOS PF':>7}  {'IS PF':>7}  {'OOS N':>6}  {'$/yr':>9}")
print("  " + "-" * 80)
for lbl, oos_sub, is_sub in all_results:
    n_o = len(oos_sub)
    n_i = len(is_sub)
    if n_o == 0: continue
    gw_o = oos_sub[oos_sub["pnl"]>0]["pnl"].sum()
    gl_o = abs(oos_sub[oos_sub["pnl"]<=0]["pnl"].sum())
    pf_o = gw_o / gl_o if gl_o > 0 else float("inf")
    gw_i = is_sub[is_sub["pnl"]>0]["pnl"].sum()
    gl_i = abs(is_sub[is_sub["pnl"]<=0]["pnl"].sum())
    pf_i = gw_i / gl_i if gl_i > 0 else 0.0
    net_o = oos_sub["pnl"].sum()
    yr_o  = net_o / len(OOS_YEARS)
    tag   = " ★ TARGET" if pf_o >= 3.0 and n_o >= 15 else ""
    print(f"  {lbl:<45}  {pf_o:>7.2f}  {pf_i:>7.2f}  {n_o:>6}  ${yr_o:>+8,.0f}{tag}")
