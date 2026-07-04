"""
brain/research/empire_rulemap.py

THE RULE-SPACE MAP — expected 12-month extraction per account for our v12
P&L stream under every combination of prop-firm rule archetypes:

  floor type:   fixed | trailing-EOD-that-locks-at-start | pure-trailing-EOD
  floor size:   $1,500 / $2,000 / $2,500 / $3,000
  consistency:  none / 20% / 40%   (payout needs total >= best_day / pct)
  payout:       daily cap $1k | 5-day cap $3k | monthly uncapped
                (all: only from balance > start, keep $1k buffer)

Output: expected extraction/account-year (mean over 2024+ starts), death %,
paid %. Placement check: Tradeify-Daily and Lucid-Direct cells should
reproduce the dedicated sims. Any real firm card = one lookup on this map.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval_boost import build_components
from itertools import product

STOPS = {"ORB": 27.0, "REJ": 20.0, "PM": 22.0, "ASIA": 25.0}
RISK1 = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}
START, KEEP = 50_000.0, 1_000.0


def build_seq():
    comp = build_components()
    all_days = sorted(set().union(*[set(comp[k]) for k in ("ORB3", "REJ", "PM", "ASIA")]))

    def day_pnl(d):
        lst = []
        for k in ("ORB3", "REJ", "PM", "ASIA"):
            key = "ORB" if k == "ORB3" else k
            lst.extend((key, *t[1:]) for t in comp[k].get(d, []))
        lst.sort(key=lambda x: x[1])
        pnl_day = morning = 0.0
        has_orb = any(s == "ORB" for s, *_ in lst)
        open_until = None
        for strat, e_t, x_t, p in lst:
            if strat == "REJ" and has_orb: continue
            if strat == "PM" and has_orb and morning < 0: continue
            if open_until is not None and e_t < open_until: continue
            if pnl_day <= -500: continue
            if (1150 + pnl_day) < RISK1[strat]: continue
            pnl_day += p
            if strat == "ORB": morning += p
            open_until = x_t
        return pnl_day

    return [(d, day_pnl(d)) for d in all_days]


def run_cell(seq, i0, floor_type, floor_amt, cons_pct, payout, horizon=252):
    bal, peak = START, START
    floor = START - floor_amt
    tp_, best, wd, last_pay = 0.0, 0.0, 0.0, -99
    for k in range(i0, min(i0 + horizon, len(seq))):
        _, p = seq[k]
        bal += p
        tp_ += p
        best = max(best, p)
        if bal <= floor:
            return wd, True
        rel = k - i0
        ok_cons = (cons_pct == 0) or (tp_ > 0 and tp_ >= best / cons_pct)
        if payout == "daily":
            can, cap = True, 1000.0
        elif payout == "5day":
            can, cap = rel - last_pay >= 5, 3000.0
        else:
            can, cap = rel - last_pay >= 21, 1e9
        if ok_cons and can and rel >= 5:
            avail = min(cap, bal - (START + KEEP))
            if avail > 0:
                bal -= avail
                wd += avail
                last_pay = rel
        peak = max(peak, bal)
        if floor_type == "fixed":
            pass
        elif floor_type == "trail_lock":
            floor = max(floor, min(peak - floor_amt, START))
        else:
            floor = max(floor, peak - floor_amt)
    return wd, False


if __name__ == "__main__":
    print("Building v12 daily P&L stream...", flush=True)
    seq = build_seq()
    idx = [i for i, (d, _) in enumerate(seq) if d.year >= 2024 and len(seq) - i >= 60]
    print(f"  {len(idx)} fresh-start worlds (2024+)\n")

    rows = []
    for ft, fa, cp, po in product(("fixed", "trail_lock", "trail_pure"),
                                  (1500, 2000, 2500, 3000),
                                  (0, 0.20, 0.40),
                                  ("daily", "5day", "monthly")):
        res = [run_cell(seq, i, ft, fa, cp, po) for i in idx]
        n = len(res)
        wd = sum(r[0] for r in res) / n
        died = sum(1 for r in res if r[1]) / n
        paid = sum(1 for r in res if r[0] > 0) / n
        rows.append((wd, died, paid, ft, fa, cp, po))

    rows.sort(reverse=True)
    print(f"{'═'*98}")
    print(f"  RULE-SPACE MAP — expected extraction / account-year (v12 stream, 1c, mean over starts)")
    print(f"{'═'*98}")
    print(f"  {'rank':<5}{'floor':<12}{'amt':>6}{'consist':>9}{'payout':>9} | {'extract/yr':>11} {'died':>6} {'paid':>6}")
    print(f"  {'─'*84}")
    for r, (wd, died, paid, ft, fa, cp, po) in enumerate(rows[:12], 1):
        print(f"  {r:<5}{ft:<12}{fa:>6}{cp:>9.0%}{po:>9} | ${wd:>10,.0f} {died:>6.0%} {paid:>6.0%}")
    print(f"  ...")
    for r, (wd, died, paid, ft, fa, cp, po) in enumerate(rows[-5:], len(rows) - 4):
        print(f"  {r:<5}{ft:<12}{fa:>6}{cp:>9.0%}{po:>9} | ${wd:>10,.0f} {died:>6.0%} {paid:>6.0%}")

    # placement of the two known cards
    print(f"\n  KNOWN CARDS ON THE MAP:")
    for name, ft, fa, cp, po in [("Tradeify 50K Daily", "trail_lock", 2000, 0, "daily"),
                                 ("Lucid 50K Direct",   "trail_lock", 2000, 0.20, "5day")]:
        res = [run_cell(seq, i, ft, fa, cp, po) for i in idx]
        n = len(res)
        wd = sum(r[0] for r in res) / n
        died = sum(1 for r in res if r[1]) / n
        rank = sum(1 for row in rows if row[0] > wd) + 1
        print(f"  {name:<22} ${wd:>9,.0f}/yr  died {died:.0%}  → rank {rank}/{len(rows)} on the map")

    # rule-value deltas (what each rule dimension is WORTH)
    print(f"\n  WHAT EACH RULE IS WORTH (avg extraction delta across the grid):")
    import statistics
    def avg_where(pred):
        v = [r[0] for r in rows if pred(r)]
        return sum(v) / len(v)
    print(f"    fixed floor vs pure-trailing:   +${avg_where(lambda r: r[3]=='fixed') - avg_where(lambda r: r[3]=='trail_pure'):>7,.0f}/yr")
    print(f"    lock-at-start vs pure-trailing: +${avg_where(lambda r: r[3]=='trail_lock') - avg_where(lambda r: r[3]=='trail_pure'):>7,.0f}/yr")
    print(f"    $3,000 floor vs $1,500:         +${avg_where(lambda r: r[4]==3000) - avg_where(lambda r: r[4]==1500):>7,.0f}/yr")
    print(f"    no consistency vs 20%:          +${avg_where(lambda r: r[5]==0) - avg_where(lambda r: r[5]==0.20):>7,.0f}/yr")
    print(f"    daily payout vs monthly:        +${avg_where(lambda r: r[6]=='daily') - avg_where(lambda r: r[6]=='monthly'):>7,.0f}/yr")
    print(f"\n{'═'*98}\n  empire_rulemap done.\n{'═'*98}")
