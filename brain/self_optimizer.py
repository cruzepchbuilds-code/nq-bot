"""
brain/self_optimizer.py
Reads insights.md and trade_memory.csv and suggests specific config.py
line changes based on data. PRINTS suggestions only -- never auto-applies.

Usage: python3 brain/self_optimizer.py
"""
import csv
import os
from collections import defaultdict

MEMORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_memory.csv")
MIN_N = 20
MIN_DELTA = 0.05   # minimum WR delta to suggest a change (5pp)


def load_trades():
    with open(MEMORY_PATH) as f:
        return list(csv.DictReader(f))


def wr_n(trades):
    if not trades:
        return 0, 0
    wins = sum(1 for t in trades if int(t["win_loss"]))
    return wins / len(trades), len(trades)


def pf(trades):
    gw = sum(float(t["pnl"]) for t in trades if float(t["pnl"]) > 0)
    gl = abs(sum(float(t["pnl"]) for t in trades if float(t["pnl"]) <= 0))
    return gw / gl if gl else float("inf")


def main():
    trades = load_trades()
    overall_wr, overall_n = wr_n(trades)
    overall_pf = pf(trades)

    suggestions = []
    sep = "=" * 70

    print(f"\n{sep}")
    print("  SELF-OPTIMIZER -- Config Suggestions (PRINT ONLY, never auto-apply)")
    print(f"{sep}")
    print(f"  Baseline: {overall_n} trades | WR {overall_wr:.1%} | PF {overall_pf:.2f}")
    print(f"{sep}\n")

    # ── 1. Entry time analysis ────────────────────────────────────────────────
    time_groups = defaultdict(list)
    for t in trades:
        h, m = map(int, t["entry_time"].split(":"))
        mins = h * 60 + m
        if mins < 600:
            time_groups["9:45-10:00"].append(t)
        elif mins < 630:
            time_groups["10:00-10:30"].append(t)
        elif mins < 660:
            time_groups["10:30-11:00"].append(t)
        elif mins < 676:
            time_groups["11:00-11:15"].append(t)

    print("  [1] Entry Time Analysis")
    print(f"  {'Window':<18} {'WR':>6} {'N':>4} {'PF':>5} {'Delta':>7}")
    print(f"  {'-'*45}")
    cutoff_suggestion = None
    for window in ["9:45-10:00", "10:00-10:30", "10:30-11:00", "11:00-11:15"]:
        g = time_groups[window]
        if g:
            w, n = wr_n(g)
            p = pf(g)
            delta = w - overall_wr
            sign = "+" if delta >= 0 else ""
            flag = " <-- WEAK" if w < 0.30 and n >= MIN_N else ""
            print(f"  {window:<18} {w:>5.1%} {n:>4} {p:>5.2f} {sign}{delta*100:>5.1f}pp{flag}")
            if w < 0.35 and n >= MIN_N and delta < -MIN_DELTA:
                if cutoff_suggestion is None:
                    # suggest cutting at the start of this window
                    prev_end = {"10:30-11:00": "10:30", "11:00-11:15": "11:00"}.get(window)
                    if prev_end:
                        cutoff_suggestion = (window, w, n, prev_end)

    if cutoff_suggestion:
        window, w, n, new_time = cutoff_suggestion
        suggestions.append({
            "priority": "HIGH",
            "config_line": f'LAST_ENTRY_TIME = "{new_time}"',
            "current":     f'LAST_ENTRY_TIME = "11:15"',
            "reason":      f"Entries in {window} window have WR {w:.1%} (n={n}), "
                           f"far below {overall_wr:.1%} baseline. "
                           f"Cutting at {new_time} removes the worst trades.",
            "note":        "Validate on OOS data before applying.",
        })

    # ── 2. Month analysis ─────────────────────────────────────────────────────
    month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    month_groups = defaultdict(list)
    for t in trades:
        month_groups[int(t["month"])].append(t)

    print(f"\n  [2] Month Analysis")
    print(f"  {'Month':<8} {'WR':>6} {'N':>4} {'PF':>5} {'Delta':>7}")
    print(f"  {'-'*35}")
    weak_months = []
    strong_months = []
    for m in range(1, 13):
        g = month_groups.get(m, [])
        if not g:
            continue
        w, n = wr_n(g)
        p = pf(g)
        delta = w - overall_wr
        sign = "+" if delta >= 0 else ""
        flag = " <-- VERY WEAK" if w < 0.30 and n >= MIN_N else ""
        print(f"  {month_names[m]:<8} {w:>5.1%} {n:>4} {p:>5.2f} {sign}{delta*100:>5.1f}pp{flag}")
        if n >= MIN_N:
            if delta < -MIN_DELTA:
                weak_months.append(m)
            elif delta > MIN_DELTA:
                strong_months.append(m)

    import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config
    current_weak   = sorted(config.WEAK_MONTHS)
    current_strong = sorted(config.STRONG_MONTHS)
    new_weak   = sorted(set(current_weak)   | set(weak_months))
    new_strong = sorted(set(current_strong) | set(strong_months))
    if new_weak != current_weak:
        suggestions.append({
            "priority": "MEDIUM",
            "config_line": f"WEAK_MONTHS   = {new_weak}",
            "current":     f"WEAK_MONTHS   = {current_weak}",
            "reason":      f"Months {[month_names[m] for m in weak_months]} showed "
                           f">5pp below-baseline WR with n>={MIN_N} trades.",
            "note":        "Validate on OOS before applying.",
        })
    if new_strong != current_strong:
        suggestions.append({
            "priority": "LOW",
            "config_line": f"STRONG_MONTHS = {new_strong}",
            "current":     f"STRONG_MONTHS = {current_strong}",
            "reason":      f"Months {[month_names[m] for m in strong_months]} showed "
                           f">5pp above-baseline WR with n>={MIN_N} trades.",
            "note":        "Strong months allow higher contract ceiling (minor impact).",
        })

    # ── 3. Consecutive losses analysis ────────────────────────────────────────
    cl_groups = defaultdict(list)
    for t in trades:
        cl_groups[min(int(t["consecutive_losses_before"]), 4)].append(t)

    print(f"\n  [3] Consecutive Losses Before Entry")
    print(f"  {'Prior Losses':<14} {'WR':>6} {'N':>4} {'PF':>5} {'Delta':>7}")
    print(f"  {'-'*40}")
    for cl in range(5):
        g = cl_groups.get(cl, [])
        if not g:
            continue
        w, n = wr_n(g)
        p = pf(g)
        delta = w - overall_wr
        sign = "+" if delta >= 0 else ""
        label = f"{cl} loss{'es' if cl != 1 else ''}" if cl < 4 else "4+ losses"
        print(f"  {label:<14} {w:>5.1%} {n:>4} {p:>5.2f} {sign}{delta*100:>5.1f}pp")
        if cl >= 2 and n >= MIN_N and delta < -MIN_DELTA:
            suggestions.append({
                "priority": "MEDIUM",
                "config_line": f"MAX_CONSECUTIVE_LOSING_DAYS = {cl}  # sit out after {cl} losing days",
                "current":     f"MAX_CONSECUTIVE_LOSING_DAYS = {config.MAX_CONSECUTIVE_LOSING_DAYS}",
                "reason":      f"After {cl} consecutive losses, WR drops to {w:.1%} "
                               f"({-delta*100:.1f}pp below baseline, n={n}). "
                               f"Sitting out one day may reduce variance.",
                "note":        "Already implemented -- check if current setting is optimal.",
            })

    # ── 4. OR size analysis ───────────────────────────────────────────────────
    or_groups = {"<10pt": [], "10-20pt": [], "20-40pt": [],
                 "40-70pt": [], "70-130pt": []}
    or_bounds = {"<10pt": (0, 10), "10-20pt": (10, 20), "20-40pt": (20, 40),
                 "40-70pt": (40, 70), "70-130pt": (70, 130)}
    for t in trades:
        v = float(t["or_size"])
        for k, (lo, hi) in or_bounds.items():
            if lo <= v < hi:
                or_groups[k].append(t)
                break

    print(f"\n  [4] OR Size Analysis")
    print(f"  {'OR Size':<12} {'WR':>6} {'N':>4} {'PF':>5} {'Delta':>7}")
    print(f"  {'-'*40}")
    for k in ["<10pt", "10-20pt", "20-40pt", "40-70pt", "70-130pt"]:
        g = or_groups[k]
        if not g:
            continue
        w, n = wr_n(g)
        p = pf(g)
        delta = w - overall_wr
        sign = "+" if delta >= 0 else ""
        print(f"  {k:<12} {w:>5.1%} {n:>4} {p:>5.2f} {sign}{delta*100:>5.1f}pp")

    # ── Print all suggestions ─────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  SUGGESTIONS ({len(suggestions)} found)")
    print(sep)
    if not suggestions:
        print("  No high-confidence suggestions from current data.")
    for i, s in enumerate(suggestions, 1):
        print(f"\n  [{i}] [{s['priority']}] {s['reason']}")
        print(f"  Current:  {s['current']}")
        print(f"  Suggest:  {s['config_line']}")
        print(f"  Note:     {s['note']}")
    print(f"\n{sep}")
    print("  REMINDER: These are suggestions only. Run walk-forward before applying.")
    print(sep)


if __name__ == "__main__":
    main()
