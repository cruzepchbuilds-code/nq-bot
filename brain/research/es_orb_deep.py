"""
brain/research/es_orb_deep.py

ES ORB Deep Dive — tests whether adding ES as a SECOND instrument
alongside NQ ORB increases trade count while maintaining PF.

GOAL: Additive, independent trades. ES and NQ don't always break out
on the same days — we want low correlation and a combined PF ≥ 1.5.

DATA:
  data/es_1min.csv  — ES 1-min bars, 2022-2026
  data/nq_full.csv  — NQ 1-min bars, 2022-2026  (for correlation)

ES PARAMS TESTED:
  Two parameter sets are run:

  A) TASK-SPECIFIED (NQ-literal params applied to ES):
     OR: 55-110pt | Stop: 27pt effective | Buffer: 4pt
     NOTE: ES median OR is ~14pt, so the 55pt floor eliminates ~99.7% of days.
     Included for completeness; produces near-zero trades.

  B) ES-CALIBRATED (proportional to NQ by price ratio ~3.8x):
     OR: 14-30pt | Stop: 7+2=9pt effective | Buffer: 1pt | Target 3R=27pt
     This is the correct dollar-equivalent of the NQ setup.
     Price ratio: ES ~5500 / NQ ~21000 → ES params ≈ NQ/3.8

CONFIDENCE SCORE (same 4-component formula as NQ):
  +1 pivot : OR close (9:44) > prior-day P=(H+L+C)/3 for long
  +1 VWAP  : OR close > prior RTH session VWAP for long
  +1 HOT   : R1 ≤ orClose ≤ R2 (long) | S2 ≤ orClose ≤ S1 (short)
  +1 slope : VWAP rising 9:35→9:44 for long; falling for short

NQ PARAMS (for comparison):
  OR window     : 9:30-9:44 | OR: 55-110pt | Stop: 27pt | Buffer: 4pt
  Target: 3R=81pt | Skip Mondays | $20/pt

IS: 2022-2024  |  OOS: 2025-2026

Usage: python3 brain/research/es_orb_deep.py
"""

import sys
import os
import csv
from datetime import date, datetime, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ── Instrument specs ─────────────────────────────────────────────────────────

# NQ: task-specified params
NQ_SPEC = dict(
    symbol            = "NQ",
    point_value       = 20.0,
    tick_size         = 0.25,
    commission        = 2.50,        # per side
    or_minutes        = 15,          # 9:30-9:44
    breakout_buffer   = 4.0,         # close > orHi + 4pt
    stop_pts          = 22.0,        # raw stop
    stop_buffer_pts   = 5.0,         # effective stop = 27pt
    rr_target         = 3.0,         # 3R = 81pt
    or_min            = 55.0,
    or_max            = 110.0,
    entry_cutoff      = time(10, 30),
    skip_mondays      = True,
)

# ES (A): task-specified NQ params applied literally to ES — produces ~0 trades
ES_SPEC_NQ_LITERAL = dict(
    symbol            = "ES_NQ-literal",
    point_value       = 50.0,
    tick_size         = 0.25,
    commission        = 2.50,
    or_minutes        = 15,
    breakout_buffer   = 4.0,
    stop_pts          = 22.0,
    stop_buffer_pts   = 5.0,
    rr_target         = 3.0,
    or_min            = 55.0,         # NQ-sized filter — eliminates ~99.7% of ES days
    or_max            = 110.0,
    entry_cutoff      = time(10, 30),
    skip_mondays      = True,
)

# ES (B): calibrated — dollar-equivalent to NQ (proportional by price ratio ~3.8x)
# OR 14-30pt ≈ NQ 55-110pt scaled  |  stop 9pt eff ≈ NQ 27pt scaled  |  buf 1pt ≈ NQ 4pt scaled
# $50/pt × 9pt stop = $450/loss   vs   NQ $20/pt × 27pt = $540/loss  (very close dollar risk)
ES_SPEC_CAL = dict(
    symbol            = "ES_calibrated",
    point_value       = 50.0,
    tick_size         = 0.25,
    commission        = 2.50,
    or_minutes        = 15,
    breakout_buffer   = 1.0,
    stop_pts          = 7.0,
    stop_buffer_pts   = 2.0,         # effective stop = 9pt
    rr_target         = 3.0,         # 3R = 27pt target
    or_min            = 14.0,
    or_max            = 30.0,
    entry_cutoff      = time(10, 30),
    skip_mondays      = True,
)

IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

# ── Data loading ─────────────────────────────────────────────────────────────

