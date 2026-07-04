"""
brain/research/fleet_audit.py

CRO AUDIT of orchestration_final.py "X3: BUSINESS MONTE CARLO".

X3 MECHANISM (documented from code, orchestration_final.py):
  - It is NOT a Monte Carlo: there is no RNG and no bootstrap anywhere in the
    file. "Worlds" are rolling 2024+ start dates over ONE deterministic
    historical sequence (lines 168-171).
  - Accounts DO share the daily stream: both Tradeify slots replay the same
    `horizon = tr_seq[i0:i0+252]` from j=0 (lines 177-198) — they are exact
    clones (identical deaths, identical payouts x2). The Lucid account replays
    `lucid_seq[i0+k]` on the same calendar days (lines 199-214). So cross-
    account correlation is 1.0 by construction — X3 is NOT falsely narrow due
    to independent sampling.
  - What X3 is NOT: the real fleet. It models 1 fixed-floor 2-contract Lucid
    (dies only at cum <= -2000 from start, line 208 — the floor never trails,
    no consistency gate, monthly uncapped sweep) + 2 Tradeify daily-$1k-sweep
    accounts with NO consistency rule and unlimited out-of-pocket replacement
    ($260 + 5td, forever), all funded on day 1.

THE REAL FLEET (this audit): up to 5x Lucid 50K Direct, $364 each.
  trailing-EOD $2,000 floor that LOCKS once it reaches the $50k start
  (empire_rulemap "trail_lock"), 20% consistency (total >= 5x best day),
  payout every 5 trading days capped $3,000 keeping a $1,000 buffer,
  purchases staggered and SELF-FUNDED: start 1 account, buy the next when
  accumulated payouts cover $364, max 5 concurrent.

Every account receives the IDENTICAL calendar daily P&L from build_seq()
(copied verbatim from empire_rulemap.py — the live v12 day-composition rules:
REJ only if no morning ORB, PM skipped if morning lost, one position at a
time, -$500 day halt, per-strategy room gate vs the $1,150 guard, 1c).
Accounts differ ONLY in purchase date, floor state, consistency state and
payout phase — exactly like the real fleet.

Outputs (rolling 2024+ starts, 252-td horizon, same world convention as X3):
  1. corrected fleet 12-mo extraction p10/p50/p90 (gross and net of fees)
  2. P(losing 2+ accounts in the same ISO calendar week)  — correlated death
  3. worst fleet calendar month in dollars (cash and paper)
  4. staggered vs synchronized-start vs no-replacement comparison — does
     staggering de-synchronize floors, or does the shared stream dominate?
  5. IS (2022-2024) vs OOS (2025-2026) split of the underlying stream and of
     fleet extraction — flag anything that only works in one.

Run from repo root:
    python3 brain/research/fleet_audit.py [--cache /path/to/seq.pkl]

--cache: optional pickle of the daily sequence; built and saved on first use
(building components takes ~8 min), loaded instantly afterwards.
"""

import sys, os, argparse, pickle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collections import defaultdict
from statistics import median

# ── Account / firm constants (Lucid 50K Direct) ─────────────────────────────
START, KEEP = 50_000.0, 1_000.0
RISK1 = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}
ACCT_COST  = 364.0          # Lucid 50K Direct, prefunded
FLOOR_AMT  = 2_000.0        # trailing-EOD, locks at start (trail_lock)
CONS_PCT   = 0.20           # payout needs total >= best_day / 0.20 (5x best)
PAY_GAP    = 5              # trading days between payouts
PAY_CAP    = 3_000.0
MAX_ACCTS  = 5              # Lucid cap per person
HORIZON    = 252            # same horizon as X3

# X3 claimed numbers — captured verbatim from a fresh run of
# brain/research/orchestration_final.py (X3 section) on 2026-07-03
# ("333 start-date worlds, 2024+"; the widely quoted "$133k/yr p50" is the NET line).
X3_CLAIMED = {
    "m12_income": (60_355.0, 138_847.0, 246_253.0),  # p10/p50/p90 income by month 12
    "m12_net":    (58_275.0, 133_127.0, 240_572.0),  # p10/p50/p90 12-mo income - fees
}


