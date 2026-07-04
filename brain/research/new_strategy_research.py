"""
brain/research/new_strategy_research.py

High R:R strategy discovery + comparison vs v10 baselines.
Goal: find strategies that pass the Lucid 50K eval faster by
risking less per trade and needing fewer wins.

Strategies tested
─────────────────
BASELINES (v10 config)
  BL_EVAL    : ORB eval mode  — 1c, 2R, 27pt stop
  BL_FUNDED  : ORB funded     — 2c, 3R, 27pt stop

NEW — 5-min Micro Opening Range (9:30-9:34)
  MICRO5_A   : 10pt stop, 5R target  (breakeven WR 16.7%)
  MICRO5_B   :  8pt stop, 4R target  (breakeven WR 20.0%)
  MICRO5_C   : 12pt stop, 4R target  (breakeven WR 20.0%, more room)
  MICRO5_D   : 10pt stop, 5R target — NO weak-month / Monday filters

NEW — 1-min Opening Bar (9:30 bar only)
  MICRO1_A   :  8pt stop, 4R target
  MICRO1_B   : 10pt stop, 5R target

NEW — ORB signal + modified exits
  ORB_TIGHT  : existing ORB signal, 10pt stop, 5R target
  ORB_5R     : existing ORB signal, 22pt stop, 5R target

NEW — Asia eval-safe (1c during eval)
  ASIA_EVAL  : Asia gap continuation at 1c (skip EVAL block)

Usage:
    cd /Users/Cruz/Desktop/nq_bot_final-main
    python3 brain/research/new_strategy_research.py
"""

import sys
import os
import csv
from collections import defaultdict
from datetime import datetime, date, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
import strategies.strategy_us as su
from backtest import Backtester, load_csv

DATA       = "data/nq_full.csv"
OOS_YEARS  = range(2023, 2027)
POINT_VAL  = 20.0
SLIP       = 2 * 0.25       # 2-tick slippage in NQ points
COMM_RT    = 10.0           # round-turn commission per contract ($5 × 2 sides)


# ═══════════════════════════════════════════════════════════════════════════════
# Data helpers
# ═══════════════════════════════════════════════════════════════════════════════

def bars_by_date(bars):
    d = defaultdict(list)
    for b in bars:
        d[b["timestamp"].date()].append(b)
    return dict(sorted(d.items()))


def oos_bars(all_bars, year):
    ystart = date(year, 1, 1)
    yend   = date(year + 1, 1, 1)
    return [b for b in all_bars if ystart <= b["timestamp"].date() < yend]


# ═══════════════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════════════

def calc(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "ev": 0.0, "wk": 0.0}
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    net    = gw - gl
    pf     = gw / gl if gl else float("inf")
    wr     = len(wins) / len(trades)
    ev     = net / len(trades)
    n_wks  = len(OOS_YEARS) * 52
    wk     = net / n_wks
    # per-year breakdown
    def _year(t):
        d = t.get("date")
        if d is None:
            return 0
        if isinstance(d, str):
            return int(d[:4])
        if hasattr(d, "year"):
            return d.year
        return 0

    by_yr  = {}
    for yr in OOS_YEARS:
        yt = [t for t in trades if _year(t) == yr]
        by_yr[yr] = round(sum(t["pnl"] for t in yt), 0)
    return {"n": len(trades), "net": round(net, 0), "wr": wr,
            "pf": round(pf, 3), "ev": round(ev, 0),
            "wk": round(wk, 0), "by_yr": by_yr}


# ═══════════════════════════════════════════════════════════════════════════════
# Baseline: existing ORB via Backtester (config patching)
# ═══════════════════════════════════════════════════════════════════════════════

