"""
Overnight Research Orchestrator — CruzCapital NQ Bot

Runs after the Asia session ends (~9:15 PM ET) and before the next US open (9:20 AM ET).
Tests hypotheses on the latest data, checks for strategy drift, and posts findings to Telegram.

Usage (run directly or via cron/systemd):
    python3 research_overnight.py

The VPS systemd timer fires this at 10:00 PM ET nightly (see deploy/research.timer).
"""

import sys
import os
import time
from datetime import datetime, date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import live.telegram_alerts as tg
from backtest import Backtester, load_csv
from walk_forward import _run_silent, _summary

Backtester.run_silent = _run_silent
Backtester.summary    = _summary

# ── Baseline (current v8 config) ─────────────────────────────────────────────

BASELINE_PF  = 2.18   # v8 OOS PF (update after each config change)
BASELINE_NET = 56395  # v8 4yr net (update after each config change)
PASS_THRESHOLD = 1.10  # hypothesis must beat baseline PF by this factor to "pass"

DATA_FILE = "data/nq_full.csv"
OOS_START = date(2025, 1, 1)   # always evaluate on OOS data only


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_oos(bars, overrides: dict) -> dict:
    """Run OOS backtest with config overrides. Returns summary dict."""
    original = {}
    try:
        for k, v in overrides.items():
            original[k] = getattr(config, k, None)
            setattr(config, k, v)
        oos = [b for b in bars if b["timestamp"].date() >= OOS_START]
        bt = Backtester()
        bt.run_silent(oos)
        return bt.summary("oos")
    finally:
        for k, v in original.items():
            setattr(config, k, v)


def _baseline(bars) -> dict:
    return _run_oos(bars, {})


def _drift_check(bars) -> str:
    """Compare last 30 days of live-equivalent data vs full OOS expectation."""
    today = date.today()
    cutoff = date(today.year, today.month, 1)  # current month
    recent = [b for b in bars if b["timestamp"].date() >= cutoff]
    if len(recent) < 100:
        return "Insufficient recent data for drift check"

    bt = Backtester()
    bt.run_silent(recent)
    s = bt.summary("recent")
    if not s["trades"]:
        return "No trades in current month — check filters"

    pf_str = f"{s['pf']:.2f}" if s["pf"] else "n/a"
    return (f"Current month: {s['trades']} trades | WR {s['win_rate']:.0%} | "
            f"PF {pf_str} | Net ${s['net']:+,.0f}")


# ── Hypothesis suite ──────────────────────────────────────────────────────────
# Each hypothesis is (label, config_overrides_dict).
# Add new ideas here — they run every night on fresh data.

HYPOTHESES = [
    ("Stop 20pt (vs 22pt)",        {"ORB_FIXED_STOP_POINTS": 20.0}),
    ("Stop 24pt (vs 22pt)",        {"ORB_FIXED_STOP_POINTS": 24.0}),
    ("Last entry 10:15 (vs 10:30)",{"LAST_ENTRY_TIME": "10:15"}),
    ("Last entry 10:45 (vs 10:30)",{"LAST_ENTRY_TIME": "10:45"}),
    ("OR min 50pt (vs 55pt)",      {"ORB_MIN_RANGE_POINTS": 50.0}),
    ("OR min 60pt (vs 55pt)",      {"ORB_MIN_RANGE_POINTS": 60.0}),
    ("OR max 100pt (vs 110pt)",    {"ORB_MAX_RANGE_POINTS": 100.0}),
    ("OR max 120pt (vs 110pt)",    {"ORB_MAX_RANGE_POINTS": 120.0}),
    ("Skip Mondays off",           {"SKIP_MONDAYS": False}),
    ("Score threshold 55 (vs 60)", {"SIGNAL_STRENGTH_MIN_SCORE": 55}),
    ("Score threshold 65 (vs 60)", {"SIGNAL_STRENGTH_MIN_SCORE": 65}),
    ("Asia skip Thu off",          {"ASIA_SKIP_THURSDAYS": False}),
    ("Asia gap min 25pt (vs 30pt)",{"ASIA_GAP_MIN_POINTS": 25.0}),
    ("Asia gap max 90pt (vs 80pt)",{"ASIA_GAP_MAX_POINTS": 90.0}),
]


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    today = date.today()
    print(f"\n{'='*60}")
    print(f"  Overnight Research — {today}")
    print(f"{'='*60}\n")

    tg.send_research_start(len(HYPOTHESES), today)

    print("Loading data ...")
    try:
        bars = load_csv(DATA_FILE)
    except Exception as e:
        msg = f"Failed to load {DATA_FILE}: {e}"
        print(msg); tg.send(f"Research ERROR: {msg}")
        return

    # Drift check
    print("Running drift check ...")
    drift = _drift_check(bars)
    print(f"  {drift}")
    tg.send(f"Drift check: {drift}")

    # Baseline
    print("Running baseline ...")
    base = _baseline(bars)
    base_pf  = base["pf"]  or 0.0
    base_net = base["net"]
    print(f"  Baseline OOS: PF {base_pf:.2f} | Net ${base_net:+,.0f} | {base['trades']} trades")

    # Hypotheses
    passed = []
    for label, overrides in HYPOTHESES:
        print(f"  Testing: {label} ...")
        try:
            result = _run_oos(bars, overrides)
            pf     = result["pf"] or 0.0
            net    = result["net"]
            change = net - base_net
            verdict = "PASS" if pf >= base_pf * PASS_THRESHOLD else "fail"
            print(f"    PF {pf:.2f} (base {base_pf:.2f}) | Net ${net:+,.0f} ({change:+,.0f}) | {verdict}")

            if verdict == "PASS":
                passed.append((label, pf, change))
                tg.send_research_finding(label, "tested",
                                         base_pf, pf, change, "PASS")
        except Exception as e:
            print(f"    ERROR: {e}")

    # Summary
    best_label = best_gain = ""
    if passed:
        best = max(passed, key=lambda x: x[2])
        best_label, _, best_gain = best

    tg.send_research_summary(today, len(HYPOTHESES), len(passed),
                             best_label, best_gain if passed else 0)

    print(f"\nDone. {len(passed)}/{len(HYPOTHESES)} hypotheses beat baseline.")
    if passed:
        print("  Passed:")
        for label, pf, change in sorted(passed, key=lambda x: -x[2]):
            print(f"    {label}: PF {pf:.2f}  net change +${change:,.0f}")
    print()


if __name__ == "__main__":
    run()
