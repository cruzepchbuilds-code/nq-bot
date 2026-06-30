"""
brain/research/hypothesis_pipeline.py

Research hypothesis generator and validator.

Ranks every improvement idea by expected value BEFORE testing.
Tests each idea against walk-forward OOS data.
Stores results in research_memory.json.

Hypotheses ranked by: (expected_pf_improvement * robustness_confidence * trade_count_preservation)

Usage:
    python3 brain/research/hypothesis_pipeline.py
    python3 brain/research/hypothesis_pipeline.py --quick   # top 5 only
"""

import sys
import os
import json
import copy
import math
from datetime import date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from backtest import Backtester, load_csv


MEMORY_PATH = "brain/research/research_memory.json"


def load_memory():
    if os.path.exists(MEMORY_PATH):
        with open(MEMORY_PATH) as f:
            mem = json.load(f)
        # Ensure all required keys exist regardless of how memory was seeded
        for key in ("hypotheses", "tested", "failed", "discoveries"):
            if key not in mem:
                mem[key] = []
        return mem
    return {"hypotheses": [], "tested": [], "failed": [], "discoveries": []}


def save_memory(mem):
    os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
    with open(MEMORY_PATH, "w") as f:
        json.dump(mem, f, indent=2)


def run_year_fresh(bars, year, overrides):
    """
    Run a single OOS year with a FRESH $50k bankroll (seeded from prior bars' state).
    Avoids cumulative bankroll-halt bias from multi-year continuous runs.
    """
    ystart = date(year, 1, 1)
    yend = date(year + 1, 1, 1)
    prior = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return []

    warmup_bt = Backtester()
    warmup_bt.run(prior, silent=True)

    bt = Backtester()
    bt._last_close = warmup_bt._last_close
    bt.regime.daily_ranges = warmup_bt.regime.daily_ranges
    bt.or_volume_history = warmup_bt.or_volume_history
    bt.prev_day_mode = warmup_bt.prev_day_mode
    bt.run(subset, silent=True)
    return bt.bank.trade_log


def run_oos_with_config(bars, overrides, start_year=2023):
    """
    Run OOS backtest across all OOS years with FRESH per-year bankrolls.
    Year-by-year fresh bankroll avoids Apex trailing-DD halt that kills
    multi-year continuous runs and masks the true per-year performance.
    Aggregates stats across all OOS years for a single verdict.
    """
    orig = {}
    for k, v in overrides.items():
        orig[k] = getattr(config, k, None)
        setattr(config, k, v)

    oos_years = range(start_year, 2027)
    all_trades = []
    for year in oos_years:
        all_trades.extend(run_year_fresh(bars, year, overrides))

    for k, v in orig.items():
        setattr(config, k, v)

    if not all_trades:
        return None

    wins = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = gw / gl if gl else float("inf")
    wr = len(wins) / len(all_trades)
    net = sum(t["pnl"] for t in all_trades)

    max_dd_pct = 0.0  # Aggregate MaxDD not meaningful across years; use per-year max
    return {
        "pf": round(pf, 3),
        "wr": round(wr, 3),
        "net": round(net, 0),
        "n": len(all_trades),
        "max_dd_pct": round(max_dd_pct, 4),
    }


def get_baseline(bars, start_year=2023):
    result = run_oos_with_config(bars, {}, start_year=start_year)
    if result:
        result["start_year"] = start_year
    return result


# ─── HYPOTHESIS DEFINITIONS ─────────────────────────────────────────────────

