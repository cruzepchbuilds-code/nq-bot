"""
brain/research/extended_window.py

Extended entry-window study: does allowing ORB breakouts AFTER 10:30 add value?

Currently LAST_ENTRY_TIME = "10:30". This script scans every day in 2022-2026
and finds ALL bars where price crossed the OR boundary AFTER the OR ended (9:44),
bucketed by time-of-day, with IS (2022-2024) and OOS (2025-2026) splits.

This is a standalone simulation — it does NOT use the Backtester.
It directly processes 1-min bars, computes or_hi/or_lo from 9:30-9:44, then
finds the FIRST qualifying breakout bar in each time window for each day.

Confidence score (0-4 points, same definition as confidence_score_test.py):
  +1  pivot  : OR close (9:44) > prior-day P=(H+L+C)/3 for long, < P for short
  +1  vwap   : OR close > prior-session RTH VWAP for long, < for short
  +1  zone   : R1 ≤ or_close ≤ R2 (long HOT zone) or S2 ≤ or_close ≤ S1 (short HOT zone)
  +1  slope  : VWAP rising 9:35→9:44 for long, falling for short

Trade mechanics:
  Entry  : close of first qualifying breakout bar
  Target : entry + 81pt (long) or entry - 81pt (short)   [3R at 27pt eff stop]
  Stop   : entry - 27pt (long) or entry + 27pt (short)   [22pt stop + 5pt buffer]
  Exit   : first bar where low ≤ stop (loss) or high ≥ target (win), or 15:55 flatten
  P&L    : in NQ dollars ($20/pt, 1 contract, no commission for clean comparison)

Time buckets:
  A: 09:45-09:59   (first 15 min after OR)
  B: 10:00-10:29   (current live window, pre-10:30)
  C: 10:30-11:59   (extended window 1)
  D: 12:00-13:59   (extended window 2, lunch/afternoon)

Usage:
  python3 brain/research/extended_window.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import load_csv
from collections import defaultdict
from datetime import date, time

# ── constants ────────────────────────────────────────────────────────────────

DATA      = "data/nq_full.csv"
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

POINT_VALUE   = 20.0           # $/pt, 1 contract
BREAKOUT_BUF  = 4.0            # close must exceed OR edge by this many pts
STOP_DIST     = 27.0           # effective stop distance (22pt + 5pt buffer)
RR            = 3.0            # funded-mode target multiplier
TARGET_DIST   = STOP_DIST * RR # = 81 pt
FLATTEN_TIME  = time(15, 55)   # force exit if still open

# Time bucket definitions: (label, start_time, end_time inclusive)
BUCKETS = [
    ("A: 09:45-09:59", time(9,  45), time(9,  59)),
    ("B: 10:00-10:29", time(10,  0), time(10, 29)),
    ("C: 10:30-11:59", time(10, 30), time(11, 59)),
    ("D: 12:00-13:59", time(12,  0), time(13, 59)),
]

# Cumulative cutoffs for "what if we allow up to X?" analysis
CUTOFFS = [
    ("≤09:59  (A only)",             time(9,  59)),
    ("≤10:29  (A+B, current)",        time(10, 29)),
    ("≤10:59  (A+B+C partial)",       time(10, 59)),
    ("≤11:59  (A+B+C)",               time(11, 59)),
    ("≤13:59  (A+B+C+D, all)",        time(13, 59)),
]


# ── data helpers ─────────────────────────────────────────────────────────────

def bars_by_date(bars):
    """Group bars into {date: [bar,...]} preserving order."""
    out = defaultdict(list)
    for b in bars:
        out[b["timestamp"].date()].append(b)
    return out


def compute_pivots(bars):
    """Prior-RTH-session floor pivots for each trading date."""
    rth = defaultdict(list)
    for b in bars:
        ts = b["timestamp"]
        h, m = ts.hour, ts.minute
        if (h > 9 or (h == 9 and m >= 30)) and h < 16:
            rth[ts.date()].append(b)
    hlc = {}
    for d, day in rth.items():
        H = max(b["high"]  for b in day)
        L = min(b["low"]   for b in day)
        C = day[-1]["close"]
        hlc[d] = (H, L, C)
    sd = sorted(hlc)
    out = {}
    for i in range(1, len(sd)):
        H, L, C = hlc[sd[i - 1]]
        P = (H + L + C) / 3.0
        out[sd[i]] = {
            "P":  P,
            "R1": 2 * P - L,
            "R2": P + (H - L),
            "S1": 2 * P - H,
            "S2": P - (H - L),
        }
    return out


def compute_prior_vwap(bars):
    """Prior RTH session VWAP and intra-OR slope for each trading date."""
    rth = defaultdict(list)
    for b in bars:
        ts = b["timestamp"]
        h, m = ts.hour, ts.minute
        if (h > 9 or (h == 9 and m >= 30)) and h < 16:
            rth[ts.date()].append(b)
    sd = sorted(rth)
    sess_vwap = {}
    for d in sd:
        pv = sum(b["close"] * b["volume"] for b in rth[d])
        v  = sum(b["volume"] for b in rth[d])
        sess_vwap[d] = pv / v if v > 0 else None

    out = {}
    for i, d in enumerate(sd):
        prior_v = sess_vwap.get(sd[i - 1]) if i > 0 else None
        day     = rth[d]
        # intra-OR slope: VWAP at 9:35 vs 9:44
        def vwap_at(minute_limit):
            bs = [b for b in day
                  if b["timestamp"].hour == 9
                  and b["timestamp"].minute <= minute_limit]
            pv = sum(b["close"] * b["volume"] for b in bs)
            v  = sum(b["volume"] for b in bs)
            return pv / v if v > 0 else None
        v35   = vwap_at(35)
        v44   = vwap_at(44)
        slope = (v44 - v35) if (v44 is not None and v35 is not None) else None
        out[d] = {"prior": prior_v, "slope": slope}
    return out


def or_close_and_range(day_bars):
    """
    Compute OR high, OR low, and OR close from 9:30-9:44 bars.
    Returns (or_hi, or_lo, or_close) or None if data missing.
    """
    or_bars = [b for b in day_bars
               if b["timestamp"].hour == 9
               and 30 <= b["timestamp"].minute <= 44]
    if not or_bars:
        return None
    or_hi    = max(b["high"]  for b in or_bars)
    or_lo    = min(b["low"]   for b in or_bars)
    # OR close = 9:44 bar close (last bar of the OR)
    close_bar = max(or_bars, key=lambda b: b["timestamp"].minute)
    return or_hi, or_lo, close_bar["close"]


def confidence_score(direction, or_close, pv, vc):
    """
    Compute 0-4 confidence score same as confidence_score_test.py.
    pv = {P, R1, R2, S1, S2}
    vc = {prior: float|None, slope: float|None}
    direction = "long" | "short"
    """
    score = 0

    # 1. Pivot alignment
    above_P = or_close >= pv["P"]
    if (direction == "long"  and above_P) or \
       (direction == "short" and not above_P):
        score += 1

    # 2. Prior VWAP alignment
    prior_v = vc.get("prior")
    if prior_v is not None:
        above_v = or_close >= prior_v
        if (direction == "long"  and above_v) or \
           (direction == "short" and not above_v):
            score += 1

    # 3. HOT zone (R1_R2 for long, S2_S1 for short)
    if direction == "long"  and pv["R1"] <= or_close <= pv["R2"]:
        score += 1
    if direction == "short" and pv["S2"] <= or_close <= pv["S1"]:
        score += 1

    # 4. VWAP slope (rising 9:35→9:44 for long, falling for short)
    slope = vc.get("slope")
    if slope is not None:
        slope_up = slope > 0
        if (direction == "long"  and slope_up) or \
           (direction == "short" and not slope_up):
            score += 1

    return score


def simulate_trade(direction, entry_price, future_bars):
    """
    Simulate a single trade from entry_price using bars AFTER the entry bar.
    Returns {"result": "win"|"loss"|"flat", "pnl": float, "bars_held": int}
    """
    stop   = entry_price - STOP_DIST if direction == "long" else entry_price + STOP_DIST
    target = entry_price + TARGET_DIST if direction == "long" else entry_price - TARGET_DIST

    for i, b in enumerate(future_bars):
        bar_time = b["timestamp"].time()
        if bar_time >= FLATTEN_TIME:
            # flatten at close
            pnl = ((b["close"] - entry_price) if direction == "long"
                   else (entry_price - b["close"])) * POINT_VALUE
            return {"result": "flat", "pnl": round(pnl, 2), "bars_held": i + 1}

        if direction == "long":
            if b["low"] <= stop:
                pnl = (stop - entry_price) * POINT_VALUE
                return {"result": "loss", "pnl": round(pnl, 2), "bars_held": i + 1}
            if b["high"] >= target:
                pnl = (target - entry_price) * POINT_VALUE
                return {"result": "win",  "pnl": round(pnl, 2), "bars_held": i + 1}
        else:
            if b["high"] >= stop:
                pnl = (entry_price - stop) * POINT_VALUE
                return {"result": "loss", "pnl": round(pnl, 2), "bars_held": i + 1}
            if b["low"] <= target:
                pnl = (entry_price - target) * POINT_VALUE
                return {"result": "win",  "pnl": round(pnl, 2), "bars_held": i + 1}

    # ran off the end of data without hitting anything
    last  = future_bars[-1] if future_bars else None
    if last is None:
        return {"result": "flat", "pnl": 0.0, "bars_held": 0}
    pnl = ((last["close"] - entry_price) if direction == "long"
           else (entry_price - last["close"])) * POINT_VALUE
    return {"result": "flat", "pnl": round(pnl, 2), "bars_held": len(future_bars)}


# ── core scan ────────────────────────────────────────────────────────────────

def scan_day(d, day_bars, pivots, vwap_ctx):
    """
    Scan one trading day and return a list of simulated trades, one per
    (direction, bucket) that saw the first qualifying breakout in that window.

    Each returned dict has:
        date, dir, bucket_label, entry_time, entry_price,
        score, result, pnl, bars_held
    """
    pv = pivots.get(d)
    vc = vwap_ctx.get(d)
    if pv is None or vc is None:
        return []

    or_data = or_close_and_range(day_bars)
    if or_data is None:
        return []
    or_hi, or_lo, or_close = or_data

    # OR range sanity (mirrors main strategy filters)
    or_range = or_hi - or_lo
    if or_range < 55.0 or or_range > 110.0:
        return []

    # Bars after the OR (9:45 onward)
    post_or = [b for b in day_bars if b["timestamp"].time() >= time(9, 45)]

    trades = []

    for direction in ("long", "short"):
        threshold = (or_hi + BREAKOUT_BUF if direction == "long"
                     else or_lo - BREAKOUT_BUF)
        score = confidence_score(direction, or_close, pv, vc)

        # Find first qualifying bar in each bucket independently
        for bucket_label, bkt_start, bkt_end in BUCKETS:
            for idx, b in enumerate(post_or):
                bar_time = b["timestamp"].time()
                if bar_time < bkt_start:
                    continue
                if bar_time > bkt_end:
                    break  # bars are sorted; past the bucket

                qualifies = (b["close"] > threshold if direction == "long"
                             else b["close"] < threshold)
                if not qualifies:
                    continue

                # Found first qualifying bar in this bucket
                entry_price = b["close"]
                future_bars = [fb for fb in post_or if fb["timestamp"] > b["timestamp"]]
                outcome     = simulate_trade(direction, entry_price, future_bars)
                trades.append({
                    "date":         str(d),
                    "dir":          direction,
                    "bucket":       bucket_label,
                    "entry_time":   b["timestamp"].strftime("%H:%M"),
                    "entry_price":  entry_price,
                    "score":        score,
                    **outcome,
                })
                break   # only first qualifying bar per (direction, bucket)

    return trades


def run_all(bars):
    """Run the full scan across all bars. Returns list of trade dicts."""
    print("  Computing prior-day pivots...", end=" ", flush=True)
    pivots = compute_pivots(bars)
    print(f"{len(pivots)} sessions")

    print("  Computing prior-day VWAP & slope...", end=" ", flush=True)
    vwap_ctx = compute_prior_vwap(bars)
    print(f"{len(vwap_ctx)} sessions")

    print("  Grouping bars by date...", end=" ", flush=True)
    bbd   = bars_by_date(bars)
    dates = sorted(bbd.keys())
    print(f"{len(dates)} dates")

    print("  Scanning days...", end=" ", flush=True)
    all_trades = []
    skip_days  = {"Monday"}   # mirror SKIP_MONDAYS=True from config
    import calendar
    for d in dates:
        dow = calendar.day_name[d.weekday()]
        if dow in skip_days:
            continue
        trades = scan_day(d, bbd[d], pivots, vwap_ctx)
        all_trades.extend(trades)

    print(f"  {len(all_trades)} raw signal events across {len(dates)} days")
    return all_trades


# ── stats ─────────────────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0.0, "avg": 0.0}
    wins  = [t for t in trades if t["pnl"] > 0]
    gross = sum(t["pnl"] for t in wins)
    loss  = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    net   = sum(t["pnl"] for t in trades)
    return {
        "n":   len(trades),
        "wr":  len(wins) / len(trades),
        "pf":  round(gross / loss, 3) if loss > 0 else 99.0,
        "net": round(net, 0),
        "avg": round(net / len(trades), 0),
    }


# ── reporting ─────────────────────────────────────────────────────────────────

HDR_WIDTH = 22

def print_header():
    h = HDR_WIDTH
    print(f"  {'Bucket':<{h}} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10} {'Avg $':>7}")
    print(f"  {'─' * (h + 37)}")


def print_row(label, s, pf_flag_threshold=1.5):
    h = HDR_WIDTH
    pf_flag = " ✓" if s["pf"] >= pf_flag_threshold and s["n"] >= 10 else ""
    print(f"  {label:<{h}} {s['n']:>5} {s['wr']:>5.1%} {s['pf']:>6.3f}"
          f" {s['net']:>+10,.0f} {s['avg']:>+7,.0f}{pf_flag}")


def section(title):
    print(f"\n  ── {title} ──")
    print_header()


def print_bucket_table(trades, label_prefix=""):
    """Print IS vs OOS side-by-side for each bucket."""
    is_t  = [t for t in trades if int(t["date"][:4]) in IS_YEARS]
    oos_t = [t for t in trades if int(t["date"][:4]) in OOS_YEARS]

    h = HDR_WIDTH
    print(f"\n  {'Bucket':<{h}} {'':^2}  "
          f"{'── IS 2022-2024 ──':^35}  {'── OOS 2025-2026 ──':^35}")
    print(f"  {'':^{h}} {'':^2}  "
          f"{'N':>4} {'WR%':>5} {'PF':>5} {'Net $':>8} {'Avg':>6}  "
          f"{'N':>4} {'WR%':>5} {'PF':>5} {'Net $':>8} {'Avg':>6}")
    print(f"  {'─' * (h + 80)}")

    def fmt(s):
        pf_f = " ✓" if s["pf"] >= 1.5 and s["n"] >= 10 else "  "
        return (f"{s['n']:>4} {s['wr']:>5.1%} {s['pf']:>5.3f}"
                f" {s['net']:>+8,.0f} {s['avg']:>+6,.0f}{pf_f}")

    for bucket_label, _, _ in BUCKETS:
        bkt_is  = [t for t in is_t  if t["bucket"] == bucket_label]
        bkt_oos = [t for t in oos_t if t["bucket"] == bucket_label]
        si  = stats(bkt_is)
        so  = stats(bkt_oos)
        print(f"  {bucket_label:<{h}}     {fmt(si)}  {fmt(so)}")

    # Totals row
    print(f"  {'─' * (h + 80)}")
    si_all  = stats(is_t)
    so_all  = stats(oos_t)
    print(f"  {'ALL BUCKETS':<{h}}     {fmt(si_all)}  {fmt(so_all)}")


def print_cutoff_table(trades, filter_fn=None, title="All signals"):
    """
    For each cumulative cutoff (allow entries up to time X),
    print IS and OOS stats. Optionally pre-filter with filter_fn.
    """
    if filter_fn:
        trades = [t for t in trades if filter_fn(t)]

    print(f"\n  {title}")
    h = HDR_WIDTH + 4

    def fmt(s):
        flag = " ✓" if s["pf"] >= 1.5 and s["n"] >= 10 else "  "
        return (f"{s['n']:>4} {s['wr']:>5.1%} {s['pf']:>5.3f}"
                f" {s['net']:>+9,.0f} {s['avg']:>+6,.0f}{flag}")

    print(f"  {'Cutoff':<{h}}  "
          f"{'── IS 2022-2024 ──':^37}  {'── OOS 2025-2026 ──':^37}")
    print(f"  {'':^{h}}  "
          f"{'N':>4} {'WR%':>5} {'PF':>5} {'Net $':>9} {'Avg':>6}  "
          f"{'N':>4} {'WR%':>5} {'PF':>5} {'Net $':>9} {'Avg':>6}")
    print(f"  {'─' * (h + 80)}")

    for cutoff_label, cutoff_time in CUTOFFS:
        is_t  = [t for t in trades
                 if int(t["date"][:4]) in IS_YEARS
                 and _entry_time(t) <= cutoff_time]
        oos_t = [t for t in trades
                 if int(t["date"][:4]) in OOS_YEARS
                 and _entry_time(t) <= cutoff_time]
        si = stats(is_t)
        so = stats(oos_t)
        print(f"  {cutoff_label:<{h}}  {fmt(si)}  {fmt(so)}")


def _entry_time(t):
    h, m = map(int, t["entry_time"].split(":"))
    return time(h, m)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    W = 80
    print(f"\n{'=' * W}")
    print(f"  CruzCapital NQ — Extended Entry Window Study")
    print(f"  OR: 9:30-9:44 (15 min)  |  Breakout buf: {BREAKOUT_BUF}pt  "
          f"|  Stop: {STOP_DIST}pt  |  Target: {TARGET_DIST}pt ({RR}R)")
    print(f"  IS: {IS_YEARS}  |  OOS: {OOS_YEARS}")
    print(f"{'=' * W}\n")

    print("  Loading data...", end=" ", flush=True)
    bars = load_csv(DATA)
    print(f"{len(bars):,} bars  "
          f"({bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()})")

    all_trades = run_all(bars)
    print(f"\n  Total signal events: {len(all_trades)}")

    # ── Section 1: Trade count by bucket ──────────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  1. TRADE COUNT BY ENTRY-TIME BUCKET  (all signals, both directions)")
    print(f"{'=' * W}")
    print_bucket_table(all_trades)

    # ── Section 2: PF/WR by bucket, IS vs OOS ─────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  2. PF & WR BY ENTRY-TIME BUCKET — IS vs OOS")
    print(f"     Trade mechanics: entry=bar close, stop={STOP_DIST}pt, "
          f"target={TARGET_DIST}pt, flush at 15:55")
    print(f"{'=' * W}")

    for direction in ("long", "short", None):
        if direction is None:
            label = "ALL SIGNALS (long + short)"
            subset = all_trades
        else:
            label = f"{'LONG' if direction == 'long' else 'SHORT'} signals only"
            subset = [t for t in all_trades if t["dir"] == direction]
        print(f"\n  {label}")
        print_bucket_table(subset)

    # ── Section 3: With vs without score≥3 filter ─────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  3. CONFIDENCE SCORE FILTER  (score≥3 vs all signals)")
    print(f"     Score = pivot+VWAP+HOTzone+slope, each +1 point (max 4)")
    print(f"{'=' * W}")

    print(f"\n  --- All signals (no score filter) ---")
    print_bucket_table(all_trades, label_prefix="all")

    print(f"\n  --- Score ≥ 3 only ---")
    scored = [t for t in all_trades if t["score"] >= 3]
    print_bucket_table(scored, label_prefix="sc≥3")

    print(f"\n  Score ≥ 3 coverage: {len(scored)}/{len(all_trades)} "
          f"({len(scored)/len(all_trades):.1%}) of all signals")

    # Score distribution across buckets
    print(f"\n  Score distribution by bucket (IS+OOS combined):")
    h = HDR_WIDTH
    print(f"  {'Bucket':<{h}} {'sc=0':>5} {'sc=1':>5} {'sc=2':>5} "
          f"{'sc=3':>5} {'sc=4':>5} {'Total':>6}")
    print(f"  {'─' * (h + 36)}")
    for bucket_label, _, _ in BUCKETS:
        bt = [t for t in all_trades if t["bucket"] == bucket_label]
        row = [f"{len([t for t in bt if t['score']==sc]):>5}" for sc in range(5)]
        print(f"  {bucket_label:<{h}} {'  '.join(row)} {len(bt):>6}")

    # ── Section 4: Best cutoff — cumulative windows ───────────────────────────
    print(f"\n{'=' * W}")
    print(f"  4. BEST LAST_ENTRY_TIME — CUMULATIVE WINDOW ANALYSIS")
    print(f"     (All signals that fire on or before the cutoff time)")
    print(f"{'=' * W}")

    print_cutoff_table(all_trades, title="All signals (no score filter)")
    print_cutoff_table(
        all_trades,
        filter_fn=lambda t: t["score"] >= 3,
        title="Score ≥ 3 only"
    )

    # ── Section 5: Incremental value of each bucket ───────────────────────────
    print(f"\n{'=' * W}")
    print(f"  5. INCREMENTAL VALUE — OOS NET P&L ADDED BY EACH BUCKET")
    print(f"     'What does adding bucket X actually contribute OOS?'")
    print(f"{'=' * W}")

    oos = [t for t in all_trades if int(t["date"][:4]) in OOS_YEARS]
    oos_sc = [t for t in oos if t["score"] >= 3]

    running_net = 0.0
    running_n   = 0
    print(f"\n  All signals:")
    print(f"  {'Bucket':<{HDR_WIDTH}} {'N':>5} {'WR%':>6} {'PF':>6} "
          f"{'Incr $':>9} {'Cumul $':>10}")
    print(f"  {'─' * (HDR_WIDTH + 40)}")
    for bucket_label, _, _ in BUCKETS:
        bt = [t for t in oos if t["bucket"] == bucket_label]
        s  = stats(bt)
        running_net += s["net"]
        running_n   += s["n"]
        flag = " ✓" if s["pf"] >= 1.5 and s["n"] >= 5 else ""
        print(f"  {bucket_label:<{HDR_WIDTH}} {s['n']:>5} {s['wr']:>5.1%} "
              f"{s['pf']:>6.3f} {s['net']:>+9,.0f} {running_net:>+10,.0f}{flag}")

    running_net = 0.0
    running_n   = 0
    print(f"\n  Score ≥ 3 only:")
    print(f"  {'Bucket':<{HDR_WIDTH}} {'N':>5} {'WR%':>6} {'PF':>6} "
          f"{'Incr $':>9} {'Cumul $':>10}")
    print(f"  {'─' * (HDR_WIDTH + 40)}")
    for bucket_label, _, _ in BUCKETS:
        bt = [t for t in oos_sc if t["bucket"] == bucket_label]
        s  = stats(bt)
        running_net += s["net"]
        running_n   += s["n"]
        flag = " ✓" if s["pf"] >= 1.5 and s["n"] >= 5 else ""
        print(f"  {bucket_label:<{HDR_WIDTH}} {s['n']:>5} {s['wr']:>5.1%} "
              f"{s['pf']:>6.3f} {s['net']:>+9,.0f} {running_net:>+10,.0f}{flag}")

    # ── Section 6: Year-by-year OOS detail ───────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  6. OOS YEAR-BY-YEAR BREAKDOWN (each bucket)")
    print(f"{'=' * W}")

    for yr in OOS_YEARS:
        yr_t = [t for t in all_trades if t["date"][:4] == str(yr)]
        yr_sc = [t for t in yr_t if t["score"] >= 3]
        print(f"\n  {yr}  (N={len(yr_t)}, score≥3 N={len(yr_sc)})")
        h = HDR_WIDTH
        print(f"  {'Bucket':<{h}} {'':^2}  "
              f"{'── All signals ──':^34}  {'── Score≥3 ──':^34}")
        print(f"  {'':^{h}}     "
              f"{'N':>4} {'WR%':>5} {'PF':>5} {'Net $':>8} {'Avg':>6}  "
              f"{'N':>4} {'WR%':>5} {'PF':>5} {'Net $':>8} {'Avg':>6}")
        print(f"  {'─' * (h + 80)}")
        for bucket_label, _, _ in BUCKETS:
            bk_all = [t for t in yr_t  if t["bucket"] == bucket_label]
            bk_sc  = [t for t in yr_sc if t["bucket"] == bucket_label]
            sa = stats(bk_all)
            ss3 = stats(bk_sc)
            def fmt(s):
                flag = " ✓" if s["pf"] >= 1.5 and s["n"] >= 5 else "  "
                return (f"{s['n']:>4} {s['wr']:>5.1%} {s['pf']:>5.3f}"
                        f" {s['net']:>+8,.0f} {s['avg']:>+6,.0f}{flag}")
            print(f"  {bucket_label:<{h}}     {fmt(sa)}  {fmt(ss3)}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  VERDICT")
    print(f"{'=' * W}\n")

    # Find best OOS PF across cutoffs for score≥3
    best_pf        = 0.0
    best_cutoff    = ""
    best_n         = 0
    best_net       = 0.0
    best_all_pf    = 0.0
    best_all_cutoff = ""

    for cutoff_label, cutoff_time in CUTOFFS:
        oos_cut = [t for t in all_trades
                   if int(t["date"][:4]) in OOS_YEARS
                   and _entry_time(t) <= cutoff_time]
        oos_cut_sc = [t for t in oos_cut if t["score"] >= 3]

        s_all = stats(oos_cut)
        s_sc  = stats(oos_cut_sc)

        if s_sc["n"] >= 20 and s_sc["pf"] > best_pf:
            best_pf     = s_sc["pf"]
            best_cutoff = cutoff_label
            best_n      = s_sc["n"]
            best_net    = s_sc["net"]

        if s_all["n"] >= 20 and s_all["pf"] > best_all_pf:
            best_all_pf     = s_all["pf"]
            best_all_cutoff = cutoff_label

    # Bucket-by-bucket summary for verdict
    print(f"  OOS bucket performance summary (all signals):")
    for bucket_label, _, _ in BUCKETS:
        bt  = [t for t in oos if t["bucket"] == bucket_label]
        bts = [t for t in bt  if t["score"] >= 3]
        s   = stats(bt)
        ss3 = stats(bts)
        keep = "KEEP" if s["pf"] >= 1.5 and s["n"] >= 10 else \
               ("WEAK" if s["pf"] >= 1.2 else "DROP")
        keep3 = "KEEP" if ss3["pf"] >= 1.5 and ss3["n"] >= 5 else \
                ("WEAK" if ss3["pf"] >= 1.2 else "DROP")
        print(f"  {bucket_label}  "
              f"all: PF={s['pf']:.3f} N={s['n']:>3} [{keep}]   "
              f"sc≥3: PF={ss3['pf']:.3f} N={ss3['n']:>3} [{keep3}]")

    print()
    print(f"  Best cutoff — all signals  : {best_all_cutoff}  (OOS PF={best_all_pf:.3f})")
    print(f"  Best cutoff — score≥3 only : {best_cutoff}  "
          f"(OOS PF={best_pf:.3f}, N={best_n}, Net=${best_net:+,.0f})")

    # Practical recommendation
    print()
    oos_current = [t for t in all_trades
                   if int(t["date"][:4]) in OOS_YEARS
                   and _entry_time(t) <= time(10, 29)]
    oos_c_sc    = [t for t in oos_current if t["score"] >= 3]

    def pf_str(s):
        return f"PF={s['pf']:.3f} N={s['n']} Net=${s['net']:+,.0f}"

    print(f"  Current window (≤10:29):")
    print(f"    All signals : {pf_str(stats(oos_current))}")
    print(f"    Score ≥ 3   : {pf_str(stats(oos_c_sc))}")
    print()

    # Recommendation
    c_b = [t for t in oos if t["bucket"] == "C: 10:30-11:59"]
    d_b = [t for t in oos if t["bucket"] == "D: 12:00-13:59"]
    sc_c = stats([t for t in c_b if t["score"] >= 3])
    sc_d = stats([t for t in d_b if t["score"] >= 3])

    print(f"  RECOMMENDATION:")
    if sc_c["pf"] >= 1.5 and sc_c["n"] >= 10:
        print(f"  → ADD Bucket C (10:30-11:59) with score≥3 filter: "
              f"PF={sc_c['pf']:.3f}, N={sc_c['n']}, Net=${sc_c['net']:+,.0f}")
        print(f"    Extend LAST_ENTRY_TIME to '11:59' in config.py, gated by score≥3")
    elif sc_c["pf"] >= 1.2 and sc_c["n"] >= 10:
        print(f"  → Bucket C (10:30-11:59) score≥3 is marginal: "
              f"PF={sc_c['pf']:.3f}, N={sc_c['n']} — needs more data before live use")
    else:
        print(f"  → Bucket C (10:30-11:59) score≥3 does NOT meet PF≥1.5 bar: "
              f"PF={sc_c['pf']:.3f}, N={sc_c['n']} — do NOT extend the window")

    if sc_d["pf"] >= 1.5 and sc_d["n"] >= 10:
        print(f"  → ADD Bucket D (12:00-13:59) with score≥3 filter: "
              f"PF={sc_d['pf']:.3f}, N={sc_d['n']}, Net=${sc_d['net']:+,.0f}")
    else:
        print(f"  → Bucket D (12:00-13:59) score≥3: "
              f"PF={sc_d['pf']:.3f}, N={sc_d['n']} — does not qualify")

    print(f"\n{'=' * W}")
    print(f"  Done.")
    print(f"{'=' * W}\n")


if __name__ == "__main__":
    main()
