"""
brain/research/reversal_research.py

CruzCapital NQ — Three approaches to increase trade frequency with high PF.

Strategy A  — Failed ORB Reversal (fade)
  If ORB fires long (close > orHi+4) then within 8 bars a close < orLo-4
  appears, that is a "failed breakout" — fade it short (and vice versa).
  Stop: 27pt  Target: 3R = 81pt  Time gate: ≤ 11:30 ET  1 reversal per day.

Strategy B  — VWAP re-entry after ORB target hit
  When the first ORB trade hits its 3R target, wait for price to pull back
  within 3pt of session VWAP between 10:30–14:00.
  Stop: 20pt  Target: 2R = 40pt  1 re-entry per day.

Strategy C  — OR midpoint VWAP cross on range-bound days
  Days with no breakout (no close > orHi+4 or < orLo-4) by 10:30.
  On those days: long when close crosses above session VWAP with rising slope,
  short when crosses below with falling slope.  Window: 10:30–13:00.
  Stop: 20pt  Target: 2R = 40pt.

Combined trade count: baseline ORB + A + B + C vs 15-20 trades/month goal.

IS: [2022, 2023, 2024]   OOS: [2025, 2026]
Data: data/nq_full.csv (1-minute bars, 2022-2026)

Usage: python3 brain/research/reversal_research.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import load_csv
from collections import defaultdict
from datetime import date, time, timedelta
import config

# ── Constants ─────────────────────────────────────────────────────────────────
DATA_PATH = "data/nq_full.csv"
ALL_YEARS  = [2022, 2023, 2024, 2025, 2026]
IS_YEARS   = [2022, 2023, 2024]
OOS_YEARS  = [2025, 2026]

CPV         = config.POINT_VALUE          # $20/pt for NQ
COMMISSION  = config.COMMISSION_PER_SIDE  # $2.50 per side
SLIP_PTS    = config.SLIPPAGE_TICKS * config.TICK_SIZE  # 2 ticks slippage

# ── ORB parameters (match live config) ───────────────────────────────────────
OR_MINUTES  = config.OPENING_RANGE_MINUTES   # 15 → range is 9:30-9:44
OR_END      = time(9, 30 + OR_MINUTES - 1)  # last bar included in OR = 9:44
OR_BREAKOUT_BUFFER = config.ORB_BREAKOUT_BUFFER_POINTS  # 4pt

# ── Strategy A parameters ─────────────────────────────────────────────────────
A_WINDOW_BARS  = 8     # bars after first breakout to watch for reversal
A_STOP         = 27.0  # same effective stop as main ORB
A_TARGET_RR    = 3.0   # 3R = 81pt
A_TIME_GATE    = time(11, 30)

# ── Strategy B parameters ─────────────────────────────────────────────────────
B_VWAP_TOL     = 3.0   # within 3pt of session VWAP
B_ENTRY_START  = time(10, 30)
B_ENTRY_END    = time(14, 0)
B_STOP         = 20.0
B_TARGET_RR    = 2.0   # 2R = 40pt

# ── Strategy C parameters ─────────────────────────────────────────────────────
C_RANGE_END    = time(10, 30)  # no breakout by this time → range-bound
C_ENTRY_START  = time(10, 30)
C_ENTRY_END    = time(13, 0)
C_STOP         = 20.0
C_TARGET_RR    = 2.0   # 2R = 40pt
C_SLOPE_BARS   = 5     # minutes for VWAP slope computation


# ── Helpers ───────────────────────────────────────────────────────────────────

def pnl_from_pts(pts, n_contracts=1):
    """Convert raw point gain/loss to dollar P&L (1c default, with commissions)."""
    return pts * CPV * n_contracts - COMMISSION * 2 * n_contracts


def stats(trades):
    if not trades:
        return {"n": 0, "net": 0, "wr": 0.0, "pf": 0.0, "avg": 0}
    wins = [t for t in trades if t["pnl"] > 0]
    gl   = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    net  = sum(t["pnl"] for t in trades)
    return {
        "n":   len(trades),
        "net": round(net, 0),
        "wr":  len(wins) / len(trades),
        "pf":  round(sum(t["pnl"] for t in wins) / gl, 3) if gl else 99.0,
        "avg": round(net / len(trades), 0),
    }


def group_by_day(bars):
    """Return {date: [bars...]} for RTH only (9:30-16:00)."""
    by_day = defaultdict(list)
    for bar in bars:
        ts = bar["timestamp"]
        t  = ts.time()
        if time(9, 30) <= t <= time(16, 0):
            by_day[ts.date()].append(bar)
    return {d: sorted(v, key=lambda b: b["timestamp"]) for d, v in by_day.items()}


def compute_session_vwap(bars_up_to_i, day_bars):
    """Rolling session VWAP at bar index i (inclusive) within day_bars."""
    pv = 0.0
    vol = 0.0
    for bar in day_bars[:bars_up_to_i + 1]:
        v = bar["volume"]
        pv  += bar["close"] * v
        vol += v
    return pv / vol if vol > 0 else None


# ── Baseline ORB extraction ───────────────────────────────────────────────────

def extract_orb_trades(bars):
    """
    Simulate the baseline ORB strategy in standalone fashion for research:
    - OR = 9:30-9:44 bars
    - Breakout: close > orHi + 4  (long)  or  close < orLo - 4  (short)
    - 27pt stop (hardcoded eff stop), 3R target = 81pt
    - Entry window up to 10:30 ET
    - One trade per day, skip Mondays (match config.SKIP_MONDAYS)
    - OR size gate: 55-110pt

    Returns list of day records:
      {date, dir, orHi, orLo, entry, stop, target, hit_target, hit_stop,
       exit_bar_idx, exit_price, pnl}
    """
    by_day = group_by_day(bars)
    results = []

    for d in sorted(by_day):
        day_bars = by_day[d]
        # Monday skip
        if config.SKIP_MONDAYS and d.weekday() == 0:
            continue

        # Build OR
        or_bars = [b for b in day_bars if b["timestamp"].time() <= OR_END]
        if len(or_bars) < OR_MINUTES:
            continue
        orHi = max(b["high"]  for b in or_bars)
        orLo = min(b["low"]   for b in or_bars)
        or_size = orHi - orLo

        # OR size gate
        if or_size < config.ORB_MIN_RANGE_POINTS or or_size > config.ORB_MAX_RANGE_POINTS:
            continue

        # Post-OR bars (starting at 9:45)
        post_or = [b for b in day_bars if b["timestamp"].time() > OR_END]

        # Scan for breakout up to LAST_ENTRY (10:30)
        entry_d, stop_d, target_d, direction = None, None, None, None
        break_idx = None

        for idx, bar in enumerate(post_or):
            t = bar["timestamp"].time()
            if t > time(10, 30):
                break
            close = bar["close"]
            if close > orHi + OR_BREAKOUT_BUFFER:
                direction = "long"
                entry_d   = close + SLIP_PTS
                stop_d    = entry_d - 27.0
                target_d  = entry_d + 27.0 * 3.0
                break_idx = idx
                break
            elif close < orLo - OR_BREAKOUT_BUFFER:
                direction = "short"
                entry_d   = close - SLIP_PTS
                stop_d    = entry_d + 27.0
                target_d  = entry_d - 27.0 * 3.0
                break_idx = idx
                break

        if direction is None:
            continue

        # Simulate trade through remaining bars
        is_long    = direction == "long"
        hit_target = hit_stop = False
        exit_price = None
        exit_idx   = None

        for idx in range(break_idx + 1, len(post_or)):
            bar   = post_or[idx]
            t     = bar["timestamp"].time()
            is_long = direction == "long"

            if is_long:
                if bar["low"] <= stop_d:
                    hit_stop   = True
                    exit_price = stop_d - SLIP_PTS
                    exit_idx   = idx
                    break
                if bar["high"] >= target_d:
                    hit_target = True
                    exit_price = target_d
                    exit_idx   = idx
                    break
            else:
                if bar["high"] >= stop_d:
                    hit_stop   = True
                    exit_price = stop_d + SLIP_PTS
                    exit_idx   = idx
                    break
                if bar["low"] <= target_d:
                    hit_target = True
                    exit_price = target_d
                    exit_idx   = idx
                    break

            # Flatten at 15:55
            if t >= time(15, 55):
                exit_price = bar["close"]
                exit_idx   = idx
                break

        if exit_price is None:
            # Flatten at end of day
            exit_price = post_or[-1]["close"]
            exit_idx   = len(post_or) - 1

        pts = (exit_price - entry_d) if is_long else (entry_d - exit_price)
        trade_pnl = pnl_from_pts(pts)

        results.append({
            "date":       str(d),
            "year":       d.year,
            "dir":        direction,
            "orHi":       orHi,
            "orLo":       orLo,
            "or_size":    or_size,
            "entry":      entry_d,
            "stop":       stop_d,
            "target":     target_d,
            "hit_target": hit_target,
            "hit_stop":   hit_stop,
            "exit_price": exit_price,
            "exit_idx":   exit_idx,       # index in post_or list
            "pnl":        trade_pnl,
            "post_or":    post_or,        # full post-OR bar list (for B/C lookup)
            "break_idx":  break_idx,      # index of breakout bar in post_or
        })

    return results


# ── Strategy A — Failed ORB Reversal ─────────────────────────────────────────

def run_strategy_a(orb_trades):
    """
    For each ORB trade, scan the 8 bars immediately after the breakout bar for
    a close that pierces back through the opposite OR edge (plus buffer).
    If found within time gate, simulate a reversal entry.
    """
    a_trades = []

    for orb in orb_trades:
        d         = orb["date"]
        post_or   = orb["post_or"]
        break_idx = orb["break_idx"]
        direction = orb["dir"]
        orHi      = orb["orHi"]
        orLo      = orb["orLo"]

        # Reversal search starts the bar after the breakout
        search_start = break_idx + 1
        search_end   = min(break_idx + 1 + A_WINDOW_BARS, len(post_or))

        rev_entry   = None
        rev_dir     = None
        rev_bar_idx = None

        for idx in range(search_start, search_end):
            bar = post_or[idx]
            t   = bar["timestamp"].time()
            if t > A_TIME_GATE:
                break
            close = bar["close"]

            if direction == "long" and close < orLo - OR_BREAKOUT_BUFFER:
                # Failed long breakout → fade short
                rev_dir     = "short"
                rev_entry   = close - SLIP_PTS
                rev_bar_idx = idx
                break
            elif direction == "short" and close > orHi + OR_BREAKOUT_BUFFER:
                # Failed short breakout → fade long
                rev_dir     = "long"
                rev_entry   = close + SLIP_PTS
                rev_bar_idx = idx
                break

        if rev_dir is None:
            continue

        is_long   = rev_dir == "long"
        rev_stop  = rev_entry - A_STOP if is_long else rev_entry + A_STOP
        rev_tgt   = rev_entry + A_STOP * A_TARGET_RR if is_long else rev_entry - A_STOP * A_TARGET_RR

        hit_target = hit_stop = False
        exit_price = None

        for idx in range(rev_bar_idx + 1, len(post_or)):
            bar = post_or[idx]
            t   = bar["timestamp"].time()

            if is_long:
                if bar["low"] <= rev_stop:
                    hit_stop   = True
                    exit_price = rev_stop - SLIP_PTS
                    break
                if bar["high"] >= rev_tgt:
                    hit_target = True
                    exit_price = rev_tgt
                    break
            else:
                if bar["high"] >= rev_stop:
                    hit_stop   = True
                    exit_price = rev_stop + SLIP_PTS
                    break
                if bar["low"] <= rev_tgt:
                    hit_target = True
                    exit_price = rev_tgt
                    break

            if t >= time(15, 55):
                exit_price = bar["close"]
                break

        if exit_price is None:
            exit_price = post_or[-1]["close"]

        pts       = (exit_price - rev_entry) if is_long else (rev_entry - exit_price)
        trade_pnl = pnl_from_pts(pts)
        d_obj     = date.fromisoformat(d)

        a_trades.append({
            "date":         d,
            "year":         d_obj.year,
            "dir":          rev_dir,
            "orb_dir":      direction,
            "entry":        rev_entry,
            "stop":         rev_stop,
            "target":       rev_tgt,
            "hit_target":   hit_target,
            "hit_stop":     hit_stop,
            "exit_price":   exit_price,
            "pnl":          trade_pnl,
        })

    return a_trades


# ── Strategy B — VWAP re-entry after ORB target hit ──────────────────────────

def run_strategy_b(orb_trades, bars):
    """
    On days where the ORB trade hit its 3R target, look for a pullback to
    session VWAP between 10:30-14:00, then re-enter in same direction.
    """
    # Pre-build bar index for fast lookup
    by_day = group_by_day(bars)
    b_trades = []

    for orb in orb_trades:
        if not orb["hit_target"]:
            continue

        d_obj     = date.fromisoformat(orb["date"])
        day_bars  = by_day.get(d_obj, [])
        direction = orb["dir"]
        is_long   = direction == "long"

        # Cumulative VWAP state (we iterate the full day and track cumulative PV/V)
        cum_pv  = 0.0
        cum_vol = 0.0

        re_entry   = None
        re_bar_idx = None

        for i, bar in enumerate(day_bars):
            t = bar["timestamp"].time()

            # Accumulate VWAP
            v = bar["volume"]
            cum_pv  += bar["close"] * v
            cum_vol += v
            vwap    = cum_pv / cum_vol if cum_vol > 0 else None

            if vwap is None:
                continue
            if t < B_ENTRY_START or t > B_ENTRY_END:
                continue

            # Check pullback to within B_VWAP_TOL of session VWAP
            close = bar["close"]
            near_vwap = abs(close - vwap) <= B_VWAP_TOL

            if near_vwap and re_entry is None:
                # Enter in same direction as original ORB trade
                if is_long:
                    re_entry   = close + SLIP_PTS
                else:
                    re_entry   = close - SLIP_PTS
                re_bar_idx = i
                break

        if re_entry is None:
            continue

        re_stop  = re_entry - B_STOP if is_long else re_entry + B_STOP
        re_tgt   = re_entry + B_STOP * B_TARGET_RR if is_long else re_entry - B_STOP * B_TARGET_RR

        hit_target = hit_stop = False
        exit_price = None

        for bar in day_bars[re_bar_idx + 1:]:
            t = bar["timestamp"].time()

            if is_long:
                if bar["low"] <= re_stop:
                    hit_stop   = True
                    exit_price = re_stop - SLIP_PTS
                    break
                if bar["high"] >= re_tgt:
                    hit_target = True
                    exit_price = re_tgt
                    break
            else:
                if bar["high"] >= re_stop:
                    hit_stop   = True
                    exit_price = re_stop + SLIP_PTS
                    break
                if bar["low"] <= re_tgt:
                    hit_target = True
                    exit_price = re_tgt
                    break

            if t >= time(15, 55):
                exit_price = bar["close"]
                break

        if exit_price is None:
            exit_price = day_bars[-1]["close"]

        pts       = (exit_price - re_entry) if is_long else (re_entry - exit_price)
        trade_pnl = pnl_from_pts(pts)

        b_trades.append({
            "date":       orb["date"],
            "year":       d_obj.year,
            "dir":        direction,
            "entry":      re_entry,
            "stop":       re_stop,
            "target":     re_tgt,
            "hit_target": hit_target,
            "hit_stop":   hit_stop,
            "exit_price": exit_price,
            "pnl":        trade_pnl,
        })

    return b_trades


# ── Strategy C — Range-bound VWAP cross ──────────────────────────────────────

def run_strategy_c(bars, orb_trades):
    """
    Days where no close > orHi+4 or < orLo-4 by 10:30 ET are range-bound.
    On those days, enter when price crosses session VWAP with slope confirmation.
    """
    by_day = group_by_day(bars)

    # Build set of days that had an ORB trade (= had a breakout)
    orb_days = {date.fromisoformat(t["date"]) for t in orb_trades}

    # Also find days with valid OR (passed size gate) but no breakout
    c_trades = []
    range_bound_days = {}  # date → {orHi, orLo}

    # We need to re-derive which days had a valid OR but no breakout
    for d in sorted(by_day):
        if d in orb_days:
            continue  # breakout day — not range-bound for our purposes

        day_bars = by_day[d]
        # Monday skip
        if config.SKIP_MONDAYS and d.weekday() == 0:
            continue

        # Build OR
        or_bars = [b for b in day_bars if b["timestamp"].time() <= OR_END]
        if len(or_bars) < OR_MINUTES:
            continue
        orHi    = max(b["high"] for b in or_bars)
        orLo    = min(b["low"]  for b in or_bars)
        or_size = orHi - orLo

        # OR size gate
        if or_size < config.ORB_MIN_RANGE_POINTS or or_size > config.ORB_MAX_RANGE_POINTS:
            continue

        # Verify: no breakout bar exists up to 10:30
        had_breakout = False
        post_or = [b for b in day_bars if b["timestamp"].time() > OR_END]
        for bar in post_or:
            t = bar["timestamp"].time()
            if t > C_RANGE_END:
                break
            if bar["close"] > orHi + OR_BREAKOUT_BUFFER or bar["close"] < orLo - OR_BREAKOUT_BUFFER:
                had_breakout = True
                break

        if had_breakout:
            continue

        range_bound_days[d] = {"orHi": orHi, "orLo": orLo}

        # ── VWAP cross entry ──────────────────────────────────────────────────
        # Compute rolling VWAP and slope, look for cross with slope confirmation
        cum_pv  = 0.0
        cum_vol = 0.0
        prev_close  = None
        prev_vwap   = None
        slope_hist  = []   # recent VWAP values for slope
        entered     = False

        for i, bar in enumerate(day_bars):
            t = bar["timestamp"].time()

            v = bar["volume"]
            cum_pv  += bar["close"] * v
            cum_vol += v
            vwap    = cum_pv / cum_vol if cum_vol > 0 else None

            if vwap is not None:
                slope_hist.append(vwap)
                if len(slope_hist) > C_SLOPE_BARS:
                    slope_hist.pop(0)

            if t < C_ENTRY_START or t > C_ENTRY_END:
                prev_close = bar["close"]
                prev_vwap  = vwap
                continue

            if entered or vwap is None or prev_vwap is None or prev_close is None:
                prev_close = bar["close"]
                prev_vwap  = vwap
                continue

            close = bar["close"]

            # Slope: positive if recent VWAP trend is rising
            if len(slope_hist) >= C_SLOPE_BARS:
                slope = slope_hist[-1] - slope_hist[0]
            else:
                slope = 0.0

            # Cross above VWAP with rising slope → long
            long_signal  = (prev_close < prev_vwap and close > vwap and slope > 0)
            # Cross below VWAP with falling slope → short
            short_signal = (prev_close > prev_vwap and close < vwap and slope < 0)

            if long_signal or short_signal:
                direction = "long" if long_signal else "short"
                is_long   = direction == "long"
                c_entry   = close + SLIP_PTS if is_long else close - SLIP_PTS
                c_stop    = c_entry - C_STOP if is_long else c_entry + C_STOP
                c_tgt     = c_entry + C_STOP * C_TARGET_RR if is_long else c_entry - C_STOP * C_TARGET_RR
                entered   = True

                hit_target = hit_stop = False
                exit_price = None

                for bar2 in day_bars[i + 1:]:
                    t2 = bar2["timestamp"].time()

                    if is_long:
                        if bar2["low"] <= c_stop:
                            hit_stop   = True
                            exit_price = c_stop - SLIP_PTS
                            break
                        if bar2["high"] >= c_tgt:
                            hit_target = True
                            exit_price = c_tgt
                            break
                    else:
                        if bar2["high"] >= c_stop:
                            hit_stop   = True
                            exit_price = c_stop + SLIP_PTS
                            break
                        if bar2["low"] <= c_tgt:
                            hit_target = True
                            exit_price = c_tgt
                            break

                    if t2 >= time(15, 55):
                        exit_price = bar2["close"]
                        break

                if exit_price is None:
                    exit_price = day_bars[-1]["close"]

                pts       = (exit_price - c_entry) if is_long else (c_entry - exit_price)
                trade_pnl = pnl_from_pts(pts)

                c_trades.append({
                    "date":       str(d),
                    "year":       d.year,
                    "dir":        direction,
                    "entry":      c_entry,
                    "stop":       c_stop,
                    "target":     c_tgt,
                    "hit_target": hit_target,
                    "hit_stop":   hit_stop,
                    "exit_price": exit_price,
                    "pnl":        trade_pnl,
                })

            prev_close = bar["close"]
            prev_vwap  = vwap

    return c_trades, range_bound_days


# ── Reporting helpers ─────────────────────────────────────────────────────────

W = 74

def hdr(label_width=28):
    print(f"  {'Label':<{label_width}}  {'N':>4}  {'WR':>6}  {'PF':>5}  "
          f"{'Net $':>10}  {'Avg $':>7}")
    print(f"  {'─' * (label_width + 40)}")


def row(label, s, label_width=28):
    print(f"  {label:<{label_width}}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
          f"${s['net']:>+9,.0f}  ${s['avg']:>+6,.0f}")


def section(title):
    print(f"\n{'=' * W}")
    print(f"  {title}")
    print(f"{'=' * W}")


def subsection(title):
    print(f"\n  ── {title} ──")


# ── Annual breakdown ──────────────────────────────────────────────────────────

def annual_breakdown(trades, label_width=28):
    hdr(label_width)
    for yr in ALL_YEARS:
        yt = [t for t in trades if t["year"] == yr]
        if not yt:
            continue
        s = stats(yt)
        row(str(yr), s, label_width)
    row("ALL YEARS", stats(trades), label_width)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'=' * W}")
    print(f"  CruzCapital NQ — Reversal & Re-entry Research")
    print(f"  IS: {IS_YEARS}  |  OOS: {OOS_YEARS}")
    print(f"{'=' * W}")

    print(f"\n  Loading data...", end=" ", flush=True)
    bars = load_csv(DATA_PATH)
    print(f"{len(bars):,} bars  "
          f"({bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()})")

    # ── Baseline ORB ─────────────────────────────────────────────────────────
    print(f"  Running standalone ORB baseline...", end=" ", flush=True)
    orb_trades = extract_orb_trades(bars)
    print(f"{len(orb_trades)} trades")

    orb_is  = [t for t in orb_trades if t["year"] in IS_YEARS]
    orb_oos = [t for t in orb_trades if t["year"] in OOS_YEARS]

    section("BASELINE ORB TRADES (standalone simulation)")
    print(f"  Parameters: OR={OR_MINUTES}min, buffer={OR_BREAKOUT_BUFFER}pt, "
          f"stop=27pt, target=3R=81pt, entry window≤10:30")
    print(f"  OR size gate: {config.ORB_MIN_RANGE_POINTS}-{config.ORB_MAX_RANGE_POINTS}pt, "
          f"skip Mondays={config.SKIP_MONDAYS}")

    subsection("IS/OOS Summary")
    hdr()
    row(f"IS  {IS_YEARS}",  stats(orb_is),    )
    row(f"OOS {OOS_YEARS}", stats(orb_oos))
    row("ALL YEARS",        stats(orb_trades))

    subsection("Annual Breakdown")
    annual_breakdown(orb_trades)

    # Trade frequency
    orb_per_yr = {}
    for yr in ALL_YEARS:
        yt = [t for t in orb_trades if t["year"] == yr]
        orb_per_yr[yr] = len(yt)
        if yt:
            # Approximate months of data
            months = 12 if yr < 2026 else 6  # 2026 partial
            print(f"  {yr}: {len(yt)} trades / {months} months = "
                  f"{len(yt)/months:.1f} trades/month")

    orb_total_months = sum(12 if yr < 2026 else 6 for yr in ALL_YEARS if orb_per_yr.get(yr, 0) > 0)
    orb_avg_per_month = sum(orb_per_yr.values()) / orb_total_months if orb_total_months else 0
    print(f"\n  Average across all years: {orb_avg_per_month:.1f} trades/month "
          f"(goal is 15-20)")

    # ── Strategy A ───────────────────────────────────────────────────────────
    print(f"\n  Running Strategy A (failed ORB reversal)...", end=" ", flush=True)
    a_trades = run_strategy_a(orb_trades)
    print(f"{len(a_trades)} reversal trades")

    a_is  = [t for t in a_trades if t["year"] in IS_YEARS]
    a_oos = [t for t in a_trades if t["year"] in OOS_YEARS]

    section("STRATEGY A — Failed ORB Reversal (Fade)")
    print(f"  Logic: ORB fires long (close>orHi+{OR_BREAKOUT_BUFFER}), then within {A_WINDOW_BARS} bars")
    print(f"         close<orLo-{OR_BREAKOUT_BUFFER} → fade short (vice versa for failed short)")
    print(f"  Stop: {A_STOP}pt  Target: {A_TARGET_RR}R = {A_STOP*A_TARGET_RR:.0f}pt  "
          f"Time gate: ≤{A_TIME_GATE.strftime('%H:%M')} ET  1 per day")

    # Rate: how many ORB trades result in a failed-breakout reversal setup?
    n_orb_total = len(orb_trades)
    n_failed    = len(a_trades)
    print(f"\n  Failed breakout rate: {n_failed}/{n_orb_total} ORB days "
          f"= {n_failed/n_orb_total:.1%} of all ORB days trigger a reversal setup")

    subsection("IS/OOS Summary")
    hdr()
    row(f"IS  {IS_YEARS}",  stats(a_is))
    row(f"OOS {OOS_YEARS}", stats(a_oos))
    row("ALL YEARS",        stats(a_trades))

    subsection("Annual Breakdown")
    annual_breakdown(a_trades)

    # Win rate on the reversal specifically
    a_wins = [t for t in a_trades if t["pnl"] > 0]
    print(f"\n  Failed ORB → profitable reversal: {len(a_wins)}/{len(a_trades)} "
          f"= {len(a_wins)/len(a_trades):.1%} WR "
          f"({'> 50%' if len(a_wins)/len(a_trades) > 0.5 else '<= 50%'})")

    # Direction split
    long_rev  = [t for t in a_trades if t["dir"] == "long"]
    short_rev = [t for t in a_trades if t["dir"] == "short"]
    subsection("By reversal direction")
    hdr()
    row("Reversal LONG  (was short ORB)", stats(long_rev))
    row("Reversal SHORT (was long ORB)",  stats(short_rev))

    # ── Strategy B ───────────────────────────────────────────────────────────
    print(f"\n  Running Strategy B (VWAP re-entry after target)...", end=" ", flush=True)
    b_trades = run_strategy_b(orb_trades, bars)
    print(f"{len(b_trades)} re-entry trades")

    b_is  = [t for t in b_trades if t["year"] in IS_YEARS]
    b_oos = [t for t in b_trades if t["year"] in OOS_YEARS]

    # How many target days exist?
    orb_target_days = [t for t in orb_trades if t["hit_target"]]
    pct_get_reentry = len(b_trades) / len(orb_target_days) if orb_target_days else 0

    section("STRATEGY B — VWAP Re-entry After ORB Target Hit")
    print(f"  Logic: ORB hits 3R target → wait for pullback to session VWAP (±{B_VWAP_TOL}pt)")
    print(f"         Entry window: {B_ENTRY_START.strftime('%H:%M')}–{B_ENTRY_END.strftime('%H:%M')} ET")
    print(f"  Stop: {B_STOP}pt  Target: {B_TARGET_RR}R = {B_STOP*B_TARGET_RR:.0f}pt  1 per day")

    print(f"\n  ORB target-hitting days:  {len(orb_target_days)} / {len(orb_trades)} "
          f"= {len(orb_target_days)/len(orb_trades):.1%} of ORB days")
    print(f"  Target days that get a usable VWAP re-entry:  "
          f"{len(b_trades)} / {len(orb_target_days)} = {pct_get_reentry:.1%}")

    subsection("IS/OOS Summary")
    hdr()
    row(f"IS  {IS_YEARS}",  stats(b_is))
    row(f"OOS {OOS_YEARS}", stats(b_oos))
    row("ALL YEARS",        stats(b_trades))

    subsection("Annual Breakdown")
    annual_breakdown(b_trades)

    # Direction split
    b_long  = [t for t in b_trades if t["dir"] == "long"]
    b_short = [t for t in b_trades if t["dir"] == "short"]
    subsection("By direction")
    hdr()
    row("Long re-entries",  stats(b_long))
    row("Short re-entries", stats(b_short))

    # ── Strategy C ───────────────────────────────────────────────────────────
    print(f"\n  Running Strategy C (range-bound VWAP cross)...", end=" ", flush=True)
    c_trades, rb_days = run_strategy_c(bars, orb_trades)
    print(f"{len(c_trades)} trades  ({len(rb_days)} range-bound days total)")

    c_is  = [t for t in c_trades if t["year"] in IS_YEARS]
    c_oos = [t for t in c_trades if t["year"] in OOS_YEARS]

    section("STRATEGY C — Range-bound VWAP Cross")
    print(f"  Logic: No breakout by {C_RANGE_END.strftime('%H:%M')} ET → VWAP cross with slope")
    print(f"         Entry window: {C_ENTRY_START.strftime('%H:%M')}–{C_ENTRY_END.strftime('%H:%M')} ET")
    print(f"  Stop: {C_STOP}pt  Target: {C_TARGET_RR}R = {C_STOP*C_TARGET_RR:.0f}pt  1 per day")

    # Range-bound day count per year
    rb_by_year = defaultdict(int)
    for d in rb_days:
        rb_by_year[d.year] += 1

    print(f"\n  Range-bound days (valid OR, no breakout by 10:30):")
    total_rb = 0
    for yr in ALL_YEARS:
        n_rb  = rb_by_year.get(yr, 0)
        n_c   = len([t for t in c_trades if t["year"] == yr])
        months = 12 if yr < 2026 else 6
        print(f"  {yr}: {n_rb:>3} range-bound days ({n_rb/months:.0f}/month)  "
              f"→ {n_c} C-trades ({n_c/n_rb:.1%} fire rate)")
        total_rb += n_rb

    print(f"  Total range-bound days: {total_rb}")

    subsection("IS/OOS Summary")
    hdr()
    row(f"IS  {IS_YEARS}",  stats(c_is))
    row(f"OOS {OOS_YEARS}", stats(c_oos))
    row("ALL YEARS",        stats(c_trades))

    subsection("Annual Breakdown")
    annual_breakdown(c_trades)

    # Direction split
    c_long  = [t for t in c_trades if t["dir"] == "long"]
    c_short = [t for t in c_trades if t["dir"] == "short"]
    subsection("By direction")
    hdr()
    row("Long  (cross above VWAP)", stats(c_long))
    row("Short (cross below VWAP)", stats(c_short))

    # ── Combined Trade Count ──────────────────────────────────────────────────
    section("COMBINED TRADE COUNT — Monthly Frequency Analysis")
    print(f"  Goal: 15-20 trades/month")

    combos = [
        ("Baseline ORB only",              orb_trades, [],       [],       []),
        ("ORB + A (reversals)",            orb_trades, a_trades, [],       []),
        ("ORB + B (VWAP re-entries)",      orb_trades, [],       b_trades, []),
        ("ORB + C (range-bound)",          orb_trades, [],       [],       c_trades),
        ("ORB + A + B",                    orb_trades, a_trades, b_trades, []),
        ("ORB + A + C",                    orb_trades, a_trades, [],       c_trades),
        ("ORB + B + C",                    orb_trades, [],       b_trades, c_trades),
        ("ORB + A + B + C (all)",          orb_trades, a_trades, b_trades, c_trades),
    ]

    print(f"\n  {'Combo':<38}  {'Total':>6}  {'N/mo':>5}  {'Net $':>10}  {'PF':>5}")
    print(f"  {'─' * 65}")

    for label, base, sa, sb, sc in combos:
        all_t   = base + sa + sb + sc
        total_n = len(all_t)
        total_months = sum(12 if yr < 2026 else 6 for yr in ALL_YEARS
                          if any(t["year"] == yr for t in all_t))
        per_mo  = total_n / total_months if total_months else 0
        net     = sum(t["pnl"] for t in all_t)
        all_s   = stats(all_t)
        goal_flag = ""
        if 15 <= per_mo <= 20:
            goal_flag = "  ← IN GOAL RANGE"
        elif per_mo > 20:
            goal_flag = "  ← ABOVE GOAL"
        print(f"  {label:<38}  {total_n:>6}  {per_mo:>5.1f}  "
              f"${net:>+9,.0f}  {all_s['pf']:>5.3f}{goal_flag}")

    # OOS only version
    print(f"\n  OOS {OOS_YEARS} only:")
    print(f"  {'Combo':<38}  {'Total':>6}  {'N/mo':>5}  {'Net $':>10}  {'PF':>5}")
    print(f"  {'─' * 65}")

    for label, base, sa, sb, sc in combos:
        base_y = [t for t in base if t["year"] in OOS_YEARS]
        sa_y   = [t for t in sa   if t["year"] in OOS_YEARS]
        sb_y   = [t for t in sb   if t["year"] in OOS_YEARS]
        sc_y   = [t for t in sc   if t["year"] in OOS_YEARS]
        all_t  = base_y + sa_y + sb_y + sc_y
        if not all_t:
            continue
        total_n = len(all_t)
        oos_months = sum(12 if yr < 2026 else 6 for yr in OOS_YEARS)
        per_mo  = total_n / oos_months
        net     = sum(t["pnl"] for t in all_t)
        all_s   = stats(all_t)
        goal_flag = ""
        if 15 <= per_mo <= 20:
            goal_flag = "  ← IN GOAL RANGE"
        elif per_mo > 20:
            goal_flag = "  ← ABOVE GOAL"
        print(f"  {label:<38}  {total_n:>6}  {per_mo:>5.1f}  "
              f"${net:>+9,.0f}  {all_s['pf']:>5.3f}{goal_flag}")

    # ── Final Verdict ─────────────────────────────────────────────────────────
    section("FINAL VERDICT")

    orb_s   = stats(orb_oos)
    a_s     = stats(a_oos)
    b_s     = stats(b_oos)
    c_s     = stats(c_oos)
    abc_all = orb_oos + a_oos + b_oos + c_oos
    abc_s   = stats(abc_all)

    oos_months = sum(12 if yr < 2026 else 6 for yr in OOS_YEARS)

    def per_mo(t_list):
        return len(t_list) / oos_months if oos_months else 0

    print(f"\n  OOS {OOS_YEARS}  ({oos_months} months)")
    print(f"  {'Strategy':<32}  {'N':>4}  {'N/mo':>5}  {'WR':>6}  {'PF':>5}  {'Net $':>10}")
    print(f"  {'─' * 63}")
    print(f"  {'Baseline ORB':<32}  {orb_s['n']:>4}  {per_mo(orb_oos):>5.1f}  "
          f"{orb_s['wr']:.1%}  {orb_s['pf']:.3f}  ${orb_s['net']:>+9,.0f}")
    print(f"  {'A — Failed ORB Reversal':<32}  {a_s['n']:>4}  {per_mo(a_oos):>5.1f}  "
          f"{a_s['wr']:.1%}  {a_s['pf']:.3f}  ${a_s['net']:>+9,.0f}")
    print(f"  {'B — VWAP Re-entry':<32}  {b_s['n']:>4}  {per_mo(b_oos):>5.1f}  "
          f"{b_s['wr']:.1%}  {b_s['pf']:.3f}  ${b_s['net']:>+9,.0f}")
    print(f"  {'C — Range-bound VWAP Cross':<32}  {c_s['n']:>4}  {per_mo(c_oos):>5.1f}  "
          f"{c_s['wr']:.1%}  {c_s['pf']:.3f}  ${c_s['net']:>+9,.0f}")
    print(f"  {'─' * 63}")
    print(f"  {'ALL COMBINED (ORB+A+B+C)':<32}  {abc_s['n']:>4}  {per_mo(abc_all):>5.1f}  "
          f"{abc_s['wr']:.1%}  {abc_s['pf']:.3f}  ${abc_s['net']:>+9,.0f}")

    print(f"\n  Key questions answered:")
    a_wr = stats(a_oos)["wr"] if a_oos else 0
    print(f"  Q1: Is failed ORB reversal WR > 50%?  "
          f"{'YES' if a_wr > 0.5 else 'NO'} — OOS WR = {a_wr:.1%}")

    b_tgt_days_oos = [t for t in orb_oos if t["hit_target"]]
    b_reentry_pct  = len(b_oos) / len(b_tgt_days_oos) if b_tgt_days_oos else 0
    print(f"  Q2: % of target-hitting days with usable VWAP re-entry?  "
          f"{b_reentry_pct:.1%}  ({len(b_oos)}/{len(b_tgt_days_oos)} days)")

    rb_oos = {d: v for d, v in rb_days.items() if d.year in OOS_YEARS}
    print(f"  Q3: Range-bound days in OOS?  "
          f"{len(rb_oos)} days / {oos_months} months = {len(rb_oos)/oos_months:.0f}/month")

    all_per_month = per_mo(abc_all)
    print(f"\n  Combined frequency: {all_per_month:.1f} trades/month  "
          f"(goal: 15-20)")
    if all_per_month < 15:
        gap = 15 - all_per_month
        print(f"  Still {gap:.1f} trades/month short of the 15/month floor.")
    elif all_per_month > 20:
        print(f"  Exceeds 20/month ceiling — may need to filter the weakest strategy.")
    else:
        print(f"  IN GOAL RANGE.")

    print(f"\n  Strategy assessment (OOS PF ≥ 1.5 = green, < 1.0 = avoid):")
    for strat_label, s_val in [
        ("ORB baseline",              stats(orb_oos)),
        ("A — Failed reversal",       stats(a_oos)),
        ("B — VWAP re-entry",         stats(b_oos)),
        ("C — Range-bound VWAP",      stats(c_oos)),
    ]:
        pf = s_val["pf"]
        flag = ("GREEN (implement)"      if pf >= 1.5 and s_val["n"] >= 10 else
                "YELLOW (promising, N low)" if pf >= 1.5 and s_val["n"] < 10  else
                "YELLOW (marginal)"        if 1.0 <= pf < 1.5               else
                "RED (avoid)")
        print(f"  {strat_label:<32}  PF={pf:.3f}  N={s_val['n']:>3}  → {flag}")

    print(f"\n{'=' * W}\n")


if __name__ == "__main__":
    main()