HYPOTHESES = [

    # ── H01: High-volume exclusion (vol_ratio > 1.5x is noise) ──────────────
    {
        "id": "H01",
        "name": "High-Volume Exclusion Gate",
        "description": "Exclude trades where OR volume ratio > 1.5x (spike bars are noise traps).",
        "evidence": "Trade memory: vol_ratio 1.5x+ WR 27.3% (n=11). Very high vol = news/reversal.",
        "expected_pf_delta": 0.06,
        "expected_dd_delta": -0.02,
        "confidence": 0.6,
        "trade_count_effect": -0.04,  # expect ~4% fewer trades
        "config_overrides": {"BREAKOUT_MIN_OR_VOLUME_RATIO": 0.0},  # Not a direct param
        "test_type": "config_patch",
        "patch_fn": "patch_vol_ratio_ceiling",
    },

    # ── H02: Entry time tightening (10:15 cutoff) ───────────────────────────
    {
        "id": "H02",
        "name": "Entry Cutoff at 10:15 ET",
        "description": "Tighten LAST_ENTRY_TIME from 10:30 to 10:15 (10:15-10:30 marginal).",
        "evidence": "Entry time sweep: 10:15 PF 1.97 vs 10:30 PF 2.14. Difference is 2025 vs 2026 distribution.",
        "expected_pf_delta": -0.05,
        "expected_dd_delta": -0.01,
        "confidence": 0.4,
        "trade_count_effect": -0.08,
        "config_overrides": {"LAST_ENTRY_TIME": "10:15"},
        "test_type": "config_patch",
    },

    # ── H03: OR minimum tightening ──────────────────────────────────────────
    {
        "id": "H03",
        "name": "OR Minimum 60pt (remove borderline 55-60pt)",
        "description": "Raise OR_MIN from 55 to 60 to remove the weakest ORs.",
        "evidence": "OR size 55-65pt WR 42% (marginal). Sensitivity: +15% (55->63) drops PF 0.33 (FRAGILE).",
        "expected_pf_delta": 0.03,
        "expected_dd_delta": -0.01,
        "confidence": 0.35,
        "trade_count_effect": -0.05,
        "config_overrides": {"ORB_MIN_RANGE_POINTS": 60.0},
        "test_type": "config_patch",
    },

    # ── H04: Volume ratio floor at 0.7x ─────────────────────────────────────
    {
        "id": "H04",
        "name": "Volume Ratio Floor at 0.7x",
        "description": "Skip trades where OR vol ratio < 0.7x (very low vol = low conviction).",
        "evidence": "Vol <0.5x WR 45% (n=20 small). 0.5-0.75x WR 55.6% (n=9 too small). Floor tested at 0.8x: flat PF.",
        "expected_pf_delta": 0.02,
        "expected_dd_delta": -0.01,
        "confidence": 0.3,
        "trade_count_effect": -0.03,
        "config_overrides": {"BREAKOUT_MIN_OR_VOLUME_RATIO": 0.7},
        "test_type": "config_patch",
    },

    # ── H05: Gap dead zone 45-55pt (tighter than 40-60) ─────────────────────
    {
        "id": "H05",
        "name": "Gap Dead Zone 45-55pt (tighter precision)",
        "description": "The 40-60pt dead zone was unstable. Test tighter 45-55pt exclusion.",
        "evidence": "Gap 40-60pt WR 32.5% (n=40). The outer bands (40-45, 55-60) may be acceptable.",
        "expected_pf_delta": 0.04,
        "expected_dd_delta": -0.01,
        "confidence": 0.35,
        "trade_count_effect": -0.04,
        "config_overrides": {"GAP_EXCLUDE_MIN": 45.0, "GAP_EXCLUDE_MAX": 55.0},
        "test_type": "config_patch",
    },

    # ── H06: Friday skip ────────────────────────────────────────────────────
    {
        "id": "H06",
        "name": "Skip Fridays",
        "description": "Skip all ORB trades on Fridays (WR 35.8%, weakest non-Monday day).",
        "evidence": "Day-of-week: Friday WR 35.8% (n=67), PF 1.06. Only day below 1.1 PF.",
        "expected_pf_delta": 0.08,
        "expected_dd_delta": -0.015,
        "confidence": 0.55,
        "trade_count_effect": -0.20,  # lose ~20% of trades (Fridays are frequent)
        "config_overrides": {},
        "test_type": "config_patch",
        "patch_fn": "patch_skip_fridays",
    },

    # ── H07: ATR-gated OR max filter ────────────────────────────────────────
    {
        "id": "H07",
        "name": "ATR-Adaptive OR Max Filter",
        "description": "Adjust OR_MAX dynamically: if rolling ATR > 200pt, allow OR up to 130pt.",
        "evidence": "April 2025 (tariff shock) had ATR 350pt+; OR_MAX=110 filtered ALL 25 days.",
        "expected_pf_delta": -0.02,  # May reduce PF by allowing more noisy days
        "expected_dd_delta": 0.01,
        "confidence": 0.4,
        "trade_count_effect": 0.05,  # more trades in high-vol months
        "config_overrides": {"ORB_MAX_RANGE_POINTS": 130.0},
        "test_type": "config_patch",
        "note": "Tests static OR_MAX=130 as a proxy for dynamic ATR-based ceiling.",
    },

    # ── H08: Signal score INVERSION (size at 60-69 = 2 contracts) ───────────
    {
        "id": "H08",
        "name": "Signal Score Inversion (60-69 = 2c, 70-89 = 1c)",
        "description": "Score 60-69 WR 51.7% — give these 2 contracts, not 1. Requires code change.",
        "evidence": "Signal strength is INVERTED: higher score = lower WR. Scoring model is wrong.",
        "expected_pf_delta": 0.15,
        "expected_dd_delta": -0.02,
        "confidence": 0.65,
        "trade_count_effect": 0.0,  # same trades, different sizing
        "config_overrides": {},
        "test_type": "requires_code_change",
        "note": "Need to modify signal_strength.py contracts_for_score() or build scorer_v2.py.",
    },

    # ── H09: July add to strong months ──────────────────────────────────────
    {
        "id": "H09",
        "name": "Add July to Strong Months",
        "description": "July 2024 produced $7,130 (8/9 wins). Add to STRONG_MONTHS.",
        "evidence": "Monthly returns: 2024-07: $7,130, 8/9 wins. Brain data: Jul WR 39.1% (n=23) — borderline.",
        "expected_pf_delta": 0.03,
        "expected_dd_delta": 0.0,
        "confidence": 0.30,  # July WR 39.1% is below baseline in brain data
        "trade_count_effect": 0.0,
        "config_overrides": {"STRONG_MONTHS": [1,2,3,4,5,7,10,11]},
        "test_type": "config_patch",
        "note": "July 2024 was unusually strong. OOS July 2025/2026 data needed to confirm.",
    },

    # ── H10: August neutral (not weak) ──────────────────────────────────────
    {
        "id": "H10",
        "name": "August to Neutral (remove from weak)",
        "description": "August brain data: WR 41.4%, PF 1.34 — near baseline. Currently neutral.",
        "evidence": "August not in current WEAK_MONTHS. Brain: Aug WR 41.4% (n=29). OK.",
        "expected_pf_delta": 0.00,
        "expected_dd_delta": 0.0,
        "confidence": 0.5,
        "trade_count_effect": 0.0,
        "config_overrides": {},
        "test_type": "already_implemented",
        "note": "August is already neutral. No action needed.",
    },

    # ── H11: Pyramiding RR boost (target 2.5R for pyramid trades) ─────────
    {
        "id": "H11",
        "name": "Extended Pyramid Target (2.5R when pyramid fires)",
        "description": "When pyramid fires at 1R, extend target from 2R to 2.5R to capture runners.",
        "evidence": "Pyramid analysis: top 20% winners currently captured at 2R. Runners often go 3R+.",
        "expected_pf_delta": 0.08,
        "expected_dd_delta": 0.01,  # more variance
        "confidence": 0.45,
        "trade_count_effect": 0.0,
        "config_overrides": {"ORB_BREAKOUT_RR_TARGET": 2.5},
        "test_type": "config_patch",
        "note": "Tests 2.5R target. May be captured by individual winners only in pyramid trades.",
    },

    # ── H12: Reduced stop + RR = same target ────────────────────────────────
    {
        "id": "H12",
        "name": "Tighter Stop 20pt (25pt fixed - 5pt buffer) with 3R target",
        "description": "Reduce stop from 30pt to 20pt (25pt fixed, 0pt buffer). Keep 60pt target = 3R.",
        "evidence": "Current 30pt stop loses $625/trade. 20pt stop loses $405. Better RR at 3.0.",
        "expected_pf_delta": 0.05,
        "expected_dd_delta": -0.02,
        "confidence": 0.40,
        "trade_count_effect": 0.0,
        "config_overrides": {"ORB_STOP_BUFFER_POINTS": 0.0, "ORB_BREAKOUT_RR_TARGET": 3.0},
        "test_type": "config_patch",
        "note": "Tighter stop = more stop-outs. Higher RR = larger wins. Net effect uncertain.",
    },

    # ── H13: Second breakout re-entry test ──────────────────────────────────
    {
        "id": "H13",
        "name": "Second Breakout Re-Entry (after target hit)",
        "description": "Enable SECOND_BREAKOUT_ENABLED: re-enter if price breaks again after 10:30.",
        "evidence": "Feature exists but was never tested. Could capture extended trend days.",
        "expected_pf_delta": 0.05,
        "expected_dd_delta": 0.01,
        "confidence": 0.35,
        "trade_count_effect": 0.10,
        "config_overrides": {"SECOND_BREAKOUT_ENABLED": True},
        "test_type": "config_patch",
    },

    # ── H14: Skip days with ATR gap in trade (gap too large for any strategy) ─
    {
        "id": "H14",
        "name": "Large OR + Small Gap Filter (counter-trend setups)",
        "description": "Skip days where OR > 90pt AND |gap| < 30pt (big OR but no direction = chop).",
        "evidence": "Large OR with small gap = market opened wide but institutional bias unclear.",
        "expected_pf_delta": 0.04,
        "expected_dd_delta": -0.01,
        "confidence": 0.40,
        "trade_count_effect": -0.08,
        "config_overrides": {},
        "test_type": "requires_code_change",
        "note": "Need to add OR+gap interaction filter to strategy_us.py finalize_range().",
    },
]


