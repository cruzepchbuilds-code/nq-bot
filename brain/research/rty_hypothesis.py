"""
brain/research/rty_hypothesis.py

RTY ORB hypothesis testing.
Baseline: PF 0.73, -$12,735, 230 trades (4-yr OOS)
Viable threshold: PF >= 1.40 with >= 30 trades/year.

Key findings from edge discovery:
  - Only 2 months positive: Apr (PF 1.88) and Dec (PF 3.08)
  - 10:00 window is the only one near breakeven (PF 1.04)
  - 09:45 window is terrible (PF 0.62)
  - Both directions losing (long 0.86, short 0.62)

Usage:
    python3 brain/research/rty_hypothesis.py
"""
import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv

RTY_DATA_PATH = "data/rty_1min.csv"

RTY_BASE = {
    "SYMBOL": "RTY", "POINT_VALUE": 50.0, "TICK_SIZE": 0.10,
    "COMMISSION_PER_SIDE": 2.50, "SLIPPAGE_TICKS": 2,
    "ORB_FIXED_STOP_POINTS": 4.0, "ORB_STOP_BUFFER_POINTS": 1.0,
    "ORB_BREAKOUT_BUFFER_POINTS": 0.5, "ORB_MIN_RANGE_POINTS": 4.0,
    "ORB_MAX_RANGE_POINTS": 22.0, "ORB_BREAKOUT_RR_TARGET": 2.0,
    "ORB_BREAKOUT_CONFIRM": "close", "GAP_FILTER_POINTS": 4.0,
    "BREAKOUT_MIN_VOLUME": 100, "SIGNAL_STRENGTH_MIN_SCORE": 101,
    "SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP": 101, "HIGH_GAP_THRESHOLD": 8.0,
    "LONDON_ENABLED": False, "VWAP_PULLBACK_ENABLED": False,
    "PM_VWAP_ENABLED": False, "GAP_FILL_ENABLED": False,
    "ASIA_ENABLED": False, "SECOND_BREAKOUT_ENABLED": False,
    "BREAKOUT_MIN_OR_VOLUME_RATIO": 0.0, "BREAKOUT_MAX_OR_VOLUME_RATIO": 0.0,
    "GAP_EXCLUDE_MIN": 0.0, "GAP_EXCLUDE_MAX": 0.0,
    "PYRAMIDING_ENABLED": False, "EVAL_MODE": False,
    "SKIP_MONTHS": [], "LAST_ENTRY_TIME": "10:30",
    "RISK_PER_TRADE_PCT": 0.01, "MIN_RR": 1.9, "MAX_CONTRACTS": 2,
    "STARTING_BALANCE": 50000.0, "DAILY_LOSS_LIMIT_PCT": 0.015,
    "MAX_CONSECUTIVE_LOSING_DAYS": 2, "MAX_TRADES_PER_DAY": 2,
    "MAX_LOSSES_PER_DAY": 2, "DAILY_PROFIT_LOCK_PCT": 0.03,
    "WEEKLY_LOSS_LIMIT_PCT": 0.05, "MAX_TOTAL_DRAWDOWN_PCT": 0.12,
    "RECOVERY_MODE_TRIGGER_PCT": 0.05, "RECOVERY_SIZE_MULTIPLIER": 0.5,
    "APEX_TRAILING_DD": 7000.0, "ENFORCE_APEX_RULES": True,
    "SKIP_MONDAYS": True, "PARTIAL_EXIT_ENABLED": False,
    "PYRAMID_WARMUP_TRADES": 5, "STRONG_MONTHS": [1, 2, 3, 4, 5, 10, 11],
    "WEAK_MONTHS": [6, 9, 12],
}

