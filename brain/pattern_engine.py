"""
brain/pattern_engine.py
Analyzes trade_memory.csv and finds statistically significant patterns.
Only reports buckets with >= 20 trades.

Usage: python3 brain/pattern_engine.py
Writes: brain/insights.md
"""
import csv
import os
import math
from collections import defaultdict

MEMORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_memory.csv")
INSIGHTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "insights.md")
MIN_SAMPLE = 20


def load_trades():
    with open(MEMORY_PATH) as f:
        return list(csv.DictReader(f))


def wr(trades):
    if not trades:
        return 0, 0
    wins = sum(1 for t in trades if int(t["win_loss"]))
    return wins / len(trades), len(trades)


def pf(trades):
    gw = sum(float(t["pnl"]) for t in trades if float(t["pnl"]) > 0)
    gl = abs(sum(float(t["pnl"]) for t in trades if float(t["pnl"]) <= 0))
    return gw / gl if gl else float("inf")


def wilson_lower(w, n, z=1.645):
    """Wilson confidence interval lower bound (90% CI)."""
    if n == 0:
        return 0.0
    p = w / n
    return (p + z*z/(2*n) - z*math.sqrt((p*(1-p)+z*z/(4*n))/n)) / (1+z*z/n)


def bucket_analysis(trades, key_fn, label, buckets):
    """Analyze WR by bucket. Returns list of (bucket_label, wr, n, pf_val, lower_ci)."""
    groups = defaultdict(list)
    for t in trades:
        v = key_fn(t)
        for bname, (lo, hi) in buckets.items():
            if lo <= v < hi:
                groups[bname].append(t)
                break
    results = []
    for bname, _ in buckets.items():
        g = groups[bname]
        if len(g) >= MIN_SAMPLE:
            w, n = wr(g)
            results.append((bname, w, n, pf(g), wilson_lower(w, n)))
    return results


def simple_group(trades, key_fn):
    """Group trades by string key."""
    groups = defaultdict(list)
    for t in trades:
        groups[key_fn(t)].append(t)
    return groups


def format_bucket_table(results, baseline_wr):
    """Format analysis results as a markdown table with delta vs baseline."""
    header = f"| Bucket | WR | N | PF | vs Baseline | Lower CI |\n|--------|----|----|-----|------------|----------|\n"
    rows = ""
    for bname, w, n, pf_v, lci in sorted(results, key=lambda x: x[1], reverse=True):
        delta = w - baseline_wr
        sign  = "+" if delta >= 0 else ""
        rows += (f"| {bname} | {w:.1%} | {n} | {pf_v:.2f} |"
                 f" {sign}{delta*100:.1f}pp | {lci:.1%} |\n")
    return header + rows if rows else "(insufficient data)\n"