def load_bars(path):
    bars = []
    with open(path) as f:
        for row in csv.DictReader(f):
            ts_str = row["timestamp"]
            # Strip timezone offset if present (data uses naive ET timestamps
            # but later rows in 2026 include +/-HHMM suffix)
            if len(ts_str) > 19 and (ts_str[19] in ('+', '-') or ts_str[19] == 'Z'):
                ts_str = ts_str[:19]
            bars.append({
                "timestamp": datetime.fromisoformat(ts_str),
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": float(row.get("volume", 0)),
            })
    return bars

# ── Pivot / VWAP computation ─────────────────────────────────────────────────

def compute_prior_day_context(bars):
    """
    Returns {trade_date: {P, R1, R2, S1, S2, prev_vwap, vwap_935, vwap_944}}
    using prior RTH H/L/C.  vwap_935 / vwap_944 are intraday snapshots
    of the CURRENT session for the slope filter.
    """
    rth_by_date = defaultdict(list)
    for b in bars:
        ts = b["timestamp"]
        h, m = ts.hour, ts.minute
        if (h > 9 or (h == 9 and m >= 30)) and h < 16:
            rth_by_date[ts.date()].append(b)

    session_data = {}
    for d, day_bars in rth_by_date.items():
        H    = max(b["high"]  for b in day_bars)
        L    = min(b["low"]   for b in day_bars)
        C    = day_bars[-1]["close"]
        pv   = sum(b["close"] * b["volume"] for b in day_bars)
        vol  = sum(b["volume"] for b in day_bars)
        vwap = pv / vol if vol > 0 else None
        session_data[d] = (H, L, C, vwap)

    # Intraday VWAP slope snapshots (9:35 and 9:44 running VWAP) per date
    vwap_snap = {}
    for d, day_bars in rth_by_date.items():
        pv_run = 0.0
        v_run  = 0.0
        snap935 = snap944 = None
        for b in sorted(day_bars, key=lambda x: x["timestamp"]):
            ts = b["timestamp"]
            t  = ts.time()
            bv = b["volume"]
            if bv > 0:
                pv_run += b["close"] * bv
                v_run  += bv
            if t == time(9, 35) and snap935 is None and v_run > 0:
                snap935 = pv_run / v_run
            if t == time(9, 44) and v_run > 0:
                snap944 = pv_run / v_run
        vwap_snap[d] = (snap935, snap944)

    sorted_dates = sorted(session_data)
    ctx = {}
    for i in range(1, len(sorted_dates)):
        curr_d = sorted_dates[i]
        H, L, C, vwap = session_data[sorted_dates[i - 1]]
        P  = (H + L + C) / 3.0
        R1 = 2 * P - L
        R2 = P + (H - L)
        S1 = 2 * P - H
        S2 = P - (H - L)
        s935, s944 = vwap_snap.get(curr_d, (None, None))
        ctx[curr_d] = {
            "P": P, "R1": R1, "R2": R2, "S1": S1, "S2": S2,
            "prev_vwap": vwap,
            "vwap_935":  s935,
            "vwap_944":  s944,
        }
    return ctx

# ── Confidence score ─────────────────────────────────────────────────────────

def confidence_score(direction, or_close, ctx):
    """0-4 conviction score. Returns None if ctx missing."""
    if ctx is None or or_close is None:
        return None
    is_long = direction == "long"
    px = or_close
    P  = ctx["P"]
    R1, R2 = ctx["R1"], ctx["R2"]
    S1, S2 = ctx["S1"], ctx["S2"]

    score = 0
    # +1 pivot alignment
    if (is_long and px > P) or (not is_long and px < P):
        score += 1
    # +1 prior VWAP alignment
    pv = ctx.get("prev_vwap")
    if pv is not None:
        if (is_long and px > pv) or (not is_long and px < pv):
            score += 1
    # +1 HOT zone
    if is_long and R1 <= px <= R2:
        score += 1
    elif not is_long and S2 <= px <= S1:
        score += 1
    # +1 VWAP slope (rising 9:35→9:44 for long, falling for short)
    s935 = ctx.get("vwap_935")
    s944 = ctx.get("vwap_944")
    if s935 is not None and s944 is not None:
        slope = s944 - s935
        if (is_long and slope > 0) or (not is_long and slope < 0):
            score += 1
    return score

# ── Core backtester ──────────────────────────────────────────────────────────

OR_START    = time(9, 30)
OR_END_TIME = time(9, 44)   # last bar of the 15-min OR (9:30-9:44 inclusive)
SESSION_END = time(16, 0)
EOD_FLATTEN = time(15, 55)