# ── copied VERBATIM from empire_rulemap.build_seq (lines 30-54) ──────────────
# (the live v12 day-composition rules; 1c; Lucid $1,150 guard)
def build_seq():
    from eval_boost import build_components
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


# ── copied VERBATIM from empire_rulemap.run_cell (lines 57-89) ───────────────
# (reference single-account implementation — used only for the parity check)
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


# ── one Lucid Direct account, day-step form (semantics identical to run_cell
#    with floor_type="trail_lock", floor_amt=2000, cons_pct=0.20, payout="5day")
class Lucid:
    __slots__ = ("aid", "bal", "peak", "floor", "tp", "best",
                 "wd", "last_pay", "rel", "dead", "born", "lock_day")

    def __init__(self, aid, born):
        self.aid = aid
        self.born = born                  # world-relative day index of first trade
        self.bal = self.peak = START
        self.floor = START - FLOOR_AMT
        self.tp = self.best = self.wd = 0.0
        self.last_pay = -99
        self.rel = 0
        self.dead = False
        self.lock_day = None              # world-rel day the floor locked at $50k

    def step(self, p, krel):
        """Process one trading day of P&L p. Returns payout dollars (0 if none)."""
        self.bal += p
        self.tp += p
        self.best = max(self.best, p)
        if self.bal <= self.floor:
            self.dead = True
            self.rel += 1
            return 0.0
        pay = 0.0
        ok_cons = self.tp > 0 and self.tp >= self.best / CONS_PCT
        can = self.rel - self.last_pay >= PAY_GAP
        if ok_cons and can and self.rel >= PAY_GAP:
            avail = min(PAY_CAP, self.bal - (START + KEEP))
            if avail > 0:
                self.bal -= avail
                self.wd += avail
                pay = avail
                self.last_pay = self.rel
        self.peak = max(self.peak, self.bal)
        self.floor = max(self.floor, min(self.peak - FLOOR_AMT, START))
        if self.lock_day is None and self.floor >= START:
            self.lock_day = krel
        self.rel += 1
        return pay


def parity_check(seq, worlds):
    """Lucid.step must reproduce run_cell exactly for a solo account."""
    bad = 0
    for i0 in worlds:
        ref_wd, ref_died = run_cell(seq, i0, "trail_lock", int(FLOOR_AMT), CONS_PCT, "5day", HORIZON)
        a = Lucid(0, 0)
        for k in range(i0, min(i0 + HORIZON, len(seq))):
            if a.dead:
                break
            a.step(seq[k][1], k - i0)
        if abs(a.wd - ref_wd) > 1e-9 or a.dead != ref_died:
            bad += 1
    return bad