def analyze(trades):
    overall_wr, overall_n = wr(trades)
    overall_pf = pf(trades)

    results = {}

    # 1. Signal strength buckets
    ss_buckets = {"60-69": (60, 70), "70-79": (70, 80),
                  "80-89": (80, 90), "90-100": (90, 101)}
    results["signal_strength"] = bucket_analysis(
        trades, lambda t: int(t["signal_strength"]), "Signal Strength", ss_buckets)

    # 2. OR size buckets (points)
    or_buckets = {"<10pt": (0, 10), "10-20pt": (10, 20), "20-40pt": (20, 40),
                  "40-70pt": (40, 70), "70-130pt": (70, 130)}
    results["or_size"] = bucket_analysis(
        trades, lambda t: float(t["or_size"]), "OR Size", or_buckets)

    # 3. Gap size buckets
    gap_buckets = {"0-20pt": (0, 20), "20-40pt": (20, 40),
                   "40-60pt": (40, 60), "60pt+": (60, 9999)}
    results["gap_size"] = bucket_analysis(
        trades, lambda t: abs(float(t["gap_points"])), "Gap Size", gap_buckets)

    # 4. Day of week
    days = {"Monday": 0, "Tuesday": 0, "Wednesday": 0, "Thursday": 0, "Friday": 0}
    day_groups = simple_group(trades, lambda t: t["day_of_week"])
    results["day_of_week"] = [
        (d, *wr(day_groups[d]), pf(day_groups[d]),
         wilson_lower(wr(day_groups[d])[0], wr(day_groups[d])[1]))
        for d in ["Monday","Tuesday","Wednesday","Thursday","Friday"]
        if len(day_groups[d]) >= MIN_SAMPLE
    ]

    # 5. Month
    month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    month_groups = simple_group(trades, lambda t: int(t["month"]))
    results["month"] = []
    for m in range(1, 13):
        g = month_groups.get(m, [])
        if len(g) >= MIN_SAMPLE:
            w, n = wr(g)
            results["month"].append((month_names[m], w, n, pf(g), wilson_lower(w, n)))

    # 6. Consecutive losses before entry
    cl_buckets = {"0 prior losses": (0, 1), "1 loss": (1, 2),
                  "2 losses": (2, 3), "3+ losses": (3, 99)}
    results["consec_losses"] = bucket_analysis(
        trades, lambda t: int(t["consecutive_losses_before"]),
        "Consec Losses Before", cl_buckets)

    # 7. Entry time
    def time_bucket(t):
        h, m = map(int, t["entry_time"].split(":"))
        return h * 60 + m
    et_buckets = {"9:45-10:00": (575, 601), "10:00-10:30": (600, 631),
                  "10:30-11:00": (630, 661), "11:00-11:15": (660, 676)}
    results["entry_time"] = bucket_analysis(
        trades, time_bucket, "Entry Time", et_buckets)

    # 8. Prev day result
    pdr_groups = simple_group(trades, lambda t: t["prev_day_result"])
    results["prev_day"] = [
        (k, *wr(pdr_groups[k]), pf(pdr_groups[k]),
         wilson_lower(wr(pdr_groups[k])[0], wr(pdr_groups[k])[1]))
        for k in ["win", "loss", "none"]
        if len(pdr_groups.get(k, [])) >= MIN_SAMPLE
    ]

    # 9. Drawdown at entry
    dd_buckets = {"0-2% DD": (0, 2), "2-5% DD": (2, 5),
                  "5-10% DD": (5, 10), "10%+ DD": (10, 100)}
    results["drawdown"] = bucket_analysis(
        trades, lambda t: float(t["drawdown_at_entry"]), "DD at Entry", dd_buckets)

    return overall_wr, overall_n, overall_pf, results


def top_filters(results, baseline_wr):
    """Find top 3 filters that improve WR most and bottom 3 that hurt most."""
    all_buckets = []
    for category, buckets in results.items():
        if not buckets:
            continue
        for b in buckets:
            bname, w, n, pf_v, lci = b
            delta = w - baseline_wr
            all_buckets.append((category, bname, w, n, pf_v, lci, delta))

    all_buckets.sort(key=lambda x: x[6], reverse=True)
    top3    = all_buckets[:3]
    bottom3 = [b for b in all_buckets if b[6] < 0][-3:]
    return top3, bottom3


def generate_grade(baseline_wr, overall_pf, n_total, top3_delta):
    """Grade the edge consistency: A/B/C/D."""
    if overall_pf >= 1.6 and baseline_wr >= 0.44 and n_total >= 200:
        return "A", "Strong, consistent edge across 4+ years"
    if overall_pf >= 1.4 and baseline_wr >= 0.38 and n_total >= 150:
        return "B", "Solid edge with some regime sensitivity"
    if overall_pf >= 1.2 and baseline_wr >= 0.35:
        return "C", "Marginal edge -- needs tighter filters"
    return "D", "Insufficient edge -- revisit strategy"