def run_orb(bars, spec, years):
    """
    Self-contained ORB backtest on bars for the given years.
    Uses prior-day context computed from ALL bars (for warmup continuity).

    Returns list of trade dicts with conf_score attached.
    """
    pv    = spec["point_value"]
    com   = spec["commission"] * 2   # round trip
    buf   = spec["breakout_buffer"]
    stp   = spec["stop_pts"] + spec["stop_buffer_pts"]  # effective stop distance
    rr    = spec["rr_target"]
    or_min  = spec["or_min"]
    or_max  = spec["or_max"]
    cutoff  = spec["entry_cutoff"]
    skip_mon = spec["skip_mondays"]

    # Compute prior-day context across ALL bars for continuity
    ctx_map = compute_prior_day_context(bars)

    # Filter to target years only
    year_set = set(years)
    day_bars = defaultdict(list)
    for b in bars:
        d = b["timestamp"].date()
        if d.year in year_set:
            day_bars[d].append(b)

    trades = []

    for d in sorted(day_bars):
        if skip_mon and d.weekday() == 0:
            continue   # skip Mondays

        dbars = sorted(day_bars[d], key=lambda b: b["timestamp"])

        or_hi = or_lo = or_close = None
        or_done      = False
        entry_taken  = False
        or_size_ok   = None   # evaluated once OR is complete

        for b in dbars:
            ts = b["timestamp"]
            t  = ts.time()

            if t < OR_START:
                continue

            # ── Accumulate Opening Range (9:30-9:44) ─────────────────────────
            if not or_done:
                if or_hi is None:
                    or_hi = b["high"]
                    or_lo = b["low"]
                else:
                    or_hi = max(or_hi, b["high"])
                    or_lo = min(or_lo, b["low"])

                if t == OR_END_TIME:
                    or_close = b["close"]
                    or_done  = True
                    or_size  = or_hi - or_lo
                    or_size_ok = or_min <= or_size <= or_max
                continue

            # ── Post-OR entry window ──────────────────────────────────────────
            if entry_taken:
                break
            if t > cutoff:
                break
            if not or_size_ok:
                break   # OR size filter fails — skip this day

            # Check breakout
            direction = None
            if b["close"] > or_hi + buf:
                direction = "long"
            elif b["close"] < or_lo - buf:
                direction = "short"

            if direction is None:
                continue

            entry_px = b["close"]
            if direction == "long":
                stop_px = entry_px - stp
                tgt_px  = entry_px + stp * rr
            else:
                stop_px = entry_px + stp
                tgt_px  = entry_px - stp * rr

            # Confidence score at OR close (9:44)
            cs = confidence_score(direction, or_close, ctx_map.get(d))

            # Simulate outcome on subsequent bars
            result   = "open"
            exit_pts = None

            remaining = [bb for bb in dbars if bb["timestamp"].time() > t]

            for eb in remaining:
                et = eb["timestamp"].time()
                if direction == "long":
                    if eb["low"] <= stop_px:
                        result   = "stop"
                        exit_pts = -stp
                        break
                    if eb["high"] >= tgt_px:
                        result   = "target"
                        exit_pts = stp * rr
                        break
                else:
                    if eb["high"] >= stop_px:
                        result   = "stop"
                        exit_pts = -stp
                        break
                    if eb["low"] <= tgt_px:
                        result   = "target"
                        exit_pts = stp * rr
                        break
                if et >= EOD_FLATTEN:
                    eod_pts  = ((eb["close"] - entry_px) if direction == "long"
                                else (entry_px - eb["close"]))
                    result   = "flatten"
                    exit_pts = eod_pts
                    break

            if exit_pts is None:
                # Flatten at last bar of session
                last    = dbars[-1]
                eod_pts = ((last["close"] - entry_px) if direction == "long"
                           else (entry_px - last["close"]))
                result   = "flatten"
                exit_pts = eod_pts

            pnl = exit_pts * pv - com

            trades.append({
                "date":       str(d),
                "direction":  direction,
                "entry":      round(entry_px, 2),
                "stop":       round(stop_px, 2),
                "target":     round(tgt_px, 2),
                "result":     result,
                "points":     round(exit_pts, 2),
                "pnl":        round(pnl, 2),
                "or_size":    round(or_hi - or_lo, 2),
                "or_close":   round(or_close, 2) if or_close is not None else None,
                "conf_score": cs,
                "entry_time": t.strftime("%H:%M"),
            })
            entry_taken = True

    return trades

