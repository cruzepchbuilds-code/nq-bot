"""
brain/research/ph_vwap_optimizer.py

Grid-search optimizer for two new NQ strategies:
  1. Power Hour (PH)   — 2:00–3:30 PM ET, morning-trend continuation
  2. VWAP Reclaim (VR) — 10:00 AM–12:30 PM ET, fade overextension back to VWAP

Data: NQ 2024-2026 + ES 2022-2026

Key fix (v2): PH trend direction determined at 2PM close, not at entry bar.

Usage:
    cd /Users/Cruz/Desktop/nq_bot_final-main
    python3 brain/research/ph_vwap_optimizer.py
"""

import csv
import os
from datetime import datetime, time, date
from collections import defaultdict
from itertools import product

# ── Constants ────────────────────────────────────────────────────────────────
NQ_POINT   = 20.0
ES_POINT   = 50.0
COMMISSION = 4.50   # round-trip ($2.25/side)
SLIP_NQ    = 5.00   # 1 tick * $5 each side
SLIP_ES    = 6.25   # 1 tick * $12.50 each side
WEAK_NQ    = {6, 9, 12}
WEAK_ES    = {1, 5, 6, 7, 8, 10}

# ── Load & bucket data by trading day ────────────────────────────────────────

def load_bars(path):
    bars_by_day = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            ts_str = row["timestamp"][:19]
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            # Only regular + power-hour session (9:00–16:00 ET)
            if ts.hour < 9 or ts.hour >= 16:
                continue
            bars_by_day[ts.date()].append({
                "ts":    ts,
                "t":     ts.time(),
                "o":     float(row["open"]),
                "h":     float(row["high"]),
                "l":     float(row["low"]),
                "c":     float(row["close"]),
                "v":     float(row["volume"]),
                "dow":   ts.weekday(),
                "month": ts.month,
            })
    return bars_by_day


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 1: POWER HOUR (v2 — trend fixed at 2PM close)
# ─────────────────────────────────────────────────────────────────────────────

def run_ph_day(bars, stop_pt, rr, last_entry_t, pt_val, slip):
    """
    FIXED: trend direction locked at close of 2PM reference bar.
    Entry: next bar after 2PM that closes beyond 2PM H/L in trend direction.
    """
    T_930  = time(9, 30)
    T_1400 = time(14,  0)
    T_1555 = time(15, 55)

    open_930  = None
    ph_hi = ph_lo = ph_trend = None
    ph_set = False
    traded = in_pos = False
    pos_long = None
    entry_px = sl = tp = None
    trades = []

    COST = COMMISSION + slip * 2

    for b in bars:
        t = b["t"]
        if t >= T_1555:
            break

        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]

        if t < T_1400:
            continue

        # Capture 2:00 PM reference candle and LOCK trend here
        if not ph_set and t >= T_1400 and t < time(14, 1):
            ph_hi   = b["h"]
            ph_lo   = b["l"]
            # Trend direction = 2PM close vs 9:30 open (not entry bar)
            ph_trend = "bull" if (open_930 and b["c"] > open_930) else "bear"
            ph_set  = True
            continue

        if not ph_set:
            continue

        close = b["c"]

        # Manage open position (runs even after traded=True)
        if in_pos:
            if pos_long:
                if   b["l"] <= sl:  pnl_pts = sl - entry_px
                elif b["h"] >= tp:  pnl_pts = tp - entry_px
                else: continue
            else:
                if   b["h"] >= sl:  pnl_pts = entry_px - sl
                elif b["l"] <= tp:  pnl_pts = entry_px - tp
                else: continue
            trades.append({"pnl_pts": pnl_pts, "pnl_usd": pnl_pts * pt_val - COST})
            in_pos = False
            continue

        # Entry (only if haven't traded yet)
        if traded:
            continue
        if open_930 is None or ph_hi is None:
            continue
        if t > last_entry_t:
            continue

        # Entry: price breaks 2PM H/L in direction of locked morning trend
        if ph_trend == "bull" and close > ph_hi:
            entry_px = close
            sl, tp   = close - stop_pt, close + stop_pt * rr
            pos_long = True;  in_pos = traded = True
        elif ph_trend == "bear" and close < ph_lo:
            entry_px = close
            sl, tp   = close + stop_pt, close - stop_pt * rr
            pos_long = False; in_pos = traded = True

    if in_pos and entry_px is not None:
        last_c   = bars[-1]["c"]
        pnl_pts  = (last_c - entry_px) if pos_long else (entry_px - last_c)
        trades.append({"pnl_pts": pnl_pts, "pnl_usd": pnl_pts * pt_val - COST})

    return trades


