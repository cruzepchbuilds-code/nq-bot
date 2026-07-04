"""
brain/research/firm_actuary.py

THE FIRM-SIDE ACTUARY — flips the rule-pricing engine to the PROP FIRM's P&L.

A prop firm is an insurer: it sells accounts (premium = price + commission
markup) and pays payouts (claims). This script prices each rule card from the
FIRM's side of the ledger against a two-type customer population:

  SHARK  — skilled systematic trader: the cached v12 daily P&L stream
           (data/v12_daily_stream.csv, 1 contract, 710 td 2022-2026), run
           from rolling 2024+ starts under empire_rulemap.run_cell() mechanics
           (replicated here verbatim: death check before payout, $1k buffer
           above start, 5-day payout seasoning, payout-cadence gap+cap,
           consistency = total >= best_day/pct, trail_lock floor capped at
           start, peak updated post-withdrawal, 252-td horizon, truncated
           late windows >= 60 td kept — same convention as the rulemap).
           Withdrawal policy = the rulemap's: request the max allowed, ASAP.
           Firm liability = payouts made; revenue = price + $0.60/RT markup
           x ~1.3 trades/day while alive.

  CHUM   — typical retail loser, PARAMETRIC: daily P&L ~ Normal(mu, sigma),
           base mu = -$60/day, sigma = $600/day; sensitivity grid
           mu in {-120, -60, -20} x sigma in {400, 600, 800}. Dies at the
           floor under the same account mechanics. 2,000 paths per card,
           numpy Generator with FIXED SEED; the SAME 2,000 noise paths are
           reused across every card and parameter combo (common random
           numbers), so rule deltas are apples-to-apples.
           Chum withdrawal BEHAVIOR is unknowable without firm data, so it
           is bounded by two scenarios evaluated everywhere:
             strip   — same greedy policy as the shark engine (worst case
                       for the firm: lucky streaks get cashed out)
             passive — never requests a payout (best case for the firm)
           Revenue = price + $0.60/RT x ~3 trades/day while alive.

CARDS (from the firm's side):
  Lucid 50K Direct    trail_lock $2,000, consistency 20%, 5-day cap $3k, $364
  Tradeify 50K Daily  trail_lock $2,000, none,            daily cap $1k, $260
  ETF 50K Static      fixed      $2,000, none, 10-td cap $1,250,
                      $99/eval + ~70% modeled pass gate + $177 activation
                      (gate applied to both cohorts as stated model; real
                      chum pass rates are lower — direction noted in output)
  + 12-cell mini-grid: floor {fixed, trail_lock} x amt {1500, 2000, 2500}
    x consistency {0, 20%}, 5-day $3k cadence, price $364 ($2,000 tier kept
    so the grid contains the Lucid coordinates).

KEY OUTPUT: breakeven shark share s* solving
    s * E[profit_shark] + (1 - s) * E[profit_chum] = 0
Low s* = card is fragile against skilled flow (mispriced -> best for OUR
fleet to buy). High s* = robust (what a firm should actually sell).
Headline ranking uses PASSIVE chum (the firm's best case); the strip
scenario is shown alongside as the stress bound.

HONESTY: the chum model is parametric, not empirical — the sensitivity grid
is printed so no single mu/sigma carries the conclusion; real firm customer
data would calibrate it in a paid engagement (that is the business model).
The shark stream is ONE strategy at 1 contract, not the population of all
skilled traders. Eval-phase commissions and chum re-purchases are excluded
from the per-account unit (both firm-favorable; noted where they matter).

Run:  python3 brain/research/firm_actuary.py     (from repo root; ~15 s)
"""

import csv
import datetime
import os
from itertools import product

import numpy as np

