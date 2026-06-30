"""
brain/research/research_memory.py

Persistent research memory system.
Every discovery is stored permanently. Every failed strategy is documented.
No knowledge is ever lost.

This module:
  - Loads/saves the central research memory (brain/research/research_memory.json)
  - Provides an API for adding discoveries, failures, and hypotheses
  - Generates a human-readable summary report

Usage:
    python3 brain/research/research_memory.py            # show current memory
    python3 brain/research/research_memory.py --summary  # write summary report
"""

import sys
import os
import json
from datetime import datetime

MEMORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research_memory.json")
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research_log.md")


def load():
    if os.path.exists(MEMORY_PATH):
        with open(MEMORY_PATH) as f:
            return json.load(f)
    return {
        "version": "1.0",
        "last_updated": "",
        "discoveries": [],
        "failed_strategies": [],
        "tested_hypotheses": [],
        "confirmed_edges": [],
        "config_history": [],
        "data_quality_notes": [],
    }


def save(mem):
    mem["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
    with open(MEMORY_PATH, "w") as f:
        json.dump(mem, f, indent=2)


def add_discovery(mem, title, finding, evidence, action=None, date=None):
    """Record a new research finding."""
    entry = {
        "title": title,
        "finding": finding,
        "evidence": evidence,
        "action": action or "None — documented only",
        "date": date or datetime.now().strftime("%Y-%m-%d"),
    }
    existing_titles = [d["title"] for d in mem["discoveries"]]
    if title not in existing_titles:
        mem["discoveries"].append(entry)
    else:
        idx = existing_titles.index(title)
        mem["discoveries"][idx] = entry
    return mem


def add_failed_strategy(mem, name, config_tested, results, reason_failed, date=None):
    """Record a strategy that was tested and rejected."""
    entry = {
        "name": name,
        "config": config_tested,
        "results": results,
        "reason_failed": reason_failed,
        "date": date or datetime.now().strftime("%Y-%m-%d"),
    }
    existing = [f["name"] for f in mem["failed_strategies"]]
    if name not in existing:
        mem["failed_strategies"].append(entry)
    return mem


def add_confirmed_edge(mem, name, edge_type, evidence, parameters, oos_pf, oos_wr, trades, date=None):
    """Record a confirmed, robust trading edge."""
    entry = {
        "name": name,
        "edge_type": edge_type,
        "evidence": evidence,
        "parameters": parameters,
        "oos_pf": oos_pf,
        "oos_wr": oos_wr,
        "oos_trades": trades,
        "date": date or datetime.now().strftime("%Y-%m-%d"),
    }
    existing = [e["name"] for e in mem["confirmed_edges"]]
    if name not in existing:
        mem["confirmed_edges"].append(entry)
    else:
        idx = existing.index(name)
        mem["confirmed_edges"][idx] = entry
    return mem


def initialize_memory_from_audit():
    """Populate memory from the known state of the project at time of audit."""
    mem = load()

    # Confirmed edges
    mem = add_confirmed_edge(
        mem,
        name="NQ ORB Breakout v7",
        edge_type="Opening Range Breakout — trend-following intraday",
        evidence="OOS 2025-26: PF 2.14, WR 47.2%, 72 trades. Improving YoY. MC pass 93.2%.",
        parameters={
            "or_range": "55-110pt",
            "breakout_buffer": "4pt",
            "gap_filter": "20pt",
            "stop": "30pt",
            "target": "60pt (2R)",
            "entry_window": "9:45-10:30 ET",
            "skip_mondays": True,
            "weak_months": [6, 9, 12],
            "strong_months": [1, 2, 3, 4, 5, 10, 11],
        },
        oos_pf=2.14,
        oos_wr=0.472,
        trades=72,
        date="2026-06-14",
    )

    mem = add_confirmed_edge(
        mem,
        name="Asia Gap Continuation (CME halt)",
        edge_type="Gap continuation — overnight institutional positioning",
        evidence="OOS 2024-26: PF 1.80, WR 56%, 77 trades. Improving YoY: 1.42→1.82→2.31.",
        parameters={
            "halt_gap_range": "30-80pt",
            "entry_time": "18:15 ET",
            "stop": "15pt",
            "target": "22.5pt (1.5R)",
            "hard_exit": "21:00 ET",
            "skip_thursdays": True,
            "weak_months": [8, 11],
        },
        oos_pf=1.80,
        oos_wr=0.56,
        trades=77,
        date="2026-06-14",
    )

    # Failed strategies
    failed_list = [
        ("PM VWAP Continuation", "12:00-14:30 ET, VWAP touch + direction bias",
         {"oos_net": -11500, "oos_pf": 0.4}, "Strong negative OOS P&L, cascades halts"),
        ("Gap Fill Strategy", "Large gap days, fade toward prior close",
         {"oos_net": -4400, "oos_pf": 0.8}, "Consistent OOS drag across all years"),
        ("VWAP Pullback (AM)", "Second trade after ORB, VWAP touch",
         {"oos_net": -1400, "oos_pf": 1.46}, "Redundant to ORB, insufficient independent edge"),
        ("London/NY Overlap 8am-9:25am", "Range classification, entry at 9:05",
         {"oos_net": -2900, "oos_pf": 1.14}, "Below PF threshold, unstable YoY"),
        ("London Pre-Market 3am-5am", "ORB at 3:00-3:15 ET, exit 5am",
         {"best_oos_pf": 1.17, "volume_pct_of_us": 0.042},
         "Max OOS PF 1.17 across all configs. Volume 4.2% of US → 4-10 tick live slippage."),
    ]
    for name, cfg, results, reason in failed_list:
        mem = add_failed_strategy(mem, name, cfg, results, reason, "2026-06-14")

    # Key discoveries
    discoveries = [
        (
            "Signal Strength Scorer is Inverted",
            "The signal strength scorer (0-100) is inversely correlated with actual trade quality. "
            "Score 60-69: WR 51.7%, PF 2.03. Score 70-79: WR 36.5%, PF 1.09. "
            "The system currently sizes UP on exactly the wrong trades.",
            "trade_memory.csv (286 trades, 2022-2026), insights.md analysis",
            "Recalibrate scorer. Short-term: invert sizing (60-69=2c, 70-89=1c). "
            "Long-term: rebuild scorer from OOS-only component testing.",
        ),
        (
            "Monthly-Independent Simulation Bias",
            "The walk_forward.py and monte_carlo.py monthly-independent mode starts each month "
            "with prev_close=None, blocking all entries on day 1 of each month (neutral gap). "
            "April 2025: 0 trades in monthly mode vs 1 trade in continuous mode. April 2025 "
            "was the Trump tariff shock month (ORs 140-352pt on most days; only 4 days tradeable).",
            "Direct comparison: Backtester() on April 2025 alone = 0 trades; continuous from "
            "Jan 2025 = 1 trade on April 1.",
            "Fix monthly-independent simulation by seeding prev_close from prior month's last bar.",
        ),
        (
            "April 2025 Tariff Shock — Zero Trading Month",
            "April 2025 had 25 trading days but 0 qualifying ORB setups in monthly-independent mode. "
            "Root cause: Trump 'Liberation Day' tariffs (April 2) caused NQ OR of 140-352pt "
            "on 17/21 non-Monday days. OR_MAX filter of 110pt correctly excluded these chaos days. "
            "Only 4 days (Apr 1, 16, 25, 29) had OR in 55-110pt range.",
            "bar count analysis: 28,977 bars, 25 trading days. OR analysis per day in April 2025.",
            "No action needed — OR_MAX filter working correctly. Document as expected behavior "
            "during high-volatility events.",
        ),
        (
            "Friday Day-of-Week is Weakest Non-Monday",
            "Friday WR 35.8% (n=67), PF 1.06 — the weakest non-Monday day. "
            "Monday is skipped. Friday is borderline: PF 1.06 barely above 1.0. "
            "Skipping Fridays would remove ~20% of trades but improve average quality.",
            "insights.md Day of Week table (286 trades, 2022-2026)",
            "Test Friday skip hypothesis. Expected: +0.08 PF improvement, -20% trade count.",
        ),
        (
            "Gap Dead Zone 40-60pt is Unstable Across Years",
            "Gap 40-60pt has WR 32.5% (n=40) in aggregate, but excluding these gaps was tested "
            "and rejected: 2025 OOS improved (PF 2.22) but 2026 OOS collapsed (PF 1.17). "
            "The dead zone pattern is real but not consistent across years.",
            "improvement_results.md Improvement #4. Two-year OOS instability confirmed.",
            "Do not apply hard exclusion. Consider single-contract sizing for gap 40-60pt days.",
        ),
        (
            "Consecutive Losing Days Rule is Optimal at 2",
            "Testing MAX_CONSECUTIVE_LOSING_DAYS at 3 (PF 1.63) and 999 (PF 1.68) both "
            "performed worse than the current setting of 2 (PF 1.73). The bankroll protection "
            "of sitting out after 2 losing days outweighs the marginal WR improvement from trading.",
            "improvement_results.md Improvement #5",
            "Keep MAX_CONSECUTIVE_LOSING_DAYS=2. Do not change.",
        ),
        (
            "Volume Ratio Gate: Floor Works, Ceiling Doesn't",
            "Vol ratio floor at 0.8x: flat PF (1.73). Vol ratio floor at 1.0x: worse (-0.02). "
            "BUT: vol ratio 1.5x+ WR 27.3% (n=11). A CEILING (exclude high vol) may work "
            "even though a floor didn't. High-volume OR bars are often news/reversal events.",
            "improvement_results.md Improvement #7, trade_memory.csv volume analysis",
            "Test: exclude trades where vol_ratio > 1.5x. Hypothesis H01 in pipeline.",
        ),
        (
            "Regime Detector Fade Mode is Dead Code",
            "REGIME_BREAKOUT_THRESHOLD = REGIME_FADE_THRESHOLD = 0.18. Equal thresholds mean "
            "'fade' is never returned by classify(). The fade strategy code path is permanently "
            "inaccessible in current configuration.",
            "regime.py classify() function analysis",
            "Fix threshold asymmetry: breakout >= 0.25, fade < 0.15, skip < 0.08. "
            "Then test fade strategy OOS before enabling.",
        ),
    ]
    for title, finding, evidence, action in discoveries:
        mem = add_discovery(mem, title, finding, evidence, action, "2026-06-14")

    return mem


def generate_report(mem):
    lines = []
    lines.append("# CruzCapital Research Log\n\n")
    lines.append(f"**Last Updated:** {mem.get('last_updated', '2026-06-14')}\n\n")
    lines.append("---\n\n")

    lines.append("## Confirmed Edges\n\n")
    for e in mem.get("confirmed_edges", []):
        lines.append(f"### {e['name']}\n\n")
        lines.append(f"- **Type:** {e['edge_type']}\n")
        lines.append(f"- **OOS PF:** {e['oos_pf']} | WR: {e['oos_wr']:.1%} | Trades: {e['oos_trades']}\n")
        lines.append(f"- **Evidence:** {e['evidence']}\n")
        lines.append(f"- **Key Parameters:** {json.dumps(e['parameters'])}\n\n")

    lines.append("---\n\n## Failed Strategies (Do Not Revisit Without New Evidence)\n\n")
    lines.append("| Strategy | Config | Results | Why Failed |\n")
    lines.append("|----------|--------|---------|------------|\n")
    for f in mem.get("failed_strategies", []):
        lines.append(f"| {f['name']} | {str(f['config'])[:40]} | "
                     f"{str(f['results'])[:30]} | {f['reason_failed'][:50]} |\n")

    lines.append("\n---\n\n## Key Research Discoveries\n\n")
    for d in mem.get("discoveries", []):
        lines.append(f"### {d['title']}\n\n")
        lines.append(f"**Finding:** {d['finding']}\n\n")
        lines.append(f"**Evidence:** {d['evidence']}\n\n")
        lines.append(f"**Action:** {d['action']}\n\n")

    lines.append("---\n\n## Tested Hypotheses\n\n")
    tested = mem.get("tested", mem.get("tested_hypotheses", []))
    if tested:
        lines.append("| ID | Name | OOS Result | Verdict | Date |\n")
        lines.append("|----|------|------------|---------|------|\n")
        for h in tested:
            r = h.get("result", {})
            if r:
                lines.append(f"| {h.get('id','?')} | {h.get('name','?')} | "
                              f"PF {r.get('test_pf', '?')} (Δ{r.get('delta_pf','?')}) | "
                              f"{r.get('verdict','?')} | {h.get('test_date','?')} |\n")
    else:
        lines.append("_No hypotheses tested yet. Run hypothesis_pipeline.py._\n")

    with open(REPORT_PATH, "w") as f:
        f.writelines(lines)
    print(f"Report saved: {REPORT_PATH}")


def main():
    mem = initialize_memory_from_audit()
    save(mem)
    print(f"Research memory saved: {MEMORY_PATH}")
    print(f"  Confirmed edges: {len(mem.get('confirmed_edges', []))}")
    print(f"  Failed strategies: {len(mem.get('failed_strategies', []))}")
    print(f"  Discoveries: {len(mem.get('discoveries', []))}")
    generate_report(mem)


if __name__ == "__main__":
    main()
