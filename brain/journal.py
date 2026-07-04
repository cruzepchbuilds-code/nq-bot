"""
brain/journal.py — the live-vs-sim drift meter (the scale-up trigger's judge)

Feed it the [v12] telemetry lines from the NT8 Output window (copy-paste into
a text file, or export). It pairs each ENTRY with its EXIT, compares realized
P&L against the theoretical outcome (full target / full stop at printed
prices, $14.50/trade cost model), and reports drift per strategy.

The trigger it exists to answer: "are live fills within ~25% of sim?"
  -> DRIFT OK  = scale-up authorized (deck Phase 2)
  -> DRIFT BAD = pause purchases, investigate fills

Usage:
    python3 brain/journal.py path/to/output.txt
    python3 brain/journal.py --demo          # self-test on synthetic lines
"""

import sys, re
from collections import defaultdict

COST = 14.50
PT = 20.0
# theoretical outcomes per signal: (stop_pts, target_pts)
SPEC = {"ORB1": (27, 81), "ORB2": (27, 81), "REJ": (20, 60),
        "PM_ORB": (22, 55), "ASIA": (25, 75), "PYR": (27, 81)}

ENTRY_RE = re.compile(
    r"\[v12\]\s+(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\s+ENTRY (\S+) (LONG|SHORT) (\d+)c @ ([\d.]+)")
EXIT_RE = re.compile(
    r"\[v12\]\s+(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\s+EXIT\s+(\S+) ([+-]\d+)")

DEMO = """\
[v12] 2026-07-06 09:52  ENTRY ORB1 LONG 1c @ 21514.25  stop 21487.25  tgt 21595.25
[v12] 2026-07-06 10:31  EXIT  ORB1 +1583  | day +1583 | life +1583
[v12] 2026-07-07 11:24  ENTRY REJ SHORT 1c @ 21488.00  stop 21508.00  tgt 21428.00
[v12] 2026-07-07 11:58  EXIT  REJ -428  | day -428 | life +1155
[v12] 2026-07-08 13:22  ENTRY PM_ORB LONG 1c @ 21530.50  stop 21508.50  tgt 21585.50
[v12] 2026-07-08 14:05  EXIT  PM_ORB +1074  | day +1074 | life +2229
"""


def classify(sig, qty, realized):
    """Match realized P&L to nearest theoretical outcome; return (kind, theo, drift)."""
    stop_pts, tgt_pts = SPEC.get(sig, (25, 60))
    theo_win = (tgt_pts * PT - COST) * qty
    theo_loss = (-stop_pts * PT - COST) * qty
    # nearest outcome (flatten exits land between; classify as 'time')
    d_win, d_loss = abs(realized - theo_win), abs(realized - theo_loss)
    if min(d_win, d_loss) > 0.35 * abs(theo_win):
        return "time-exit", None, None
    if d_win <= d_loss:
        return "target", theo_win, realized - theo_win
    return "stop", theo_loss, realized - theo_loss


def main():
    if "--demo" in sys.argv:
        text = DEMO
        print("(demo mode — synthetic fills)\n")
    else:
        if len(sys.argv) < 2:
            print(__doc__)
            return
        with open(sys.argv[1]) as f:
            text = f.read()

    entries = {}
    trades = []
    for line in text.splitlines():
        m = ENTRY_RE.search(line)
        if m:
            d, t, sig, dirn, qty, px = m.groups()
            entries[sig] = (d, t, dirn, int(qty), float(px))
            continue
        m = EXIT_RE.search(line)
        if m:
            d, t, sig, pnl = m.groups()
            e = entries.pop(sig, None)
            trades.append((d, sig, e[2] if e else "?", e[3] if e else 1, float(pnl)))

    if not trades:
        print("No [v12] trades found in input.")
        return

    print(f"{'date':<12}{'signal':<8}{'dir':<7}{'kind':<10}{'realized':>10}{'theo':>10}{'drift':>9}")
    print("─" * 66)
    drift_by = defaultdict(list)
    for d, sig, dirn, qty, pnl in trades:
        kind, theo, drift = classify(sig, qty, pnl)
        drift_by[sig].append((drift if drift is not None else 0, kind))
        print(f"{d:<12}{sig:<8}{dirn:<7}{kind:<10}{pnl:>+10,.0f}"
              f"{(theo if theo is not None else float('nan')):>10,.0f}"
              f"{(drift if drift is not None else 0):>+9,.0f}")

    print("─" * 66)
    all_d = [x for v in drift_by.values() for x, k in v if k != "time-exit"]
    n_res = len(all_d)
    if n_res:
        avg = sum(all_d) / n_res
        print(f"\nresolved trades: {n_res}   avg drift: ${avg:+,.0f}/trade   "
              f"(budget: -$15/trade; sim already charges $14.50)")
        per_trade_sim = 0.25 * 150   # 25% of ~$150 avg trade expectancy
        verdict = ("DRIFT OK — scale-up trigger satisfied so far"
                   if avg > -per_trade_sim else
                   "DRIFT BAD — fills eating the edge; pause purchases, investigate")
        print(f"VERDICT: {verdict}")
        print(f"(threshold: avg drift better than -${per_trade_sim:.0f}/trade ≈ 25% of expectancy; "
              f"needs ~15+ resolved trades before it means anything)")
    by_sig = {s: round(sum(x for x, k in v if k != 'time-exit') /
                       max(1, sum(1 for _, k in v if k != 'time-exit')))
              for s, v in drift_by.items()}
    print(f"per-strategy avg drift: {by_sig}")


if __name__ == "__main__":
    main()
