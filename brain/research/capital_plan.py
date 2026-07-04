"""
brain/research/capital_plan.py

REINVESTMENT-POLICY ENGINE — fastest safe path from 1 account to $10k/mo.

The fleet starts as 1 live Lucid Direct TODAY ($364 initial out-of-pocket).
Growth is self-funded from payouts only (plus an optional +$364/month external
top-up variant). Every account trades the IDENTICAL v12 daily P&L stream
(same signals, same days) — accounts differ only in start date, balance/floor
state, and payout timing. So the stream is built ONCE (build_seq copied
verbatim from empire_rulemap.py) and every policy sim is cheap arithmetic.

ACCOUNT CARDS
  Lucid 50K Direct ($364, funded immediately, max 5):
    trail-lock floor: EOD-trailing $2,000 that locks once it reaches $50k start
    20% consistency: payout requires total profit >= 5 x best day
    payout every >=5 trading days, capped $3,000, keep $1k buffer above start,
    first payout requires >=5 trading days.  (Mechanics == empire run_cell
    trail_lock / 2000 / 0.20 / 5day cell.)
  ETF 50K Static ($99 eval, max 20):
    EVAL modeled honestly: same daily stream from purchase date, STATIC -$2,000
    floor, passes at +$4,000 (cross-check vs historical ~73%, ~10-13 td).
    On pass: $177 activation -> funded: fixed $2,000 floor, NO consistency,
    payout every >=10 trading days capped $1,250 (flat — conservative),
    keep-$1k-above-start buffer assumed (mirrors Lucid; conservative).

POLICIES (purchase gates; Lucid has strict priority until its cap, then ETF)
  reinvest-first : buy the next account the moment wallet cash covers it
  prove-first    : buy account N+1 only after the newest account has taken 2
                   payouts (proxy for "2 consecutive successful payout cycles";
                   a dead/failed newest account unblocks the gate)
  calendar       : at most 1 purchase per calendar month, funds permitting
                   (the day-0 Lucid consumes month 1's slot)
  x replace-on-death : replace=True  -> caps are CONCURRENT (5 Lucid alive /
                                        20 ETF active) so dead accounts are
                                        re-bought from payout cash
                       replace=False -> caps are LIFETIME purchases (5 / 20);
                                        dead accounts are never replaced
  x top-up variant   : +$364 external cash injected on the first trading day
                       of each month after month 1

MECHANICS / TIMING
  - payouts land EOD; purchases/activations happen the same EOD; a bought or
    newly-activated account starts trading the NEXT trading day
  - $177 activations have priority over new purchases
  - consistency counters (total profit / best day) never reset after payout
    (matches empire run_cell / lucid_direct_sim)
  - fleet ledger: cum_net = total payouts - total spend (incl. the day-0 $364
    and activations). Top-up cash sits in the wallet and only hits cum_net
    when spent — so max cash-flow drawdown = worst cumulative out-of-pocket.

METRICS (distribution over fleet-start dates rolled every 5 trading days
across 2024+, each run to the end of available data — horizon reported)
  - months to first $5,000 / $10,000 net calendar month (net = payouts-spend)
  - cumulative net cash at months 6 / 12 / 24 (p10/p50/p90)
  - max cash-flow drawdown (worst cumulative out-of-pocket point)
  - fleet size (funded accounts alive) median at months 6 / 12 / 24

IS/OOS: strategies were tuned in-sample 2022-2024; 2025-2026 is out-of-sample.
Fleet starts include 2024 (IS) — an OOS-only (2025+ starts) column is printed.

Run from repo root:   python3 brain/research/capital_plan.py
First run builds the stream through the backtest engine (~30s-8min depending
on machine); it is then cached to a temp JSON (override path with env
CAPITAL_PLAN_CACHE; delete the cache after a data refresh). The policy grid
itself runs in ~1s.

NOTE (2026-07-03): upstream fix — portfolio_policy.run_year_morning's
regime-ATR window is now a bounded 14-day deque (was unbounded list ->
expanding mean). This script inherits the fix via import; results recorded
BEFORE this date used the buggy morning stream (composed v12 stream deltas:
710 -> 713 trading days, full-period net -0.08%, OOS 2025-26 net +2.4%) —
re-run before citing absolute numbers. Its temp-JSON stream cache was
built PRE-fix and is reused silently — delete it (or set
CAPITAL_PLAN_CACHE to a fresh path) before the re-run.
"""

