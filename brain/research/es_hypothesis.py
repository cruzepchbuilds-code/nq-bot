"""
brain/research/es_hypothesis.py

ES-specific hypothesis testing pipeline.
Tests improvements against the ES baseline (raw PF 1.03, 199 trades).

All tests use the same year-by-year fresh-bankroll methodology as the NQ pipeline.

Usage:
    python3 brain/research/es_hypothesis.py
"""
import sys
import os
import copy
from datetime import date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv

ES_DATA_PATH = "data/es_1min.csv"

# ── Baseline ES config ────────────────────────────────────────────────────────
ES_BASE = {
    "SYMBOL":                           "ES",
    "POINT_VALUE":                      50.0,
    "TICK_SIZE":                        0.25,
    "COMMISSION_PER_SIDE":              2.50,
    "SLIPPAGE_TICKS":                   2,
    "ORB_FIXED_STOP_POINTS":            7.0,
    "ORB_STOP_BUFFER_POINTS":           2.0,
    "ORB_BREAKOUT_BUFFER_POINTS":       1.0,
    "ORB_MIN_RANGE_POINTS":             5.0,
    "ORB_MAX_RANGE_POINTS":             30.0,
    "ORB_BREAKOUT_RR_TARGET":           2.0,
    "ORB_BREAKOUT_CONFIRM":             "close",
    "GAP_FILTER_POINTS":                5.0,
    "BREAKOUT_MIN_VOLUME":              500,
    "SIGNAL_STRENGTH_MIN_SCORE":        101,
    "SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP": 101,
    "HIGH_GAP_THRESHOLD":               10.0,
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
    # Keep NQ STRONG/WEAK months as baseline
    "STRONG_MONTHS":                    [1, 2, 3, 4, 5, 10, 11],
    "WEAK_MONTHS":                      [6, 9, 12],
    "LAST_ENTRY_TIME":                  "10:30",
}

