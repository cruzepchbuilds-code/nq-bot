"""
brain/payout.py — Lucid 50K Direct first-payout planner

Answers: "can I request a payout yet — and if not, what exactly is blocking
me and how far away is it?"

Reads daily P&L from the live journal (data/live_journal.txt, built by
live/eod_digest.py) or from a manual list, then checks the three gates:
  1. >= 5 trading days
  2. 20% consistency: best day <= 20% of total profit (total >= 5 x best)
  3. balance above start (policy: keep a $1,000 buffer above $50,000)

Usage:
    python3 brain/payout.py                       # reads data/live_journal.txt
    python3 brain/payout.py --days "700,-350,420" # manual day P&Ls
    python3 brain/payout.py --since 2026-08-01    # window (if Lucid says
                                                  #  consistency resets per cycle)
    python3 brain/payout.py --demo                # synthetic self-test

Modeled assumptions (confirm with Lucid support — ticket already drafted):
  - consistency measured over account LIFETIME (use --since if it resets)
  - payout cap $3,000 per 5-trading-day cycle
  - trailing $2,000 EOD floor locks once it reaches $50,000
"""

import sys, os, re

START = 50_000.0
BUFFER = 5_000.0          # HOUSE POLICY (buffer_policy.py 2026-07-03): withdraw down
                          # to start+5k, never below — free cushion (consistency rule
                          # builds it anyway), makes the account ~5x longer-lived
CAP = 3_000.0             # modeled per-cycle cap
MIN_DAYS = 5
CONS = 0.20               # best day must be <= 20% of total

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(BASE, "data", "live_journal.txt")

DAY_RE = re.compile(r"\[v12\]\s+(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\s+EXIT\s+\S+ [+-][\d,]+\s+\|\s+day ([+-][\d,]+)")


def day_pnls_from_journal(path, since=None):
    """Last EXIT line per date carries that date's final day P&L."""
    days = {}
    with open(path) as f:
        for line in f:
            m = DAY_RE.search(line)
            if m:
                d, v = m.group(1), float(m.group(2).replace(",", ""))
                if since and d < since:
                    continue
                days[d] = v
    return [days[d] for d in sorted(days)], sorted(days)


def gates(pnls):
    total = sum(pnls)
    best = max([p for p in pnls if p > 0], default=0.0)
    days = len(pnls)
    need_cons = 5 * best
    g = {
        "days":    (days >= MIN_DAYS,        f"{days}/{MIN_DAYS} trading days"),
        "consist": (total >= need_cons and total > 0,
                    f"total ${total:,.0f} vs needed ${need_cons:,.0f} (5 x best day ${best:,.0f})"),
        "balance": (total >= BUFFER,
                    f"profit ${total:,.0f} vs ${BUFFER:,.0f} buffer above start"),
    }
    return g, total, best, days


def days_until(pnls, steady):
    """More trading days at +steady/day until all gates pass (<=200 lookahead)."""
    total = sum(pnls)
    best = max([p for p in pnls if p > 0], default=0.0)
    days = len(pnls)
    for extra in range(1, 201):
        total += steady
        days += 1
        best = max(best, steady)
        if days >= MIN_DAYS and total > 0 and total >= 5 * best and total >= BUFFER:
            return extra
    return None


def main():
    args = sys.argv[1:]
    since = None
    if "--since" in args:
        since = args[args.index("--since") + 1]

    if "--demo" in args:
        pnls, dates = [1583.0, -428.0, 1074.0], ["2026-07-06", "2026-07-07", "2026-07-08"]
        print("(demo mode — synthetic days)\n")
    elif "--days" in args:
        raw = args[args.index("--days") + 1]
        pnls = [float(x) for x in raw.split(",") if x.strip()]
        dates = [f"day {i+1}" for i in range(len(pnls))]
    else:
        if not os.path.exists(JOURNAL):
            print(f"No journal at {JOURNAL} yet — run live/eod_digest.py first,")
            print('or use --days "700,-350,420" / --demo.')
            return
        pnls, dates = day_pnls_from_journal(JOURNAL, since)

    if not pnls:
        print("No trading days found yet. Gates: 5 days traded, best day <= 20% of")
        print("total, profit above the $1,000 buffer. Check back after the first trades.")
        return

    g, total, best, days = gates(pnls)

    print("LUCID 50K DIRECT — PAYOUT PLANNER")
    print("─" * 46)
    print(f"trading days   {days}   ({dates[0]} → {dates[-1]})")
    print(f"total profit   ${total:+,.0f}")
    print(f"best day       ${best:+,.0f}")
    peak_gain = 0.0
    run = 0.0
    for p in pnls:
        run += p
        peak_gain = max(peak_gain, run)
    floor = max(START - 2000, min(START + peak_gain - 2000, START))
    print(f"floor (trail)  ${floor:,.0f}" + ("  — LOCKED at start" if floor >= START else ""))
    print(f"scale DLL      ${max(1200.0, 0.60 * peak_gain):,.0f}  (modeled: max($1,200, 60% of peak gain))")
    print("─" * 46)

    ok_all = True
    for name, (ok, detail) in g.items():
        mark = "PASS" if ok else "BLOCK"
        ok_all &= ok
        label = {"days": "5-day minimum", "consist": "20% consistency", "balance": "$1k buffer"}[name]
        print(f"  [{mark:<5}] {label:<16} {detail}")

    print("─" * 46)
    if ok_all:
        avail = min(CAP, (START + total) - (START + BUFFER))
        print(f"ELIGIBLE — request up to ${avail:,.0f} now (modeled ${CAP:,.0f}/cycle cap).")
        print("After payout: rerun this tool — consistency math changes with the balance.")
    else:
        print("NOT ELIGIBLE YET. Time to eligibility at a steady daily pace:")
        for steady in (250, 500, 750):
            n = days_until(pnls, steady)
            msg = f"~{n} more trading days" if n else ">200 days (pace too slow)"
            print(f"  at +${steady}/day: {msg}")
        if best > 0 and total < 5 * best:
            print(f"\n  the binding gate is CONSISTENCY: one ${best:,.0f} day means the")
            print(f"  account must reach ${5*best:,.0f} total before anything can be pulled.")
    print("─" * 46)
    if since:
        print(f"(windowed from {since} — per-cycle consistency mode)")


if __name__ == "__main__":
    main()