import sys, os, json, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date, datetime
from collections import defaultdict
from math import isinf

# ── v12 canonical daily stream (copied from empire_rulemap.build_seq) ────────

RISK1 = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}
START, KEEP = 50_000.0, 1_000.0

CACHE = os.environ.get("CAPITAL_PLAN_CACHE",
                       os.path.join(tempfile.gettempdir(), "capital_plan_stream_v12.json"))


def build_components_orb3():
    """eval_boost.build_components, minus the ORB2R leg that build_seq never
    reads (halves build time; the ORB3/REJ/PM/ASIA legs are byte-identical:
    same config flags, same call order)."""
    import config
    import portfolio_policy as pp
    from vwap_fulldata import load_days as vwap_load
    from backtest import load_csv

    comp = {"ORB3": defaultdict(list), "REJ": defaultdict(list),
            "PM": defaultdict(list), "ASIA": defaultdict(list)}

    config.EVAL_MODE = False
    print("  loading bars for morning engine...", flush=True)
    bars = load_csv(pp.DATA)
    morning = []
    for y in pp.YEARS:
        print(f"  morning ORB {y}...", flush=True)
        morning.extend(pp.run_year_morning(bars, y))
    del bars
    for t in morning:
        d = date.fromisoformat(t["date"])
        e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
        x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
        c = max(1, t.get("contracts", 1))
        comp["ORB3"][d].append(("ORB", e, x, t["pnl"] / c))

    print("  rejection / PM / Asia legs...", flush=True)
    rth = vwap_load(pp.DATA)
    eve = pp.load_days(pp.DATA, 16, 21)
    for d in sorted(rth):
        wd, mo = d.weekday(), d.month
        if wd != 0 and mo not in (4, 5, 6, 9, 12):
            r = pp.rejection_day(rth[d])
            if r: comp["REJ"][d].append(("REJ", *r))
        if wd not in (0, 4):
            r = pp.pm_day(rth[d])
            if r: comp["PM"][d].append(("PM", *r))
    for d in sorted(eve):
        if d.weekday() != 3 and d.month not in (8, 11):
            r = pp.asia_day(eve[d])
            if r: comp["ASIA"][d].append(("ASIA", *r))
    return comp


def build_seq():
    """Verbatim day-composition rules from empire_rulemap.build_seq."""
    comp = build_components_orb3()
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


def get_seq():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            raw = json.load(f)
        print(f"Loaded cached stream: {CACHE} ({len(raw)} days)", flush=True)
        return [(date.fromisoformat(d), p) for d, p in raw]
    print("Building v12 daily P&L stream (backtest engine — first run only)...", flush=True)
    t0 = time.time()
    seq = build_seq()
    with open(CACHE, "w") as f:
        json.dump([(d.isoformat(), p) for d, p in seq], f)
    print(f"  built {len(seq)} days in {time.time()-t0:.0f}s — cached to {CACHE}", flush=True)
    return seq


# ── account state machines ───────────────────────────────────────────────────

LUCID_PRICE, LUCID_CAP_N = 364.0, 5
ETF_PRICE, ETF_ACT, ETF_CAP_N = 99.0, 177.0, 20


def new_lucid(k):
    return {"kind": "lucid", "state": "funded", "start_k": k,
            "bal": START, "peak": START, "floor": START - 2000.0,
            "tp": 0.0, "best": 0.0, "last_pay": -99, "n_pay": 0}