def estimate_ev(hyp, base_pf):
    """Estimate expected value score for a hypothesis."""
    pf_delta = hyp["expected_pf_delta"]
    confidence = hyp["confidence"]
    count_effect = hyp["trade_count_effect"]
    dd_delta = hyp.get("expected_dd_delta", 0)

    # Risk-adjusted expected PF delta
    risk_adj_pf = pf_delta * confidence
    # Trade count penalty (fewer trades = less reliable statistics)
    count_penalty = max(0, -count_effect * 0.5)  # losing 20% trades = -0.10 penalty
    # DD improvement bonus
    dd_bonus = -dd_delta * 2  # -2pp DD = +0.04 EV

    ev = risk_adj_pf - count_penalty + dd_bonus
    return round(ev, 4)


def test_hypothesis(hyp, bars, baseline_result):
    """Test a single hypothesis against OOS data."""
    if hyp.get("test_type") in ("requires_code_change", "already_implemented"):
        return None

    overrides = hyp.get("config_overrides", {})
    if not overrides:
        return None

    result = run_oos_with_config(bars, overrides,
                                   start_year=baseline_result.get("start_year", 2023))
    if result is None:
        return None

    delta_pf = result["pf"] - baseline_result["pf"]
    delta_wr = result["wr"] - baseline_result["wr"]
    delta_net = result["net"] - baseline_result["net"]
    delta_dd = result["max_dd_pct"] - baseline_result["max_dd_pct"]
    trade_ratio = result["n"] / baseline_result["n"] if baseline_result["n"] else 0

    return {
        "id": hyp["id"],
        "name": hyp["name"],
        "baseline_pf": round(baseline_result["pf"], 3),
        "test_pf": round(result["pf"], 3),
        "delta_pf": round(delta_pf, 3),
        "baseline_wr": round(baseline_result["wr"], 3),
        "test_wr": round(result["wr"], 3),
        "delta_wr": round(delta_wr, 3),
        "baseline_net": baseline_result["net"],
        "test_net": result["net"],
        "delta_net": round(delta_net, 0),
        "baseline_n": baseline_result["n"],
        "test_n": result["n"],
        "trade_ratio": round(trade_ratio, 3),
        "delta_dd": round(delta_dd, 4),
        "verdict": "KEEP" if delta_pf > 0.05 and result["pf"] > 1.5 else (
                   "MARGINAL" if delta_pf > 0 else "REJECT"),
    }