# ── Stats helpers ─────────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "avg": 0.0}
    wins = [t for t in trades if t["pnl"] > 0]
    gl   = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    gw   = sum(t["pnl"] for t in wins)
    net  = gw - gl
    return {
        "n":   len(trades),
        "net": round(net, 0),
        "wr":  round(len(wins) / len(trades) * 100, 1),
        "pf":  round(gw / gl, 3) if gl > 0 else float("inf"),
        "avg": round(net / len(trades), 0),
    }

def pf_str(s):
    return f"{s['pf']:.3f}" if s["pf"] != float("inf") else "  inf"

def header(title, W=78):
    print(f"\n{'=' * W}")
    print(f"  {title}")
    print(f"{'=' * W}")

def section(title, W=78):
    print(f"\n  {'─' * (W - 4)}")
    print(f"  {title}")
    print(f"  {'─' * (W - 4)}")

def print_row(label, s, base_pf=None, w=32):
    dpf = (f"  {s['pf'] - base_pf:+.3f}" if base_pf is not None and s["n"] > 0 else "")
    print(f"  {label:<{w}}  N={s['n']:>4}  WR={s['wr']:>5.1f}%  "
          f"PF={pf_str(s)}  Net=${s['net']:>+9,.0f}  Avg=${s['avg']:>+6,.0f}{dpf}")

def year_breakdown(trades, indent="  "):
    by_yr = defaultdict(list)
    for t in trades:
        by_yr[t["date"][:4]].append(t)
    for yr in sorted(by_yr):
        s = stats(by_yr[yr])
        print(f"{indent}{yr:<10}  N={s['n']:>4}  WR={s['wr']:>5.1f}%  "
              f"PF={pf_str(s)}  Net=${s['net']:>+9,.0f}  Avg=${s['avg']:>+6,.0f}")

# ── Correlation helpers ───────────────────────────────────────────────────────

def trade_day_map(trades):
    """Return {date_str: direction} for first trade per day."""
    out = {}
    for t in trades:
        if t["date"] not in out:
            out[t["date"]] = t["direction"]
    return out