def main():
    trades = load_trades()
    print(f"Loaded {len(trades)} trades from {MEMORY_PATH}")

    overall_wr, overall_n, overall_pf, results = analyze(trades)
    top3, bottom3 = top_filters(results, overall_wr)

    grade, grade_desc = generate_grade(overall_wr, overall_pf, overall_n,
                                        top3[0][6] if top3 else 0)

    # ── Print to console ──────────────────────────────────────────────────────
    print(f"\nOverall: WR {overall_wr:.1%} | PF {overall_pf:.2f} | {overall_n} trades")
    print(f"Grade: {grade} -- {grade_desc}")

    print("\nTop 3 WR-improving conditions:")
    for i, (cat, bname, w, n, pf_v, lci, delta) in enumerate(top3, 1):
        print(f"  {i}. [{cat}] {bname}: WR {w:.1%} (n={n}, +{delta*100:.1f}pp vs baseline)")

    print("\nBottom 3 WR-hurting conditions:")
    for i, (cat, bname, w, n, pf_v, lci, delta) in enumerate(bottom3, 1):
        print(f"  {i}. [{cat}] {bname}: WR {w:.1%} (n={n}, {delta*100:.1f}pp vs baseline)")

    # ── Write insights.md ─────────────────────────────────────────────────────
    lines = []
    lines.append("# Brain Insights Report\n")
    lines.append(f"**Trades analyzed**: {overall_n} (2022-2026, monthly-independent)  \n")
    lines.append(f"**Overall WR**: {overall_wr:.1%}  **PF**: {overall_pf:.2f}  \n")
    lines.append(f"**Edge Grade**: **{grade}** -- {grade_desc}  \n\n")

    lines.append("---\n\n## Top 3 Filters That Improve Win Rate\n\n")
    for i, (cat, bname, w, n, pf_v, lci, delta) in enumerate(top3, 1):
        lines.append(f"### {i}. [{cat.replace('_',' ').title()}] {bname}\n")
        lines.append(f"- Win rate: **{w:.1%}** (baseline {overall_wr:.1%}, "
                     f"**+{delta*100:.1f}pp improvement**)  \n")
        lines.append(f"- Sample: {n} trades | PF {pf_v:.2f} | "
                     f"90% CI lower bound: {lci:.1%}  \n\n")

    lines.append("---\n\n## Bottom 3 Conditions Where Strategy Consistently Loses\n\n")
    for i, (cat, bname, w, n, pf_v, lci, delta) in enumerate(bottom3, 1):
        lines.append(f"### {i}. [{cat.replace('_',' ').title()}] {bname}\n")
        lines.append(f"- Win rate: **{w:.1%}** (baseline {overall_wr:.1%}, "
                     f"**{delta*100:.1f}pp drag**)  \n")
        lines.append(f"- Sample: {n} trades | PF {pf_v:.2f} | "
                     f"90% CI lower bound: {lci:.1%}  \n\n")

    lines.append("---\n\n## Pattern Details by Category\n\n")
    section_labels = {
        "signal_strength": "Signal Strength",
        "or_size": "Opening Range Size",
        "gap_size": "Gap Size",
        "day_of_week": "Day of Week",
        "month": "Month",
        "consec_losses": "Consecutive Losses Before Entry",
        "entry_time": "Entry Time",
        "prev_day": "Previous Day Result",
        "drawdown": "Account Drawdown at Entry",
    }
    for key, label in section_labels.items():
        data = results.get(key, [])
        if not data:
            continue
        lines.append(f"### {label}\n\n")
        lines.append(format_bucket_table(data, overall_wr))
        lines.append("\n")

    lines.append("---\n\n## Recommended Config Changes\n\n")
    # Auto-suggest based on findings
    suggestions = []
    # Find the worst entry time
    for cat, bname, w, n, pf_v, lci, delta in bottom3:
        if cat == "entry_time" and delta < -0.05:
            suggestions.append(f"- Consider tightening `LAST_ENTRY_TIME` — "
                                f"trades in the **{bname}** window have WR {w:.1%} "
                                f"({delta*100:.1f}pp below baseline).")
        if cat == "or_size" and "pt" in bname and delta < -0.05:
            lo_hi = bname.replace("pt","").split("-")
            suggestions.append(f"- Consider tightening OR size filter: "
                                f"**{bname}** OR days drag WR by {-delta*100:.1f}pp.")
        if cat == "consec_losses" and delta < -0.05:
            suggestions.append(f"- After **{bname}** consecutive losses, "
                                f"WR drops {-delta*100:.1f}pp. Consider sitting out.")
        if cat == "month" and delta < -0.05:
            suggestions.append(f"- **{bname}** is a weak month (WR {w:.1%}). "
                                f"Consider adding to `WEAK_MONTHS`.")
    if not suggestions:
        suggestions.append("- No high-confidence config changes suggested from current data.")
    lines.extend(s + "  \n" for s in suggestions)

    lines.append("\n---\n\n## Overall Grade\n\n")
    lines.append(f"## Grade: **{grade}**\n\n{grade_desc}\n\n")
    lines.append(f"_Generated from {overall_n} trades across 2022-2026 NQ data._\n")

    with open(INSIGHTS_PATH, "w") as f:
        f.writelines(lines)
    print(f"\nWrote insights -> {INSIGHTS_PATH}")


if __name__ == "__main__":
    main()