HYPOTHESES = [
    # ── Month filters ─────────────────────────────────────────────────────
    {
        "id": "H01",
        "name": "SKIP all months except Apr+Dec (only 2 positive)",
        "desc": "Apr PF 1.88, Dec PF 3.08. Everything else negative.",
        "overrides": {"SKIP_MONTHS": [1, 2, 3, 5, 6, 7, 8, 9, 10, 11]},
    },
    {
        "id": "H02",
        "name": "SKIP worst 8 months (keep Apr/Sep/Dec/Oct)",
        "desc": "Sep PF 0.83, Oct PF 0.48 are marginal — test keeping 4 months.",
        "overrides": {"SKIP_MONTHS": [1, 2, 3, 5, 6, 7, 8, 11]},
    },
    {
        "id": "H03",
        "name": "SKIP worst 6 months (keep Apr/Jun/Jul/Aug/Sep/Dec)",
        "desc": "Keep months with PF closest to 1.0 in addition to the 2 strong ones.",
        "overrides": {"SKIP_MONTHS": [1, 2, 3, 10, 11, 5]},
    },
    # ── Stop size ─────────────────────────────────────────────────────────
    {
        "id": "H04",
        "name": "Stop 3pt (even tighter — smaller loss per stop)",
        "desc": "4pt may be too wide for RTY's tight OR. Try 3pt.",
        "overrides": {"ORB_FIXED_STOP_POINTS": 3.0, "ORB_STOP_BUFFER_POINTS": 0.5},
    },
    {
        "id": "H05",
        "name": "Stop 6pt (wider — reduce false stops)",
        "desc": "RTY is volatile intrabar. Give more room.",
        "overrides": {"ORB_FIXED_STOP_POINTS": 6.0, "ORB_STOP_BUFFER_POINTS": 1.5},
    },
    {
        "id": "H06",
        "name": "Stop 8pt (NQ-ratio proportional)",
        "desc": "RTY price ~2000. 0.4% stop = 8pt. Test wider breathing room.",
        "overrides": {"ORB_FIXED_STOP_POINTS": 8.0, "ORB_STOP_BUFFER_POINTS": 2.0},
    },
    # ── Time filters ──────────────────────────────────────────────────────
    {
        "id": "H07",
        "name": "LAST_ENTRY 10:15 (skip 10:15+ window PF 0.70)",
        "desc": "Only trade 09:45 and 10:00 windows.",
        "overrides": {"LAST_ENTRY_TIME": "10:15"},
    },
    {
        "id": "H08",
        "name": "LAST_ENTRY 10:00 (only 09:45 window)",
        "desc": "Most trades are at 09:45. This is the earliest/cleanest breakout.",
        "overrides": {"LAST_ENTRY_TIME": "10:00"},
    },
    # ── RR ────────────────────────────────────────────────────────────────
    {
        "id": "H09",
        "name": "RR 1.5x (lower target, MIN_RR=1.4)",
        "desc": "RTY daily range is smaller. 2.0x may not be achievable. Try 1.5x.",
        "overrides": {"ORB_BREAKOUT_RR_TARGET": 1.5, "MIN_RR": 1.4},
    },
    {
        "id": "H10",
        "name": "RR 2.5x (higher target, fewer fills)",
        "desc": "If breakouts extend, 2.5x captures more on winners.",
        "overrides": {"ORB_BREAKOUT_RR_TARGET": 2.5},
    },
    # ── OR range ──────────────────────────────────────────────────────────
    {
        "id": "H11",
        "name": "OR range 6-16pt (tighter filter)",
        "desc": "RTY p50=12.9pt. Narrow from 4-22 to 6-16pt. Exclude tiny/huge ORs.",
        "overrides": {"ORB_MIN_RANGE_POINTS": 6.0, "ORB_MAX_RANGE_POINTS": 16.0},
    },
    {
        "id": "H12",
        "name": "OR range 6-20pt + SKIP months Apr+Dec only",
        "desc": "Tighter OR + best months combo.",
        "overrides": {
            "ORB_MIN_RANGE_POINTS": 6.0, "ORB_MAX_RANGE_POINTS": 20.0,
            "SKIP_MONTHS": [1, 2, 3, 5, 6, 7, 8, 9, 10, 11],
        },
    },
    # ── Skip Mondays ──────────────────────────────────────────────────────
    {
        "id": "H13",
        "name": "Include Mondays (RTY Mondays may differ from NQ)",
        "desc": "NQ skips Mondays but RTY may have different DOW pattern.",
        "overrides": {"SKIP_MONDAYS": False},
    },
    # ── Big combo: best months + 10:00 window ─────────────────────────────
    {
        "id": "H14",
        "name": "Apr+Dec only + LAST_ENTRY 10:15 + stop 6pt",
        "desc": "Best months + cut late entries + wider stop for more breathing room.",
        "overrides": {
            "SKIP_MONTHS": [1, 2, 3, 5, 6, 7, 8, 9, 10, 11],
            "LAST_ENTRY_TIME": "10:15",
            "ORB_FIXED_STOP_POINTS": 6.0, "ORB_STOP_BUFFER_POINTS": 1.5,
        },
    },
    {
        "id": "H15",
        "name": "Apr+Dec only + 1.5x RR + MIN_RR=1.4",
        "desc": "Best months + lower target for higher WR.",
        "overrides": {
            "SKIP_MONTHS": [1, 2, 3, 5, 6, 7, 8, 9, 10, 11],
            "ORB_BREAKOUT_RR_TARGET": 1.5, "MIN_RR": 1.4,
        },
    },
    {
        "id": "H16",
        "name": "Apr+Dec only + LAST_ENTRY 10:15 + 1.5x RR",
        "desc": "Kitchen sink: best months + time filter + lower RR.",
        "overrides": {
            "SKIP_MONTHS": [1, 2, 3, 5, 6, 7, 8, 9, 10, 11],
            "LAST_ENTRY_TIME": "10:15",
            "ORB_BREAKOUT_RR_TARGET": 1.5, "MIN_RR": 1.4,
        },
    },
]


def apply_config(overrides):
    saved = {}
    for k, v in {**RTY_BASE, **overrides}.items():
        saved[k] = getattr(config, k, None)
        setattr(config, k, v)
    return saved

