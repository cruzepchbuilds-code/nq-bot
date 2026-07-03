"""
brain/research/breakout_quality.py

Tests breakout QUALITY filters for NQ ORB:
  1. Breakout distance: how far the entry bar closed beyond the OR edge (small vs large)
  2. OR close position: where the 9:44 bar (last OR bar) sat within the OR range
  3. Body quality: body% of entry bar candle (avoids wick traps)

Goal: find a filter that raises OOS PF toward 3-5 without killing too much volume.

IS years: [2022, 2023, 2024]
OOS years: [2025, 2026]
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from datetime import time as dtime, date, timedelta
import config

DATA = "data/nq_full.csv"
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

# ── load data ─────────────────────────────────────────────────────────────────
print("Loading data...")
df = pd.read_csv(DATA)
df["timestamp"] = pd.to_datetime(df["timestamp"].astype(str).str[:19])
df = df.sort_values("timestamp").reset_index(drop=True)
df["date"] = df["timestamp"].dt.date
df["time"] = df["timestamp"].dt.time
print(f"  {len(df):,} bars\n")

# ── signal extraction ─────────────────────────────────────────────────────────

ORB_MIN = 55.0
ORB_MAX = 110.0
ORB_BUFFER = 4.0
STOP_PTS = 22.0
STOP_BUFFER = 5.0
RR = 3.0

signals = []

for day, grp in df.groupby("date"):
    grp = grp.sort_values("timestamp").reset_index(drop=True)
    yr = day.year

    # OR window: 9:30-9:44 (close of 9:44 bar = end of OR)
    or_bars = grp[(grp["time"] >= dtime(9,30)) & (grp["time"] <= dtime(9,44))]
    if len(or_bars) < 5:
        continue
    orHi = or_bars["high"].max()
    orLo = or_bars["low"].min()
    orRange = orHi - orLo
    if orRange < ORB_MIN or orRange > ORB_MAX:
        continue

    # Last OR bar close position within range (0=bottom, 1=top)
    last_or = or_bars.iloc[-1]
    or_close = last_or["close"]
    or_pos = (or_close - orLo) / orRange if orRange > 0 else 0.5

    # Entry bars: 9:45 onward until 10:30
    entry_bars = grp[(grp["time"] >= dtime(9,45)) & (grp["time"] <= dtime(10,30))]

    for _, bar in entry_bars.iterrows():
        ts = bar["time"]
        c  = bar["close"]
        o  = bar["open"]
        h  = bar["high"]
        lo_b = bar["low"]

        direction = None
        edge = None
        if c > orHi + ORB_BUFFER:
            direction = "long"
            edge = orHi
        elif c < orLo - ORB_BUFFER:
            direction = "short"
            edge = orLo

        if direction is None:
            continue

        # Breakout distance: how far close is from OR edge
        breakout_dist = abs(c - edge)

        # Body quality: body% of candle range
        candle_range = h - lo_b
        body = abs(c - o)
        body_pct = body / candle_range if candle_range > 0 else 0.5

        # Simulate trade outcome
        if direction == "long":
            entry = c
            stop  = entry - STOP_PTS - STOP_BUFFER
            target = entry + (STOP_PTS + STOP_BUFFER) * RR
        else:
            entry = c
            stop  = entry + STOP_PTS + STOP_BUFFER
            target = entry - (STOP_PTS + STOP_BUFFER) * RR

        # Walk forward bars to determine outcome
        future = grp[grp["time"] > ts]
        outcome = None
        for _, fb in future.iterrows():
            if direction == "long":
                if fb["low"] <= stop:
                    outcome = "loss"
                    break
                if fb["high"] >= target:
                    outcome = "win"
                    break
            else:
                if fb["high"] >= stop:
                    outcome = "loss"
                    break
                if fb["low"] <= target:
                    outcome = "win"
                    break

        if outcome is None:
            continue  # open trade at EOD - skip

        pnl_pts = (STOP_PTS + STOP_BUFFER) * RR if outcome == "win" else -(STOP_PTS + STOP_BUFFER)
        pnl_usd = pnl_pts * 20  # NQ $20/pt

        signals.append({
            "date": day,
            "year": yr,
            "time": ts,
            "direction": direction,
            "outcome": outcome,
            "pnl": pnl_usd,
            "breakout_dist": breakout_dist,
            "or_pos": or_pos,
            "body_pct": body_pct,
            "or_range": orRange,
        })
        break  # one trade per day

print(f"Signals: {len(signals)} total\n")
sig_df = pd.DataFrame(signals)
sig_df["is_oos"] = sig_df["year"].isin(OOS_YEARS)

# ── helper ────────────────────────────────────────────────────────────────────
def pf_stats(df_sub, label, total_n=None):
    n = len(df_sub)
    if n == 0:
        return f"  {label:<40} N=  0  —"
    wins   = df_sub[df_sub["pnl"] > 0]["pnl"].sum()
    losses = abs(df_sub[df_sub["pnl"] <= 0]["pnl"].sum())
    pf  = wins / losses if losses > 0 else float("inf")
    wr  = (df_sub["pnl"] > 0).mean()
    net = df_sub["pnl"].sum()
    tag = f"[N<15]" if n < 15 else ""
    pct = f"({n/total_n:.0%} of trades)" if total_n else ""
    return f"  {label:<40} N={n:>3}  WR={wr:.0%}  PF={pf:.2f}  Net=${net:>+8,.0f}  {tag} {pct}"

def section(title):
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)

# ── A. Breakout Distance Analysis ─────────────────────────────────────────────
section("A. Breakout Distance (pts beyond OR edge at entry)")

for split in ["IS", "OOS"]:
    sub = sig_df[~sig_df["is_oos"]] if split == "IS" else sig_df[sig_df["is_oos"]]
    n_total = len(sub)
    baseline_pf = sub[sub["pnl"] > 0]["pnl"].sum() / abs(sub[sub["pnl"] <= 0]["pnl"].sum()) if abs(sub[sub["pnl"] <= 0]["pnl"].sum()) > 0 else 0
    print(f"\n  {split} (N={n_total}, baseline PF={baseline_pf:.2f}):")

    # Quartile breakpoints
    q1 = sub["breakout_dist"].quantile(0.25)
    q2 = sub["breakout_dist"].quantile(0.50)
    q3 = sub["breakout_dist"].quantile(0.75)

    buckets = [
        (f"≤{q1:.1f}pt (bottom 25%)", sub[sub["breakout_dist"] <= q1]),
        (f"{q1:.1f}-{q2:.1f}pt (25-50%)", sub[(sub["breakout_dist"] > q1) & (sub["breakout_dist"] <= q2)]),
        (f"{q2:.1f}-{q3:.1f}pt (50-75%)", sub[(sub["breakout_dist"] > q2) & (sub["breakout_dist"] <= q3)]),
        (f">{q3:.1f}pt (top 25%)", sub[sub["breakout_dist"] > q3]),
    ]
    for lbl, b in buckets:
        print(pf_stats(b, lbl, n_total))

    # Simple cutoff thresholds
    print(f"\n  Simple cutoff tests ({split}):")
    for thresh in [5, 8, 10, 12, 15, 20, 25]:
        filt = sub[sub["breakout_dist"] <= thresh]
        print(pf_stats(filt, f"dist ≤ {thresh}pt", n_total))
    for thresh in [8, 10, 12, 15, 20, 25]:
        filt = sub[sub["breakout_dist"] > thresh]
        print(pf_stats(filt, f"dist > {thresh}pt", n_total))

# ── B. OR Close Position ──────────────────────────────────────────────────────
section("B. OR Close Position (where last OR bar sits in range)")

for split in ["IS", "OOS"]:
    sub = sig_df[~sig_df["is_oos"]] if split == "IS" else sig_df[sig_df["is_oos"]]
    n_total = len(sub)
    baseline_pf = sub[sub["pnl"] > 0]["pnl"].sum() / abs(sub[sub["pnl"] <= 0]["pnl"].sum()) if abs(sub[sub["pnl"] <= 0]["pnl"].sum()) > 0 else 0
    print(f"\n  {split} (N={n_total}, baseline PF={baseline_pf:.2f}):")

    # For long trades: high or_pos means price near top = momentum alignment
    # For short trades: low or_pos means price near bottom = momentum alignment
    longs  = sub[sub["direction"] == "long"]
    shorts = sub[sub["direction"] == "short"]

    buckets_long = [
        ("Long: or_pos > 0.6 (near top)", longs[longs["or_pos"] > 0.6]),
        ("Long: or_pos > 0.5 (above mid)", longs[longs["or_pos"] > 0.5]),
        ("Long: or_pos <= 0.5 (below mid)", longs[longs["or_pos"] <= 0.5]),
        ("Short: or_pos < 0.4 (near bot)", shorts[shorts["or_pos"] < 0.4]),
        ("Short: or_pos < 0.5 (below mid)", shorts[shorts["or_pos"] < 0.5]),
        ("Short: or_pos >= 0.5 (above mid)", shorts[shorts["or_pos"] >= 0.5]),
    ]
    for lbl, b in buckets_long:
        print(pf_stats(b, lbl, n_total))

    # Combined: directional alignment (long+high pos OR short+low pos)
    aligned = pd.concat([
        longs[longs["or_pos"] > 0.5],
        shorts[shorts["or_pos"] < 0.5],
    ])
    counter = pd.concat([
        longs[longs["or_pos"] <= 0.5],
        shorts[shorts["or_pos"] >= 0.5],
    ])
    print(pf_stats(aligned, "Directionally aligned or_pos", n_total))
    print(pf_stats(counter, "Counter-direction or_pos", n_total))

# ── C. Entry Bar Body Quality ─────────────────────────────────────────────────
section("C. Entry Bar Body Quality (body% of candle range)")

for split in ["IS", "OOS"]:
    sub = sig_df[~sig_df["is_oos"]] if split == "IS" else sig_df[sig_df["is_oos"]]
    n_total = len(sub)
    baseline_pf = sub[sub["pnl"] > 0]["pnl"].sum() / abs(sub[sub["pnl"] <= 0]["pnl"].sum()) if abs(sub[sub["pnl"] <= 0]["pnl"].sum()) > 0 else 0
    print(f"\n  {split} (N={n_total}, baseline PF={baseline_pf:.2f}):")

    for thresh in [0.3, 0.4, 0.5, 0.6, 0.7]:
        filt = sub[sub["body_pct"] >= thresh]
        print(pf_stats(filt, f"body% >= {thresh:.0%}", n_total))

# ── D. Combined Best Filters ──────────────────────────────────────────────────
section("D. Combined Filter Tests (OOS 2025-2026 only)")

oos = sig_df[sig_df["is_oos"]]
n_base = len(oos)
baseline_pf = oos[oos["pnl"] > 0]["pnl"].sum() / abs(oos[oos["pnl"] <= 0]["pnl"].sum())
print(f"\n  Baseline OOS: N={n_base}, PF={baseline_pf:.2f}\n")

# Hard gate (skip 9:45 entries)
no_early = oos[oos["time"] >= dtime(9,46)]
# Breakout dist ≤ 15 (tight breakout)
tight = oos[oos["breakout_dist"] <= 15]
# Directional or_pos alignment
aligned = pd.concat([
    oos[(oos["direction"]=="long") & (oos["or_pos"] > 0.5)],
    oos[(oos["direction"]=="short") & (oos["or_pos"] < 0.5)],
])

print(pf_stats(oos, "Baseline (all)", n_base))
print(pf_stats(no_early, "Hard gate (skip 9:45)", n_base))
print(pf_stats(tight, "Breakout dist ≤ 15pt", n_base))
print(pf_stats(aligned, "Directional or_pos alignment", n_base))

# Combinations
hard_gate_tight = no_early[no_early["breakout_dist"] <= 15]
hard_gate_aligned = pd.concat([
    no_early[(no_early["direction"]=="long") & (no_early["or_pos"] > 0.5)],
    no_early[(no_early["direction"]=="short") & (no_early["or_pos"] < 0.5)],
])
hard_gate_body = no_early[no_early["body_pct"] >= 0.5]
tight_aligned = pd.concat([
    tight[(tight["direction"]=="long") & (tight["or_pos"] > 0.5)],
    tight[(tight["direction"]=="short") & (tight["or_pos"] < 0.5)],
])

print()
print(pf_stats(hard_gate_tight, "Hard gate + dist ≤ 15pt", n_base))
print(pf_stats(hard_gate_aligned, "Hard gate + or_pos aligned", n_base))
print(pf_stats(hard_gate_body, "Hard gate + body% ≥ 50%", n_base))
print(pf_stats(tight_aligned, "Dist ≤ 15pt + or_pos aligned", n_base))
all_three = hard_gate_tight[hard_gate_tight["body_pct"] >= 0.5]
print(pf_stats(all_three, "All three filters", n_base))

# Also test with entry window 9:46-9:59 (hot window)
hot_window = oos[(oos["time"] >= dtime(9,46)) & (oos["time"] <= dtime(9,59))]
hot_tight = hot_window[hot_window["breakout_dist"] <= 15]
hot_aligned = pd.concat([
    hot_window[(hot_window["direction"]=="long") & (hot_window["or_pos"] > 0.5)],
    hot_window[(hot_window["direction"]=="short") & (hot_window["or_pos"] < 0.5)],
])
print()
print(pf_stats(hot_window, "Hot window (9:46-9:59)", n_base))
print(pf_stats(hot_tight, "Hot window + dist ≤ 15pt", n_base))
print(pf_stats(hot_aligned, "Hot window + or_pos aligned", n_base))

# ── E. VERDICT ────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("  E. VERDICT — Best filters for PF target 3-5 (OOS)")
print("=" * 70)
print()

tests = [
    ("Baseline", oos),
    ("Hard gate only", no_early),
    ("Dist ≤ 15pt", tight),
    ("OR pos aligned", aligned),
    ("Hard gate + dist ≤ 15pt", hard_gate_tight),
    ("Hard gate + or_pos", hard_gate_aligned),
    ("Hard gate + body ≥ 50%", hard_gate_body),
    ("Hot window (9:46-9:59)", hot_window),
    ("Hot window + dist ≤ 15pt", hot_tight),
    ("Hot window + or_pos", hot_aligned),
]

print(f"  {'Filter':<35}  {'N':>4}  {'WR':>5}  {'PF':>6}  {'Net':>10}  {'ΔPF':>7}")
print("  " + "-" * 70)
for lbl, sub in tests:
    n = len(sub)
    if n == 0:
        print(f"  {lbl:<35}  {0:>4}  {'—':>5}  {'—':>6}  {'—':>10}  {'—':>7}")
        continue
    wins   = sub[sub["pnl"] > 0]["pnl"].sum()
    losses = abs(sub[sub["pnl"] <= 0]["pnl"].sum())
    pf  = wins / losses if losses > 0 else float("inf")
    wr  = (sub["pnl"] > 0).mean()
    net = sub["pnl"].sum()
    dpf = pf - baseline_pf
    star = " ★" if pf >= 3.0 and net > 0 else ""
    print(f"  {lbl:<35}  {n:>4}  {wr:.0%}  {pf:>6.2f}  ${net:>+8,.0f}  {dpf:>+7.3f}{star}")

print()
print("  [N<15] = statistically weak, treat with caution")
