"""
brain/research/ratchet_lock.py

RATCHET LOCK — the user's proposal, verbatim intent:
  "once the accounts hit a certain point it goes on lockdown... if we squeeze
   10k out of it and it starts tanking it won't go back under 2k profit"

MECHANIC UNDER TEST: once account lifetime P&L (strategy-tracked; withdrawals
do NOT reduce it) reaches an ARM level, if lifetime P&L ever falls back to a
LOCK FLOOR the robot permanently halts new entries. The account is then
HARVESTED (drain balance above START across payout cycles — $3k cap, >=5-sd
cadence, frozen lifetime consistency total>=5x best must still hold), CLOSED,
and the freed Lucid slot is refilled with a fresh $364 account SAME DAY.
Lockdown = voluntary death at +LOCK instead of involuntary death at the floor;
recycling cost ($364) is identical either way.

BASELINE = house policy from buffer_policy.py: Lucid 50K Direct, KEEP=$5,000,
ramp ON (morning-only until lifetime >= +$800, then full v12 stack, one-way
latch), EOD trailing floor max(48k, min(peak-2k, 50k)) (locks at 50k once
peak >= 52k; peak taken pre-withdrawal), payout = min($3k, bal-50k-KEEP) when
>=5 sd since start & since last payout and lifetime total >= 5x best day,
death = EOD balance <= floor. No lock, ride to death.

KEY ALGEBRA (Part 3 honesty, derived before simulating): with KEEP=$5,000 the
first payout needs EOD bal > $55k, which locks the Lucid floor at $50k. From
then on balance-above-start = lifetime - W (W = total withdrawn) and death
occurs exactly when lifetime <= W. A lifetime-defined lock at +L can therefore
only fire while W < L; once cumulative withdrawals pass L the lock is a DEAD
LETTER and the account rides to death regardless. The user's literal cell
(ARM extracted>=10k, LOCK +2k) has W >= 10k > 2k at arm time => never fires.
The balance-defined reading of his words ("won't go back under 2k profit" ==
halt when balance-above-start <= +$2k) IS reachable; it is simulated as a
clearly-labeled BAL-variant grid + fleet policy c2.

GRID (Part 1, single account, ramp ON, run to death/close/data-end):
  ARM  in {after 1st payout, lifetime>=5k, lifetime>=8k, extracted>=10k}
  LOCK in {+1k, +2k, +3k, trailing peak_lifetime-4k}  on lifetime P&L
  plus BAL-variant locks {bal+1k, bal+2k, bal+3k} on balance-above-start.
  Cohorts: 2024+ starts, 2022-23 cold starts, all-history (>=60 sd ahead).
  Per cell: 12-mo extracted (incl. harvest) p10/p50/p90/mean vs baseline,
  %arm/%lock/%die-unlocked/%survive, E[cash|lock] vs E[cash|death],
  %locks where harvest was consistency-blocked (forfeit), %dead-letter.

FLEET (Part 2, buffer_policy Part-2 conventions): 5 Lucid slots, day-0 account
external $364, growth purchases from wallet, deaths AND lock-harvest-closes
replaced same day guaranteed (wallet first, external if short), new accounts
start next sd in ramp mode, rolling 2024+ fleet starts every 5 sd with >=126
sd ahead, 24-month horizon. Policies: (a) baseline ride-to-death, (b) best-IS
ratchet cell from Part 1 (2024+ cohort, live cells only), (c) user's literal
(ext>=10k / +2k lifetime — provable no-op), (c2) user's intent (ext>=10k /
bal<=+2k). Metric: net cash extracted = payouts+harvests - ALL $364 purchases;
p10/p50/p90 at m12/m24; deaths vs locks vs forfeit-closes.

HARVEST / SLOT ASSUMPTIONS (stated, Part 3 tests the sensitivity):
  - a locked account drains min($3k, bal-START) per eligible cycle; the final
    draining withdrawal takes balance to START == voluntary close; slot then
    refills same day (default: the slot is OCCUPIED while draining).
  - if frozen consistency fails at lock (lifetime < 5x best day; it can never
    heal because no new trades), the remainder above START is unpayable =>
    close immediately, forfeit the inside, recycle the slot same day.
  - Part-3 variant: slot freed ON LOCK DAY (assume Lucid lets you buy the
    replacement while the locked account drains off-slot / transfers).

Run from repo root:  python3 brain/research/ratchet_lock.py
Reuses $TMPDIR/buffer_policy_streams_v12.json (override env BUFFER_POLICY_CACHE);
if absent, shell-copies eval_boost.py -> _eb_snap5.py and rebuilds both streams
exactly as buffer_policy.py does. Never edits existing files.
"""

import sys, os, json, time, tempfile, shutil
from datetime import date