def new_etf(k):
    return {"kind": "etf", "state": "eval", "start_k": k, "cum": 0.0,
            "n_pay": 0, "last_pay": -99, "eval_td": 0}


def step_funded(a, p, k):
    """One EOD step for a funded account. Returns payout taken (0 if none).
    Order (add pnl -> death -> payout -> trail) matches empire run_cell."""
    a["bal"] += p
    if a["kind"] == "lucid":
        a["tp"] += p
        a["best"] = max(a["best"], p)
        if a["bal"] <= a["floor"]:
            a["state"] = "dead"
            return 0.0
        rel = k - a["start_k"]
        pay = 0.0
        if (a["tp"] > 0 and a["tp"] >= 5.0 * a["best"]
                and rel - a["last_pay"] >= 5 and rel >= 5):
            avail = min(3000.0, a["bal"] - (START + KEEP))
            if avail > 0:
                a["bal"] -= avail
                pay = avail
                a["last_pay"] = rel
                a["n_pay"] += 1
        a["peak"] = max(a["peak"], a["bal"])
        a["floor"] = max(a["floor"], min(a["peak"] - 2000.0, START))
        return pay
    # ETF funded: static floor, no consistency, 10-td cycle, $1,250 cap
    if a["bal"] <= START - 2000.0:
        a["state"] = "dead"
        return 0.0
    rel = k - a["start_k"]
    pay = 0.0
    if rel >= 10 and rel - a["last_pay"] >= 10:
        avail = min(1250.0, a["bal"] - (START + KEEP))
        if avail > 0:
            a["bal"] -= avail
            pay = avail
            a["last_pay"] = rel
            a["n_pay"] += 1
    return pay


# ── fleet simulation ─────────────────────────────────────────────────────────

def next_kind(accounts, bought_lucid, bought_etf, replace):
    """Which account to buy next (Lucid strict priority), or None if capped."""
    if replace:
        lucid_ok = sum(1 for a in accounts
                       if a["kind"] == "lucid" and a["state"] == "funded") < LUCID_CAP_N
        etf_ok = sum(1 for a in accounts if a["kind"] == "etf"
                     and a["state"] in ("eval", "pending", "funded")) < ETF_CAP_N
    else:
        lucid_ok = bought_lucid < LUCID_CAP_N
        etf_ok = bought_etf < ETF_CAP_N
    if lucid_ok: return "lucid"
    if etf_ok:   return "etf"
    return None


def newest_proven(accounts):
    a = accounts[-1]
    return a["state"] in ("dead", "failed") or a["n_pay"] >= 2


