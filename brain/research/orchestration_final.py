"""
brain/research/orchestration_final.py

Final sweep — the ORCHESTRATION layer (strategies are done; how they combine
across accounts is not).

  X1. SPLIT-STACK: two Tradeify accounts with different halves of the system
      vs two identical full-stack copies. Lower per-account variance should
      delay trailing death → more total extraction per fee dollar.
      (Gates only apply within an account — NT8 strategies can't see each other.)

  X2. LUCID SELECTIVE 2C: MaxContracts=1 exists because a 2c MORNING stop
      gap-through (-$1,240) breaches the $1,200 DLL. But REJ 2c risks $830 and
      PM 2c risks $910 — both fit. Size REJ/PM to 2c once cushion ≥ $1,500,
      keep ORB/Asia 1c. Room-gate (guard 1150) still caps stacking.

  X3. BUSINESS MONTE CARLO: the user's actual plan — 1 Lucid compounder +
      2 Tradeify daily-payout slots with dead-account replacement ($260, 5-day
      gap) — simulated from every 2024+ start date. Output: monthly income
      distribution at months 3/6/12, fees included.
      Assumptions: Lucid pays out monthly profit above a $2,000 retained
      cushion; Tradeify daily sweep, buffer $1,500; replacement forever.

Strict 1c base, $14.50/trade, v11.2 rules within each account.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval_boost import build_components
from collections import defaultdict

DLL   = 500.0
GUARD_TRADEIFY = 900.0
GUARD_LUCID    = 1150.0
RISK = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}


def day_pnl(lst, guard, dll=DLL, size2=None, cum=0.0):
    """One day under v11.2 rules for the components present in lst.
       size2: set of strats sized 2c when cum >= 1500. Returns day pnl."""
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
        if pnl_day <= -dll:
            continue
        mult = 1
        if size2 and strat in size2 and cum + pnl_day >= 1500:
            # room-gate at 2c risk
            if (guard + pnl_day) >= 2 * RISK[strat]:
                mult = 2
        if mult == 1 and (guard + pnl_day) < RISK[strat]:
            continue
        pnl_day += pnl * mult
        if strat == "ORB":
            morning_pnl += pnl * mult
        open_until = x_t
    return pnl_day


def tradeify_run(seq, start_i):
    """Daily-path account from seq[start_i:]. Returns (withdrawn, days_alive)."""
    bal, hwm = 50_000.0, 50_000.0
    floor = 48_000.0
    withdrawn = 0.0
    n = 0
    for d, p in seq[start_i:]:
        bal += p
        n += 1
        if bal <= floor:
            return withdrawn, n
        w = min(1000.0, bal - max(50_000.0, floor + 1500.0))
        if w > 0:
            bal -= w
            withdrawn += w
        hwm = max(hwm, bal)
        floor = max(floor, min(hwm - 2000.0, 50_000.0))
    return withdrawn, n


if __name__ == "__main__":
    print("Building components...", flush=True)
    comp = build_components()
    all_days = sorted(set().union(*[set(comp[k]) for k in ("ORB3", "REJ", "PM", "ASIA")]))

    def trades_for(d, use):
        lst = []
        for k in use:
            src = comp["ORB3"] if k == "ORB" else comp[k]
            lst.extend(src.get(d, []))
        return lst

    # ══ X1: SPLIT-STACK (Tradeify pair) ═══════════════════════════════════════
    print(f"\n{'═'*96}\n  X1: SPLIT-STACK — two Tradeify accounts, total withdrawn per pair\n{'═'*96}")
    pairs = [
        ("full + full",        ("ORB", "REJ", "PM", "ASIA"), ("ORB", "REJ", "PM", "ASIA")),
        ("ORB+REJ | PM+ASIA",  ("ORB", "REJ"),               ("PM", "ASIA")),
        ("ORB+PM | REJ+ASIA",  ("ORB", "PM"),                ("REJ", "ASIA")),
        ("ORB+ASIA | REJ+PM",  ("ORB", "ASIA"),              ("REJ", "PM")),
    ]
    for name, u1, u2 in pairs:
        seq1 = [(d, day_pnl(trades_for(d, u1), GUARD_TRADEIFY)) for d in all_days]
        seq2 = [(d, day_pnl(trades_for(d, u2), GUARD_TRADEIFY)) for d in all_days]
        tot_w, tot_life = [], []
        for i, (d0, _) in enumerate(seq1):
            if d0.year < 2024:
                continue
            w1, n1 = tradeify_run(seq1, i)
            w2, n2 = tradeify_run(seq2, i)
            tot_w.append(w1 + w2)
            tot_life.append((n1 + n2) / 2)
        avg_w = sum(tot_w) / len(tot_w)
        avg_l = sum(tot_life) / len(tot_life)
        print(f"  {name:<22} withdrawn/pair=${avg_w:>8,.0f}   avg acct life={avg_l:>5.0f} td")

    # ══ X2: LUCID SELECTIVE 2C ════════════════════════════════════════════════
    print(f"\n{'═'*96}\n  X2: LUCID (fixed floor 48k) — selective 2c on REJ/PM after +$1,500 cushion\n{'═'*96}")
    FULL = ("ORB", "REJ", "PM", "ASIA")
    for name, size2 in [("1c baseline", None), ("2c REJ+PM (cushion-gated)", {"REJ", "PM"})]:
        # sequential full-run from 2024 with cushion-aware sizing
        cum = 0.0
        monthly = defaultdict(float)
        worst_day = 0.0
        for d in all_days:
            if d.year < 2024:
                continue
            p = day_pnl(trades_for(d, FULL), GUARD_LUCID, size2=size2, cum=cum)
            cum += p
            worst_day = min(worst_day, p)
            monthly[(d.year, d.month)] += p
        mv = sorted(monthly.values())
        # fresh-start survival 2024+
        deaths = starts = 0
        for i, d0 in enumerate(all_days):
            if d0.year < 2024:
                continue
            starts += 1
            c = 0.0
            for d in all_days[i:]:
                c += day_pnl(trades_for(d, FULL), GUARD_LUCID, size2=size2, cum=c)
                if c <= -2000:
                    deaths += 1
                    break
        print(f"  {name:<28} net=${cum:>+10,.0f}  avg/mo=${cum/len(monthly):>+7,.0f}  "
              f"worst day=${worst_day:>+7,.0f}  median mo=${mv[len(mv)//2]:>+7,.0f}  "
              f"deaths={deaths}/{starts} ({deaths/starts:.0%})")

    # ══ X3: BUSINESS MONTE CARLO ═════════════════════════════════════════════
    print(f"\n{'═'*96}\n  X3: THE BUSINESS — 1 Lucid (2c REJ/PM) + 2 Tradeify slots w/ replacement")
    print(f"      (Tradeify replace on death: $260 + 5 td gap; Lucid pays monthly profit above $2k cushion)")
    print(f"{'═'*96}")
    lucid_seq, cum = [], 0.0
    for d in all_days:
        p = day_pnl(trades_for(d, FULL), GUARD_LUCID, size2={"REJ", "PM"}, cum=cum)
        cum += p
        lucid_seq.append((d, p))
    tr_seq = [(d, day_pnl(trades_for(d, FULL), GUARD_TRADEIFY)) for d in all_days]

    worlds = []
    idx_2024 = [i for i, (d, _) in enumerate(tr_seq) if d.year >= 2024]
    for i0 in idx_2024:
        horizon = tr_seq[i0:i0 + 252]
        if len(horizon) < 60:
            continue
        income = [0.0] * len(horizon)
        fees = 260.0 * 2
        # Tradeify slots
        for slot in range(2):
            j = 0
            while j < len(horizon):
                bal, hwm, floor = 50_000.0, 50_000.0, 48_000.0
                dead = False
                while j < len(horizon):
                    _, p = horizon[j]
                    bal += p
                    if bal <= floor:
                        dead = True
                        j += 5              # replacement gap
                        fees += 260.0
                        break
                    w = min(1000.0, bal - max(50_000.0, floor + 1500.0))
                    if w > 0:
                        bal -= w
                        income[min(j, len(income)-1)] += w
                    hwm = max(hwm, bal)
                    floor = max(floor, min(hwm - 2000.0, 50_000.0))
                    j += 1
                if not dead:
                    break
        # Lucid: monthly sweep of profit above +$2,000 cushion (dies at -2k fixed)
        lc = 0.0
        swept = 0.0
        alive = True
        for k in range(len(horizon)):
            _, p = lucid_seq[i0 + k]
            if not alive:
                break
            lc += p
            if lc <= -2000:
                alive = False
                continue
            if (k + 1) % 21 == 0 and lc - swept > 2000:
                pay = lc - swept - 2000
                income[k] += pay
                swept += pay
        worlds.append((sum(income[:63]), sum(income[:126]), sum(income), fees))

    for label, idx in [("month 3", 0), ("month 6", 1), ("month 12", 2)]:
        vals = sorted(w[idx] for w in worlds)
        p10, p50, p90 = vals[len(vals)//10], vals[len(vals)//2], vals[9*len(vals)//10]
        print(f"  cumulative income by {label:<9}  p10=${p10:>8,.0f}   p50=${p50:>9,.0f}   p90=${p90:>9,.0f}")
    f = sorted(w[3] for w in worlds)
    print(f"  12-mo fees (incl. replacements)  p50=${f[len(f)//2]:,.0f}")
    net12 = sorted(w[2] - w[3] for w in worlds)
    print(f"  12-mo NET (income - fees)        p10=${net12[len(net12)//10]:>8,.0f}   "
          f"p50=${net12[len(net12)//2]:>9,.0f}   p90=${net12[9*len(net12)//10]:>9,.0f}")
    print(f"  ({len(worlds)} start-date worlds, 2024+)")

    print(f"\n{'═'*96}\n  orchestration_final done.\n{'═'*96}")