RES = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RES)
sys.path.insert(0, os.path.dirname(os.path.dirname(RES)))

RISK1 = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}
START = 50_000.0
RAMP_AT = 800.0
PAY_CAP = 3000.0
KEEP = 5000.0                    # house policy (buffer_policy verdict)
LUCID_PRICE, SLOTS = 364.0, 5
CACHE = os.environ.get("BUFFER_POLICY_CACHE",
                       os.path.join(tempfile.gettempdir(),
                                    "buffer_policy_streams_v12.json"))

ARMS = [("pay1", 0.0, "1st-payout"), ("life", 5000.0, "life>=5k"),
        ("life", 8000.0, "life>=8k"), ("ext", 10000.0, "extr>=10k")]
LOCKS_LIFE = [("fix", 1000.0, "+1k"), ("fix", 2000.0, "+2k"),
              ("fix", 3000.0, "+3k"), ("trail", 4000.0, "peak-4k")]
LOCKS_BAL = [("fix", 1000.0, "bal+1k"), ("fix", 2000.0, "bal+2k"),
             ("fix", 3000.0, "bal+3k")]
USER_ARM, USER_LOCK = ("ext", 10000.0, "extr>=10k"), ("fix", 2000.0, "+2k")


# ── streams (identical machinery to buffer_policy.py) ────────────────────────

def compose_day(lst):
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


def build_streams():
    snap = os.path.join(RES, "_eb_snap5.py")
    if not os.path.exists(snap):
        shutil.copyfile(os.path.join(RES, "eval_boost.py"), snap)
        print(f"  snapshotted eval_boost.py -> {snap}", flush=True)
    from _eb_snap5 import build_components
    comp = build_components()
    all_days = sorted(set().union(*[set(comp[k]) for k in ("ORB3", "REJ", "PM", "ASIA")]))
    rows = []
    for d in all_days:
        full = []
        for k in ("ORB3", "REJ", "PM", "ASIA"):
            key = "ORB" if k == "ORB3" else k
            full.extend((key, *t[1:]) for t in comp[k].get(d, []))
        morn = [("ORB", *t[1:]) for t in comp["ORB3"].get(d, [])]
        rows.append((d, compose_day(full), compose_day(morn) if morn else 0.0))
    return rows


def get_streams():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            raw = json.load(f)
        print(f"Loaded cached streams: {CACHE} ({len(raw)} days)", flush=True)
        return [(date.fromisoformat(d), pf, pm) for d, pf, pm in raw]
    print("Cache absent — rebuilding streams via _eb_snap5 (first run only)...", flush=True)
    t0 = time.time()
    seq = build_streams()
    with open(CACHE, "w") as f:
        json.dump([(d.isoformat(), pf, pm) for d, pf, pm in seq], f)
    print(f"  built {len(seq)} days in {time.time()-t0:.0f}s — cached", flush=True)
    return seq


def months_between(d0, d1):
    return (d1.year - d0.year) * 12 + (d1.month - d0.month)