def optimize_ph(bars_by_day, pt_val, slip, weak_months, label):
    print(f"\n{'='*70}")
    print(f"POWER HOUR OPTIMIZER  [{label}]  (2:00–3:30 PM ET)")
    print(f"{'='*70}")

    stop_range = [12, 15, 20, 25, 30]
    rr_range   = [1.5, 2.0, 2.5, 3.0]
    last_entry = [time(15, 0), time(15, 15), time(15, 30)]

    results = []
    days = sorted(bars_by_day.keys())

    for stop_pt, rr, last_t in product(stop_range, rr_range, last_entry):
        all_trades = []
        for d in days:
            bars = bars_by_day[d]
            if not bars: continue
            if bars[0]["month"] in weak_months or bars[0]["dow"] == 0: continue
            all_trades.extend(run_ph_day(bars, stop_pt, rr, last_t, pt_val, slip))

        if not all_trades: continue
        wins    = [t for t in all_trades if t["pnl_usd"] > 0]
        gross_w = sum(t["pnl_usd"] for t in wins)
        gross_l = abs(sum(t["pnl_usd"] for t in all_trades if t["pnl_usd"] < 0))
        pf  = gross_w / gross_l if gross_l > 0 else 0
        wr  = len(wins) / len(all_trades)
        net = sum(t["pnl_usd"] for t in all_trades)
        results.append({"stop": stop_pt, "rr": rr, "last_t": last_t,
                         "trades": len(all_trades), "wr": wr, "pf": pf, "net": net})

    valid = sorted([r for r in results if r["trades"] >= 30 and r["pf"] > 1.0],
                   key=lambda x: x["pf"], reverse=True)

    # Show all results (helps diagnose if nothing qualifies)
    show = [r for r in results if r["trades"] >= 30]
    show.sort(key=lambda x: x["pf"], reverse=True)
    print(f"\nAll configs ≥30 trades, sorted by PF:\n")
    print(f"{'Stop':>6} {'RR':>5} {'Last':>7} {'Trades':>7} {'WR':>6} {'PF':>6} {'Net $':>10}")
    print("-" * 55)
    for r in show[:15]:
        flag = " ✓" if r["pf"] > 1.0 else ""
        print(f"{r['stop']:>6.0f} {r['rr']:>5.1f} {r['last_t'].strftime('%H:%M'):>7}"
              f" {r['trades']:>7} {r['wr']:>6.1%} {r['pf']:>6.2f} {r['net']:>10,.0f}{flag}")

    if valid:
        best = valid[0]
        print(f"\n✓ BEST: stop={best['stop']}pt, RR={best['rr']}, "
              f"last={best['last_t'].strftime('%H:%M')} | "
              f"{best['trades']} trades | WR {best['wr']:.1%} | PF {best['pf']:.2f} | ${best['net']:,.0f}")
        return best
    else:
        # Count total PH signals to diagnose
        sig_count = 0
        for d in days:
            bars = bars_by_day[d]
            if not bars or bars[0]["month"] in weak_months or bars[0]["dow"] == 0: continue
            sig_count += len(run_ph_day(bars, 20, 2.0, time(15, 30), pt_val, slip))
        print(f"\n✗ No qualifying config. Total PH signals fired (20pt/2R/15:30): {sig_count}")
        return None


