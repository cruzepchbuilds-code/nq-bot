"""
brain/research/rty_research.py

RTY (E-mini Russell 2000) ORB Research Pipeline.
Same methodology as NQ and ES: year-by-year fresh bankroll, 2022 IS warmup.

RTY instrument specs vs NQ/ES:
  POINT_VALUE : $50/pt  (same as ES, vs NQ $20)
  TICK_SIZE   : 0.10    (finer than ES/NQ 0.25 -- slippage only 0.20pt at 2 ticks)
  OR range    : median 12.9pt, p90 = 20pt  (tighter than ES 14pt/28pt)
  Daily range : median 35pt  (smaller than ES 57pt, NQ ~150pt)
  Stop        : 4pt fixed  (scaled: 0.14% of ~2800 price, same % as NQ 25pt/18000)
  Buffer      : 1pt breakout
  Gap filter  : 4pt

Usage:
    python3 brain/research/rty_research.py
    python3 brain/research/rty_research.py --quick
"""
import sys
import os
from datetime import date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv

RTY_DATA_PATH = "data/rty_1min.csv"

RTY_OVERRIDES = {
    "SYMBOL":                           "RTY",
    "POINT_VALUE":                      50.0,
    "TICK_SIZE":                        0.10,
    "COMMISSION_PER_SIDE":              2.50,
    "SLIPPAGE_TICKS":                   2,    # 2 × 0.10 = 0.20pt slip

    # OR params — RTY median OR = 12.9pt, p90 = 20pt
    "ORB_FIXED_STOP_POINTS":            4.0,
    "ORB_STOP_BUFFER_POINTS":           1.0,
    "ORB_BREAKOUT_BUFFER_POINTS":       0.5,
    "ORB_MIN_RANGE_POINTS":             4.0,
    "ORB_MAX_RANGE_POINTS":             22.0,
    "ORB_BREAKOUT_RR_TARGET":           2.0,
    "ORB_BREAKOUT_CONFIRM":             "close",

    "GAP_FILTER_POINTS":                4.0,
    "BREAKOUT_MIN_VOLUME":              100,

    # Scorer bypassed — NQ-calibrated OR-size brackets are wrong for RTY
    "SIGNAL_STRENGTH_MIN_SCORE":        101,
    "SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP": 101,
    "HIGH_GAP_THRESHOLD":               8.0,

    "LONDON_ENABLED":                   False,
    "VWAP_PULLBACK_ENABLED":            False,
    "PM_VWAP_ENABLED":                  False,
    "GAP_FILL_ENABLED":                 False,
    "ASIA_ENABLED":                     False,
    "SECOND_BREAKOUT_ENABLED":          False,
    "BREAKOUT_MIN_OR_VOLUME_RATIO":     0.0,
    "BREAKOUT_MAX_OR_VOLUME_RATIO":     0.0,
    "GAP_EXCLUDE_MIN":                  0.0,
    "GAP_EXCLUDE_MAX":                  0.0,
    "PYRAMIDING_ENABLED":               False,
    "EVAL_MODE":                        False,
    "SKIP_MONTHS":                      [],
    "LAST_ENTRY_TIME":                  "10:30",

    "RISK_PER_TRADE_PCT":               0.01,
    "MIN_RR":                           1.9,
    "MAX_CONTRACTS":                    2,
    "STARTING_BALANCE":                 50000.0,
    "DAILY_LOSS_LIMIT_PCT":             0.015,
    "MAX_CONSECUTIVE_LOSING_DAYS":      2,
    "MAX_TRADES_PER_DAY":               2,
    "MAX_LOSSES_PER_DAY":               2,
    "DAILY_PROFIT_LOCK_PCT":            0.03,
    "WEEKLY_LOSS_LIMIT_PCT":            0.05,
    "MAX_TOTAL_DRAWDOWN_PCT":           0.12,
    "RECOVERY_MODE_TRIGGER_PCT":        0.05,
    "RECOVERY_SIZE_MULTIPLIER":         0.5,
    "APEX_TRAILING_DD":                 7000.0,
    "ENFORCE_APEX_RULES":               True,
    "SKIP_MONDAYS":                     True,
    "PARTIAL_EXIT_ENABLED":             False,
    "PYRAMID_WARMUP_TRADES":            5,
    "STRONG_MONTHS":                    [1, 2, 3, 4, 5, 10, 11],
    "WEAK_MONTHS":                      [6, 9, 12],
}