def pct(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def fm(x):
    return "   n/a" if x is None else f"{x:>+,.0f}"


# ── house single-account (verbatim buffer_policy.run_single, for parity) ─────

def run_single_house(seq, i0, keep, ramp=True, horizon=None, cap=PAY_CAP,
                     peak_before_pay=True):
    d0 = seq[i0][0]
    bal = peak = START
    floor = START - 2000.0
    life_pnl = 0.0
    latched = not ramp
    tp = best = 0.0
    wd = wd12 = 0.0
    last_pay = -99
    first_pay = None
    n_pay = 0
    end = len(seq) if horizon is None else min(len(seq), i0 + horizon)
    died = False
    for k in range(i0, end):
        d, pf, pm = seq[k]
        p = pf if latched else pm
        bal += p
        life_pnl += p
        tp += p
        best = max(best, p)
        if not latched and life_pnl >= RAMP_AT:
            latched = True
        if bal <= floor:
            died = True
            break
        if peak_before_pay:
            peak = max(peak, bal)
            floor = max(floor, min(peak - 2000.0, START))
        rel = k - i0
        if keep is not None and tp > 0 and tp >= 5.0 * best and rel >= 5 \
                and rel - last_pay >= 5:
            avail = bal - (START + keep)
            if cap is not None:
                avail = min(cap, avail)
            if avail > 0:
                bal -= avail
                wd += avail
                if months_between(d0, d) < 12:
                    wd12 += avail
                n_pay += 1
                last_pay = rel
                if first_pay is None:
                    first_pay = rel + 1
        if not peak_before_pay:
            peak = max(peak, bal)
            floor = max(floor, min(peak - 2000.0, START))
    return {"died": died, "n_pay": n_pay, "wd": wd, "wd12": wd12}


# ── ratchet single-account engine ────────────────────────────────────────────

def run_ratchet(seq, i0, arm=None, lock=None, mode="life", keep=KEEP, cap=PAY_CAP):
    """arm=('pay1'|'life'|'ext', threshold, label) or None (== house baseline).
    lock=('fix'|'trail', level, label). mode: lock metric = lifetime P&L
    ('life') or balance-above-start ('bal'). Returns outcome dict."""
    d0 = seq[i0][0]
    bal = peak = START
    floor = START - 2000.0
    life = peak_life = 0.0
    latched = False
    tp = best = 0.0
    W = wd12 = harv = 0.0
    last_pay = -99
    n_pay = 0
    armed = locked = died = closed = hv_blocked = False
    at = arm[0] if arm else None
    av = arm[1] if arm else 0.0
    lt = lock[0] if lock else None
    lv = lock[1] if lock else 0.0
    for k in range(i0, len(seq)):
        d, pf, pm = seq[k]
        rel = k - i0
        if locked:                                   # harvest phase, no trading
            if rel >= 5 and rel - last_pay >= 5:
                w = min(cap, bal - START)
                if w > 0:
                    bal -= w
                    W += w
                    harv += w
                    if months_between(d0, d) < 12:
                        wd12 += w
                    last_pay = rel
                    n_pay += 1
                if bal <= START + 1e-9:
                    closed = True
                    break
            continue
        p = pf if latched else pm
        bal += p
        life += p
        tp += p
        best = max(best, p)
        if not latched and life >= RAMP_AT:
            latched = True
        peak_life = max(peak_life, life)
        if bal <= floor:
            died = True
            break
        peak = max(peak, bal)
        floor = max(floor, min(peak - 2000.0, START))
        if tp > 0 and tp >= 5.0 * best and rel >= 5 and rel - last_pay >= 5:
            avail = min(cap, bal - (START + keep))
            if avail > 0:
                bal -= avail
                W += avail
                if months_between(d0, d) < 12:
                    wd12 += avail
                n_pay += 1
                last_pay = rel
        if at is not None and not armed:
            if (at == "pay1" and n_pay >= 1) or (at == "life" and life >= av) \
                    or (at == "ext" and W >= av):
                armed = True
        if armed and lt is not None and not locked:
            lvl = (peak_life - lv) if lt == "trail" else lv
            metric = life if mode == "life" else (bal - START)
            if metric <= lvl:
                locked = True
                if not (tp > 0 and tp >= 5.0 * best):   # frozen consistency dead
                    hv_blocked = True
                    closed = True                        # forfeit inside, close
                    break
    dead_letter = False
    if armed and not locked and lt is not None and mode == "life":
        lvl = (peak_life - lv) if lt == "trail" else lv
        dead_letter = W >= lvl                 # death threshold above lock level
    return {"died": died, "armed": armed, "locked": locked, "closed": closed,
            "hvb": hv_blocked, "wd": W, "wd12": wd12, "harv": harv,
            "n_pay": n_pay, "dl": dead_letter,
            "obs12": months_between(d0, seq[-1][0]) >= 12}


# ── fleet engine (buffer_policy sim_fleet conventions, Lucid-only) ───────────

def new_acct(k):
    return {"k0": k, "bal": START, "peak": START, "floor": START - 2000.0,
            "life": 0.0, "plife": 0.0, "latched": False, "tp": 0.0,
            "best": 0.0, "W": 0.0, "last_pay": -99, "n_pay": 0,
            "armed": False, "state": "funded", "freed": False}


def sim_fleet(seq, i0, arm=None, lock=None, mode="life", free_at_lock=False,
              keep=KEEP):
    at = arm[0] if arm else None
    av = arm[1] if arm else 0.0
    lt = lock[0] if lock else None
    lv = lock[1] if lock else 0.0
    d0 = seq[i0][0]
    wallet = 0.0
    external = tot_spend = LUCID_PRICE          # day-0 account is external cash
    tot_pay = 0.0
    purchases = 1
    deaths = locks = closes = forfeits = 0
    accounts = [new_acct(i0)]
    snaps = {}
    for k in range(i0, len(seq)):
        d, pf, pm = seq[k]
        freed = 0
        pay = 0.0
        for a in accounts:
            if a["k0"] > k:
                continue
            st = a["state"]
            if st == "locked":
                rel = k - a["k0"]
                if rel >= 5 and rel - a["last_pay"] >= 5:
                    w = min(PAY_CAP, a["bal"] - START)
                    if w > 0:
                        a["bal"] -= w
                        pay += w
                        a["last_pay"] = rel
                    if a["bal"] <= START + 1e-9:
                        a["state"] = "closed"
                        closes += 1
                        if not a["freed"]:
                            freed += 1
                continue
            if st != "funded":
                continue
            p = pf if a["latched"] else pm
            a["bal"] += p
            a["life"] += p
            a["tp"] += p
            if p > a["best"]:
                a["best"] = p
            if not a["latched"] and a["life"] >= RAMP_AT:
                a["latched"] = True
            if a["life"] > a["plife"]:
                a["plife"] = a["life"]
            if a["bal"] <= a["floor"]:
                a["state"] = "dead"
                deaths += 1
                freed += 1
                continue
            if a["bal"] > a["peak"]:
                a["peak"] = a["bal"]
            f2 = min(a["peak"] - 2000.0, START)
            if f2 > a["floor"]:
                a["floor"] = f2
            rel = k - a["k0"]
            if a["tp"] > 0 and a["tp"] >= 5.0 * a["best"] and rel >= 5 \
                    and rel - a["last_pay"] >= 5:
                avail = min(PAY_CAP, a["bal"] - (START + keep))
                if avail > 0:
                    a["bal"] -= avail
                    pay += avail
                    a["W"] += avail
                    a["n_pay"] += 1
                    a["last_pay"] = rel
            if at is not None and not a["armed"]:
                if (at == "pay1" and a["n_pay"] >= 1) \
                        or (at == "life" and a["life"] >= av) \
                        or (at == "ext" and a["W"] >= av):
                    a["armed"] = True
            if at is not None and a["armed"]:
                lvl = (a["plife"] - lv) if lt == "trail" else lv
                metric = a["life"] if mode == "life" else (a["bal"] - START)
                if metric <= lvl:
                    locks += 1
                    if not (a["tp"] > 0 and a["tp"] >= 5.0 * a["best"]):
                        a["state"] = "closed"       # consistency dead: forfeit
                        closes += 1
                        forfeits += 1
                        freed += 1
                    else:
                        a["state"] = "locked"
                        if free_at_lock:
                            a["freed"] = True
                            freed += 1
        tot_pay += pay
        wallet += pay
        for _ in range(freed):                  # same-day guaranteed refills
            use = min(wallet, LUCID_PRICE)
            wallet -= use
            external += LUCID_PRICE - use
            tot_spend += LUCID_PRICE
            purchases += 1
            accounts.append(new_acct(k + 1))
        while True:                             # growth purchases from wallet
            occ = sum(1 for a in accounts if a["state"] == "funded"
                      or (a["state"] == "locked" and not a["freed"]))
            if occ >= SLOTS or wallet < LUCID_PRICE:
                break
            wallet -= LUCID_PRICE
            tot_spend += LUCID_PRICE
            purchases += 1
            accounts.append(new_acct(k + 1))
        if k == len(seq) - 1 or seq[k + 1][0].month != d.month:
            snaps[months_between(d0, d) + 1] = (
                tot_pay - tot_spend, deaths, locks, closes, forfeits,
                purchases, external)
    return {"snaps": snaps, "complete": max(snaps) - 1}


def agg_fleet(results):
    out = {}
    for m in (12, 24):
        rows = [r["snaps"][m] for r in results if r["complete"] >= m]
        if not rows:
            out[m] = None
            continue
        net = [r[0] for r in rows]
        out[m] = {"n": len(rows),
                  "net": (pct(net, .10), pct(net, .50), pct(net, .90)),
                  "deaths": pct([r[1] for r in rows], .50),
                  "locks": pct([r[2] for r in rows], .50),
                  "forf": pct([r[4] for r in rows], .50),
                  "buys": pct([r[5] for r in rows], .50),
                  "ext": (pct([r[6] for r in rows], .50),
                          max(r[6] for r in rows))}
    return out


# ── aggregation for the Part-1 grid ──────────────────────────────────────────

def agg_cell(rs):
    n = len(rs)
    o12 = [r for r in rs if r["obs12"]]
    w = [r["wd12"] for r in o12]
    lk = [r for r in rs if r["locked"]]
    dd = [r for r in rs if r["died"]]
    return {"n": n, "n12": len(o12),
            "p10": pct(w, .10), "p50": pct(w, .50), "p90": pct(w, .90),
            "mean": sum(w) / len(w) if w else None,
            "arm": sum(1 for r in rs if r["armed"]) / n,
            "lock": len(lk) / n,
            "die": len(dd) / n,
            "surv": sum(1 for r in rs if not r["died"] and not r["locked"]
                        and not r["closed"]) / n,
            "e_lock": sum(r["wd"] for r in lk) / len(lk) if lk else None,
            "e_dead": sum(r["wd"] for r in dd) / len(dd) if dd else None,
            "hvok": (sum(1 for r in lk if not r["hvb"]) / len(lk)) if lk else None,
            "dl": sum(1 for r in rs if r["dl"]) / n,
            "harv": sum(r["harv"] for r in lk) / len(lk) if lk else None}


def fp_(x):
    return " n/a" if x is None else f"{x:4.0%}"


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t_all = time.time()
    seq = get_streams()
    d_first, d_last = seq[0][0], seq[-1][0]
    print(f"Streams: {len(seq)} sd {d_first} -> {d_last} | 1c net: "
          f"full ${sum(pf for _, pf, _ in seq):,.0f}, "
          f"morning ${sum(pm for _, _, pm in seq):,.0f}", flush=True)

    starts_all = [i for i in range(len(seq)) if len(seq) - i >= 60]
    coh_defs = [("2024+ (hot IS)", [i for i in starts_all if seq[i][0].year >= 2024]),
                ("2022-23 (cold)", [i for i in starts_all if seq[i][0].year <= 2023]),
                ("all-history", starts_all)]

    # ── validation 1: ratchet engine with arm=None must equal the house sim ──
    print(f"\n{'═'*118}\n  VALIDATION\n{'═'*118}")
    t0 = time.time()
    mism = 0
    for i in starts_all:
        h = run_single_house(seq, i, KEEP)
        r = run_ratchet(seq, i, arm=None)
        if (h["died"] != r["died"] or h["n_pay"] != r["n_pay"]
                or abs(h["wd"] - r["wd"]) > 1e-6 or abs(h["wd12"] - r["wd12"]) > 1e-6):
            mism += 1
    print(f"  parity ratchet-engine(no lock) vs buffer_policy.run_single "
          f"(KEEP=5000, {len(starts_all)} starts): "
          f"{'OK — bit-identical' if mism == 0 else f'** {mism} MISMATCHES **'}"
          f"  ({time.time()-t0:.1f}s)", flush=True)

    # ── validation 2: ramp-ON anchor (~72% ever pay, buffer_policy conventions)
    s24 = [i for i in starts_all if seq[i][0].year >= 2024]
    rs = [run_single_house(seq, i, 1000.0, horizon=252, cap=None) for i in s24]
    paid = sum(1 for r in rs if r["n_pay"] > 0) / len(rs)
    unp = sum(1 for r in rs if r["died"] and r["n_pay"] == 0) / len(rs)
    avg = sum(r["wd"] for r in rs) / len(rs)
    print(f"  anchor (KEEP=1000, uncapped, 252sd, 2024+): pay {paid:.0%} "
          f"(prior ~72%) | die-unpaid {unp:.0%} (~10%) | avg extracted "
          f"${avg:,.0f} (~$12,979)", flush=True)

    # ═══ PART 1 — single-account grid ════════════════════════════════════════
    cells_life = [(a, l) for a in ARMS for l in LOCKS_LIFE]
    cells_bal = [(a, l) for a in ARMS for l in LOCKS_BAL]
    print(f"\n{'═'*118}")
    print(f"  PART 1 — SINGLE ACCOUNT, ramp ON, KEEP=$5,000, $3k payout cap, "
          f"run to death/lock-close/data-end (no replacement credit)")
    print(f"  lock metric: LIFETIME P&L (robot-trackable). BAL-variant grid "
          f"below maps the user's words onto balance-above-start.")
    print(f"{'═'*118}", flush=True)

    t0 = time.time()
    base_res = {i: run_ratchet(seq, i, arm=None) for i in starts_all}
    grid = {}
    for a, l in cells_life:
        grid[("life", a, l)] = {i: run_ratchet(seq, i, arm=a, lock=l, mode="life")
                                for i in starts_all}
    for a, l in cells_bal:
        grid[("bal", a, l)] = {i: run_ratchet(seq, i, arm=a, lock=l, mode="bal")
                               for i in starts_all}
    print(f"  simulated {1 + len(grid)} configs x {len(starts_all)} starts "
          f"in {time.time()-t0:.1f}s", flush=True)

    hdr = (f"  {'arm':<11}{'lock':<9}| {'p10':>7} {'p50':>7} {'p90':>7} "
           f"{'mean':>7} {'Δmean':>7} | {'arm%':>5} {'lock%':>5} {'die%':>5} "
           f"{'srv%':>5} | {'E$|lock':>8} {'E$|dead':>8} {'hvOK%':>5} {'dl%':>5}")
    cell_stats = {}
    for cname, cidx in coh_defs:
        print(f"\n  ── cohort: {cname}  (N={len(cidx)}, obs12 N="
              f"{sum(1 for i in cidx if base_res[i]['obs12'])}) "
              + "─" * 60)
        print(hdr)
        print(f"  {'─'*114}")
        b = agg_cell([base_res[i] for i in cidx])
        print(f"  {'BASELINE':<11}{'(none)':<9}| {fm(b['p10']):>7} {fm(b['p50']):>7} "
              f"{fm(b['p90']):>7} {fm(b['mean']):>7} {'':>7} | {'':>5} {'':>5} "
              f"{fp_(b['die']):>5} {fp_(b['surv']):>5} | {'':>8} "
              f"{fm(b['e_dead']):>8} {'':>5} {'':>5}")
        cell_stats[(cname, "BASE")] = b
        for mode, cells in (("life", cells_life), ("bal", cells_bal)):
            if mode == "bal":
                print(f"  {'─'*114}\n  BAL-variant (lock on balance-above-start "
                      f"— the user's words mapped to balance):")
            for a, l in cells:
                g = agg_cell([grid[(mode, a, l)][i] for i in cidx])
                cell_stats[(cname, (mode, a[2], l[2]))] = g
                tag = " <- user literal" if (mode == "life" and a == USER_ARM
                                             and l == USER_LOCK) else \
                      (" <- user intent" if (mode == "bal" and a == USER_ARM
                                             and l[1] == 2000.0) else "")
                noop = " (NO-OP)" if g["lock"] == 0 and g["arm"] > 0 else ""
                print(f"  {a[2]:<11}{l[2]:<9}| {fm(g['p10']):>7} {fm(g['p50']):>7} "
                      f"{fm(g['p90']):>7} {fm(g['mean']):>7} "
                      f"{fm(g['mean'] - b['mean']):>7} | {fp_(g['arm']):>5} "
                      f"{fp_(g['lock']):>5} {fp_(g['die']):>5} {fp_(g['surv']):>5} | "
                      f"{fm(g['e_lock']):>8} {fm(g['e_dead']):>8} "
                      f"{fp_(g['hvok']):>5} {fp_(g['dl']):>5}{tag}{noop}", flush=True)
    print(f"\n  12-mo extracted = cash out (payouts + post-lock harvest) in first "
          f"12 calendar months, obs12 starts only. Δmean vs baseline same cohort.")
    print(f"  E$|lock / E$|dead = full-horizon realized cash conditional on "
          f"outcome. hvOK% = locks where frozen consistency still allowed "
          f"harvest. dl% = armed accounts whose lock level sat at/below the "
          f"death threshold (W >= lock) at the end — unreachable dead letter.")

    # ── best-IS cell (2024+ cohort, life-mode, must actually fire) ───────────
    hot = "2024+ (hot IS)"
    live = [(a, l) for a, l in cells_life
            if cell_stats[(hot, ("life", a[2], l[2]))]["lock"] > 0]
    best_al = max(live, key=lambda al:
                  (cell_stats[(hot, ("life", al[0][2], al[1][2]))]["mean"],
                   cell_stats[(hot, ("life", al[0][2], al[1][2]))]["p50"]))
    bA, bL = best_al
    bstat = cell_stats[(hot, ("life", bA[2], bL[2]))]
    bbase = cell_stats[(hot, "BASE")]
    print(f"\n  best-IS live cell (2024+, by 12-mo mean): ARM {bA[2]} / LOCK "
          f"{bL[2]}  mean {fm(bstat['mean'])} vs baseline {fm(bbase['mean'])} "
          f"(Δ {fm(bstat['mean']-bbase['mean'])}), lock rate {bstat['lock']:.0%}",
          flush=True)
    # best FIRING BAL-variant for reference
    live_bal = [(a, l) for a, l in cells_bal
                if cell_stats[(hot, ("bal", a[2], l[2]))]["lock"] > 0]
    bbA, bbL = max(live_bal, key=lambda al:
                   cell_stats[(hot, ("bal", al[0][2], al[1][2]))]["mean"])
    bbstat = cell_stats[(hot, ("bal", bbA[2], bbL[2]))]
    print(f"  best firing BAL-variant cell (2024+): ARM {bbA[2]} / LOCK {bbL[2]}  "
          f"mean {fm(bbstat['mean'])} (Δ {fm(bbstat['mean']-bbase['mean'])}), "
          f"lock rate {bbstat['lock']:.0%}", flush=True)

    # ═══ PART 2 — slot-recycled fleet, 24 months ═════════════════════════════
    print(f"\n{'═'*118}")
    print(f"  PART 2 — FLEET: {SLOTS} Lucid slots, 24 mo, rolling 2024+ starts "
          f"every 5 sd (>=126 sd ahead), same-day refill on death OR lock-close")
    print(f"  net cash = all payouts+harvests - every $364 purchase (day-0 and "
          f"replacements external if wallet short; external counted)")
    print(f"{'═'*118}", flush=True)
    starts_f = [i for i in [j for j in starts_all if seq[j][0].year >= 2024][::5]
                if len(seq) - i >= 126]
    print(f"  fleet-start worlds: {len(starts_f)} ({seq[starts_f[0]][0]} -> "
          f"{seq[starts_f[-1]][0]})", flush=True)

    POLS = [("a RIDE-TO-DEATH (baseline)", None, None, "life", False),
            (f"b BEST-IS {bA[2]}/{bL[2]}", bA, bL, "life", False),
            ("c USER LITERAL extr>=10k/+2k life", USER_ARM, USER_LOCK, "life", False),
            ("c2 USER INTENT extr>=10k/bal+2k", USER_ARM, ("fix", 2000.0, "bal+2k"), "bal", False),
            (f"b' best-IS + slot-free-at-lock", bA, bL, "life", True),
            ("c2' intent + slot-free-at-lock", USER_ARM, ("fix", 2000.0, "bal+2k"), "bal", True)]
    F = {}
    for name, a, l, mode, ffl in POLS:
        t0 = time.time()
        res = [sim_fleet(seq, i, arm=a, lock=l, mode=mode, free_at_lock=ffl)
               for i in starts_f]
        F[name] = agg_fleet(res)
        print(f"  {name:<36} simulated in {time.time()-t0:4.1f}s", flush=True)

    print(f"\n  {'policy':<36}{'m':>3} {'N':>3} | "
          f"{'net cash extracted p10/p50/p90':>32} | {'death':>5} {'lock':>5} "
          f"{'forf':>5} {'buys':>5} {'ext p50/max':>15}")
    print(f"  {'─'*116}")
    for name, *_ in POLS:
        for m in (12, 24):
            s = F[name][m]
            if s is None:
                continue
            print(f"  {name:<36}{m:>3} {s['n']:>3} | {fm(s['net'][0]):>10} "
                  f"{fm(s['net'][1]):>10} {fm(s['net'][2]):>10} | "
                  f"{s['deaths']:>5.0f} {s['locks']:>5.0f} {s['forf']:>5.0f} "
                  f"{s['buys']:>5.0f} {fm(s['ext'][0]):>7}/{fm(s['ext'][1]):>6}",
                  flush=True)
        print(f"  {'─'*116}")
    print(f"  death/lock/forf/buys = median cumulative Lucid deaths, ratchet "
          f"locks, consistency-forfeit closes, $364 purchases. ext = external "
          f"cash needed (p50/worst world).")

    # ═══ PART 3 — mapping + harvest-delay honesty ════════════════════════════
    print(f"\n{'═'*118}\n  PART 3 — INTERACTION HONESTY\n{'═'*118}")
    print(f"""  LIFETIME vs BALANCE under KEEP=$5,000 (why the literal cell can't fire):
    balance = 50,000 + lifetimePnL - W (W = withdrawn to date). First payout
    needs EOD bal > 55,000 => trailing floor locks at 50,000 BEFORE any cash
    comes out. Death (bal <= 50,000) therefore happens exactly at
    lifetimePnL == W. A lifetime lock at +L fires only while W < L:
      - user literal (arm extracted>=10k, lock +2k): W >= 10k > 2k at arm =>
        the account always dies (life==W) before life can reach +2k. NO-OP.
      - every fixed +L lifetime lock goes dead-letter once payouts push W past
        L (~1-2 payouts at KEEP=5k); only the trailing peak-4k lock stays live.
      - the user's WORDS map to balance: 'won't go back under 2k profit' ==
        lock when balance-above-start (life - W) <= 2k. That is the BAL-variant
        (fires at bal 52,000; harvest <= $2k, one payout cycle, then close).
    Harvest reality: consistency (lifetime total >= 5x best day) is FROZEN at
    lock — if it fails there it can never heal (no new trades), the inside is
    unpayable and is forfeited at close. hvOK% above shows how often that bites.""")
    for nm, base_nm, short in (
            (f"b' best-IS + slot-free-at-lock", f"b BEST-IS {bA[2]}/{bL[2]}", "best-IS"),
            ("c2' intent + slot-free-at-lock", "c2 USER INTENT extr>=10k/bal+2k", "user-intent")):
        for m in (12, 24):
            if F[nm][m] and F[base_nm][m]:
                d = F[nm][m]["net"][1] - F[base_nm][m]["net"][1]
                print(f"  harvest-delay test, {short:<11} m{m}: net p50 "
                      f"(slot freed AT LOCK) minus (slot held while draining) "
                      f"= {fm(d)} ({'immaterial' if abs(d) < 500 else 'material'})")
    print(f"  assumption: locked accounts drain min($3k, bal-50k) per >=5-sd "
          f"payout cycle then close; slot refills same day at close (default) "
          f"or at lock (variant). If Lucid disallows a 6th account while one "
          f"drains, the default row is the honest number.")

    # ═══ VERDICT ═════════════════════════════════════════════════════════════
    print(f"\n{'═'*118}\n  VERDICT\n{'═'*118}")
    cold = "2022-23 (cold)"
    m24a = F["a RIDE-TO-DEATH (baseline)"][24]["net"][1]
    m24b = F[f"b BEST-IS {bA[2]}/{bL[2]}"][24]["net"][1]
    m24c2 = F["c2 USER INTENT extr>=10k/bal+2k"][24]["net"][1]
    m12a = F["a RIDE-TO-DEATH (baseline)"][12]["net"][1]
    m12b = F[f"b BEST-IS {bA[2]}/{bL[2]}"][12]["net"][1]
    m12c2 = F["c2 USER INTENT extr>=10k/bal+2k"][12]["net"][1]
    slotyr_b = (m24b - m24a) / (SLOTS * 2)
    slotyr_c2 = (m24c2 - m24a) / (SLOTS * 2)
    cold_b = (cell_stats[(cold, ("life", bA[2], bL[2]))]["mean"]
              - cell_stats[(cold, "BASE")]["mean"])
    cold_c2 = (cell_stats[(cold, ("bal", USER_ARM[2], "bal+2k"))]["mean"]
               - cell_stats[(cold, "BASE")]["mean"])
    hot_b = bstat["mean"] - bbase["mean"]
    hot_c2 = (cell_stats[(hot, ("bal", USER_ARM[2], "bal+2k"))]["mean"]
              - bbase["mean"])
    print(f"  fleet m24 net p50: baseline {fm(m24a)} | best-IS {fm(m24b)} "
          f"(Δ {fm(m24b-m24a)}, {fm(slotyr_b)}/slot-yr) | user-intent "
          f"{fm(m24c2)} (Δ {fm(m24c2-m24a)}, {fm(slotyr_c2)}/slot-yr)")
    print(f"  fleet m12 net p50: baseline {fm(m12a)} | best-IS {fm(m12b)} "
          f"(Δ {fm(m12b-m12a)}) | user-intent {fm(m12c2)} (Δ {fm(m12c2-m12a)})")
    print(f"  single-account 12-mo mean Δ vs baseline: best-IS hot {fm(hot_b)} "
          f"cold {fm(cold_b)} | user-intent hot {fm(hot_c2)} cold {fm(cold_c2)}")
    ul = cell_stats[(hot, ("life", USER_ARM[2], USER_LOCK[2]))]
    print(f"  user literal cell: lock rate {ul['lock']:.0%} in every cohort — "
          f"{'confirmed NO-OP (identical to ride-to-death)' if ul['lock'] == 0 else 'fires occasionally'}")

    # breakeven regime weight: P(cold) needed for the intent cell to pay
    if cold_c2 > 0 and hot_c2 < 0:
        w_single = -hot_c2 / (cold_c2 - hot_c2)
        w_fleet = -slotyr_c2 / (cold_c2 - slotyr_c2) if slotyr_c2 < 0 else 0.0
        print(f"  insurance pricing (user-intent cell): breakeven needs "
              f"P(2022-23-grade regime) >= {w_fleet:.0%} on fleet economics, "
              f">= {w_single:.0%} on single-account 12-mo means")

    best_hot_fleet = max(slotyr_b, slotyr_c2)
    best_cold = max(cold_b, cold_c2)
    if best_hot_fleet > 100 and best_cold > 0:
        v = (f"RATCHET VERDICT: DEPLOY-CANDIDATE (arm {bA[2]} / lock {bL[2]} "
             f"beats ride-to-death by ${slotyr_b:,.0f}/slot-year, and by "
             f"${cold_b:,.0f}/account-year in the cold cohort)")
    elif best_hot_fleet <= 100 and best_cold > 100:
        which = f"{bA[2]}/{bL[2]}" if cold_b >= cold_c2 else "extr>=10k/bal+2k"
        v = (f"RATCHET VERDICT: INSURANCE-ONLY (loses in hot regime "
             f"({fm(min(slotyr_b, 0))}/slot-yr fleet), wins in cold "
             f"({fm(best_cold)}/account-yr, cell {which}) — user's "
             f"risk-preference call)")
    else:
        worst = min(slotyr_b, slotyr_c2)
        v = (f"RATCHET VERDICT: REJECT (costs ${abs(worst):,.0f}/slot-year in "
             f"the fleet and {fm(min(hot_b, hot_c2))} hot / "
             f"{fm(min(cold_b, cold_c2))} cold per single account — same "
             f"failure as the rolling-PF kill-switch: the strategy recovers "
             f"after drawdowns, and dip-survival is where extraction comes from)")
    print(f"\n  {v}")
    print(f"{'═'*118}\n  ratchet_lock done in {time.time()-t_all:,.0f}s.\n{'═'*118}")