def sim_fleet(seq, i0, policy, replace, topup):
    d0 = seq[i0][0]
    wallet = 0.0
    tot_pay, tot_spend = 0.0, LUCID_PRICE          # day-0 Lucid, external money
    accounts = [new_lucid(i0)]
    bought_lucid, bought_etf = 1, 0
    last_buy_ym = (d0.year, d0.month)
    monthly = defaultdict(lambda: [0.0, 0.0])      # midx -> [payouts, spend]
    monthly[1][1] += LUCID_PRICE
    cum_min = tot_pay - tot_spend
    snaps = {}                                     # midx -> (funded_alive, evals, cum_net)
    ev_pass = ev_fail = 0

    for k in range(i0, len(seq)):
        d, p = seq[k]
        midx = (d.year - d0.year) * 12 + (d.month - d0.month) + 1
        if topup and k > i0 and d.month != seq[k - 1][0].month:
            wallet += 364.0                        # external injection (wallet only)

        for a in accounts:
            if a["start_k"] > k:
                continue
            st = a["state"]
            if st == "eval":
                a["cum"] += p
                a["eval_td"] += 1
                if a["cum"] <= -2000.0:
                    a["state"] = "failed"; ev_fail += 1
                elif a["cum"] >= 4000.0:
                    a["state"] = "pending"; ev_pass += 1
            elif st == "funded":
                pay = step_funded(a, p, k)
                if pay:
                    wallet += pay; tot_pay += pay; monthly[midx][0] += pay

        # activations before purchases
        for a in accounts:
            if a["state"] == "pending" and wallet >= ETF_ACT:
                wallet -= ETF_ACT; tot_spend += ETF_ACT; monthly[midx][1] += ETF_ACT
                a["state"] = "funded"
                a["start_k"] = k + 1
                a["bal"] = START
                a["last_pay"] = -99

        # purchases per policy gate
        while True:
            kind = next_kind(accounts, bought_lucid, bought_etf, replace)
            if kind is None:
                break
            price = LUCID_PRICE if kind == "lucid" else ETF_PRICE
            if wallet < price:
                break
            if policy == "prove" and not newest_proven(accounts):
                break
            if policy == "calendar" and (d.year, d.month) == last_buy_ym:
                break
            wallet -= price; tot_spend += price; monthly[midx][1] += price
            accounts.append(new_lucid(k + 1) if kind == "lucid" else new_etf(k + 1))
            if kind == "lucid": bought_lucid += 1
            else:               bought_etf += 1
            last_buy_ym = (d.year, d.month)

        cum_min = min(cum_min, tot_pay - tot_spend)
        if k == len(seq) - 1 or seq[k + 1][0].month != d.month:
            funded = sum(1 for a in accounts if a["state"] == "funded")
            evals = sum(1 for a in accounts if a["state"] in ("eval", "pending"))
            snaps[midx] = (funded, evals, tot_pay - tot_spend)

    last_midx = max(snaps)
    complete = last_midx - 1                       # last calendar month may be partial
    first5k = first10k = float("inf")
    for m in range(1, last_midx + 1):
        net_m = monthly[m][0] - monthly[m][1]
        if isinf(first5k) and net_m >= 5000.0:  first5k = m
        if isinf(first10k) and net_m >= 10000.0: first10k = m
    return {"first5k": first5k, "first10k": first10k,
            "cum": {m: snaps[m][2] for m in snaps},
            "alive": {m: snaps[m][0] for m in snaps},
            "dd": -(cum_min),                      # worst out-of-pocket, positive $
            "complete": complete,
            "ev_pass": ev_pass, "ev_fail": ev_fail}


# ── metrics helpers ──────────────────────────────────────────────────────────