# ----------------------------------------------------------------- constants
START, KEEP = 50_000.0, 1_000.0
HORIZON = 252
SEED = 20260703            # fixed -> fully deterministic
N_CHUM = 2000
RT_MARKUP = 0.60           # firm's commission markup per round-turn, $
SHARK_TPD = 1.3            # shark trades (RT) per day  — stated assumption
CHUM_TPD = 3.0             # chum trades (RT) per day   — stated assumption
CHUM_BASE = (-60.0, 600.0)
CHUM_MUS = (-120.0, -60.0, -20.0)
CHUM_SIGMAS = (400.0, 600.0, 800.0)

# payout cadence -> (min gap in td since last payout, cap per payout)
PAYOUTS = {
    "daily":   (1, 1_000.0),
    "5day":    (5, 3_000.0),
    "10td":    (10, 1_250.0),
    "monthly": (21, 1e9),
}

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STREAM_CSV = os.path.join(REPO, "data", "v12_daily_stream.csv")


# ------------------------------------------------------------------- loading
def load_stream():
    rows = []
    with open(STREAM_CSV) as f:
        rd = csv.reader(f)
        next(rd)                                   # header
        for d, p in rd:
            rows.append((datetime.date.fromisoformat(d), float(p)))
    rows.sort()
    return [d for d, _ in rows], [p for _, p in rows]


# ------------------------------------------------- shark: run_cell replicated
def run_shark(pnl, i0, ft, fa, cp, gap, cap, horizon=HORIZON):
    """Exact port of empire_rulemap.run_cell + days-alive for commissions."""
    bal = peak = START
    floor = START - fa
    tp = best = wd = 0.0
    last_pay = -99
    days = 0
    for k in range(i0, min(i0 + horizon, len(pnl))):
        p = pnl[k]
        bal += p
        tp += p
        if p > best:
            best = p
        days = k - i0 + 1
        if bal <= floor:                           # death BEFORE payout
            return wd, True, days
        rel = k - i0
        ok_cons = (cp == 0) or (tp > 0 and tp >= best / cp)
        if ok_cons and (rel - last_pay) >= gap and rel >= 5:
            avail = min(cap, bal - (START + KEEP))
            if avail > 0:
                bal -= avail
                wd += avail
                last_pay = rel
        if bal > peak:                             # peak AFTER withdrawal
            peak = bal
        if ft == "trail_lock":
            floor = max(floor, min(peak - fa, START))
        elif ft == "trail_pure":
            floor = max(floor, peak - fa)
    return wd, False, days


_shark_cache = {}


def shark_stats(pnl, idx, ft, fa, cp, po):
    key = (ft, fa, cp, po)
    if key not in _shark_cache:
        gap, cap = PAYOUTS[po]
        res = [run_shark(pnl, i, ft, fa, cp, gap, cap) for i in idx]
        n = len(res)
        _shark_cache[key] = dict(
            wd=sum(r[0] for r in res) / n,
            died=sum(1 for r in res if r[1]) / n,
            days=sum(r[2] for r in res) / n,
            paid=sum(1 for r in res if r[0] > 0) / n,
        )
    return _shark_cache[key]


# ------------------------------------------- chum: vectorised, same mechanics
_chum_cache = {}