def correlation_report(nq_t, es_t, period_label):
    nq_days = trade_day_map(nq_t)
    es_days = trade_day_map(es_t)
    all_dates = set(nq_days) | set(es_days)
    if not all_dates:
        print(f"  {period_label}: no trades")
        return None
    nq_only  = [d for d in all_dates if d in nq_days and d not in es_days]
    es_only  = [d for d in all_dates if d in es_days and d not in nq_days]
    both     = [d for d in all_dates if d in nq_days and d in es_days]
    same_dir = [d for d in both if nq_days[d] == es_days[d]]
    opp_dir  = [d for d in both if nq_days[d] != es_days[d]]

    pct_both  = len(both) / len(all_dates) * 100
    pct_same  = len(same_dir) / len(both) * 100 if both else 0.0
    corr_flag = "GOOD — additive" if pct_same < 70 else "HIGH CORRELATION — limited benefit"

    print(f"\n  {period_label}:")
    print(f"    Total days with ≥1 trade  : {len(all_dates)}")
    print(f"    NQ fires only             : {len(nq_only):>4}  ({len(nq_only)/len(all_dates)*100:.1f}%)")
    print(f"    ES fires only             : {len(es_only):>4}  ({len(es_only)/len(all_dates)*100:.1f}%)")
    print(f"    Both fire                 : {len(both):>4}  ({pct_both:.1f}%)")
    if both:
        print(f"      ↳ same direction        : {len(same_dir):>4}  ({pct_same:.1f}% of dual-fire days)")
        print(f"      ↳ opposite direction    : {len(opp_dir):>4}  ({100-pct_same:.1f}% of dual-fire days)")
        print(f"      ↳ Verdict: {corr_flag}")
    return pct_same

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    base   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ES_PATH = os.path.join(base, "data", "es_1min.csv")
    NQ_PATH = os.path.join(base, "data", "nq_full.csv")

    print("\nCruzCapital — ES ORB Deep Research")
    print(f"IS: {IS_YEARS}  |  OOS: {OOS_YEARS}")

    # ── Load ──────────────────────────────────────────────────────────────────
    print("\nLoading ES data...", end=" ", flush=True)
    es_bars = load_bars(ES_PATH)
    print(f"{len(es_bars):,} bars  {es_bars[0]['timestamp'].date()} → {es_bars[-1]['timestamp'].date()}")

    print("Loading NQ data...", end=" ", flush=True)
    nq_bars = load_bars(NQ_PATH)
    print(f"{len(nq_bars):,} bars  {nq_bars[0]['timestamp'].date()} → {nq_bars[-1]['timestamp'].date()}")

    # ══════════════════════════════════════════════════════════════════════════
    # NOTE ON ES PARAMS
    # ══════════════════════════════════════════════════════════════════════════
    header("PARAMETER NOTE — NQ-literal vs ES-calibrated")
    print("""
  Task-specified params (55pt OR min, 27pt stop, 4pt buffer) are NQ-calibrated.
  ES median OR range is ~14pt; only 3 of 909 non-Monday trading days (0.3%)
  have an OR ≥ 55pt. The NQ-literal test is run for completeness but produces
  ~0 trades — it effectively means "no ES trading."

  ES-calibrated params use the dollar-equivalent scaled by price ratio (~3.8x):
    NQ: OR 55-110pt, stop 27pt, buf 4pt, $20/pt → $540 max risk
    ES: OR 14-30pt,  stop 9pt,  buf 1pt, $50/pt → $450 max risk  (very close)

  The calibrated params match how es_research.py was built and represent
  the correct apples-to-apples comparison for the portfolio question.
""")

    # ══════════════════════════════════════════════════════════════════════════
    # PART A: NQ-LITERAL PARAMS ON ES (task-specified)
    # ══════════════════════════════════════════════════════════════════════════
    header("PART A — ES with NQ-literal params (OR 55-110pt, Stop 27pt, Buf 4pt)")
    print("  [Expected: near-zero trades — ES OR is too small for NQ params]")

    print("\n  Running IS...", end=" ", flush=True)
    es_lit_is  = run_orb(es_bars, ES_SPEC_NQ_LITERAL, IS_YEARS)
    print(f"{len(es_lit_is)} trades")

    print("  Running OOS...", end=" ", flush=True)
    es_lit_oos = run_orb(es_bars, ES_SPEC_NQ_LITERAL, OOS_YEARS)
    print(f"{len(es_lit_oos)} trades")

    s_lit_is  = stats(es_lit_is)
    s_lit_oos = stats(es_lit_oos)
    print_row(f"IS   {IS_YEARS}",   s_lit_is)
    print_row(f"OOS  {OOS_YEARS}", s_lit_oos)
    if len(es_lit_is) + len(es_lit_oos) < 5:
        print("\n  Conclusion: NQ-literal params produce no viable ES signal.")
        print("  Proceeding with ES-calibrated params (Part B).")

    # ══════════════════════════════════════════════════════════════════════════
    # PART B: ES-CALIBRATED PARAMS
    # ══════════════════════════════════════════════════════════════════════════
    header("PART B — ES with Calibrated Params (OR 14-30pt, Stop 9pt, Buf 1pt, 3R=27pt)")
    print(f"  $50/pt | Commission $5 round-trip | Skip Mondays | Entry cutoff 10:30")

    print("\n  Running NQ IS...", end=" ", flush=True)
    nq_is_t = run_orb(nq_bars, NQ_SPEC, IS_YEARS)
    print(f"{len(nq_is_t)} trades")

    print("  Running NQ OOS...", end=" ", flush=True)
    nq_oos_t = run_orb(nq_bars, NQ_SPEC, OOS_YEARS)
    print(f"{len(nq_oos_t)} trades")

    print("  Running ES IS (calibrated)...", end=" ", flush=True)
    es_is_t = run_orb(es_bars, ES_SPEC_CAL, IS_YEARS)
    print(f"{len(es_is_t)} trades")

    print("  Running ES OOS (calibrated)...", end=" ", flush=True)
    es_oos_t = run_orb(es_bars, ES_SPEC_CAL, OOS_YEARS)
    print(f"{len(es_oos_t)} trades")

    nq_all = nq_is_t + nq_oos_t
    es_all = es_is_t + es_oos_t

    # ── 1. ES baseline stats ─────────────────────────────────────────────────
    section("1. ES Baseline — IS vs OOS")
    print_row(f"ES IS   {IS_YEARS}",   stats(es_is_t))
    print_row(f"ES OOS  {OOS_YEARS}", stats(es_oos_t))
    print_row("ES ALL  2022-2026",     stats(es_all))

    section("ES Baseline — Year-by-Year")
    year_breakdown(es_all)

    section("ES Baseline — Direction")
    print_row("Long",  stats([t for t in es_all if t["direction"] == "long"]))
    print_row("Short", stats([t for t in es_all if t["direction"] == "short"]))

    section("ES Baseline — Exit Reason")
    for reason in ["target", "stop", "flatten"]:
        ts = [t for t in es_all if t["result"] == reason]
        if ts:
            s = stats(ts)
            print(f"  {reason:<12}  N={s['n']:>4}  WR={s['wr']:>5.1f}%  "
                  f"Net=${s['net']:>+9,.0f}  Avg=${s['avg']:>+6,.0f}")

    section("NQ Baseline (same params applied to NQ data) — IS vs OOS")
    print_row(f"NQ IS   {IS_YEARS}",   stats(nq_is_t))
    print_row(f"NQ OOS  {OOS_YEARS}", stats(nq_oos_t))
    print_row("NQ ALL  2022-2026",     stats(nq_all))

    section("NQ Baseline — Year-by-Year")
    year_breakdown(nq_all)

    # ── 2. ES confidence score ────────────────────────────────────────────────
    header("SECTION 2 — ES Confidence Score")
    print("  +1 pivot: OR close > prior-day P=(H+L+C)/3")
    print("  +1 VWAP : OR close > prior RTH session VWAP")
    print("  +1 HOT  : R1 ≤ orClose ≤ R2 (long) | S2 ≤ orClose ≤ S1 (short)")
    print("  +1 slope: VWAP rising 9:35→9:44 for long")

    # Score distribution
    scored = [t for t in es_all if t["conf_score"] is not None]
    sc_counts = defaultdict(int)
    for t in scored:
        sc_counts[t["conf_score"]] += 1
    total_sc = len(scored)

    section(f"Score Distribution (N={total_sc} / {len(es_all)} have score data)")
    for sc in sorted(sc_counts):
        pct = sc_counts[sc] / total_sc * 100 if total_sc else 0
        print(f"  score={sc}   count={sc_counts[sc]:>4}  ({pct:.1f}%)")

    section("IS Performance by Score Tier (2022-2024)")
    is_sc = [t for t in es_is_t if t["conf_score"] is not None]
    base_is = stats(is_sc)
    print_row("IS Baseline (with score)", base_is)
    print()
    for sc in range(5):
        ts = [t for t in is_sc if t["conf_score"] == sc]
        if ts:
            print_row(f"  IS score={sc}", stats(ts), base_is["pf"])

    section("OOS Performance by Score Tier (2025-2026)")
    oos_sc = [t for t in es_oos_t if t["conf_score"] is not None]
    base_oos = stats(oos_sc)
    print_row("OOS Baseline (with score)", base_oos)
    print()
    for sc in range(5):
        ts = [t for t in oos_sc if t["conf_score"] == sc]
        if ts:
            print_row(f"  OOS score={sc}", stats(ts), base_oos["pf"])

    section("OOS — Score Threshold Filter (keep score ≥ N)")
    print_row("OOS Baseline (all scored)", base_oos)
    print()
    for min_sc in [1, 2, 3]:
        kept   = [t for t in oos_sc if t["conf_score"] >= min_sc]
        skipped = [t for t in oos_sc if t["conf_score"] < min_sc]
        print(f"  score ≥ {min_sc}:  keep={len(kept)}  skip={len(skipped)}")
        print_row(f"    keep score≥{min_sc}", stats(kept),    base_oos["pf"])
        print_row(f"    skip score<{min_sc}", stats(skipped), base_oos["pf"])
        print()

    # OOS year-by-year by score
    oos_ge1 = [t for t in oos_sc if t["conf_score"] >= 1]
    oos_ge2 = [t for t in oos_sc if t["conf_score"] >= 2]
    oos_ge3 = [t for t in oos_sc if t["conf_score"] >= 3]

    section("OOS Year-by-Year by Score Tier")
    for label, tr in [("Baseline (all)", oos_sc),
                       ("score ≥ 1",      oos_ge1),
                       ("score ≥ 2",      oos_ge2)]:
        print(f"\n  {label}:")
        year_breakdown(tr, indent="    ")

    # IS/OOS split for each tier
    section("IS vs OOS — Score Tiers")
    is_ge1 = [t for t in is_sc if t["conf_score"] >= 1]
    is_ge2 = [t for t in is_sc if t["conf_score"] >= 2]
    is_ge3 = [t for t in is_sc if t["conf_score"] >= 3]

    for label, is_tr, oos_tr in [
        ("score ≥ 1", is_ge1, oos_ge1),
        ("score ≥ 2", is_ge2, oos_ge2),
        ("score ≥ 3", is_ge3, oos_ge3),
    ]:
        print(f"\n  {label}:")
        print_row(f"    IS  {IS_YEARS}",   stats(is_tr))
        print_row(f"    OOS {OOS_YEARS}", stats(oos_tr))

    # ── 3. Correlation check ──────────────────────────────────────────────────
    header("SECTION 3 — NQ vs ES Correlation (% same-direction dual-fire days)")
    print("  Goal: < 70% same direction → instruments are additive, not redundant")

    corr_oos = None
    for label, nq_t, es_t in [
        (f"IS  {IS_YEARS}",   nq_is_t,  es_is_t),
        (f"OOS {OOS_YEARS}", nq_oos_t, es_oos_t),
        ("ALL 2022-2026",    nq_all,   es_all),
    ]:
        c = correlation_report(nq_t, es_t, label)
        if "OOS" in label:
            corr_oos = c

    # ── 4. Combined NQ + ES portfolio ─────────────────────────────────────────
    header("SECTION 4 — Combined NQ + ES Portfolio")
    print("  On dual-fire days: take both. On single-fire: take that one.")
    print("  NQ: $20/pt | ES: $50/pt")

    cb_is_all  = nq_is_t  + es_is_t
    cb_oos_all = nq_oos_t + es_oos_t
    cb_all     = nq_all   + es_all

    cb_oos_sc1 = nq_oos_t + oos_ge1
    cb_oos_sc2 = nq_oos_t + oos_ge2

    section("Combined — IS vs OOS vs ALL")
    print_row(f"IS  NQ only  {IS_YEARS}",   stats(nq_is_t))
    print_row(f"IS  ES only  {IS_YEARS}",   stats(es_is_t))
    print_row(f"IS  COMBINED {IS_YEARS}",   stats(cb_is_all))
    print()
    print_row(f"OOS NQ only  {OOS_YEARS}", stats(nq_oos_t))
    print_row(f"OOS ES only  {OOS_YEARS}", stats(es_oos_t))
    print_row(f"OOS COMBINED {OOS_YEARS}", stats(cb_oos_all))
    print()
    print_row("ALL NQ only  2022-2026",    stats(nq_all))
    print_row("ALL ES only  2022-2026",    stats(es_all))
    print_row("ALL COMBINED 2022-2026",    stats(cb_all))

    section("Combined OOS — with ES Confidence Score Filter")
    print_row("OOS NQ only",              stats(nq_oos_t))
    print_row("OOS ES (baseline)",        stats(es_oos_t))
    print_row("OOS NQ + ES baseline",     stats(cb_oos_all))
    print()
    print_row("OOS ES score≥1",           stats(oos_ge1))
    print_row("OOS NQ + ES score≥1",      stats(cb_oos_sc1))
    print()
    print_row("OOS ES score≥2",           stats(oos_ge2))
    print_row("OOS NQ + ES score≥2",      stats(cb_oos_sc2))

    section("Trade Count per Year — NQ | ES | Combined")
    print(f"  {'Year':<6}  {'NQ':>5}  {'ES':>5}  {'Comb':>5}  "
          f"{'NQ Net $':>10}  {'ES Net $':>10}  {'Comb Net $':>11}  {'Comb PF':>8}")
    print(f"  {'─' * 78}")
    by_yr_nq = defaultdict(list)
    by_yr_es = defaultdict(list)
    for t in nq_all:
        by_yr_nq[t["date"][:4]].append(t)
    for t in es_all:
        by_yr_es[t["date"][:4]].append(t)
    for yr in sorted(set(by_yr_nq) | set(by_yr_es)):
        nqt = by_yr_nq.get(yr, [])
        est = by_yr_es.get(yr, [])
        cbt = nqt + est
        nq_s, es_s, cb_s = stats(nqt), stats(est), stats(cbt)
        print(f"  {yr:<6}  {nq_s['n']:>5}  {es_s['n']:>5}  {cb_s['n']:>5}  "
              f"${nq_s['net']:>+9,.0f}  ${es_s['net']:>+9,.0f}  ${cb_s['net']:>+10,.0f}  "
              f"{pf_str(cb_s):>8}")

    section("OOS Year-by-Year Detail")
    for yr in [str(y) for y in OOS_YEARS]:
        nqt = [t for t in nq_oos_t if t["date"][:4] == yr]
        est = [t for t in es_oos_t if t["date"][:4] == yr]
        print(f"\n  {yr}:")
        print_row("    NQ only",   stats(nqt))
        print_row("    ES only",   stats(est))
        print_row("    Combined",  stats(nqt + est))

    # ── 5. Verdict ────────────────────────────────────────────────────────────
    header("SECTION 5 — VERDICT")

    s_nq_oos  = stats(nq_oos_t)
    s_es_oos  = stats(es_oos_t)
    s_cb_oos  = stats(cb_oos_all)
    s_ge1     = stats(oos_ge1)
    s_cb_ge1  = stats(cb_oos_sc1)
    s_ge2     = stats(oos_ge2)
    s_cb_ge2  = stats(cb_oos_sc2)

    nq_n  = s_nq_oos["n"]
    es_n  = s_es_oos["n"]
    cb_n  = s_cb_oos["n"]
    add_pct = (cb_n - nq_n) / nq_n * 100 if nq_n else 0

    print(f"""
  OOS RESULTS SUMMARY (2025-2026):

  NQ baseline  : N={nq_n:>3}  WR={s_nq_oos['wr']:.1f}%  PF={pf_str(s_nq_oos)}  Net=${s_nq_oos['net']:>+8,.0f}
  ES calibrated: N={es_n:>3}  WR={s_es_oos['wr']:.1f}%  PF={pf_str(s_es_oos)}  Net=${s_es_oos['net']:>+8,.0f}
  NQ+ES combin.: N={cb_n:>3}  WR={s_cb_oos['wr']:.1f}%  PF={pf_str(s_cb_oos)}  Net=${s_cb_oos['net']:>+8,.0f}

  ES score≥1   : N={s_ge1['n']:>3}  WR={s_ge1['wr']:.1f}%  PF={pf_str(s_ge1)}  Net=${s_ge1['net']:>+8,.0f}
  NQ+ES≥1      : N={s_cb_ge1['n']:>3}  WR={s_cb_ge1['wr']:.1f}%  PF={pf_str(s_cb_ge1)}  Net=${s_cb_ge1['net']:>+8,.0f}

  ES score≥2   : N={s_ge2['n']:>3}  WR={s_ge2['wr']:.1f}%  PF={pf_str(s_ge2)}  Net=${s_ge2['net']:>+8,.0f}
  NQ+ES≥2      : N={s_cb_ge2['n']:>3}  WR={s_cb_ge2['wr']:.1f}%  PF={pf_str(s_cb_ge2)}  Net=${s_cb_ge2['net']:>+8,.0f}

  CORRELATION (OOS, same-direction dual-fire):
    {f"{corr_oos:.1f}%" if corr_oos is not None else "N/A"}
""")

    pf_maintained  = s_cb_oos["pf"] >= 1.50
    trade_additive = add_pct >= 20.0     # ≥20% more trades
    corr_good      = (corr_oos or 100.0) < 70.0

    ok = lambda b: "PASS" if b else "FAIL"
    print("  CRITERIA CHECK:")
    print(f"    PF maintained (≥1.50) : {ok(pf_maintained)}"
          f"  [Combined OOS PF = {pf_str(s_cb_oos)}]")
    print(f"    Trade count +20%      : {ok(trade_additive)}"
          f"  [NQ={nq_n} → Combined={cb_n}, +{add_pct:.0f}%]")
    print(f"    Correlation <70%      : {ok(corr_good)}"
          f"  [Same-dir dual-fire: {f'{corr_oos:.1f}%' if corr_oos is not None else 'N/A'}]")

    if pf_maintained and trade_additive and corr_good:
        verdict = ("YES — adding ES meaningfully increases trade count WITHOUT killing PF. "
                   "Instruments are sufficiently independent to be additive.")
    elif pf_maintained and trade_additive:
        verdict = ("PARTIAL — trade count up and PF maintained, but high correlation limits "
                   "diversification. Still worth adding for raw trade volume.")
    elif pf_maintained and corr_good:
        verdict = ("PARTIAL — instruments are independent but ES adds fewer trades than expected. "
                   "Consider looser ES filters or different entry cutoff.")
    elif trade_additive and corr_good:
        verdict = ("NO — combined PF falls below 1.50. ES drags profitability at these params. "
                   "Try confidence score filter (score≥2) before adding ES.")
    else:
        verdict = ("NO — ES does not add sufficient independent edge at these parameters. "
                   "Further optimization required.")

    print(f"\n  FINAL VERDICT: {verdict}")

    # Best ES config
    candidates = [(lbl, s) for lbl, s in [
        ("ES baseline",  s_es_oos),
        ("ES score≥1",   s_ge1),
        ("ES score≥2",   s_ge2),
    ] if s["n"] >= 5]
    if candidates:
        best_label, best_s = max(candidates, key=lambda x: x[1]["pf"])
        print(f"\n  BEST ES STANDALONE CONFIG: {best_label}")
        print(f"    N={best_s['n']}  WR={best_s['wr']:.1f}%  PF={pf_str(best_s)}  "
              f"Net=${best_s['net']:>+,.0f}  Avg=${best_s['avg']:>+,.0f}/trade")

    print(f"\n{'=' * 78}")
    print(f"  Done — brain/research/es_orb_deep.py")
    print(f"{'=' * 78}\n")


if __name__ == "__main__":
    main()