def pct(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def fm(x):        # money
    return "n/a" if x is None else f"{x:>+,.0f}"


def fmo(x):       # months (may be inf)
    if x is None: return "n/a"
    return "—" if isinf(x) else f"{x:.0f}"


def agg(results, starts):
    """Aggregate one variant's per-start results into the metric row."""
    out = {}
    for m in (6, 12, 24):
        c = [r["cum"][m] for r in results if r["complete"] >= m]
        a = [r["alive"][m] for r in results if r["complete"] >= m]
        out[f"cum{m}"] = (pct(c, .10), pct(c, .50), pct(c, .90), len(c))
        out[f"alive{m}"] = pct(a, .50)
        if m == 12 and c:      # ruin branch: account 1 died before ever paying
            out["stuck12"] = sum(1 for x in c if x <= 0) / len(c)
    f5 = [r["first5k"] for r in results]
    f10 = [r["first10k"] for r in results]
    out["f5"] = (pct(f5, .10), pct(f5, .50), pct(f5, .90),
                 sum(1 for x in f5 if not isinf(x)) / len(f5))
    out["f10"] = (pct(f10, .10), pct(f10, .50), pct(f10, .90),
                  sum(1 for x in f10 if not isinf(x)) / len(f10))
    dd = [r["dd"] for r in results]
    out["dd_worst"] = max(dd)
    out["dd_p50"] = pct(dd, .50)
    # IS (2024 starts) vs OOS (2025+ starts) month-12 cum net
    is12 = [r["cum"][12] for r, s in zip(results, starts)
            if r["complete"] >= 12 and s.year == 2024]
    oos12 = [r["cum"][12] for r, s in zip(results, starts)
             if r["complete"] >= 12 and s.year >= 2025]
    out["is12"] = (pct(is12, .50), len(is12))
    out["oos12"] = (pct(oos12, .50), len(oos12))
    out["horizon"] = (min(r["complete"] for r in results),
                      pct([r["complete"] for r in results], .50),
                      max(r["complete"] for r in results))
    ep = sum(r["ev_pass"] for r in results)
    ef = sum(r["ev_fail"] for r in results)
    out["evals"] = (ep, ef)
    return out


# ── standalone validation (single account vs known references) ───────────────

def validate(seq, idx_all):
    print(f"\n{'─'*100}")
    print("  VALIDATION vs known references (same stream, single account)")
    # Lucid solo, 12-month horizon (lucid_direct_sim reference)
    res = []
    for i in idx_all:
        a = new_lucid(i)
        wd = 0.0
        died = False
        fp = None
        for k in range(i, min(i + 252, len(seq))):
            pay = step_funded(a, seq[k][1], k)
            wd += pay
            if pay and fp is None: fp = k - i + 1
            if a["state"] == "dead":
                died = True; break
        res.append((wd, died, fp))
    n = len(res)
    fps = sorted(r[2] for r in res if r[2] is not None)
    print(f"  Lucid solo (12mo, {n} starts): survive {sum(1 for r in res if not r[1])/n:.0%}, "
          f"mean withdrawn ${sum(r[0] for r in res)/n:,.0f}, "
          f"median 1st payout {fps[len(fps)//2] if fps else -1} td "
          f"({len(fps)/n:.0%} reach payout)", flush=True)
    # ETF eval solo (expect ~73% pass, median ~10-13 td)
    p_days, fails, censored = [], 0, 0
    for i in idx_all:
        cum = 0.0
        hit = None
        for k in range(i, len(seq)):
            cum += seq[k][1]
            if cum <= -2000.0: hit = "fail"; break
            if cum >= 4000.0:  hit = ("pass", k - i + 1); break
        if hit == "fail": fails += 1
        elif hit is None: censored += 1
        else: p_days.append(hit[1])
    tot = len(p_days) + fails
    p_days.sort()
    print(f"  ETF eval solo ({tot} resolved, {censored} censored): "
          f"pass {len(p_days)/tot:.0%} (historical ~73%), "
          f"median {p_days[len(p_days)//2]} td to pass (historical ~10-13)", flush=True)


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t_start = time.time()
    seq = get_seq()
    d_first, d_last = seq[0][0], seq[-1][0]
    net_all = sum(p for _, p in seq)
    print(f"Stream: {len(seq)} trading days, {d_first} -> {d_last}, "
          f"1c net ${net_all:,.0f}", flush=True)

    # fleet-start worlds: every 5th trading day across 2024+, >=126 td forward
    idx_all = [i for i, (d, _) in enumerate(seq) if d.year >= 2024]
    starts_i = [i for i in idx_all[::5] if len(seq) - i >= 126]
    starts_d = [seq[i][0] for i in starts_i]
    print(f"Fleet-start worlds: {len(starts_i)} (every 5 td, {starts_d[0]} -> {starts_d[-1]}, "
          f"each run to data end)", flush=True)

    validate(seq, idx_all)

    POLICIES = ["reinvest", "prove", "calendar"]
    LABEL = {"reinvest": "reinvest-first", "prove": "prove-first", "calendar": "calendar"}
    grid = {}
    print(f"\n{'─'*100}")
    print("  Running policy grid (3 policies x replace on/off x top-up on/off "
          f"x {len(starts_i)} starts)...", flush=True)
    for policy in POLICIES:
        for replace in (False, True):
            for topup in (False, True):
                t0 = time.time()
                res = [sim_fleet(seq, i, policy, replace, topup) for i in starts_i]
                grid[(policy, replace, topup)] = agg(res, starts_d)
                print(f"    {LABEL[policy]:<15} replace={str(replace):<5} "
                      f"topup={str(topup):<5} done in {time.time()-t0:4.1f}s", flush=True)

    hz = grid[(POLICIES[0], False, False)]["horizon"]
    print(f"\n  Horizon actually used per start (complete months): "
          f"min {hz[0]} / median {hz[1]} / max {hz[2]}", flush=True)

    # ── leaderboard (base: self-funded, no top-up), sorted by cum net @ m12 p50
    def row_key(k): return grid[k]["cum12"][1] if grid[k]["cum12"][1] is not None else -1e18
    base_keys = sorted([k for k in grid if not k[2]], key=row_key, reverse=True)

    W = 118
    print(f"\n{'═'*W}")
    print("  POLICY LEADERBOARD — self-funded (no top-up), sorted by cumulative net cash at month 12 (p50)")
    print(f"  net = payouts - all spend (incl. day-0 $364). alive = funded accounts. DD = worst out-of-pocket.")
    print(f"{'═'*W}")
    print(f"  {'#':<2} {'policy':<15} {'repl':<5} {'net@m6 p50':>11} "
          f"{'net@m12 p10/p50/p90':>26} {'net@m24 p50':>12} "
          f"{'5k mo':>6} {'10k mo':>7} {'rch10k':>7} {'DDworst':>8} {'aliv12':>7}")
    print(f"  {'─'*(W-4)}")
    for r, key in enumerate(base_keys, 1):
        g = grid[key]
        c12 = g["cum12"]
        print(f"  {r:<2} {LABEL[key[0]]:<15} {'yes' if key[1] else 'no':<5} "
              f"{fm(g['cum6'][1]):>11} "
              f"{fm(c12[0]):>9}/{fm(c12[1]):>8}/{fm(c12[2]):>8} "
              f"{fm(g['cum24'][1]):>12} "
              f"{fmo(g['f5'][1]):>6} {fmo(g['f10'][1]):>7} {g['f10'][3]:>7.0%} "
              f"{fm(-g['dd_worst']):>8} {g['alive12']:>7}", flush=True)
    n6 = grid[base_keys[0]]["cum6"][3]; n12 = grid[base_keys[0]]["cum12"][3]
    n24 = grid[base_keys[0]]["cum24"][3]
    print(f"  starts covering month 6 / 12 / 24: {n6} / {n12} / {n24}  "
          f"(5k/10k month cols = p50 months-to-first; rch10k = share of starts that ever get there)")

    # detail: months-to percentiles + fleet trajectory for base variants
    print(f"\n  DETAIL (self-funded): months to first $5k / $10k month (p10/p50/p90), ruin branch, fleet medians")
    print(f"  {'policy':<15} {'repl':<5} {'5k p10/50/90':>15} {'reach':>6} "
          f"{'10k p10/50/90':>16} {'reach':>6} {'stuck@12':>9} {'alive m6/m12/m24':>17} {'evals P/F':>10}")
    for key in base_keys:
        g = grid[key]
        f5, f10 = g["f5"], g["f10"]
        print(f"  {LABEL[key[0]]:<15} {'yes' if key[1] else 'no':<5} "
              f"{fmo(f5[0]):>4}/{fmo(f5[1]):>4}/{fmo(f5[2]):>4} {f5[3]:>6.0%} "
              f"{fmo(f10[0]):>5}/{fmo(f10[1]):>4}/{fmo(f10[2]):>4} {f10[3]:>6.0%} "
              f"{g['stuck12']:>9.0%} "
              f"{g['alive6']:>5}/{g['alive12']:>4}/{str(g['alive24']):>4} "
              f"{g['evals'][0]:>5}/{g['evals'][1]}")
    print(f"  (stuck@12 = share of starts with net <= 0 at month 12: account #1 died before its first payout,")
    print(f"   and with self-funding there is no cash to replace it — the fleet is frozen at -$364 forever.)")

    # ── top-up variant: what +$364/month external buys you
    print(f"\n{'─'*W}")
    print("  TOP-UP VARIANT (+$364/month external): cumulative net @ m12 p50 vs self-funded")
    for key in base_keys:
        g0, g1 = grid[key], grid[(key[0], key[1], True)]
        d12 = (g1["cum12"][1] or 0) - (g0["cum12"][1] or 0)
        print(f"  {LABEL[key[0]]:<15} {'yes' if key[1] else 'no':<5} "
              f"m12 {fm(g1['cum12'][1]):>9} (Δ {fm(d12):>8})   "
              f"10k mo {fmo(g1['f10'][1]):>3} (base {fmo(g0['f10'][1])})   "
              f"stuck@12 {g1['stuck12']:>4.0%} (base {g0['stuck12']:>3.0%})   "
              f"DDworst {fm(-g1['dd_worst']):>7}")

    # ── IS vs OOS starts
    print(f"\n{'─'*W}")
    print("  IS/OOS CHECK — net @ m12 p50 by fleet-start year (2024 = in-sample tuning years; 2025+ = OOS)")
    for key in base_keys:
        g = grid[key]
        print(f"  {LABEL[key[0]]:<15} {'yes' if key[1] else 'no':<5} "
              f"IS-2024 starts {fm(g['is12'][0]):>9} (N={g['is12'][1]:>2})   "
              f"OOS-2025+ starts {fm(g['oos12'][0]):>9} (N={g['oos12'][1]:>2})")

    # ── recommendation: near-ties on m12 broken by earlier 10k month, lower DD
    best12 = grid[base_keys[0]]["cum12"][1]
    cands = [k for k in base_keys
             if grid[k]["cum12"][1] is not None and grid[k]["cum12"][1] >= 0.97 * best12]
    cands.sort(key=lambda k: (grid[k]["f10"][1] if grid[k]["f10"][1] is not None else float("inf"),
                              grid[k]["dd_worst"],
                              -(grid[k]["cum12"][1] or 0)))
    rk = cands[0]
    g = grid[rk]
    print(f"\n{'═'*W}")
    print(f"  RECOMMENDED POLICY: {LABEL[rk[0]]} / replace-on-death={'yes' if rk[1] else 'no'} (self-funded)")
    print(f"    net by month 12 (p50): ${g['cum12'][1]:,.0f}   "
          f"(p10 ${g['cum12'][0]:,.0f} / p90 ${g['cum12'][2]:,.0f})")
    print(f"    first $5k net month:  month {fmo(g['f5'][1])} (p50)   "
          f"first $10k net month: month {fmo(g['f10'][1])} (p50, {g['f10'][3]:.0%} of starts reach)")
    print(f"    worst cash drawdown:  ${g['dd_worst']:,.0f} out-of-pocket   "
          f"fleet @ m12: {g['alive12']} funded accounts (median)")
    print(f"    ruin branch: {g['stuck12']:.0%} of starts freeze at -$364 (account #1 dies pre-payout;")
    gt = grid[(rk[0], rk[1], True)]
    print(f"    self-funding cannot replace it). The +$364/mo top-up variant clears it: "
          f"stuck {gt['stuck12']:.0%}, m12 p50 ${gt['cum12'][1]:,.0f},")
    print(f"    first $10k month {fmo(gt['f10'][1])} ({gt['f10'][3]:.0%} reach), "
          f"worst out-of-pocket ${gt['dd_worst']:,.0f} — cheap ruin insurance.")
    print(f"{'═'*W}\n  capital_plan done in {time.time()-t_start:,.0f}s.\n{'═'*W}")