def chum_stats(Z, mu, sigma, ft, fa, cp, po, strip):
    """strip=True: greedy withdrawals (same engine as shark).
    strip=False: passive customer, never requests a payout."""
    key = (mu, sigma, ft, fa, cp, po, strip)
    if key in _chum_cache:
        return _chum_cache[key]
    gap, cap = PAYOUTS[po]
    n, horizon = Z.shape
    bal = np.full(n, START)
    peak = np.full(n, START)
    floor = np.full(n, START - fa)
    tp = np.zeros(n)
    best = np.zeros(n)
    wd = np.zeros(n)
    last_pay = np.full(n, -99)
    alive = np.ones(n, bool)
    died = np.zeros(n, bool)
    days = np.zeros(n)
    for k in range(horizon):
        if not alive.any():
            break
        p = mu + sigma * Z[:, k]
        bal[alive] += p[alive]
        tp[alive] += p[alive]
        best[alive] = np.maximum(best[alive], p[alive])
        days[alive] = k + 1
        dead_now = alive & (bal <= floor)          # death BEFORE payout
        died |= dead_now
        alive = alive & ~dead_now
        if strip:
            ok = np.ones(n, bool) if cp == 0 else ((tp > 0) & (tp >= best / cp))
            elig = alive & ok & ((k - last_pay) >= gap) & (k >= 5)
            if elig.any():
                avail = np.minimum(cap, bal - (START + KEEP))
                pay = elig & (avail > 0)
                bal[pay] -= avail[pay]
                wd[pay] += avail[pay]
                last_pay[pay] = k
        peak[alive] = np.maximum(peak[alive], bal[alive])
        if ft == "trail_lock":
            floor[alive] = np.maximum(floor[alive],
                                      np.minimum(peak[alive] - fa, START))
        elif ft == "trail_pure":
            floor[alive] = np.maximum(floor[alive], peak[alive] - fa)
    st = dict(wd=float(wd.mean()), died=float(died.mean()),
              days=float(days.mean()), paid=float((wd > 0).mean()))
    _chum_cache[key] = st
    return st


# --------------------------------------------------------------- firm ledger
def firm_profit(price_or_gate, tpd, st):
    """Firm P&L per account SOLD. gate = (eval_fee, pass_rate, activation)."""
    comm = RT_MARKUP * tpd * st["days"]
    if isinstance(price_or_gate, tuple):
        fee, pr, act = price_or_gate
        return fee + pr * (act + comm - st["wd"])  # eval-phase comm ignored
    return price_or_gate + comm - st["wd"]


def s_star(pi_s, pi_c):
    """Shark share where firm expected profit crosses zero."""
    if pi_s >= 0:
        return float("inf")                        # profitable even on sharks
    if pi_c <= 0:
        return 0.0                                 # loses money even on chum
    return pi_c / (pi_c - pi_s)


def fmt_s(s):
    return " never" if s == float("inf") else f"{s:6.2%}"


def econ(card, pnl, idx, Z, mu, sigma):
    """-> (pi_shark, pi_chum_strip, pi_chum_passive, shark_st, chum_st_strip,
    chum_st_passive) for one card at one chum (mu, sigma)."""
    ss = shark_stats(pnl, idx, card["ft"], card["fa"], card["cp"], card["po"])
    cg = chum_stats(Z, mu, sigma, card["ft"], card["fa"], card["cp"], card["po"], True)
    cp_ = chum_stats(Z, mu, sigma, card["ft"], card["fa"], card["cp"], card["po"], False)
    pay = card.get("gate") or card["price"]
    return (firm_profit(pay, SHARK_TPD, ss),
            firm_profit(pay, CHUM_TPD, cg),
            firm_profit(pay, CHUM_TPD, cp_), ss, cg, cp_)


# --------------------------------------------------------------------- cards
CARDS = [
    dict(name="Lucid 50K Direct",   ft="trail_lock", fa=2000, cp=0.20,
         po="5day",  price=364.0, gate=None),
    dict(name="Tradeify 50K Daily", ft="trail_lock", fa=2000, cp=0.0,
         po="daily", price=260.0, gate=None),
    dict(name="ETF 50K Static",     ft="fixed",      fa=2000, cp=0.0,
         po="10td",  price=None,  gate=(99.0, 0.70, 177.0)),
]

GRID = [dict(name=f"{ft}/{fa}/{int(cp*100)}%", ft=ft, fa=fa, cp=cp,
             po="5day", price=364.0, gate=None)
        for ft, fa, cp in product(("fixed", "trail_lock"),
                                  (1500, 2000, 2500), (0.0, 0.20))]

SWEEP = [dict(name=f"trail_lock/2000/0% {po}", ft="trail_lock", fa=2000,
              cp=0.0, po=po, price=364.0, gate=None) for po in PAYOUTS]