def walk_forward_ph(bars_by_day, best, pt_val, slip, weak_months, label, split_year=2025):
    if not best: return
    print(f"\n--- PH Walk-Forward [{label}]  IS: pre-{split_year} / OOS: {split_year}+ ---")
    is_cut = date(split_year, 1, 1)
    for lbl, filt in [(f"IS  pre-{split_year}", lambda d: d < is_cut),
                       (f"OOS {split_year}+",    lambda d: d >= is_cut)]:
        days = [d for d in sorted(bars_by_day.keys()) if filt(d)]
        tlist = []
        for d in days:
            bars = bars_by_day[d]
            if not bars or bars[0]["month"] in weak_months or bars[0]["dow"] == 0: continue
            tlist.extend(run_ph_day(bars, best["stop"], best["rr"], best["last_t"], pt_val, slip))
        if not tlist:
            print(f"  {lbl}: no trades")
            continue
        wins = [t for t in tlist if t["pnl_usd"] > 0]
        gw = sum(t["pnl_usd"] for t in wins)
        gl = abs(sum(t["pnl_usd"] for t in tlist if t["pnl_usd"] < 0))
        pf = gw/gl if gl > 0 else 0
        print(f"  {lbl}: {len(tlist):3d} trades | WR {len(wins)/len(tlist):.1%} "
              f"| PF {pf:.2f} | Net ${sum(t['pnl_usd'] for t in tlist):,.0f}")


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 2: VWAP RECLAIM (with trend-aligned filter)
# ─────────────────────────────────────────────────────────────────────────────

def run_vwap_day(bars, stop_pt, rr, min_extend, window_end_t, max_trades, trend_aligned,
                 pt_val, slip):
    """
    trend_aligned=True: only trade reclaims in direction of morning trend.
    Morning trend = 10:30 AM close vs 9:30 AM open.
    """
    T_930       = time(9,  30)
    T_1030      = time(10, 30)
    T_ENTRY_MIN = time(10,  0)
    T_1555      = time(15, 55)

    sum_pv = sum_vol = 0.0
    vwap = None
    open_930 = am_trend = None

    was_extended = False
    prev_above   = None
    trades_today = 0
    in_pos       = False
    pos_long     = None
    entry_px = sl = tp = None
    trades = []
    COST = COMMISSION + slip * 2

    for b in bars:
        t = b["t"]
        if t >= T_1555: break

        if t >= T_930 and t < time(9, 31) and open_930 is None:
            open_930 = b["o"]

        # Update VWAP from 9:30
        if t >= T_930:
            tp_price = (b["h"] + b["l"] + b["c"]) / 3.0
            sum_pv  += tp_price * b["v"]
            sum_vol += b["v"]
            if sum_vol > 0:
                vwap = sum_pv / sum_vol

        # Lock AM trend at 10:30
        if am_trend is None and t >= T_1030:
            if open_930 and vwap:
                am_trend = "bull" if b["c"] > open_930 else "bear"

        if vwap is None or t < T_ENTRY_MIN:
            prev_above = (b["c"] > vwap) if vwap else None
            continue

        close = b["c"]

        # Manage open position (runs past window end)
        if in_pos:
            if pos_long:
                if   b["l"] <= sl:  pnl_pts = sl - entry_px
                elif b["h"] >= tp:  pnl_pts = tp - entry_px
                else:
                    if t >= window_end_t: pnl_pts = close - entry_px
                    else:
                        prev_above = close > vwap; continue
                trades.append({"pnl_pts": pnl_pts, "pnl_usd": pnl_pts * pt_val - COST})
                in_pos = False
            else:
                if   b["h"] >= sl:  pnl_pts = entry_px - sl
                elif b["l"] <= tp:  pnl_pts = entry_px - tp
                else:
                    if t >= window_end_t: pnl_pts = entry_px - close
                    else:
                        prev_above = close > vwap; continue
                trades.append({"pnl_pts": pnl_pts, "pnl_usd": pnl_pts * pt_val - COST})
                in_pos = False
            prev_above = close > vwap
            continue

        if trades_today >= max_trades or t >= window_end_t:
            prev_above = close > vwap
            continue

        # Track extension
        if not was_extended and abs(close - vwap) > min_extend:
            was_extended = True

        curr_above = close > vwap
        if was_extended and prev_above is not None:
            crossed_up   = (not prev_above) and curr_above
            crossed_down = prev_above and (not curr_above)

            go_long  = crossed_up   and (not trend_aligned or am_trend == "bull")
            go_short = crossed_down and (not trend_aligned or am_trend == "bear")

            if go_long:
                entry_px = close
                sl, tp   = close - stop_pt, close + stop_pt * rr
                pos_long = True;  in_pos = True; trades_today += 1; was_extended = False
            elif go_short:
                entry_px = close
                sl, tp   = close + stop_pt, close - stop_pt * rr
                pos_long = False; in_pos = True; trades_today += 1; was_extended = False

        prev_above = curr_above

    if in_pos and entry_px is not None:
        last_c  = bars[-1]["c"]
        pnl_pts = (last_c - entry_px) if pos_long else (entry_px - last_c)
        trades.append({"pnl_pts": pnl_pts, "pnl_usd": pnl_pts * pt_val - COST})

    return trades