# ── fleet simulation: identical stream to every account ─────────────────────
def fleet_world(seq, i0, mode="staggered", horizon=HORIZON):
    """
    mode:
      staggered  — 1 account at start; buy next when payout pool >= $364,
                   max 5 concurrent; dead accounts may be replaced (pool-funded)
      sync5      — all 5 purchased at world start (out of pocket);
                   replacements pool-funded as above
      no_replace — staggered, but at most 5 purchases EVER
    Returns metrics dict. Every account receives the IDENTICAL (date, pnl).
    """
    end = min(i0 + horizon, len(seq))
    accts = []
    pool = 0.0
    purchases = 0
    pending = []                          # accounts that start trading next day
    income = 0.0
    fees = 0.0
    deaths = []                           # (date, aid, krel)
    monthly_cash = defaultdict(float)     # payouts - purchase costs, by (y,m)
    monthly_paper = defaultdict(float)    # sum of daily pnl over live accounts
    alive_path = []

    def buy(krel, d):
        nonlocal purchases, fees
        a = Lucid(purchases, krel + 1)
        purchases += 1
        fees += ACCT_COST
        monthly_cash[(d.year, d.month)] -= ACCT_COST
        pending.append(a)

    # first account: out of pocket, trades from day 0
    a0 = Lucid(0, 0)
    purchases, fees = 1, ACCT_COST
    d0 = seq[i0][0]
    monthly_cash[(d0.year, d0.month)] -= ACCT_COST
    accts.append(a0)
    if mode == "sync5":
        for _ in range(4):
            a = Lucid(purchases, 0)
            purchases += 1
            fees += ACCT_COST
            monthly_cash[(d0.year, d0.month)] -= ACCT_COST
            accts.append(a)

    for k in range(i0, end):
        d, p = seq[k]
        krel = k - i0
        # activate accounts purchased yesterday
        for a in pending:
            if a.born <= krel:
                accts.append(a)
        pending = [a for a in pending if a.born > krel]

        day_pay = 0.0
        for a in accts:
            if a.dead:
                continue
            monthly_paper[(d.year, d.month)] += p
            pay = a.step(p, krel)
            day_pay += pay
            if a.dead:
                deaths.append((d, a.aid, krel))
        income += day_pay
        pool += day_pay
        monthly_cash[(d.year, d.month)] += day_pay

        # EOD purchases from the payout pool
        n_live = sum(1 for a in accts if not a.dead) + len(pending)
        while pool >= ACCT_COST and n_live < MAX_ACCTS:
            if mode == "no_replace" and purchases >= MAX_ACCTS:
                break
            pool -= ACCT_COST
            buy(krel, d)
            n_live += 1
        alive_path.append(sum(1 for a in accts if not a.dead))

    # correlated-death metrics
    weeks = defaultdict(int)
    for d, _, _ in deaths:
        iso = d.isocalendar()
        weeks[(iso[0], iso[1])] += 1
    multi_death_week = any(v >= 2 for v in weeks.values())
    gaps = []
    for i in range(len(deaths)):
        for j in range(i + 1, len(deaths)):
            gaps.append(abs(deaths[i][2] - deaths[j][2]))

    days_run = end - i0
    all_accts = accts + pending
    pd_sorted = sorted(a.born for a in all_accts)
    return {
        "income": income, "fees": fees, "net": income - fees,
        "days": days_run,
        "n_deaths": len(deaths), "deaths": deaths,
        "multi_death_week": multi_death_week,
        "max_week_deaths": max(weeks.values()) if weeks else 0,
        "dead_unpaid": sum(1 for a in all_accts if a.dead and a.wd == 0),
        "ramp": (pd_sorted[4] - pd_sorted[0]) if len(pd_sorted) >= 5 else None,
        "pair_gaps": gaps,
        "worst_month_cash": min(monthly_cash.values()) if monthly_cash else 0.0,
        "worst_month_cash_id": min(monthly_cash, key=monthly_cash.get) if monthly_cash else None,
        "worst_month_paper": min(monthly_paper.values()) if monthly_paper else 0.0,
        "worst_month_paper_id": min(monthly_paper, key=monthly_paper.get) if monthly_paper else None,
        "max_alive": max(alive_path) if alive_path else 0,
        "reached5": 5 in alive_path,
        "extinct": (alive_path[-1] == 0 and not pending and
                    (pool < ACCT_COST or (mode == "no_replace" and purchases >= MAX_ACCTS))
                    ) if alive_path else False,
        "purchases": purchases,
        "locks": [a.lock_day for a in accts if a.lock_day is not None],
        "purchase_days": sorted(a.born for a in accts + pending),
    }


def pct(vals, q):
    v = sorted(vals)
    if not v:
        return float("nan")
    return v[min(len(v) - 1, int(q * len(v)))]