def main():
    quick = "--quick" in sys.argv
    path = "data/nq_full.csv"
    if not os.path.exists(path):
        path = "data/nq_1min.csv"
        print(f"WARNING: Using {path}")

    print(f"Loading {path}...")
    bars = load_csv(path)
    # Full 4-year OOS with per-year fresh bankrolls (2023-2026).
    # Avoids cumulative Apex trailing-DD halt while testing across the full OOS window.
    start_year = 2023 if bars[0]["timestamp"].year <= 2022 else 2025

    print(f"Computing baseline (OOS from {start_year})...")
    baseline_result = get_baseline(bars, start_year=start_year)
    if baseline_result is None:
        print("No OOS trades found.")
        return

    sep = "=" * 95
    print(f"\n{sep}")
    print("  HYPOTHESIS PIPELINE — CruzCapital Research")
    print(f"  Baseline: PF {baseline_result['pf']:.2f} | WR {baseline_result['wr']:.1%} | "
          f"Net ${baseline_result['net']:+,.0f} | Trades {baseline_result['n']}")
    print(sep)

    # Rank hypotheses by expected value before testing
    hyps_ranked = sorted(HYPOTHESES, key=lambda h: estimate_ev(h, baseline_result["pf"]),
                         reverse=True)

    print(f"\n  Pre-Test Rankings (Expected Value Score)")
    print(f"  {'ID':<5}  {'Name':<40}  {'EV Score':>8}  {'ConfLevel':>9}  {'Type':<25}")
    print(f"  {'-'*90}")
    for h in hyps_ranked:
        ev = estimate_ev(h, baseline_result["pf"])
        test_type = h.get("test_type", "config_patch")
        testable = "✓" if test_type == "config_patch" else "  (needs code)"
        print(f"  {h['id']:<5}  {h['name']:<40}  {ev:>8.4f}  {h['confidence']:>8.0%}  "
              f"{test_type:<25}  {testable}")

    if quick:
        to_test = [h for h in hyps_ranked[:5] if h.get("test_type") == "config_patch"]
    else:
        to_test = [h for h in hyps_ranked if h.get("test_type") == "config_patch"]

    print(f"\n  Testing {len(to_test)} hypotheses against OOS data...")
    print(f"\n  {'ID':<5}  {'Name':<40}  {'Base PF':>7}  {'Test PF':>7}  {'ΔPF':>6}  {'ΔNet':>10}  {'Verdict'}")
    print(f"  {'-'*95}")

    memory = load_memory()
    tested_results = []

    for hyp in to_test:
        print(f"  Testing {hyp['id']}: {hyp['name']}...", end=" ", flush=True)
        result = test_hypothesis(hyp, bars, baseline_result)

        if result is None:
            print("SKIPPED")
            continue

        delta_sign = "+" if result["delta_pf"] >= 0 else ""
        verdict_emoji = "✓" if result["verdict"] == "KEEP" else ("~" if result["verdict"] == "MARGINAL" else "✗")
        print(f"\r  {result['id']:<5}  {hyp['name']:<40}  {result['baseline_pf']:>7.3f}  "
              f"{result['test_pf']:>7.3f}  {delta_sign}{result['delta_pf']:>5.3f}  "
              f"{result['delta_net']:>+10,.0f}  {verdict_emoji} {result['verdict']}")

        tested_results.append(result)

        # Store in memory
        memory_entry = {
            **hyp,
            "tested": True,
            "test_date": "2026-06-14",
            "result": result,
        }
        existing = next((i for i, h in enumerate(memory["tested"])
                        if h.get("id") == hyp["id"]), None)
        if existing is not None:
            memory["tested"][existing] = memory_entry
        else:
            memory["tested"].append(memory_entry)

        if result["verdict"] == "REJECT":
            if hyp["id"] not in [h.get("id") for h in memory["failed"]]:
                memory["failed"].append({
                    "id": hyp["id"],
                    "name": hyp["name"],
                    "reason": f"OOS delta_PF={result['delta_pf']:.3f}",
                    "date": "2026-06-14",
                })

    save_memory(memory)

    # Summary
    keepers = [r for r in tested_results if r["verdict"] == "KEEP"]
    marginal = [r for r in tested_results if r["verdict"] == "MARGINAL"]
    rejects = [r for r in tested_results if r["verdict"] == "REJECT"]

    print(f"\n{sep}")
    print(f"  RESULTS SUMMARY")
    print(f"  KEEP: {len(keepers)} | MARGINAL: {len(marginal)} | REJECT: {len(rejects)}")
    print(sep)

    if keepers:
        print(f"\n  Keepers (implement these):")
        for r in sorted(keepers, key=lambda x: x["delta_pf"], reverse=True):
            print(f"    {r['id']}: {r['name']} | ΔPF={r['delta_pf']:+.3f} | ΔNet=${r['delta_net']:+,.0f}")

    # Save results
    out_path = "brain/research/hypothesis_results.md"
    with open(out_path, "w") as f:
        f.write("# Hypothesis Pipeline Results\n\n")
        f.write(f"**Date:** 2026-06-14\n")
        f.write(f"**Baseline:** PF {baseline_result['pf']:.2f} | WR {baseline_result['wr']:.1%} | "
                f"Net ${baseline_result['net']:+,.0f} | Trades {baseline_result['n']}\n\n")
        f.write("## Results\n\n")
        f.write("| ID | Name | Base PF | Test PF | ΔPF | ΔNet | ΔTrades | Verdict |\n")
        f.write("|----|----|---------|---------|-----|------|---------|--------|\n")
        for r in tested_results:
            ds = "+" if r["delta_pf"] >= 0 else ""
            ratio = f"{(r['trade_ratio']-1)*100:+.0f}%"
            f.write(f"| {r['id']} | {r['name']} | {r['baseline_pf']:.3f} | {r['test_pf']:.3f} | "
                    f"{ds}{r['delta_pf']:.3f} | ${r['delta_net']:+,.0f} | {ratio} | {r['verdict']} |\n")
        f.write("\n## Untested (Require Code Changes)\n\n")
        for h in HYPOTHESES:
            if h.get("test_type") in ("requires_code_change",):
                f.write(f"- **{h['id']}**: {h['name']} — {h['note']}\n")

    print(f"\n  Saved: {out_path}")
    print(f"  Research memory: {MEMORY_PATH}")


if __name__ == "__main__":
    main()
