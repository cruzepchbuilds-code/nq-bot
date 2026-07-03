"""
brain/research/lucid_direct_sim.py

Lucid 50K DIRECT under the REAL rule card (2026-07-03 screenshot):
  - Max loss $2,000, EOD TRAILING until it locks at start (assumed lock at
    breakeven once peak gain >= $2,000)  [was modeled as FIXED floor before]
  - DLL $1,200 below initial trail; LucidScale above: 60% of peak EOD gain
    (only grows; never binds - our worst day is -$909)
  - Consistency 20%: payout requires total profit >= 5x best single day
  - Min 5 trading days between payouts; payouts only from balance > start
  - $364 one-time; max 5 accounts

Sim: strict 1c v11.2/v12 stack, every 2024+ start date, 12-month horizon.
Payout policy: withdraw available above (start + keep_buffer) when eligible.
Assumption: floor is balance-based; withdrawals cannot push balance below
start (never withdraw into the locked floor). keep_buffer $1,000.

Day-governor variants (helps the 20% rule by capping best-day):
  none | stop new entries at day >= +$900 | +$700
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval_boost import build_components
from collections import defaultdict

DLL_INT = 500.0
GUARD = 1150.0
RISK = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}
START = 50_000.0
KEEP = 1_000.0


def day_pnl(lst, green_lock=None):
    pnl_day = 0.0
    morning_pnl = 0.0
    has_orb = any(s == "ORB" for s, _, _, _ in lst)
    open_until = None
    for strat, e_t, x_t, pnl in sorted(lst, key=lambda x: x[1]):
        if strat == "REJ" and has_orb:
            continue
        if strat == "PM" and has_orb and morning_pnl < 0:
            continue
        if open_until is not None and e_t < open_until:
            continue
        if pnl_day <= -DLL_INT:
            continue
        if green_lock is not None and pnl_day >= green_lock:
            continue
        if (GUARD + pnl_day) < RISK[strat]:
            continue
        pnl_day += pnl
        if strat == "ORB":
            morning_pnl += pnl
        open_until = x_t
    return pnl_day


def run_lucid(seq, i0, horizon=252):
    """Real Lucid Direct rules. Returns (withdrawn, died, first_payout_day, n_payouts)."""
    bal = START
    peak = START
    floor = START - 2000.0
    total_profit = 0.0
    best_day = 0.0
    withdrawn = 0.0
    last_pay = -10
    first_pay = None
    n_pay = 0
    days = seq[i0:i0 + horizon]
    for k, (_, p) in enumerate(days):
        bal += p
        total_profit += p
        best_day = max(best_day, p)
        # EOD death check (trailing floor)
        if bal <= floor:
            return withdrawn, True, first_pay, n_pay
        # payout eligibility: 20% consistency + 5-day spacing + above start
        eligible = (total_profit >= 5 * best_day and total_profit > 0
                    and k - last_pay >= 5 and k >= 5)
        if eligible:
            avail = bal - (START + KEEP)
            if avail > 0:
                bal -= avail
                withdrawn += avail
                n_pay += 1
                last_pay = k
                if first_pay is None:
                    first_pay = k + 1
        # trailing floor update (locks at start)
        peak = max(peak, bal)
        floor = max(floor, min(peak - 2000.0, START))
    return withdrawn, False, first_pay, n_pay


if __name__ == "__main__":
    print("Building components...", flush=True)
    comp = build_components()
    all_days = sorted(set().union(*[set(comp[k]) for k in ("ORB3", "REJ", "PM", "ASIA")]))

    def trades_for(d):
        lst = []
        for k in ("ORB3", "REJ", "PM", "ASIA"):
            key = "ORB" if k == "ORB3" else k
            lst.extend(comp[k].get(d, []))
        return lst

    print(f"\n{'═'*96}")
    print(f"  LUCID 50K DIRECT — real rules (EOD trail→lock, 20% consistency, 5-day min, $364)")
    print(f"  strict 1c, 12-month horizon per start, 2024+ starts")
    print(f"{'═'*96}")
    print(f"  {'day-governor':<16} {'survive12mo':>11} {'withdrawn/acct':>14} {'1st payout':>16} {'payouts/12mo':>13}")
    print(f"  {'─'*80}")
    for cap, tag in [(None, "none"), (900, "stop at +$900"), (700, "stop at +$700")]:
        seq = [(d, day_pnl(trades_for(d), green_lock=cap)) for d in all_days]
        res = []
        for i, (d0, _) in enumerate(seq):
            if d0.year < 2024 or len(seq) - i < 60:
                continue
            res.append(run_lucid(seq, i))
        n = len(res)
        surv = sum(1 for r in res if not r[1]) / n
        w_avg = sum(r[0] for r in res) / n
        fps = sorted(r[2] for r in res if r[2] is not None)
        fp_med = fps[len(fps)//2] if fps else -1
        fp_n = len(fps) / n
        pays = sorted(r[3] for r in res)
        print(f"  {tag:<16} {surv:>11.0%} {w_avg:>14,.0f} "
              f"{f'med {fp_med} td ({fp_n:.0%} reach)':>16} {pays[len(pays)//2]:>13}")

    # deeper stats for the winner config (chosen after seeing table — print all)
    print(f"\n  Reference: fixed-floor model (old assumption) had 26% deaths / $2,700-3,700 per mo P&L.")
    print(f"  Trailing-to-lock + consistency turns Lucid into: survive gauntlet → locked compounder")
    print(f"  with payout drag. Numbers above are the real expectation for the $364.")
    print(f"\n{'═'*96}\n  lucid_direct_sim done.\n{'═'*96}")