def fmt_month(m):
    return f"{m[0]}-{m[1]:02d}" if m else "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None, help="pickle path for the daily sequence")
    args = ap.parse_args()

    # ── build / load the single shared daily P&L stream ─────────────────────
    if args.cache and os.path.exists(args.cache):
        print(f"Loading cached v12 daily stream from {args.cache} ...", flush=True)
        with open(args.cache, "rb") as f:
            seq = pickle.load(f)
    else:
        print("Building v12 daily P&L stream (build_components ~8 min)...", flush=True)
        seq = build_seq()
        if args.cache:
            with open(args.cache, "wb") as f:
                pickle.dump(seq, f)
            print(f"  cached -> {args.cache}", flush=True)

    by_year = defaultdict(float)
    for d, p in seq:
        by_year[d.year] += p
    print(f"  {len(seq)} trading days {seq[0][0]} -> {seq[-1][0]}")
    print("  1c stream by year: " + "  ".join(f"{y}: ${v:+,.0f}" for y, v in sorted(by_year.items())))
    is_pnl = sum(v for y, v in by_year.items() if y <= 2024)
    oos_pnl = sum(v for y, v in by_year.items() if y >= 2025)
    print(f"  IS 2022-2024 total ${is_pnl:+,.0f}   OOS 2025-2026 total ${oos_pnl:+,.0f}", flush=True)

    worlds = [i for i, (d, _) in enumerate(seq) if d.year >= 2024 and len(seq) - i >= 60]
    full_worlds = [i for i in worlds if len(seq) - i >= HORIZON]
    print(f"  {len(worlds)} start worlds (2024+, >=60 td) — X3 convention; "
          f"{len(full_worlds)} with a full 252-td horizon\n", flush=True)

    # ── parity: day-step account == empire_rulemap.run_cell ─────────────────
    print("Parity check vs empire_rulemap.run_cell (solo account, every world)...", flush=True)
    bad = parity_check(seq, worlds)
    print(f"  mismatches: {bad}/{len(worlds)}" + ("  OK" if bad == 0 else "  *** FAIL ***"))
    solo = [run_cell(seq, i, "trail_lock", 2000, 0.20, "5day", HORIZON) for i in worlds]
    solo_wd = [w for w, _ in solo]
    solo_die = sum(1 for _, dd in solo if dd) / len(solo)
    print(f"  solo Lucid Direct (reference): mean 12-mo extraction ${sum(solo_wd)/len(solo_wd):,.0f}, "
          f"p50 ${pct(solo_wd, .5):,.0f}, died {solo_die:.0%}\n", flush=True)

    # ── fleet simulations ────────────────────────────────────────────────────
    results = {}
    for mode in ("staggered", "sync5", "no_replace"):
        print(f"Simulating fleet mode={mode} over {len(worlds)} worlds...", flush=True)
        rs = []
        for n, i0 in enumerate(worlds):
            rs.append(fleet_world(seq, i0, mode=mode))
            if (n + 1) % 200 == 0:
                print(f"  {n+1}/{len(worlds)} worlds", flush=True)
        results[mode] = rs
    print(flush=True)

    stag = results["staggered"]

    # ── 1. corrected fleet extraction ────────────────────────────────────────
    inc = [r["income"] for r in stag]
    net = [r["net"] for r in stag]
    inc_full = [stag[worlds.index(i)]["income"] for i in full_worlds]
    net_full = [stag[worlds.index(i)]["net"] for i in full_worlds]

    W = 100
    print("═" * W)
    print("  1. CORRECTED FLEET — 5x Lucid Direct, IDENTICAL daily stream, staggered self-funded")
    print("═" * W)
    print(f"  12-mo GROSS payouts  p10=${pct(inc,.1):>9,.0f}  p50=${pct(inc,.5):>9,.0f}  p90=${pct(inc,.9):>9,.0f}   (all {len(worlds)} worlds, X3 convention)")
    print(f"  12-mo NET of fees    p10=${pct(net,.1):>9,.0f}  p50=${pct(net,.5):>9,.0f}  p90=${pct(net,.9):>9,.0f}")
    print(f"  full-horizon only    p10=${pct(inc_full,.1):>9,.0f}  p50=${pct(inc_full,.5):>9,.0f}  p90=${pct(inc_full,.9):>9,.0f}   ({len(full_worlds)} worlds with 252 td)")
    print(f"  fees p50 ${pct([r['fees'] for r in stag],.5):,.0f}   purchases p50 {pct([r['purchases'] for r in stag],.5):.0f}   "
          f"reached 5 accounts in {sum(1 for r in stag if r['reached5'])/len(stag):.0%} of worlds   "
          f"fleet extinct by month 12 in {sum(1 for r in stag if r['extinct'])/len(stag):.0%}")
    zero_pay = sum(1 for r in stag if r["income"] == 0) / len(stag)
    tot_acct = sum(r["purchases"] for r in stag)
    tot_unpaid = sum(r["dead_unpaid"] for r in stag)
    print(f"  worlds with ZERO payouts in 12 mo: {zero_pay:.0%}   "
          f"accounts that died without EVER paying out: {tot_unpaid}/{tot_acct} ({tot_unpaid/tot_acct:.0%})")
    print(f"  (mechanism: 20% consistency needs total >= 5x best day BEFORE the first payout,")
    print(f"   while the $2,000 floor trails the whole time — most accounts lose that race.)")

    # ── 2. correlated death ──────────────────────────────────────────────────
    print("\n" + "═" * W)
    print("  2. CORRELATED DEATH — all accounts share one stream")
    print("═" * W)
    for mode in ("staggered", "sync5"):
        rs = results[mode]
        p_multi = sum(1 for r in rs if r["multi_death_week"]) / len(rs)
        multi_deaths = [r for r in rs if r["n_deaths"] >= 2]
        p_multi_cond = (sum(1 for r in multi_deaths if r["multi_death_week"]) / len(multi_deaths)) if multi_deaths else 0.0
        all_gaps = [g for r in rs for g in r["pair_gaps"]]
        same_wk_pairs = sum(1 for g in all_gaps if g <= 5)
        print(f"  {mode:<10} P(2+ accounts die in the SAME ISO week, within 12 mo) = {p_multi:5.1%}   "
              f"| given >=2 deaths: {p_multi_cond:5.1%}")
        if all_gaps:
            print(f"  {'':<10} death pairs: n={len(all_gaps)}, median gap {median(all_gaps):.0f} td, "
                  f"{same_wk_pairs/len(all_gaps):.0%} within 5 td")
        print(f"  {'':<10} deaths/world p50={pct([r['n_deaths'] for r in rs],.5):.0f} "
              f"mean={sum(r['n_deaths'] for r in rs)/len(rs):.2f}   "
              f"worlds with any death {sum(1 for r in rs if r['n_deaths']>0)/len(rs):.0%}   "
              f"worst single week {max(r['max_week_deaths'] for r in rs)} deaths")

    # ── 3. worst calendar month ──────────────────────────────────────────────
    print("\n" + "═" * W)
    print("  3. WORST FLEET CALENDAR MONTH (staggered fleet)")
    print("═" * W)
    wm_cash = min(stag, key=lambda r: r["worst_month_cash"])
    wm_paper = min(stag, key=lambda r: r["worst_month_paper"])
    print(f"  CASH  (payouts - purchases): worst month across all worlds = "
          f"${wm_cash['worst_month_cash']:,.0f} in {fmt_month(wm_cash['worst_month_cash_id'])}   "
          f"| median world's worst month = ${pct([r['worst_month_cash'] for r in stag],.5):,.0f}")
    print(f"  PAPER (sum of live-account daily P&L): worst month = "
          f"${wm_paper['worst_month_paper']:,.0f} in {fmt_month(wm_paper['worst_month_paper_id'])}   "
          f"| median world's worst month = ${pct([r['worst_month_paper'] for r in stag],.5):,.0f}")
    from collections import Counter
    cc = Counter(fmt_month(r["worst_month_paper_id"]) for r in stag)
    print(f"  most frequent worst-paper month across worlds: {cc.most_common(3)}")

    # ── 4. does staggering de-synchronize? ───────────────────────────────────
    print("\n" + "═" * W)
    print("  4. STAGGERING vs SHARED STREAM")
    print("═" * W)
    print(f"  {'mode':<11}{'inc p50':>10}{'net p50':>10}{'P(2+ same wk)':>15}{'med pair gap':>14}{'deaths/wld':>11}{'reached5':>9}")
    for mode in ("staggered", "sync5", "no_replace"):
        rs = results[mode]
        all_gaps = [g for r in rs for g in r["pair_gaps"]]
        print(f"  {mode:<11}"
              f"${pct([r['income'] for r in rs],.5):>9,.0f}"
              f"${pct([r['net'] for r in rs],.5):>9,.0f}"
              f"{sum(1 for r in rs if r['multi_death_week'])/len(rs):>15.1%}"
              f"{(str(round(median(all_gaps))) + ' td') if all_gaps else '   n/a':>14}"
              f"{sum(r['n_deaths'] for r in rs)/len(rs):>11.2f}"
              f"{sum(1 for r in rs if r['reached5'])/len(rs):>9.0%}")
    dominated = sum(1 for a, b in zip(results["staggered"], results["no_replace"])
                    if a["income"] < b["income"])
    same_inc = sum(1 for a, b in zip(results["staggered"], results["no_replace"])
                   if a["income"] == b["income"])
    print(f"\n  sanity: staggered income < no_replace in {dominated}/{len(worlds)} worlds "
          f"(must be 0); equal in {same_inc} — replacement churn adds nothing in those worlds")
    stag_pd = [r["purchase_days"] for r in stag if len(r["purchase_days"]) >= 2]
    gaps_purch = [b - a for pd in stag_pd for a, b in zip(pd, pd[1:])]
    ramps = [r["ramp"] for r in stag if r["ramp"] is not None]
    locks = [l for r in stag for l in r["locks"]]
    if gaps_purch:
        print(f"  staggered fleet: median gap between consecutive purchases = {median(gaps_purch):.0f} td; "
              f"account #1 -> #5 ramp median {median(ramps):.0f} td" if ramps else "")
        print(f"  (one $3,000 payout covers 8x the $364 price — the 'stagger' collapses on the first payout day)")
    if locks:
        print(f"  floor locks at $50k a median {median(locks):.0f} td into the world "
              f"({len(locks)} lock events) — after lock every account sits on the same $50k floor,")
        print(f"  stripped to ~$51k by each payout: purchase-date diversity cannot survive the lock.")

    # ── 5. IS/OOS split ──────────────────────────────────────────────────────
    print("\n" + "═" * W)
    print("  5. IS (2024 starts) vs OOS (2025-2026 starts) — annualized, staggered fleet")
    print("═" * W)
    for label, sel in [("2024 starts (IS boundary)", lambda d: d.year == 2024),
                       ("2025-26 starts (OOS)",      lambda d: d.year >= 2025)]:
        rs = [r for i, r in zip(worlds, stag) if sel(seq[i][0])]
        if not rs:
            continue
        ann = [r["income"] / r["days"] * 252 for r in rs]
        p_md = sum(1 for r in rs if r["multi_death_week"]) / len(rs)
        print(f"  {label:<26} n={len(rs):>3}  annualized gross p10=${pct(ann,.1):>9,.0f} "
              f"p50=${pct(ann,.5):>9,.0f} p90=${pct(ann,.9):>9,.0f}  P(2+ same wk)={p_md:.0%}")

    # ── comparison table ─────────────────────────────────────────────────────
    print("\n" + "═" * W)
    print("  X3 CLAIMED vs CORRECTED (12-mo, rolling 2024+ starts)")
    print("═" * W)
    c_inc, c_net = X3_CLAIMED["m12_income"], X3_CLAIMED["m12_net"]
    if c_inc[0] is not None:
        print(f"  {'':<26}{'p10':>12}{'p50':>12}{'p90':>12}")
        print(f"  {'X3 claimed income':<26}${c_inc[0]:>11,.0f}${c_inc[1]:>11,.0f}${c_inc[2]:>11,.0f}")
        print(f"  {'corrected fleet gross':<26}${pct(inc,.1):>11,.0f}${pct(inc,.5):>11,.0f}${pct(inc,.9):>11,.0f}")
        print(f"  {'X3 claimed net':<26}${c_net[0]:>11,.0f}${c_net[1]:>11,.0f}${c_net[2]:>11,.0f}")
        print(f"  {'corrected fleet net':<26}${pct(net,.1):>11,.0f}${pct(net,.5):>11,.0f}${pct(net,.9):>11,.0f}")
    else:
        print("  (X3_CLAIMED not filled — run brain/research/orchestration_final.py and paste its X3 numbers)")
        print(f"  corrected fleet gross p10/p50/p90 = ${pct(inc,.1):,.0f} / ${pct(inc,.5):,.0f} / ${pct(inc,.9):,.0f}")
        print(f"  corrected fleet net   p10/p50/p90 = ${pct(net,.1):,.0f} / ${pct(net,.5):,.0f} / ${pct(net,.9):,.0f}")
    print("\n  NOTE: X3 modeled a DIFFERENT business (1 fixed-floor 2c Lucid + 2 Tradeify daily-sweep")
    print("  accounts, no consistency rule, unlimited replacement capital, all funded day 1).")
    print("  Its accounts DID share one daily stream — the miss is the account rules, not the sampling.")
    print("═" * W)
    print("  fleet_audit done.")
    print("═" * W)


if __name__ == "__main__":
    main()