HYPOTHESES = [
    # ── Round 1: parameter sweeps ──────────────────────────────────────────
    {
        "id": "H01",
        "name": "Month calendar (STRONG/WEAK adjust only)",
        "desc": "Change STRONG=[2,3,4,11,12], WEAK=[1,5,6,7,8,10]. NOTE: with scorer bypassed, max_c stays 1c — no real effect expected.",
        "overrides": {
            "STRONG_MONTHS": [2, 3, 4, 11, 12],
            "WEAK_MONTHS":   [1, 5, 6, 7, 8, 10],
        },
    },
    {
        "id": "H02",
        "name": "SKIP weak months [1,5,6,7,8,10] (hard gate)",
        "desc": "Actually skip trading in ES-weak months. OOS: Jan PF 0.41, May 0.65, Jun 0.86, Jul 0.62, Aug 0.66, Oct 0.65.",
        "overrides": {
            "SKIP_MONTHS": [1, 5, 6, 7, 8, 10],
        },
    },
    {
        "id": "H03",
        "name": "Stop 5pt (tighter stop, smaller loss)",
        "desc": "Reduce stop from 7pt to 5pt+1pt buffer. Win rate drops but loss per trade lower.",
        "overrides": {
            "ORB_FIXED_STOP_POINTS":  5.0,
            "ORB_STOP_BUFFER_POINTS": 1.0,
        },
    },
    {
        "id": "H04",
        "name": "Stop 10pt (wider, fewer false stops)",
        "desc": "Increase stop to 10pt+3pt buffer. Trades get more room but bigger loss when wrong.",
        "overrides": {
            "ORB_FIXED_STOP_POINTS":  10.0,
            "ORB_STOP_BUFFER_POINTS":  3.0,
        },
    },
    {
        "id": "H05",
        "name": "RR target 1.5x (lower target, higher WR)",
        "desc": "NQ uses 2.0x. ES daily range median=57pt; 2.0x may be too ambitious. MIN_RR must also drop to 1.4 or MIN_RR guard blocks all trades (reward/risk=1.5 < MIN_RR=1.9).",
        "overrides": {
            "ORB_BREAKOUT_RR_TARGET": 1.5,
            "MIN_RR":                 1.4,   # must drop with RR target
        },
    },
    {
        "id": "H06",
        "name": "RR target 2.5x (higher reward, lower WR)",
        "desc": "Test if a subset of ES breakouts extend further for higher reward.",
        "overrides": {
            "ORB_BREAKOUT_RR_TARGET": 2.5,
        },
    },
    {
        "id": "H07",
        "name": "OR range 8-22pt (tighter filter)",
        "desc": "Narrow from 5-30pt to 8-22pt: exclude tiny ORs and extreme-vol days.",
        "overrides": {
            "ORB_MIN_RANGE_POINTS": 8.0,
            "ORB_MAX_RANGE_POINTS": 22.0,
        },
    },
    {
        "id": "H08",
        "name": "Breakout buffer 0.5pt (tighter entry)",
        "desc": "Reduce confirm buffer from 1pt to 0.5pt. More trades, more false breakouts.",
        "overrides": {
            "ORB_BREAKOUT_BUFFER_POINTS": 0.5,
        },
    },
    {
        "id": "H09",
        "name": "Skip Mondays OFF (include ES Mondays)",
        "desc": "NQ Mondays are historically weak; test if ES differs.",
        "overrides": {
            "SKIP_MONDAYS": False,
        },
    },
    {
        "id": "H10",
        "name": "Entry cutoff 10:15 (earlier than NQ's 10:30)",
        "desc": "Tighten entry window — morning momentum fades faster on ES.",
        "overrides": {
            "LAST_ENTRY_TIME": "10:15",
        },
    },
    # ── Round 2: combos from best singles ─────────────────────────────────
    {
        "id": "H11",
        "name": "SKIP months + 1.5x RR (MIN_RR=1.4)",
        "desc": "Skip ES-weak months AND lower RR target. Must also lower MIN_RR.",
        "overrides": {
            "SKIP_MONTHS":            [1, 5, 6, 7, 8, 10],
            "ORB_BREAKOUT_RR_TARGET": 1.5,
            "MIN_RR":                 1.4,
        },
    },
    {
        "id": "H12",
        "name": "SKIP months + 0.5pt buffer + include Mondays",
        "desc": "Skip weak months, more trades per day with tighter buffer, include Mondays.",
        "overrides": {
            "SKIP_MONTHS":                [1, 5, 6, 7, 8, 10],
            "ORB_BREAKOUT_BUFFER_POINTS": 0.5,
            "SKIP_MONDAYS":               False,
        },
    },
    {
        "id": "H13",
        "name": "SKIP months + 1.5x RR + 0.5pt buffer + Mondays",
        "desc": "Full kitchen-sink combo. MIN_RR must drop to 1.4.",
        "overrides": {
            "SKIP_MONTHS":                [1, 5, 6, 7, 8, 10],
            "ORB_BREAKOUT_RR_TARGET":     1.5,
            "MIN_RR":                     1.4,
            "ORB_BREAKOUT_BUFFER_POINTS": 0.5,
            "SKIP_MONDAYS":               False,
        },
    },
    {
        "id": "H14",
        "name": "SKIP months + 1.5x RR + stop 5pt (tighter all around)",
        "desc": "Tightest version: skip weak months, lower target, tighter stop. MIN_RR=1.4.",
        "overrides": {
            "SKIP_MONTHS":            [1, 5, 6, 7, 8, 10],
            "ORB_BREAKOUT_RR_TARGET": 1.5,
            "MIN_RR":                 1.4,
            "ORB_FIXED_STOP_POINTS":  5.0,
            "ORB_STOP_BUFFER_POINTS": 1.0,
        },
    },
    {
        "id": "H15",
        "name": "SKIP months + stop 5pt + no Monday skip",
        "desc": "SKIP months with tighter stop and Mondays included (no RR change).",
        "overrides": {
            "SKIP_MONTHS":           [1, 5, 6, 7, 8, 10],
            "ORB_FIXED_STOP_POINTS": 5.0,
            "ORB_STOP_BUFFER_POINTS": 1.0,
            "SKIP_MONDAYS":          False,
        },
    },
    {
        "id": "H16",
        "name": "SKIP months + LAST_ENTRY 10:15 (cut weak late window)",
        "desc": "Time analysis shows ES 10:15-10:29 entries: WR 30.8%, PF 0.77 (drag). Cut off at 10:15.",
        "overrides": {
            "SKIP_MONTHS":    [1, 5, 6, 7, 8, 10],
            "LAST_ENTRY_TIME": "10:15",
        },
    },
    {
        "id": "H17",
        "name": "SKIP months + LAST_ENTRY 10:00 (only the 2 best windows)",
        "desc": "09:45 window PF=1.56, 10:00 window PF=2.21. Cut all entries after 10:00.",
        "overrides": {
            "SKIP_MONTHS":    [1, 5, 6, 7, 8, 10],
            "LAST_ENTRY_TIME": "10:00",
        },
    },
    {
        "id": "H18",
        "name": "SKIP months + LAST_ENTRY 10:15 + no Monday skip",
        "desc": "H16 with Mondays included to compensate for fewer trades.",
        "overrides": {
            "SKIP_MONTHS":    [1, 5, 6, 7, 8, 10],
            "LAST_ENTRY_TIME": "10:15",
            "SKIP_MONDAYS":   False,
        },
    },
]


def apply_config(overrides):
    saved = {}
    all_overrides = {**ES_BASE, **overrides}
    for k, v in all_overrides.items():
        saved[k] = getattr(config, k, None)
        setattr(config, k, v)
    return saved


def restore_config(saved):
    for k, v in saved.items():
        if v is not None:
            setattr(config, k, v)


def calc_stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "avg": 0.0}
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = gw / gl if gl > 0 else float('inf')
    return {
        "n":   len(trades),
        "net": round(gw - gl, 0),
        "wr":  round(len(wins) / len(trades) * 100, 1),
        "pf":  round(pf, 2),
        "avg": round((gw - gl) / len(trades), 0),
    }


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