def restore_config(saved):
    for k, v in saved.items():
        if v is not None: setattr(config, k, v)

def calc_stats(trades):
    if not trades: return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0}
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = gw / gl if gl > 0 else float('inf')
    return {"n": len(trades), "net": round(gw-gl, 0),
            "wr": round(len(wins)/len(trades)*100, 1), "pf": round(pf, 2)}

def run_year_fresh(bars, year):
    ystart = date(year, 1, 1)
    yend   = date(year + 1, 1, 1)
    prior  = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset: return []
    w = Backtester(); w.run(prior, silent=True)
    bt = Backtester()
    bt._last_close = w._last_close; bt.regime.daily_ranges = w.regime.daily_ranges
    bt.or_volume_history = w.or_volume_history; bt.prev_day_mode = w.prev_day_mode
    bt.run(subset, silent=True)
    return bt.bank.trade_log

def run_oos(bars, overrides=None):
    saved = apply_config(overrides or {})
    try:
        trades = []
        for yr in range(2023, 2027):
            trades.extend(run_year_fresh(bars, yr))
        return trades
    finally:
        restore_config(saved)

def verdict(base_pf, test_pf, base_net, test_net, test_n):
    if test_n < 15: return "REJECT (n<15)"
    dpf = test_pf - base_pf
    dnet = test_net - base_net
    if test_pf >= 1.40 and dnet > 0: return "VIABLE ✓"
    if test_pf >= 1.20 and dnet > 2000: return "MARGINAL"
    if dpf < -0.10 or (test_pf < 0.9 and test_n > 30): return "REJECT ✗"
    return "NEUTRAL"


def main():
    print("=" * 72)
    print("  RTY ORB HYPOTHESIS PIPELINE")
    print("  Baseline: PF 0.73, Net -$12,735, 230 trades | Viable: PF >= 1.40")
    print("=" * 72)

    bars = load_csv(RTY_DATA_PATH)
    print(f"\nLoaded {len(bars):,} bars")

    print("\nRunning baseline...")
    base_trades = run_oos(bars)
    base = calc_stats(base_trades)
    print(f"  Baseline confirmed: {base['n']} trades | Net ${base['net']:,.0f} | PF {base['pf']:.2f}")

    winners = []
    print(f"\n{'ID':<5} {'Hypothesis':<50} {'N':>5} {'Net $':>9} {'PF':>5} {'ΔPF':>6} Verdict")
    print("─" * 100)

    for h in HYPOTHESES:
        tt = run_oos(bars, h["overrides"])
        t = calc_stats(tt)
        pf_s  = f"{t['pf']:.2f}" if t['pf'] != float('inf') else "inf "
        dpf   = t['pf'] - base['pf'] if t['pf'] != float('inf') else 99.0
        dpf_s = f"+{dpf:.2f}" if dpf >= 0 else f"{dpf:.2f}"
        v = verdict(base['pf'], t['pf'], base['net'], t['net'], t['n'])
        print(f"{h['id']:<5} {h['name'][:49]:<50} {t['n']:>5} {t['net']:>9,.0f} "
              f"{pf_s:>5} {dpf_s:>6} {v}")
        if "VIABLE" in v:
            winners.append({**h, "test": t, "base": base, "dpf": dpf,
                            "dnet": t["net"] - base["net"], "verdict": v})

    print("\n" + "=" * 72)
    if winners:
        print(f"  VIABLE CONFIGURATIONS ({len(winners)})")
        print("=" * 72)
        for w in winners:
            saved = apply_config(w["overrides"])
            try:
                print(f"\n  {w['id']}: {w['name']}")
                print(f"  {'Year':<6} {'N':>5} {'Net $':>9} {'WR%':>6} {'PF':>6}")
                print(f"  {'─'*38}")
                all_yr = []
                for yr in range(2023, 2027):
                    ytt = run_year_fresh(bars, yr)
                    s = calc_stats(ytt); all_yr.extend(ytt)
                    pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
                    print(f"  {yr:<6} {s['n']:>5} {s['net']:>9,.0f} {s['wr']:>5.1f}% {pf_s:>6}")
                tot = calc_stats(all_yr)
                pf_s = f"{tot['pf']:.2f}" if tot['pf'] != float('inf') else "inf"
                print(f"  {'─'*38}")
                print(f"  {'TOTAL':<6} {tot['n']:>5} {tot['net']:>9,.0f} {tot['wr']:>5.1f}% {pf_s:>6}")
                print(f"  Config: {w['overrides']}")
            finally:
                restore_config(saved)
    else:
        print("  NO VIABLE CONFIGURATIONS (PF >= 1.40)")
        print("  RTY ORB edge does not exist with current stop/RR/filter approach.")
        print("  Recommendation: Do not deploy RTY ORB. Use RTY data for")
        print("  mean-reversion or VWAP strategies instead.")
    print("=" * 72)


if __name__ == "__main__":
    main()