def _save():
    return {k: getattr(config, k, None) for k in RTY_OVERRIDES}

def _apply():
    for k, v in RTY_OVERRIDES.items():
        setattr(config, k, v)

def _restore(saved):
    for k, v in saved.items():
        if v is not None:
            setattr(config, k, v)


def calc_stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "avg": 0.0}
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gw = sum(wins)   if wins   else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = gw / gl if gl > 0 else float('inf')
    return {"n": len(trades), "net": round(gw-gl, 0), "wr": round(len(wins)/len(trades)*100, 1),
            "pf": round(pf, 2), "avg": round((gw-gl)/len(trades), 0)}


def run_year_fresh(bars, year):
    ystart = date(year, 1, 1)
    yend   = date(year + 1, 1, 1)
    prior  = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return []
    warmup = Backtester()
    warmup.run(prior, silent=True)
    bt = Backtester()
    bt._last_close         = warmup._last_close
    bt.regime.daily_ranges = warmup.regime.daily_ranges
    bt.or_volume_history   = warmup.or_volume_history
    bt.prev_day_mode       = warmup.prev_day_mode
    bt.run(subset, silent=True)
    return bt.bank.trade_log


def main():
    quick = "--quick" in sys.argv
    print("=" * 66)
    print("  RTY ORB RESEARCH PIPELINE")
    print(f"  OOS: 2023-2026 | Warmup: 2022 IS | Data: {RTY_DATA_PATH}")
    print("=" * 66)

    bars = load_csv(RTY_DATA_PATH)
    print(f"\nLoaded {len(bars):,} bars | "
          f"{bars[0]['timestamp'].date()} — {bars[-1]['timestamp'].date()}")
    print(f"RTY config: stop={RTY_OVERRIDES['ORB_FIXED_STOP_POINTS']}pt | "
          f"buffer={RTY_OVERRIDES['ORB_BREAKOUT_BUFFER_POINTS']}pt | "
          f"OR {RTY_OVERRIDES['ORB_MIN_RANGE_POINTS']}-{RTY_OVERRIDES['ORB_MAX_RANGE_POINTS']}pt | "
          f"tick=0.10 | $50/pt")

    saved = _save()
    _apply()

    try:
        print("\n── Year-by-Year OOS ─────────────────────────────────────────────\n")
        print(f"{'Year':<6} {'Trades':>7} {'Net $':>10} {'WR%':>7} {'PF':>6} {'Avg $':>8}")
        print("─" * 52)
        all_trades = []
        for year in range(2023, 2027):
            trades = run_year_fresh(bars, year)
            s = calc_stats(trades)
            all_trades.extend(trades)
            pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
            if s["n"] > 0:
                print(f"{year:<6} {s['n']:>7} {s['net']:>10,.0f} {s['wr']:>6.1f}% {pf_s:>6} {s['avg']:>8,.0f}")
            else:
                print(f"{year:<6} {'0':>7} {'—':>10} {'—':>7} {'—':>6} {'—':>8}")
        print("─" * 52)
        total = calc_stats(all_trades)
        pf_s = f"{total['pf']:.2f}" if total['pf'] != float('inf') else "inf"
        print(f"{'TOTAL':<6} {total['n']:>7} {total['net']:>10,.0f} {total['wr']:>6.1f}% {pf_s:>6} {total['avg']:>8,.0f}")
    finally:
        _restore(saved)

    print(f"\n  vs ES (H16): 94 trades | Net +$15,792 | WR 50.0% | PF 1.67")
    print(f"  vs NQ base : 206 trades | Net +$36,675 | WR 47.2% | PF 2.14")

    if not all_trades or quick:
        if not all_trades:
            print("\nNo trades — check config ranges")
        else:
            print("\n[--quick mode: edge discovery skipped]")
        return

    # ── Edge Discovery ─────────────────────────────────────────────────────
    print("\n── Edge Discovery ───────────────────────────────────────────────\n")

    def time_bucket(et):
        if not et or ":" not in str(et):
            return None
        h, m = int(str(et).split(":")[0]), int(str(et).split(":")[1])
        if h == 9 and 30 <= m < 45: return "09:30"
        if h == 9 and 45 <= m < 60: return "09:45"
        if h == 10 and  0 <= m < 15: return "10:00"
        if h == 10 and 15 <= m < 30: return "10:15"
        if h == 10 and 30 <= m < 45: return "10:30"
        return "other"

    def show(label, buckets, order):
        print(f"{label}:")
        print(f"  {'Key':<10} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10}")
        for k in order:
            ts = buckets.get(k, [])
            if not ts: continue
            s = calc_stats(ts)
            pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
            flag = " ◀ WEAK" if s["pf"] < 0.9 else (" ▲ STRONG" if s["pf"] >= 1.5 else "")
            print(f"  {k:<10} {s['n']:>5} {s['wr']:>5.1f}% {pf_s:>6} {s['net']:>10,.0f}{flag}")

    bt_time = defaultdict(list)
    bt_dow  = defaultdict(list)
    bt_mon  = defaultdict(list)
    bt_dir  = defaultdict(list)
    bt_year = defaultdict(list)

    dow_n = ["Mon","Tue","Wed","Thu","Fri"]
    mon_n = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
             7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}

    for t in all_trades:
        bk = time_bucket(t.get("entry_time", ""))
        if bk: bt_time[bk].append(t)
        d = date.fromisoformat(t["date"])
        bt_dow[dow_n[d.weekday()]].append(t)
        bt_mon[d.month].append(t)
        bt_dir[t.get("dir","?")].append(t)
        bt_year[d.year].append(t)

    show("Time Window", bt_time, ["09:30","09:45","10:00","10:15","10:30","other"])
    print()
    show("Day of Week", bt_dow, dow_n)
    print()
    print("Month:")
    print(f"  {'Month':<10} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10}")
    for m in range(1, 13):
        ts = bt_mon.get(m, [])
        if not ts: continue
        s = calc_stats(ts)
        pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        flag = " ◀ WEAK" if s["pf"] < 0.9 else (" ▲ STRONG" if s["pf"] >= 1.5 else "")
        print(f"  {mon_n[m]:<10} {s['n']:>5} {s['wr']:>5.1f}% {pf_s:>6} {s['net']:>10,.0f}{flag}")
    print()
    show("Direction", bt_dir, ["long","short"])
    print()
    print("Year Detail:")
    print(f"  {'Year':<6} {'N':>5} {'WR%':>6} {'PF':>6} {'Net $':>10} {'Avg $':>8}")
    for yr in sorted(bt_year):
        ts = bt_year[yr]
        s = calc_stats(ts)
        pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        print(f"  {yr:<6} {s['n']:>5} {s['wr']:>5.1f}% {pf_s:>6} {s['net']:>10,.0f} {s['avg']:>8,.0f}")

    print("\n" + "=" * 66)
    print("  SUMMARY")
    print("=" * 66)
    print(f"  Instrument   : RTY (E-mini Russell 2000) — $50/pt, tick 0.10")
    print(f"  Period       : 2023-2026 OOS | 2022 IS warmup")
    print(f"  Trades       : {total['n']}")
    print(f"  Net P&L      : ${total['net']:,.0f}")
    print(f"  Win Rate     : {total['wr']}%")
    print(f"  Profit Factor: {pf_s}")
    print(f"  Avg/Trade    : ${total['avg']:,.0f}")
    print("=" * 66)


if __name__ == "__main__":
    main()