# ---------------------------------------------------------------------- main
def main():
    W = 104
    print("=" * W)
    print("  FIRM ACTUARY — pricing prop-firm rule cards from the FIRM's side of the ledger")
    print("=" * W)

    print("[1/6] loading cached v12 stream ...", flush=True)
    dates, pnl = load_stream()
    idx = [i for i, d in enumerate(dates)
           if d.year >= 2024 and len(dates) - i >= 60]
    print(f"      {len(pnl)} td  {dates[0]} -> {dates[-1]}   "
          f"mean ${np.mean(pnl):+,.0f}/day  sd ${np.std(pnl):,.0f}")
    print(f"      {len(idx)} rolling fresh-start worlds (2024+, >=60 td) — rulemap convention")

    print(f"[2/6] chum engine: {N_CHUM:,} paths x {HORIZON} td, Normal(mu,sigma), "
          f"seed {SEED}, common random numbers,")
    print("      each card priced under BOTH chum behaviors: "
          "strip (greedy payouts) and passive (never withdraws)", flush=True)
    Z = np.random.default_rng(SEED).standard_normal((N_CHUM, HORIZON))
    bmu, bsg = CHUM_BASE

    # ---------------------------------------------------------- section 1
    print("[3/6] pricing the 3 named cards ...", flush=True)
    print("\n" + "=" * W)
    print("  1) CARD PRICING — firm P&L per account sold  "
          f"(chum base: mu {bmu:+.0f}/d, sigma {bsg:.0f}/d)")
    print("      revenue = price + $0.60/RT x trades (shark 1.3/d, chum 3/d, while alive)")
    print("      cost = payouts under the card's own rules;  ETF unit = per EVAL sold (70% gate, both cohorts)")
    print("=" * W)
    rows = []
    for c in CARDS:
        pi_s, pi_cg, pi_cp, ss, cg, cpv = econ(c, pnl, idx, Z, bmu, bsg)
        rows.append(dict(name=c["name"], pi_s=pi_s, pi_cg=pi_cg, pi_cp=pi_cp,
                         sg=s_star(pi_s, pi_cg), sp=s_star(pi_s, pi_cp),
                         ss=ss, cg=cg, cpv=cpv))
    rows.sort(key=lambda r: r["sp"])
    print(f"  {'card':<20}{'firm/shark':>12}{'firm/chum':>11}{'firm/chum':>11}"
          f"{'s* strip':>10}{'s* passive':>11}{'shark extr':>12}{'shark die':>10}{'chum days':>10}")
    print(f"  {'':<20}{'':>12}{'(strip)':>11}{'(passive)':>11}{'':>10}{'':>11}{'/yr':>12}{'':>10}{'(strip)':>10}")
    print("  " + "-" * (W - 4))
    for r in rows:
        print(f"  {r['name']:<20}{r['pi_s']:>+12,.0f}{r['pi_cg']:>+11,.0f}{r['pi_cp']:>+11,.0f}"
              f"{fmt_s(r['sg']):>10}{fmt_s(r['sp']):>11}{r['ss']['wd']:>12,.0f}"
              f"{r['ss']['died']:>10.0%}{r['cg']['days']:>10.1f}")
    print("\n  ranking by s* passive = the firm's BEST case for customer behavior "
          "(strip scenario is the stress bound).")
    print("  low s* = fragile vs skilled flow = mispriced = what OUR fleet buys; "
          "high s* = what a firm should sell:")
    for i, r in enumerate(rows, 1):
        tag = "FRAGILE" if r["sp"] < 0.03 else ("MEDIUM " if r["sp"] < 0.12 else "ROBUST ")
        print(f"    {i}. {tag}  {r['name']:<20} s* = {fmt_s(r['sp']).strip()} passive"
              f"  ({fmt_s(r['sg']).strip()} if customers strip payouts)")

    # ------------------------------------------------- chum sensitivity grid
    print("\n[4/6] chum sensitivity grid (9 mu x sigma combos per card) ...", flush=True)
    print("\n  1b) SENSITIVITY — s* (passive chum) under every chum assumption; "
          "no single mu/sigma carries the story")
    hdr = "".join(f" mu{int(m):+4d}/s{int(s):<3d}" for m in CHUM_MUS for s in CHUM_SIGMAS)
    print(f"  {'card':<19}{hdr}")
    for c in CARDS:
        pas, stp = [], []
        for m in CHUM_MUS:
            for s in CHUM_SIGMAS:
                pi_s, pi_cg, pi_cp, *_ = econ(c, pnl, idx, Z, m, s)
                pas.append(s_star(pi_s, pi_cp))
                stp.append(s_star(pi_s, pi_cg))
        print(f"  {c['name']:<19}" + "".join(f"{fmt_s(v):>12}" for v in pas))
        print(f"  {'':<19}  -> passive s* {fmt_s(min(pas)).strip()}..{fmt_s(max(pas)).strip()}"
              f";  strip-stress s* {fmt_s(min(stp)).strip()}..{fmt_s(max(stp)).strip()}")

    # ---------------------------------------------------------- section 2
    print("\n[5/6] 12-cell mini-grid + rule levers ...", flush=True)
    print("\n" + "=" * W)
    print("  2) 12-CELL MINI-GRID — floor {fixed,trail_lock} x amt {1500,2000,2500} x cons {0,20%}")
    print("     5-day/$3k cadence, price $364, chum at base ($2,000 tier kept so Lucid's cell is on the map)")
    print("=" * W)
    print(f"  {'cell':<22}{'firm/shark':>12}{'firm/chum':>11}{'firm/chum':>11}"
          f"{'s* strip':>10}{'s* passive':>11}{'shark extr/yr':>15}")
    print(f"  {'':<22}{'':>12}{'(strip)':>11}{'(passive)':>11}")
    print("  " + "-" * (W - 4))
    grid_res = {}
    for c in GRID:
        pi_s, pi_cg, pi_cp, ss, cg, cpv = econ(c, pnl, idx, Z, bmu, bsg)
        grid_res[(c["ft"], c["fa"], c["cp"])] = (pi_s, pi_cg, pi_cp, ss, cpv)
        print(f"  {c['name']:<22}{pi_s:>+12,.0f}{pi_cg:>+11,.0f}{pi_cp:>+11,.0f}"
              f"{fmt_s(s_star(pi_s, pi_cg)):>10}{fmt_s(s_star(pi_s, pi_cp)):>11}"
              f"{ss['wd']:>15,.0f}")

    def gavg(sel, j):
        v = [grid_res[k][j] for k in grid_res if sel(k)]
        return sum(v) / len(v)

    d_fl = [gavg(lambda k: k[0] == "trail_lock", j) - gavg(lambda k: k[0] == "fixed", j)
            for j in (0, 1, 2)]
    d_cn = [gavg(lambda k: k[2] == 0.20, j) - gavg(lambda k: k[2] == 0.0, j)
            for j in (0, 1, 2)]

    sweep_res = {}
    for c in SWEEP:
        pi_s, pi_cg, pi_cp, ss, cg, cpv = econ(c, pnl, idx, Z, bmu, bsg)
        sweep_res[c["po"]] = (pi_s, pi_cg, pi_cp, ss, cpv)
    d_cap = {po: [sweep_res[po][j] - sweep_res["monthly"][j] for j in (0, 1, 2)]
             for po in ("daily", "5day", "10td")}

    print("\n  RULE-LEVER VALUE, FIRM SIDE (delta firm profit per account sold; + = rule helps the firm)")
    print(f"  {'lever':<42}{'vs shark':>10}{'vs chum':>10}{'vs chum':>10}  verdict")
    print(f"  {'':<42}{'':>10}{'(strip)':>10}{'(passive)':>10}")
    print("  " + "-" * (W - 4))

    def verdict(ds, dcg, dcp):
        if ds > 0 and min(dcg, dcp) > -60:
            return "EFFICIENT: cuts shark claims, chum barely feels it"
        if ds > 0:
            return "protects vs sharks, costs chum revenue if they strip"
        return "BACKFIRES: forces cushion -> accounts live -> more claims"

    for label, (ds, dcg, dcp) in [
        ("trailing(lock) floor vs fixed", d_fl),
        ("consistency 20% vs none", d_cn),
        ("daily $1k cap vs monthly uncapped", d_cap["daily"]),
        ("5-day $3k cap vs monthly uncapped", d_cap["5day"]),
        ("10-td $1,250 cap vs monthly uncapped", d_cap["10td"]),
    ]:
        print(f"  {label:<42}{ds:>+10,.0f}{dcg:>+10,.0f}{dcp:>+10,.0f}  {verdict(ds, dcg, dcp)}")
    print("     (cap rows measured on trail_lock/$2,000/no-consistency at $364; floor/consistency rows =")
    print("      mini-grid pairwise averages. The BACKFIRES rows are a policy interaction: under the")
    print("      rulemap's withdraw-ASAP policy, rules that delay payouts force the account to build a")
    print("      survival cushion, so it dies less and claims MORE over the year. A cushion-aware shark")
    print("      would blunt this; the behavior module is calibration item #2 in an engagement.")

    # ---------------------------------------------------------- section 3
    print("\n[6/6] win-win frontier scan ...", flush=True)
    print("\n" + "=" * W)
    print("  3) WIN-WIN FRONTIER — cells where the firm survives shark-share <= 10% (s* >= 10%)")
    print("     AND shark extraction >= $10k/yr (rules worth selling AND worth buying)")
    print("=" * W)
    universe = []
    for c in CARDS + GRID + SWEEP:
        pi_s, pi_cg, pi_cp, ss, cg, cpv = econ(c, pnl, idx, Z, bmu, bsg)
        universe.append((c, pi_s, pi_cp, ss, cpv, s_star(pi_s, pi_cp)))
    winwin = [u for u in universe if u[3]["wd"] >= 10_000 and u[5] >= 0.10]
    rich = [u for u in universe if u[3]["wd"] >= 10_000]
    bc, bpi_s, bpi_cp, bss, bcpv, bsp = max(rich, key=lambda u: u[5])
    p_star = (0.1 * (bss["wd"] - RT_MARKUP * SHARK_TPD * bss["days"])
              + 0.9 * (bcpv["wd"] - RT_MARKUP * CHUM_TPD * bcpv["days"]))
    if winwin:
        for c, pi_s, pi_cp, ss, cpv, sp in winwin:
            print(f"  WIN-WIN: {c['name']}  s*={fmt_s(sp).strip()}  "
                  f"extraction ${ss['wd']:,.0f}/yr  firm/chum {pi_cp:+,.0f}")
    else:
        print("  EMPTY — even under the firm-friendly passive-chum scenario, no card on this menu both")
        print("  pays a skilled trader >= $10k/yr and survives 10% skilled flow. Best cell with")
        print(f"  extraction >= $10k:  {bc['name']}  s* = {fmt_s(bsp).strip()}"
              f"  (extraction ${bss['wd']:,.0f}/yr, firm/shark {bpi_s:+,.0f}, firm/chum {bpi_cp:+,.0f})")
        print(f"  What closes the gap: price >= ${p_star:,.0f}/account (vs $364) makes that cell breakeven")
        print(f"  at exactly 10% shark share, i.e. the win-win exists only at ~{p_star / 364:.1f}x current pricing")
        print(f"  or while actual skilled share stays below {fmt_s(bsp).strip()}.")
        safe = [u for u in universe if u[5] >= 0.10]
        if safe:
            mx = max(safe, key=lambda u: u[3]["wd"])
            print(f"  Best shark extraction among cells that DO survive 10% sharks: "
                  f"{mx[0]['name']} at ${mx[3]['wd']:,.0f}/yr (s* {fmt_s(mx[5]).strip()}).")
    n_resets = 252 / max(1e-9, chum_stats(Z, bmu, bsg, "trail_lock", 2000, 0.0, "5day", False)["days"])
    print(f"  Note: unit is per account SOLD. Real chum re-buys after death (~{n_resets:.0f} resets/customer-yr")
    print("  at base, passive) — repurchase revenue raises every s* and is calibration item #1.")

    # ---------------------------------------------------------- section 4
    by = {r["name"].split()[0]: r for r in rows}
    lucid, trad, etf = by["Lucid"], by["Tradeify"], by["ETF"]
    worst = min(rows, key=lambda r: r["pi_s"])
    n_chum_per_shark = abs(worst["pi_s"]) / max(1.0, worst["pi_cp"])
    print("\n" + "=" * W)
    print("  4) TEN LINES FOR A PROP-FIRM CEO")
    print("=" * W)
    for i, ln in enumerate([
        "You are an insurer that has never priced its claims: the account fee is the premium, every payout is a claim.",
        f"Typical losing customers are safe money — roughly +${trad['pi_cp']:,.0f} to +${lucid['pi_cp']:,.0f} each, and they re-buy ~{n_resets:.0f} times a year.",
        f"The unpriced risk is skilled flow: ONE skilled trader on {worst['name']} costs you ${abs(worst['pi_s']):,.0f} per account-year.",
        f"That is ~{n_chum_per_shark:,.0f} ordinary customers carrying one skilled account — your survival is a mix ratio you currently guess.",
        f"We measured it: the card starts bleeding once skilled share passes {fmt_s(etf['sp']).strip()} (ETF), {fmt_s(lucid['sp']).strip()} (Lucid), {fmt_s(trad['sp']).strip()} (Tradeify).",
        f"Rules are not interchangeable: a trailing floor saves +${d_fl[0]:,.0f}/yr against skilled accounts; ordinary customers never notice it.",
        f"A 20% consistency clause BACKFIRES by ${abs(d_cn[0]):,.0f}/yr: delaying payouts forces accounts to build the cushion that keeps them alive.",
        f"Payout caps sit in between: a daily $1k cap is worth +${d_cap['daily'][0]:,.0f}/yr per skilled account versus uncapped monthly.",
        f"Nothing on today's menu both pays skilled traders $10k+/yr and survives 10% skilled flow — that takes ~${p_star:,.0f} pricing, not $364.",
        "Bring us your real customer P&L: we calibrate the loser model to your book, and every rule and price change ships pre-priced.",
    ], 1):
        print(f"  {i:>2}. {ln}")

    # ------------------------------------------------------------- honesty
    print("\n" + "=" * W)
    print("  HONESTY BOX")
    print("=" * W)
    print("  - CHUM is parametric Normal(mu,sigma), not empirical — the 9-combo sensitivity grid is printed")
    print("    so no single mu/sigma carries the conclusion; chum withdrawal behavior is bounded by the")
    print("    strip/passive scenarios. Real firm customer data calibrates both in a paid engagement.")
    print("  - SHARK is one strategy (v12 composed stream, 1 contract) under the rulemap's withdraw-ASAP")
    print("    policy — a floor on skilled-flow diversity, not a population model.")
    print("  - ETF's ~70% eval pass gate is applied to BOTH cohorts as stated; real chum pass rates are")
    print("    lower, which pushes chum P&L toward the kept $99 eval fee (firm-favorable).")
    print("  - Eval-phase commissions and chum re-purchases are excluded from the per-account unit; both")
    print("    are firm-favorable, so the s* figures here are conservative (true robustness sits higher).")
    print("  - Shark late starts use truncated windows (>=60 td), same as the rulemap convention.")
    print("=" * W)
    print("  firm_actuary done.   Run:  python3 brain/research/firm_actuary.py")
    print("=" * W)


if __name__ == "__main__":
    main()