def run_year_bt(all_bars, year, patch_fn=None, unpatch_fn=None):
    """Run one OOS year with optional config patch, return trade_log."""
    ystart = date(year, 1, 1)
    yend   = date(year + 1, 1, 1)
    prior  = [b for b in all_bars if b["timestamp"].date() < ystart]
    subset = [b for b in all_bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return []
    warmup = Backtester()
    warmup.run(prior, silent=True)
    bt = Backtester()
    bt._last_close         = warmup._last_close
    bt.regime.daily_ranges = list(warmup.regime.daily_ranges)
    bt.or_volume_history   = list(warmup.or_volume_history)
    bt.prev_day_mode       = warmup.prev_day_mode
    if patch_fn:
        patch_fn()
    bt.run(subset, silent=True)
    if unpatch_fn:
        unpatch_fn()
    trades = bt.bank.trade_log
    for t in trades:
        if "date" not in t:
            t["date"] = bt.bank.s.current_day
    return trades


def run_bt_baseline(all_bars, eval_mode, max_c, rr_target, stop_pt=22.0):
    """Run all OOS years with given config, return all trades."""
    def patch():
        config.EVAL_MODE                = eval_mode
        config.MAX_CONTRACTS            = max_c
        config.ORB_BREAKOUT_RR_TARGET   = rr_target
        config.ORB_FIXED_STOP_POINTS    = stop_pt
    def unpatch():
        config.EVAL_MODE                = True
        config.MAX_CONTRACTS            = 2
        config.ORB_BREAKOUT_RR_TARGET   = 2.0
        config.ORB_FIXED_STOP_POINTS    = 22.0
    trades = []
    for yr in OOS_YEARS:
        trades.extend(run_year_bt(all_bars, yr, patch, unpatch))
    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# Trade simulator (for custom strategies)
# ═══════════════════════════════════════════════════════════════════════════════

def sim_trade(day_bars, entry_time, direction, entry_price,
              stop_price, target_price, contracts=1,
              flatten=time(15, 55)):
    """
    Bar-by-bar simulation.  Returns {"pnl", "pts", "result", "date"} or None.
    Checks stop first (conservative), then target.
    """
    for bar in day_bars:
        t = bar["timestamp"].time()
        if t <= entry_time:
            continue

        if t >= flatten:
            ep = bar["open"]
            pts = (ep - entry_price) if direction == "long" else (entry_price - ep)
            pnl = pts * POINT_VAL * contracts - COMM_RT * contracts
            return {"pnl": pnl, "pts": pts, "result": "flat"}

        if direction == "long":
            if bar["low"] <= stop_price:
                pts = stop_price - entry_price
                pnl = pts * POINT_VAL * contracts - COMM_RT * contracts
                return {"pnl": pnl, "pts": pts, "result": "loss"}
            if bar["high"] >= target_price:
                pts = target_price - entry_price
                pnl = pts * POINT_VAL * contracts - COMM_RT * contracts
                return {"pnl": pnl, "pts": pts, "result": "win"}
        else:  # short — pts is negative for loss, positive for win
            if bar["high"] >= stop_price:
                pts = entry_price - stop_price   # e.g. -8 (stop above entry)
                pnl = pts * POINT_VAL * contracts - COMM_RT * contracts   # -170
                return {"pnl": pnl, "pts": pts, "result": "loss"}
            if bar["low"] <= target_price:
                pts = entry_price - target_price   # e.g. +32 (target below entry)
                pnl = pts * POINT_VAL * contracts - COMM_RT * contracts   # +630
                return {"pnl": pnl, "pts": pts, "result": "win"}
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# New strategy: 5-min Micro-OR (9:30–9:34)
# ═══════════════════════════════════════════════════════════════════════════════

def run_micro5(all_bars, stop_pt=10.0, rr=5.0,
               min_range=12.0, max_range=55.0,
               buffer=1.0, contracts=1,
               skip_mondays=True, week_filter=True):
    """
    Build 9:30-9:34 mini-range. Enter on 9:35 bar close breakout.
    Fixed stop, fixed R:R target.
    """
    SKIP_MON  = skip_mondays
    WEAK_MON  = [6, 9, 12] if week_filter else []
    byd = bars_by_date(all_bars)
    trades = []

    for d, bars in byd.items():
        if d.year not in OOS_YEARS:
            continue
        if SKIP_MON and d.weekday() == 0:
            continue
        if d.month in WEAK_MON:
            continue

        micro = [b for b in bars if time(9, 30) <= b["timestamp"].time() <= time(9, 34)]
        if len(micro) < 5:
            continue
        rhi = max(b["high"] for b in micro)
        rlo = min(b["low"]  for b in micro)
        if (rhi - rlo) < min_range or (rhi - rlo) > max_range:
            continue

        bar35 = next((b for b in bars if b["timestamp"].time() == time(9, 35)), None)
        if bar35 is None:
            continue

        if bar35["close"] > rhi + buffer:
            entry = bar35["close"] + SLIP
            stop  = entry - stop_pt
            tgt   = entry + stop_pt * rr
            dirn  = "long"
        elif bar35["close"] < rlo - buffer:
            entry = bar35["close"] - SLIP
            stop  = entry + stop_pt
            tgt   = entry - stop_pt * rr
            dirn  = "short"
        else:
            continue

        res = sim_trade(bars, time(9, 35), dirn, entry, stop, tgt, contracts)
        if res:
            res["date"] = d
            trades.append(res)

    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# New strategy: 1-min Opening Bar (just the 9:30 bar)
# ═══════════════════════════════════════════════════════════════════════════════

def run_micro1(all_bars, stop_pt=8.0, rr=4.0,
               min_range=8.0, max_range=40.0,
               buffer=0.5, contracts=1):
    byd = bars_by_date(all_bars)
    trades = []

    for d, bars in byd.items():
        if d.year not in OOS_YEARS:
            continue
        if d.weekday() == 0:
            continue
        if d.month in [6, 9, 12]:
            continue

        b930 = next((b for b in bars if b["timestamp"].time() == time(9, 30)), None)
        b931 = next((b for b in bars if b["timestamp"].time() == time(9, 31)), None)
        if b930 is None or b931 is None:
            continue

        rng = b930["high"] - b930["low"]
        if rng < min_range or rng > max_range:
            continue

        if b931["close"] > b930["high"] + buffer:
            entry = b931["close"] + SLIP
            stop  = entry - stop_pt
            tgt   = entry + stop_pt * rr
            dirn  = "long"
        elif b931["close"] < b930["low"] - buffer:
            entry = b931["close"] - SLIP
            stop  = entry + stop_pt
            tgt   = entry - stop_pt * rr
            dirn  = "short"
        else:
            continue

        res = sim_trade(bars, time(9, 31), dirn, entry, stop, tgt, contracts)
        if res:
            res["date"] = d
            trades.append(res)

    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# New strategy: Micro-5min + ORB direction confirmation
# (5-min tight stop, but only trade in the direction the 15-min OR breaks)
# ═══════════════════════════════════════════════════════════════════════════════

def run_micro5_orb_confirmed(all_bars, stop_pt=10.0, rr=5.0,
                              min_range=12.0, max_range=55.0,
                              buffer=1.0, contracts=1):
    """
    5-min micro-OR entry with 15-min OR direction filter.
    Trade only if 5-min breakout agrees with the 15-min OR breakout direction.
    Tight stop (10pt), large target (5R).
    """
    byd = bars_by_date(all_bars)
    trades = []

    for d, bars in byd.items():
        if d.year not in OOS_YEARS:
            continue
        if d.weekday() == 0:
            continue
        if d.month in [6, 9, 12]:
            continue

        # 5-min range
        micro = [b for b in bars if time(9, 30) <= b["timestamp"].time() <= time(9, 34)]
        if len(micro) < 5:
            continue
        rhi = max(b["high"] for b in micro)
        rlo = min(b["low"]  for b in micro)
        if (rhi - rlo) < min_range or (rhi - rlo) > max_range:
            continue

        bar35 = next((b for b in bars if b["timestamp"].time() == time(9, 35)), None)
        if bar35 is None:
            continue

        if bar35["close"] > rhi + buffer:
            micro_dir = "long"
        elif bar35["close"] < rlo - buffer:
            micro_dir = "short"
        else:
            continue

        # 15-min OR range (9:30-9:44)
        or_bars = [b for b in bars if time(9, 30) <= b["timestamp"].time() <= time(9, 44)]
        if len(or_bars) < 15:
            continue
        or_hi = max(b["high"] for b in or_bars)
        or_lo = min(b["low"]  for b in or_bars)
        or_sz = or_hi - or_lo
        if or_sz < 55 or or_sz > 110:      # same ORB range filter
            continue

        # 9:45 bar for OR breakout direction
        bar945 = next((b for b in bars if b["timestamp"].time() == time(9, 45)), None)
        if bar945 is None:
            continue

        if bar945["close"] > or_hi + 4.0:
            or_dir = "long"
        elif bar945["close"] < or_lo - 4.0:
            or_dir = "short"
        else:
            continue

        # Only trade if 5-min micro and 15-min OR agree
        if micro_dir != or_dir:
            continue

        if micro_dir == "long":
            entry = bar35["close"] + SLIP
            stop  = entry - stop_pt
            tgt   = entry + stop_pt * rr
        else:
            entry = bar35["close"] - SLIP
            stop  = entry + stop_pt
            tgt   = entry - stop_pt * rr

        res = sim_trade(bars, time(9, 35), micro_dir, entry, stop, tgt, contracts)
        if res:
            res["date"] = d
            trades.append(res)

    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# Asia 1c eval-safe
# ═══════════════════════════════════════════════════════════════════════════════

def run_asia_eval(all_bars, contracts=1):
    """
    Asia gap continuation at 1c — skips the EVAL_MODE block.
    Use existing AsiaStrategy logic: halt gap 30-80pt, skip Thu, skip Aug/Nov.
    Returns synthetic trade list from OOS bars.
    """
    from strategies.strategy_asia import AsiaStrategy

    byd = bars_by_date(all_bars)
    trades = []

    for d, bars in byd.items():
        if d.year not in OOS_YEARS:
            continue
        if d.weekday() == 3:       # Thursday skip
            continue
        if d.month in [8, 11]:    # Asia weak months
            continue

        asia = AsiaStrategy()
        prev_close = None
        # Get previous day close approx from last bar before 17:00 on day d
        prev_bars = [b for b in all_bars
                     if b["timestamp"].date() == d
                     and b["timestamp"].time() < time(17, 0)]
        if prev_bars:
            prev_close = prev_bars[-1]["close"]
        else:
            continue

        # 6:00 PM bar — classify gap
        bar_1800 = next((b for b in bars
                         if b["timestamp"].time() == time(18, 0)), None)
        if bar_1800 is None:
            continue

        gap = bar_1800["open"] - prev_close
        gap_abs = abs(gap)
        if gap_abs < config.ASIA_GAP_MIN_POINTS or gap_abs > config.ASIA_GAP_MAX_POINTS:
            continue

        direction = "long" if gap > 0 else "short"

        # Entry at 6:15 PM bar close
        bar_1815 = next((b for b in bars
                         if b["timestamp"].time() == time(18, 15)), None)
        if bar_1815 is None:
            continue

        if direction == "long":
            entry = bar_1815["close"] + SLIP
            stop  = entry - config.ASIA_STOP_POINTS
            tgt   = entry + config.ASIA_STOP_POINTS * config.ASIA_RR_TARGET
        else:
            entry = bar_1815["close"] - SLIP
            stop  = entry + config.ASIA_STOP_POINTS
            tgt   = entry - config.ASIA_STOP_POINTS * config.ASIA_RR_TARGET

        res = sim_trade(bars, time(18, 15), direction, entry, stop, tgt,
                        contracts=contracts, flatten=time(21, 0))
        if res:
            res["date"] = d
            trades.append(res)

    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\nLoading {DATA} ...", end=" ", flush=True)
    bars = load_csv(DATA)
    print(f"{len(bars):,} bars\n")

    W   = 112
    sep = "═" * W

    print(sep)
    print("  HIGH R:R STRATEGY RESEARCH — NQ 2023-2026 OOS")
    print("  All strategies: 1 contract, fresh per-year P&L, skip Mon + weak months unless noted")
    print(sep)

    results = {}

    # ── Baselines ─────────────────────────────────────────────────────────────
    print("\n  [1/10] BL_EVAL    : ORB eval (1c, 2R, 27pt stop) ...", end=" ", flush=True)
    t = run_bt_baseline(bars, eval_mode=True,  max_c=1, rr_target=2.0, stop_pt=22.0)
    results["BL_EVAL"] = calc(t)
    print(f"{results['BL_EVAL']['n']} trades")

    print("  [2/10] BL_FUNDED  : ORB funded (2c, 3R, 27pt stop) ...", end=" ", flush=True)
    t = run_bt_baseline(bars, eval_mode=False, max_c=2, rr_target=3.0, stop_pt=22.0)
    results["BL_FUNDED"] = calc(t)
    print(f"{results['BL_FUNDED']['n']} trades")

    # ── ORB modified exits ─────────────────────────────────────────────────────
    print("  [3/10] ORB_TIGHT  : ORB signal, 10pt stop, 5R target ...", end=" ", flush=True)
    t = run_bt_baseline(bars, eval_mode=True,  max_c=1, rr_target=5.0, stop_pt=10.0)
    results["ORB_TIGHT"] = calc(t)
    print(f"{results['ORB_TIGHT']['n']} trades")

    print("  [4/10] ORB_5R     : ORB signal, 22pt stop, 5R target ...", end=" ", flush=True)
    t = run_bt_baseline(bars, eval_mode=True,  max_c=1, rr_target=5.0, stop_pt=22.0)
    results["ORB_5R"] = calc(t)
    print(f"{results['ORB_5R']['n']} trades")

    # ── 5-min Micro-OR ────────────────────────────────────────────────────────
    print("  [5/10] MICRO5_A   : 5-min OR, 10pt stop, 5R target ...", end=" ", flush=True)
    t = run_micro5(bars, stop_pt=10.0, rr=5.0)
    results["MICRO5_A"] = calc(t)
    print(f"{results['MICRO5_A']['n']} trades")

    print("  [6/10] MICRO5_B   : 5-min OR, 8pt stop, 4R target ...", end=" ", flush=True)
    t = run_micro5(bars, stop_pt=8.0, rr=4.0)
    results["MICRO5_B"] = calc(t)
    print(f"{results['MICRO5_B']['n']} trades")

    print("  [7/10] MICRO5_C   : 5-min OR + no filters, 10pt stop, 5R ...", end=" ", flush=True)
    t = run_micro5(bars, stop_pt=10.0, rr=5.0, skip_mondays=False, week_filter=False)
    results["MICRO5_C"] = calc(t)
    print(f"{results['MICRO5_C']['n']} trades")

    print("  [8/10] MICRO5_ORB : 5-min micro confirmed by 15-min OR direction ...", end=" ", flush=True)
    t = run_micro5_orb_confirmed(bars, stop_pt=10.0, rr=5.0)
    results["MICRO5_ORB"] = calc(t)
    print(f"{results['MICRO5_ORB']['n']} trades")

    # ── 1-min Opening Bar ────────────────────────────────────────────────────
    print("  [9/10] MICRO1_A   : 1-min bar, 8pt stop, 4R target ...", end=" ", flush=True)
    t = run_micro1(bars, stop_pt=8.0, rr=4.0)
    results["MICRO1_A"] = calc(t)
    print(f"{results['MICRO1_A']['n']} trades")

    print(" [10/10] ASIA_EVAL  : Asia gap, 1c eval-safe ...", end=" ", flush=True)
    t = run_asia_eval(bars, contracts=1)
    results["ASIA_EVAL"] = calc(t)
    print(f"{results['ASIA_EVAL']['n']} trades")

    # ── Results table ─────────────────────────────────────────────────────────
    YEARS = list(OOS_YEARS)

    print(f"\n{sep}")
    hdr = (f"  {'Strategy':<18} {'T':>4} {'WR':>6} {'PF':>5} "
           f"{'Net 4yr':>10} {'$/wk':>7} {'EV/tr':>7} | "
           + "  ".join(f"{y}" for y in YEARS))
    print(hdr)
    print(f"  {'-'*(W-2)}")

    ORDER = ["BL_EVAL", "BL_FUNDED", "ORB_TIGHT", "ORB_5R",
             "MICRO5_A", "MICRO5_B", "MICRO5_C", "MICRO5_ORB",
             "MICRO1_A", "ASIA_EVAL"]
    LABELS = {
        "BL_EVAL":    "BL_EVAL  [v10]",
        "BL_FUNDED":  "BL_FUNDED[v10]",
        "ORB_TIGHT":  "ORB 10pt/5R",
        "ORB_5R":     "ORB 22pt/5R",
        "MICRO5_A":   "MICRO5 10pt/5R",
        "MICRO5_B":   "MICRO5 8pt/4R",
        "MICRO5_C":   "MICRO5 nofilter",
        "MICRO5_ORB": "MICRO5+OR conf",
        "MICRO1_A":   "MICRO1 8pt/4R",
        "ASIA_EVAL":  "ASIA 1c eval",
    }

    for key in ORDER:
        s = results[key]
        by_yr_str = "  ".join(f"${s['by_yr'].get(y, 0):>+7,.0f}" for y in YEARS)
        flag = ""
        if s["net"] > 0 and s["pf"] > 1.3 and s["n"] >= 20:
            flag = "  ✓ POSITIVE"
        elif s["net"] > 0 and s["pf"] > 1.0:
            flag = "  ~ marginal"
        else:
            flag = "  ✗ negative"

        print(f"  {LABELS[key]:<18} {s['n']:>4}  {s['wr']:>5.1%}  {s['pf']:>4.2f}"
              f"  {s['net']:>+9,.0f}  {s['wk']:>+6,.0f}  {s['ev']:>+6,.0f} |  "
              f"{by_yr_str}{flag}")

    print(f"\n{sep}")
    print("  KEY  T=trades  WR=win rate  PF=profit factor  EV=expected value per trade")
    print(f"  Breakeven WR: 10pt/5R=16.7%  8pt/4R=20%  22pt/5R=30%  27pt/2R=56%  27pt/3R=50%")
    print(f"  ✓ = net positive + PF>1.3 + ≥20 trades | ~ = marginal | ✗ = negative")
    print(sep)

    # ── Eval speed comparison ─────────────────────────────────────────────────
    print("\n  EVAL SPEED ESTIMATE (1c, $3k target, median EV/trade × 1.2 trades/wk):")
    for key in ORDER:
        s = results[key]
        if s["ev"] > 0 and s["n"] >= 10:
            trades_wk = s["n"] / (len(OOS_YEARS) * 52)
            wks = 3000 / (s["ev"] * trades_wk) if s["ev"] > 0 else 999
            loss_per  = (1 - s["wr"]) / s["wr"] * (-s["net"] / s["n"] / (1 - s["wr"]) if (1 - s["wr"]) > 0 else 0)
            avg_loss = abs(s["net"] / s["n"] - s["wr"] / (1 - s["wr"] if (1-s["wr"]) > 0 else 1) * s["ev"])
            print(f"    {LABELS[key]:<20}  {trades_wk:.1f} tr/wk  EV=${s['ev']:>+5,.0f}/tr"
                  f"  →  ~{wks:.0f} wks to pass")

    print()


if __name__ == "__main__":
    main()