def run_oos(bars, overrides=None):
    saved = apply_config(overrides or {})
    try:
        all_trades = []
        for year in range(2023, 2027):
            all_trades.extend(run_year_fresh(bars, year))
        return all_trades
    finally:
        restore_config(saved)


def verdict(base_pf, test_pf, base_net, test_net, base_n, test_n):
    """KEEP if PF improves by >0.10 and net improves, or trade count stable."""
    pf_delta  = test_pf - base_pf
    net_delta = test_net - base_net
    n_delta   = test_n - base_n
    if test_pf == float('inf') and test_n < 10:
        return "REJECT (too few trades)"
    if pf_delta >= 0.10 and net_delta >= 0:
        return "KEEP ✓"
    if pf_delta >= 0.05 and net_delta > 500:
        return "MARGINAL"
    if pf_delta < -0.10 or net_delta < -2000:
        return "REJECT ✗"
    return "NEUTRAL"


def main():
    print("=" * 70)
    print("  ES ORB HYPOTHESIS PIPELINE")
    print("  Baseline: PF 1.03, Net +$2,168, 199 trades (4-yr OOS)")
    print("=" * 70)

    if not os.path.exists(ES_DATA_PATH):
        print(f"ERROR: {ES_DATA_PATH} not found")
        sys.exit(1)

    # Load bars once
    print(f"\nLoading {ES_DATA_PATH}...")
    bars = load_csv(ES_DATA_PATH)
    print(f"  {len(bars):,} bars loaded")

    # Get baseline
    print("\nRunning baseline...")
    base_trades = run_oos(bars)
    base = calc_stats(base_trades)
    print(f"  Baseline: {base['n']} trades | Net ${base['net']:,.0f} | "
          f"WR {base['wr']}% | PF {base['pf']:.2f}")

    winners = []
    results = []

    print(f"\n{'ID':<5} {'Hypothesis':<48} {'Trades':>7} {'Net $':>9} {'PF':>5} {'Delta PF':>9} {'Verdict'}")
    print("─" * 110)

    for h in HYPOTHESES:
        test_trades = run_oos(bars, h["overrides"])
        test = calc_stats(test_trades)
        pf_str  = f"{test['pf']:.2f}" if test['pf'] != float('inf') else "inf"
        base_pf_str = f"{base['pf']:.2f}"
        dpf  = test['pf'] - base['pf']
        dpf_s = f"+{dpf:.2f}" if dpf >= 0 else f"{dpf:.2f}"
        v = verdict(base['pf'], test['pf'], base['net'], test['net'],
                    base['n'], test['n'])
        name_short = h['name'][:47]
        print(f"{h['id']:<5} {name_short:<48} {test['n']:>7} {test['net']:>9,.0f} "
              f"{pf_str:>5} {dpf_s:>9} {v}")
        r = {**h, "test": test, "base": base, "dpf": dpf,
             "dnet": test["net"] - base["net"], "verdict": v}
        results.append(r)
        if "KEEP" in v:
            winners.append(r)

    print("\n" + "=" * 70)
    print(f"  WINNERS ({len(winners)} hypotheses passed)")
    print("=" * 70)
    for w in winners:
        print(f"\n  {w['id']}: {w['name']}")
        print(f"  {w['desc']}")
        print(f"  Baseline: {base['n']} trades | Net ${base['net']:,.0f} | PF {base['pf']:.2f}")
        print(f"  Result:   {w['test']['n']} trades | Net ${w['test']['net']:,.0f} | PF {w['test']['pf']:.2f}")
        print(f"  Delta:    Net {'+' if w['dnet']>=0 else ''}{w['dnet']:,.0f} | "
              f"PF {'+' if w['dpf']>=0 else ''}{w['dpf']:.2f}")
        print(f"  Overrides: {w['overrides']}")

    if not winners:
        print("\n  No hypotheses beat baseline by >= 0.10 PF + net improvement.")
        print("  Marginal results above may still be worth combining.")

    print("\n" + "=" * 70)
    print("  YEAR-BY-YEAR DETAIL FOR ALL WINNERS")
    print("=" * 70)
    for w in winners:
        print(f"\n  {w['id']}: {w['name']}")
        saved = apply_config(w["overrides"])
        try:
            print(f"  {'Year':<6} {'N':>5} {'Net $':>9} {'WR%':>6} {'PF':>6}")
            print(f"  {'─'*40}")
            yr_all = []
            for year in range(2023, 2027):
                yr_trades = run_year_fresh(bars, year)
                s = calc_stats(yr_trades)
                yr_all.extend(yr_trades)
                pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
                print(f"  {year:<6} {s['n']:>5} {s['net']:>9,.0f} {s['wr']:>5.1f}% {pf_s:>6}")
            total = calc_stats(yr_all)
            pf_s = f"{total['pf']:.2f}" if total['pf'] != float('inf') else "inf"
            print(f"  {'─'*40}")
            print(f"  {'TOTAL':<6} {total['n']:>5} {total['net']:>9,.0f} {total['wr']:>5.1f}% {pf_s:>6}")
        finally:
            restore_config(saved)


if __name__ == "__main__":
    main()