def optimize_vwap(bars_by_day, pt_val, slip, weak_months, label):
    print(f"\n{'='*70}")
    print(f"VWAP RECLAIM OPTIMIZER  [{label}]  (10:00 AM–window_end)")
    print(f"{'='*70}")

    stop_range    = [8, 10, 12, 15, 20]
    rr_range      = [1.5, 2.0, 2.5]
    extend_range  = [10, 15, 20, 25]
    window_ends   = [time(12, 0), time(12, 30), time(13, 0)]
    max_t_range   = [1, 2]
    trend_opts    = [False, True]

    results = []
    days = sorted(bars_by_day.keys())

    for stop_pt, rr, min_ext, win_end, max_t, talign in product(
            stop_range, rr_range, extend_range, window_ends, max_t_range, trend_opts):

        all_trades = []
        for d in days:
            bars = bars_by_day[d]
            if not bars or bars[0]["month"] in weak_months: continue
            all_trades.extend(
                run_vwap_day(bars, stop_pt, rr, min_ext, win_end, max_t, talign, pt_val, slip))

        if not all_trades: continue
        wins    = [t for t in all_trades if t["pnl_usd"] > 0]
        gross_w = sum(t["pnl_usd"] for t in wins)
        gross_l = abs(sum(t["pnl_usd"] for t in all_trades if t["pnl_usd"] < 0))
        pf  = gross_w / gross_l if gross_l > 0 else 0
        wr  = len(wins) / len(all_trades)
        net = sum(t["pnl_usd"] for t in all_trades)
        results.append({
            "stop": stop_pt, "rr": rr, "min_ext": min_ext,
            "win_end": win_end, "max_t": max_t, "talign": talign,
            "trades": len(all_trades), "wr": wr, "pf": pf, "net": net
        })

    valid = sorted([r for r in results if r["trades"] >= 30 and r["pf"] > 1.1],
                   key=lambda x: x["pf"], reverse=True)

    print(f"\nTop 10 (min 30 trades, PF > 1.1):\n")
    print(f"{'Stop':>6} {'RR':>5} {'Ext':>5} {'End':>7} {'MaxT':>5} {'Algn':>5}"
          f" {'Trades':>7} {'WR':>6} {'PF':>6} {'Net $':>10}")
    print("-" * 70)
    for r in valid[:10]:
        print(f"{r['stop']:>6.0f} {r['rr']:>5.1f} {r['min_ext']:>5.0f}"
              f" {r['win_end'].strftime('%H:%M'):>7} {r['max_t']:>5}"
              f" {'Y' if r['talign'] else 'N':>5}"
              f" {r['trades']:>7} {r['wr']:>6.1%} {r['pf']:>6.2f} {r['net']:>10,.0f}")

    if valid:
        best = valid[0]
        print(f"\n✓ BEST: stop={best['stop']}pt, RR={best['rr']}, extend={best['min_ext']}pt, "
              f"end={best['win_end'].strftime('%H:%M')}, max_t={best['max_t']}, "
              f"trend_aligned={best['talign']}")
        print(f"  {best['trades']} trades | WR {best['wr']:.1%} | PF {best['pf']:.2f} "
              f"| Net ${best['net']:,.0f}")
        return best
    else:
        print(f"\n✗ No VWAP config met threshold (PF > 1.1, ≥30 trades)")
        return None


def walk_forward_vwap(bars_by_day, best, pt_val, slip, weak_months, label, split_year=2025):
    if not best: return
    print(f"\n--- VWAP Walk-Forward [{label}]  IS: pre-{split_year} / OOS: {split_year}+ ---")
    is_cut = date(split_year, 1, 1)
    for lbl, filt in [(f"IS  pre-{split_year}", lambda d: d < is_cut),
                       (f"OOS {split_year}+",    lambda d: d >= is_cut)]:
        days = [d for d in sorted(bars_by_day.keys()) if filt(d)]
        tlist = []
        for d in days:
            bars = bars_by_day[d]
            if not bars or bars[0]["month"] in weak_months: continue
            tlist.extend(run_vwap_day(bars, best["stop"], best["rr"], best["min_ext"],
                                      best["win_end"], best["max_t"], best["talign"],
                                      pt_val, slip))
        if not tlist:
            print(f"  {lbl}: no trades")
            continue
        wins = [t for t in tlist if t["pnl_usd"] > 0]
        gw = sum(t["pnl_usd"] for t in wins)
        gl = abs(sum(t["pnl_usd"] for t in tlist if t["pnl_usd"] < 0))
        pf = gw/gl if gl > 0 else 0
        print(f"  {lbl}: {len(tlist):3d} trades | WR {len(wins)/len(tlist):.1%} "
              f"| PF {pf:.2f} | Net ${sum(t['pnl_usd'] for t in tlist):,.0f}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    # ── NQ data ──────────────────────────────────────────────────────────────
    nq_path = os.path.join(BASE, "data", "nq_1min.csv")
    print(f"Loading NQ data...")
    nq_bars = load_bars(nq_path)
    print(f"  {sum(len(v) for v in nq_bars.values()):,} bars | {len(nq_bars)} trading days")

    # ── ES data ──────────────────────────────────────────────────────────────
    es_path = os.path.join(BASE, "data", "es_1min.csv")
    print(f"Loading ES data...")
    es_bars = load_bars(es_path)
    print(f"  {sum(len(v) for v in es_bars.values()):,} bars | {len(es_bars)} trading days")

    # ── Power Hour: NQ ───────────────────────────────────────────────────────
    best_ph_nq = optimize_ph(nq_bars, NQ_POINT, SLIP_NQ, WEAK_NQ, "NQ")
    walk_forward_ph(nq_bars, best_ph_nq, NQ_POINT, SLIP_NQ, WEAK_NQ, "NQ")

    # ── Power Hour: ES ───────────────────────────────────────────────────────
    best_ph_es = optimize_ph(es_bars, ES_POINT, SLIP_ES, WEAK_ES, "ES")
    walk_forward_ph(es_bars, best_ph_es, ES_POINT, SLIP_ES, WEAK_ES, "ES", split_year=2024)

    # ── VWAP Reclaim: NQ ─────────────────────────────────────────────────────
    best_vwap_nq = optimize_vwap(nq_bars, NQ_POINT, SLIP_NQ, WEAK_NQ, "NQ")
    walk_forward_vwap(nq_bars, best_vwap_nq, NQ_POINT, SLIP_NQ, WEAK_NQ, "NQ")

    # ── VWAP Reclaim: ES ─────────────────────────────────────────────────────
    best_vwap_es = optimize_vwap(es_bars, ES_POINT, SLIP_ES, WEAK_ES, "ES")
    walk_forward_vwap(es_bars, best_vwap_es, ES_POINT, SLIP_ES, WEAK_ES, "ES", split_year=2024)

    print("\n" + "="*70)
    print("FINAL SUMMARY")
    print("="*70)
    for sym, b_ph, b_vw in [("NQ", best_ph_nq, best_vwap_nq), ("ES", best_ph_es, best_vwap_es)]:
        if b_ph:
            print(f"  PH  [{sym}]: stop={b_ph['stop']}pt, RR={b_ph['rr']}, "
                  f"last={b_ph['last_t'].strftime('%H:%M')} → PF {b_ph['pf']:.2f}")
        else:
            print(f"  PH  [{sym}]: ✗ no edge found")
        if b_vw:
            print(f"  VWAP[{sym}]: stop={b_vw['stop']}pt, RR={b_vw['rr']}, "
                  f"extend={b_vw['min_ext']}pt, end={b_vw['win_end'].strftime('%H:%M')} "
                  f"→ PF {b_vw['pf']:.2f}")
        else:
            print(f"  VWAP[{sym}]: ✗ no edge found")
