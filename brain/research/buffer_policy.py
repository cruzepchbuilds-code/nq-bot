"""
brain/research/buffer_policy.py

BUFFER vs HARVEST vs GRADUATE — the user's question, verbatim intent:
  "if the account survives and I have a buffer, won't it last way longer if I
   leave money in? or better to take max payouts and reinvest — maybe into a
   new LIVE account with no prop rules? draw the projection."

ACCOUNT — Lucid 50K Direct ($364), REAL rules:
  balance starts 50,000; EOD trailing floor = max(48,000, min(peak_EOD - 2,000,
  50,000)) — trails up only, LOCKS at 50,000 forever once peak >= 52,000.
  Withdrawals never lower the floor and never lower peak (peak is taken on the
  pre-withdrawal EOD balance — payouts process after the EOD mark).
  Death = EOD balance <= floor.
  Payout: >=5 signal-days since start and since last payout, lifetime
  consistency total_profit >= 5 x best_day, balance > 50,000 + KEEP,
  amount = min($3,000, balance - 50,000 - KEEP).

RAMP MODE ON (the live account's real config, previously missing from fleet
sims): until an account's LIFETIME net P&L reaches +$800 it trades the MORNING
ORB COMPONENT ONLY; once lifetime >= +$800 it trades the full v12 stack
(one-way latch, evaluated at start of day). Two daily streams are built ONCE —
morning-only and full — both under the v12 day-composition rules copied from
empire_rulemap.build_seq (sort by entry time; REJ only if no morning ORB that
day; PM skipped if morning traded AND lost; one position at a time; halt day
at -$500; room gate (1150 + day_pnl) >= risk {ORB 565, REJ 415, PM 455,
ASIA 515}) — and every account switches stream per its lifetime state.
Ramp scope: LUCID accounts only (it exists to protect the trailing floor
before it locks). The ETF Static model is reused untouched from
capital_plan.py (static floors, no ramp); the personal live account trades
the full stream (no ramp) per spec.

PART 1 — KEEP sweep (single account, ramp ON, run to death or data end):
  KEEP in {500, 1000, 2000, 3000, 5000, NEVER(no withdrawals ever)};
  cohorts: 2024+ starts and all-history starts (every signal-day start with
  >=60 sd of data ahead). Reports Kaplan-Meier median lifespan (signal-days,
  censoring-aware), % alive at 12 calendar months, % ever paid, median sd to
  first payout, and cash extracted in the first 12 months p10/p50/p90/mean
  (NEVER reports paper profit above start at 12 months instead — $0 if dead,
  because money still inside a breached account is gone).

PART 2 — system-level 24-month projection, three policies, fleet sim in the
capital_plan.py mold (shared daily streams, per-account state, purchases from
extracted cash, month-indexed snapshots), rolling 2024+ fleet starts every
5 signal-days with >=126 sd of data ahead:
  A FORTRESS  : KEEP = best-lifespan value from Part 1; max 5 Lucid
                concurrent; no ETF, no live account.
  B HARVESTER : KEEP = 1000; reinvest-first — Lucid to 5 concurrent, then ETF
                Statics to 20 active (eval $99 -> +4k pass / -2k fail static;
                $177 activation; funded: fixed 48k floor, no consistency,
                >=10-sd cycle, $1,250 cap, keep-$1k — capital_plan's model).
  C GRADUATE  : same as B until CUMULATIVE NET extracted cash (payouts - all
                prop spend) >= TRIGGER ($20k headline; $10k/$30k swept), then:
                fleet frozen (no purchases, activations, or replacements;
                in-flight evals abandoned), banked wallet cash seeds a
                PERSONAL LIVE futures account, and all further payout cash
                deposits there.
  Replace-on-death: LUCID deaths are replaced SAME DAY, guaranteed — from
  wallet cash first, external funds if short (user's commitment; external
  dollars are counted and net against wealth). ETF deaths/fails refill from
  wallet cash only via the concurrent caps. After C graduates: nothing is
  replaced.
  New/replacement accounts start trading the NEXT signal-day and start in
  ramp mode (lifetime 0).

  PERSONAL LIVE ACCOUNT (policy C) — assumptions, stated plainly:
    - seeded at graduation with the entire banked wallet; starts TRADING the
      next signal-day once its balance >= $20,000 (immediate at the $20k+
      trigger; the $10k trigger waits for deposits to reach $20k)
    - trades the same full v12 1-contract daily stream x contracts;
      contracts = floor(balance / $20,000), min 1, max 5, resized daily on
      the prior EOD balance; the $14.50/trade cost is already embedded in
      the per-contract stream (not double-counted)
    - no prop floor, no consistency, no withdrawals (pure compounding)
    - ruin if balance < $10,000 -> stop trading forever, keep the remainder;
      payout cash arriving after ruin accumulates as plain cash

  WEALTH LINES (both reported at months 6/12/24, p10/p50/p90 over starts):
    no-credit : wallet cash + live-account balance - external dollars spent;
                $0 credit for money inside prop accounts (it isn't yours)
    50%-credit: no-credit + 0.5 x sum(max(0, balance - 50,000)) over funded
                prop accounts (so the buffer policy isn't strawmanned;
                eval balances get $0 — eval money is never withdrawable)

PART 3 — verdict: which policy wins at 12/24 months on both wealth lines,
does buffer extend life and is it worth the cash cost, graduation-trigger
sweet spot, IS(2024 starts)/OOS(2025+ starts) rank-flip flags.

VALIDATION ANCHOR (must land before trusting anything): single account,
KEEP=1000, ramp ON, 2024+ starts, 252-sd horizon, uncapped payouts
(lucid_direct_sim conventions) -> prior result: ~72% ever paid (vs ~51% ramp
OFF), gauntlet deaths ~10% (vs ~41%), avg extracted ~$12,979, ~0% survive.

Run from repo root:
  python3 brain/research/buffer_policy.py
First run builds both streams through the backtest engine via
brain/research/_eb_snapshot.py (frozen copy of eval_boost.py, taken because
eval_boost.py may be edited concurrently) and caches them to
$TMPDIR/buffer_policy_streams_v12.json (override: env BUFFER_POLICY_CACHE;
delete the cache after a data refresh). Cached runs finish in seconds.
"""

import sys, os, json, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date
from collections import defaultdict

RISK1 = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}
START = 50_000.0
RAMP_AT = 800.0
PAY_CAP = 3000.0
LUCID_PRICE, LUCID_CAP_N = 364.0, 5
ETF_PRICE, ETF_ACT, ETF_CAP_N = 99.0, 177.0, 20
KEEP_GRID = [500.0, 1000.0, 2000.0, 3000.0, 5000.0, None]   # None = NEVER withdraw

CACHE = os.environ.get("BUFFER_POLICY_CACHE",
                       os.path.join(tempfile.gettempdir(), "buffer_policy_streams_v12.json"))
CP_CACHE = os.path.join(tempfile.gettempdir(), "capital_plan_stream_v12.json")


# ── the two daily streams (v12 day composition, verbatim empire_rulemap) ─────

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
    from _eb_snapshot import build_components          # frozen eval_boost copy
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
    print("Building morning-only + full streams (backtest engine, first run only)...", flush=True)
    t0 = time.time()
    seq = build_streams()
    with open(CACHE, "w") as f:
        json.dump([(d.isoformat(), pf, pm) for d, pf, pm in seq], f)
    print(f"  built {len(seq)} days in {time.time()-t0:.0f}s — cached to {CACHE}", flush=True)
    return seq


def cross_check_full_stream(seq):
    """My full stream must equal capital_plan's cached v12 stream day-by-day."""
    if not os.path.exists(CP_CACHE):
        print("  (capital_plan stream cache absent — skipping cross-check)", flush=True)
        return
    with open(CP_CACHE) as f:
        cp = {d: p for d, p in json.load(f)}
    mine = {d.isoformat(): pf for d, pf, _ in seq}
    if set(cp) != set(mine):
        print(f"  WARNING: day-set mismatch vs capital_plan cache "
              f"({len(cp)} vs {len(mine)} days)", flush=True)
        return
    diff = max(abs(cp[d] - mine[d]) for d in cp)
    print(f"  cross-check vs capital_plan full-stream cache: {len(cp)} days, "
          f"max |diff| = ${diff:.4f} {'OK' if diff < 0.01 else '** MISMATCH **'}", flush=True)


# ── single Lucid account (Part 1 + anchor) ───────────────────────────────────

def months_between(d0, d1):
    return (d1.year - d0.year) * 12 + (d1.month - d0.month)


def run_single(seq, i0, keep, ramp=True, horizon=None, cap=PAY_CAP,
               peak_before_pay=True):
    """One Lucid Direct from seq[i0] to death / horizon / data end.
    keep=None -> NEVER withdraw. Returns a metrics dict."""
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
    k_last = i0
    death_m = None
    bal12 = None                                    # balance snapshot at 12 months
    for k in range(i0, end):
        d, pf, pm = seq[k]
        if bal12 is None and months_between(d0, d) >= 12:
            bal12 = bal                             # first day of month 13 -> prior EOD
        p = pf if latched else pm
        bal += p
        life_pnl += p
        tp += p
        best = max(best, p)
        if not latched and life_pnl >= RAMP_AT:
            latched = True
        k_last = k
        if bal <= floor:
            died = True
            death_m = months_between(d0, d)
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
    if bal12 is None and not died:
        bal12 = bal                                 # data ended inside month 12
    obs12 = months_between(d0, seq[-1][0]) >= 12
    alive12 = (not died) or (death_m is not None and death_m >= 12)
    return {"died": died, "life": k_last - i0 + 1, "first_pay": first_pay,
            "n_pay": n_pay, "wd": wd, "wd12": wd12, "obs12": obs12,
            "alive12": alive12, "surv_hz": not died,
            "bal12": (0.0 if (died and death_m is not None and death_m < 12)
                      else (bal12 - START if bal12 is not None else None))}


def km_median(pairs):
    """pairs = [(t, died)]; Kaplan-Meier median with right censoring.
    Returns (median_t, is_censored_beyond)."""
    pairs = sorted(pairs)
    at_risk = len(pairs)
    s = 1.0
    for t, died in pairs:
        if died:
            s *= (at_risk - 1) / at_risk
        at_risk -= 1
        if s <= 0.5:
            return t, False
    return max(t for t, _ in pairs), True


def pct(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


# ── fleet accounts (Part 2) ──────────────────────────────────────────────────

def new_lucid(k, keep):
    return {"kind": "lucid", "state": "funded", "start_k": k, "bal": START,
            "peak": START, "floor": START - 2000.0, "tp": 0.0, "best": 0.0,
            "last_pay": -99, "n_pay": 0, "life": 0.0, "latched": False,
            "keep": keep}


def new_etf(k):
    return {"kind": "etf", "state": "eval", "start_k": k, "cum": 0.0,
            "n_pay": 0, "last_pay": -99, "bal": 0.0}


def step_lucid(a, k, pf, pm):
    p = pf if a["latched"] else pm
    a["bal"] += p
    a["life"] += p
    a["tp"] += p
    a["best"] = max(a["best"], p)
    if not a["latched"] and a["life"] >= RAMP_AT:
        a["latched"] = True
    if a["bal"] <= a["floor"]:
        a["state"] = "dead"
        return 0.0
    a["peak"] = max(a["peak"], a["bal"])                    # pre-withdrawal EOD peak
    a["floor"] = max(a["floor"], min(a["peak"] - 2000.0, START))
    rel = k - a["start_k"]
    if a["tp"] > 0 and a["tp"] >= 5.0 * a["best"] and rel >= 5 \
            and rel - a["last_pay"] >= 5:
        avail = min(PAY_CAP, a["bal"] - (START + a["keep"]))
        if avail > 0:
            a["bal"] -= avail
            a["last_pay"] = rel
            a["n_pay"] += 1
            return avail
    return 0.0


def step_etf_funded(a, k, p):
    a["bal"] += p
    if a["bal"] <= START - 2000.0:
        a["state"] = "dead"
        return 0.0
    rel = k - a["start_k"]
    if rel >= 10 and rel - a["last_pay"] >= 10:
        avail = min(1250.0, a["bal"] - (START + 1000.0))
        if avail > 0:
            a["bal"] -= avail
            a["last_pay"] = rel
            a["n_pay"] += 1
            return avail
    return 0.0


def sim_fleet(seq, i0, policy, keep_lucid, trigger=None):
    """policy: 'A' lucid-only | 'B' lucid->ETF | 'C' B + graduation.
    Returns month-indexed snapshots + counters."""
    d0 = seq[i0][0]
    wallet = 0.0
    tot_pay, tot_spend, external = 0.0, LUCID_PRICE, LUCID_PRICE   # day-0 Lucid is external
    accounts = [new_lucid(i0, keep_lucid)]
    deaths = ev_fail = ev_pass = 0
    graduated = False
    live_bal = 0.0
    live_trading = live_ruined = False
    live_start_k = None
    snaps = {}

    for k in range(i0, len(seq)):
        d, pf, pm = seq[k]
        midx = months_between(d0, d) + 1

        # 1) personal live account trades on prior-EOD balance
        if live_trading and not live_ruined and k >= live_start_k:
            c = min(5, max(1, int(live_bal // 20000)))
            live_bal += c * pf
            if live_bal < 10000:
                live_ruined = True

        # 2) prop accounts trade
        pay_today = 0.0
        lucid_deaths_today = 0
        for a in accounts:
            if a["start_k"] > k:
                continue
            st = a["state"]
            if st == "eval":
                a["cum"] += pf
                if a["cum"] <= -2000.0:
                    a["state"] = "failed"; ev_fail += 1
                elif a["cum"] >= 4000.0:
                    a["state"] = "pending"; ev_pass += 1
            elif st == "funded":
                if a["kind"] == "lucid":
                    pay_today += step_lucid(a, k, pf, pm)
                    if a["state"] == "dead":
                        deaths += 1; lucid_deaths_today += 1
                else:
                    pay_today += step_etf_funded(a, k, pf)
                    if a["state"] == "dead":
                        deaths += 1
        tot_pay += pay_today

        # 3) route payout cash
        if graduated and not live_ruined:
            live_bal += pay_today
            if not live_trading and live_bal >= 20000.0:
                live_trading = True
                live_start_k = k + 1
        else:
            wallet += pay_today

        # 4) graduation check (before any prop spend today)
        if policy == "C" and not graduated and tot_pay - tot_spend >= trigger:
            graduated = True
            live_bal += wallet                     # banked cash seeds the live account
            wallet = 0.0
            if live_bal >= 20000.0:
                live_trading = True
                live_start_k = k + 1
            for a in accounts:
                if a["state"] in ("eval", "pending"):
                    a["state"] = "abandoned"

        if not graduated:
            # 5) guaranteed same-day Lucid replacements (external if cash short)
            for _ in range(lucid_deaths_today):
                use = min(wallet, LUCID_PRICE)
                wallet -= use
                external += LUCID_PRICE - use
                tot_spend += LUCID_PRICE
                accounts.append(new_lucid(k + 1, keep_lucid))
            # 6) ETF activations (wallet cash only)
            if policy in ("B", "C"):
                for a in accounts:
                    if a["state"] == "pending" and wallet >= ETF_ACT:
                        wallet -= ETF_ACT
                        tot_spend += ETF_ACT
                        a["state"] = "funded"
                        a["start_k"] = k + 1
                        a["bal"] = START
                        a["last_pay"] = -99
            # 7) growth purchases from wallet (Lucid priority, concurrent caps)
            while True:
                n_lucid = sum(1 for a in accounts
                              if a["kind"] == "lucid" and a["state"] == "funded")
                n_etf = sum(1 for a in accounts if a["kind"] == "etf"
                            and a["state"] in ("eval", "pending", "funded"))
                if n_lucid < LUCID_CAP_N:
                    kind = "lucid"
                elif policy in ("B", "C") and n_etf < ETF_CAP_N:
                    kind = "etf"
                else:
                    break
                price = LUCID_PRICE if kind == "lucid" else ETF_PRICE
                if wallet < price:
                    break
                wallet -= price
                tot_spend += price
                accounts.append(new_lucid(k + 1, keep_lucid) if kind == "lucid"
                                else new_etf(k + 1))

        # 8) month-end snapshot
        if k == len(seq) - 1 or seq[k + 1][0].month != d.month:
            prop_credit = 0.5 * sum(max(0.0, a["bal"] - START)
                                    for a in accounts if a["state"] == "funded")
            nc = wallet + live_bal - external
            funded_alive = sum(1 for a in accounts if a["state"] == "funded")
            snaps[midx] = (nc, nc + prop_credit, funded_alive, deaths,
                           external, live_bal, graduated, live_ruined)

    last = max(snaps)
    grad_m = min((m for m in snaps if snaps[m][6]), default=None)
    return {"snaps": snaps, "complete": last - 1, "ev_pass": ev_pass,
            "ev_fail": ev_fail, "grad_m": grad_m}


def agg_fleet(results, starts_d):
    out = {}
    for m in (6, 12, 24):
        rows = [r for r in results if r["complete"] >= m]
        nc = [r["snaps"][m][0] for r in rows]
        hc = [r["snaps"][m][1] for r in rows]
        out[m] = {"n": len(rows),
                  "nc": (pct(nc, .10), pct(nc, .50), pct(nc, .90)),
                  "hc": (pct(hc, .10), pct(hc, .50), pct(hc, .90)),
                  "fleet": pct([r["snaps"][m][2] for r in rows], .50),
                  "deaths": pct([r["snaps"][m][3] for r in rows], .50),
                  "ext": (pct([r["snaps"][m][4] for r in rows], .50),
                          max((r["snaps"][m][4] for r in rows), default=None)),
                  "live": pct([r["snaps"][m][5] for r in rows], .50),
                  "grad": (sum(1 for r in rows if r["snaps"][m][6]) / len(rows)
                           if rows else None),
                  "ruin": (sum(1 for r in rows if r["snaps"][m][7]) / len(rows)
                           if rows else None)}
    # IS/OOS: month-6 and month-12 no-credit p50 by start-year cohort
    for m in (6, 12):
        for tag, pred in (("is", lambda y: y == 2024), ("oos", lambda y: y >= 2025)):
            v = [r["snaps"][m][0] for r, s in zip(results, starts_d)
                 if r["complete"] >= m and pred(s.year)]
            out[f"{tag}{m}"] = (pct(v, .50), len(v))
    out["grad_m"] = pct([r["grad_m"] for r in results if r["grad_m"] is not None], .50)
    return out


def fm(x):
    return "  n/a" if x is None else f"{x:>+,.0f}"


def fp_(x):
    return "n/a" if x is None else f"{x:.0%}"


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t_all = time.time()
    seq = get_streams()
    cross_check_full_stream(seq)
    d_first, d_last = seq[0][0], seq[-1][0]
    n_mo = months_between(d_first, d_last) + 1
    net_full = sum(pf for _, pf, _ in seq)
    net_morn = sum(pm for _, _, pm in seq)
    print(f"Streams: {len(seq)} signal-days {d_first} -> {d_last} "
          f"({len(seq)/n_mo:.1f} sd/calendar month) | 1c net: full ${net_full:,.0f}, "
          f"morning-only ${net_morn:,.0f}", flush=True)

    starts_2024 = [i for i, (d, _, _) in enumerate(seq)
                   if d.year >= 2024 and len(seq) - i >= 60]
    starts_all = [i for i in range(len(seq)) if len(seq) - i >= 60]

    # ═══ ANCHOR — reproduce prior ramp-ON lucid_direct_sim result ════════════
    print(f"\n{'═'*100}")
    print("  ANCHOR CHECK — single Lucid, KEEP=1000, 2024+ starts, 252-sd horizon, "
          "UNCAPPED payouts (prior conventions)")
    print("  prior session: ramp ON 72% pay / ~10% die unpaid / avg extracted ~$12,979 "
          "/ ~0% survive; ramp OFF 51% pay / 41% unpaid")
    print(f"{'═'*100}")
    for tag, ramp, pbp in (("ramp ON  (peak pre-pay) ", True, True),
                           ("ramp ON  (peak post-pay)", True, False),
                           ("ramp OFF (peak pre-pay) ", False, True)):
        rs = [run_single(seq, i, 1000.0, ramp=ramp, horizon=252, cap=None,
                         peak_before_pay=pbp) for i in starts_2024]
        n = len(rs)
        paid = sum(1 for r in rs if r["n_pay"] > 0)
        unpaid_dead = sum(1 for r in rs if r["died"] and r["n_pay"] == 0)
        wsum = sum(r["wd"] for r in rs)
        med_payer = pct([r["wd"] for r in rs if r["n_pay"] > 0], .50)
        surv = sum(1 for r in rs if r["surv_hz"])
        print(f"  {tag}: pay {paid/n:5.0%} | die-unpaid {unpaid_dead/n:5.0%} | "
              f"avg extracted ${wsum/n:>7,.0f} | payer p50 ${med_payer:>7,.0f} | "
              f"survive horizon {surv/n:4.0%}  (N={n})", flush=True)

    # ═══ PART 1 — KEEP sweep ═════════════════════════════════════════════════
    part1 = {}
    for cname, cohort in (("2024+", starts_2024), ("all-history", starts_all)):
        print(f"\n{'═'*112}")
        print(f"  PART 1 — KEEP SWEEP | single Lucid, ramp ON, $3k payout cap, run to "
              f"death/data-end | {cname} starts (N={len(cohort)})")
        print(f"{'═'*112}")
        print(f"  {'KEEP':>6} | {'KM med life':>12} {'alive@12mo':>10} {'ever paid':>9} "
              f"{'sd->1st pay':>11} | {'extracted in first 12 mo (obs12 starts)':>46}")
        print(f"  {'':>6} | {'(signal-days)':>12} {'':>10} {'':>9} {'(median)':>11} | "
              f"{'p10':>10} {'p50':>10} {'p90':>10} {'mean':>10}")
        print(f"  {'─'*108}")
        for keep in KEEP_GRID:
            t0 = time.time()
            rs = [run_single(seq, i, keep) for i in cohort]
            n = len(rs)
            kmed, cens = km_median([(r["life"], r["died"]) for r in rs])
            a12 = [r for r in rs if r["obs12"]]
            alive12 = (sum(1 for r in a12 if r["alive12"]) / len(a12)) if a12 else None
            paid = sum(1 for r in rs if r["n_pay"] > 0) / n
            fp = pct([r["first_pay"] for r in rs if r["first_pay"] is not None], .50)
            label = "NEVER" if keep is None else f"{keep:,.0f}"
            if keep is None:
                b = [r["bal12"] for r in a12 if r["bal12"] is not None]
                tail = (f"{fm(pct(b,.10)):>10} {fm(pct(b,.50)):>10} {fm(pct(b,.90)):>10} "
                        f"{fm(sum(b)/len(b) if b else None):>10}  <- paper P&L @12mo, $0 if dead")
            else:
                w = [r["wd12"] for r in a12]
                tail = (f"{fm(pct(w,.10)):>10} {fm(pct(w,.50)):>10} {fm(pct(w,.90)):>10} "
                        f"{fm(sum(w)/len(w) if w else None):>10}")
            print(f"  {label:>6} | {('>' if cens else '') + str(kmed):>12} "
                  f"{(f'{alive12:.0%}' if alive12 is not None else 'n/a'):>10} "
                  f"{paid:>9.0%} {(str(fp) if fp else 'n/a'):>11} | {tail}", flush=True)
            part1[(cname, keep)] = {"km": kmed, "cens": cens, "alive12": alive12,
                                    "paid": paid, "fp": fp,
                                    "wd12_p50": pct([r["wd12"] for r in a12], .50) if keep is not None else None}
    # curve-bend note (2024+ cohort)
    finite = [k for k in KEEP_GRID if k is not None]

    def _life(k):
        p = part1[("2024+", k)]
        return f"{'>' if p['cens'] else ''}{p['km']}sd"
    print(f"\n  curve: lifespan by KEEP (2024+): " +
          "  ".join(f"${k:,.0f}->{_life(k)}" for k in finite) +
          f"  NEVER->{_life(None)}", flush=True)

    # ═══ PART 2 — fleet policies ═════════════════════════════════════════════
    keep_A = max(finite, key=lambda k: (part1[("2024+", k)]["km"],
                                        part1[("2024+", k)]["alive12"] or 0))
    print(f"\n{'═'*112}")
    print(f"  PART 2 — 24-MONTH FLEET PROJECTION | rolling 2024+ starts every 5 sd, "
          f">=126 sd ahead | ramp ON everywhere (Lucids)")
    print(f"  A FORTRESS KEEP={keep_A:,.0f} (best-lifespan from Part 1), 5 Lucid max, no ETF | "
          f"B HARVESTER KEEP=1000 +ETF to 20 | C GRADUATE=B until net cash >= $20k -> live acct")
    print(f"{'═'*112}", flush=True)
    starts_i = [i for i in starts_2024[::5] if len(seq) - i >= 126]
    starts_d = [seq[i][0] for i in starts_i]
    print(f"  fleet-start worlds: {len(starts_i)} ({starts_d[0]} -> {starts_d[-1]})", flush=True)

    POL = [("A FORTRESS", "A", keep_A, None),
           ("B HARVESTER", "B", 1000.0, None),
           ("C GRADUATE", "C", 1000.0, 20000.0),
           # synthesis rows (not in the spec'd trio): the Part-1 buffer inside
           # the Part-2 winning architecture — is buffer-vs-redeploy a false choice?
           ("S B+5KBUF", "B", keep_A, None),
           ("S C+5KBUF", "C", keep_A, 20000.0)]
    G = {}
    for name, pol, keep, trig in POL:
        t0 = time.time()
        res = [sim_fleet(seq, i, pol, keep, trig) for i in starts_i]
        G[name] = agg_fleet(res, starts_d)
        print(f"  {name:<12} simulated in {time.time()-t0:4.1f}s", flush=True)

    hdr = (f"  {'policy':<13}{'m':>3} {'N':>4} | {'no-credit wealth p10/p50/p90':>34} | "
           f"{'50%-credit wealth p10/p50/p90':>34} | {'fleet':>5} {'death':>5} "
           f"{'ext p50/max':>15} {'liveP50':>9} {'grad':>5} {'ruin':>5}")
    print(f"\n{hdr}\n  {'─'*146}")
    for name, *_ in POL:
        g = G[name]
        for m in (6, 12, 24):
            s = g[m]
            if s["n"] == 0:
                continue
            print(f"  {name:<13}{m:>3} {s['n']:>4} | "
                  f"{fm(s['nc'][0]):>10} {fm(s['nc'][1]):>11} {fm(s['nc'][2]):>11} | "
                  f"{fm(s['hc'][0]):>10} {fm(s['hc'][1]):>11} {fm(s['hc'][2]):>11} | "
                  f"{s['fleet']:>5} {s['deaths']:>5} "
                  f"{fm(s['ext'][0]):>7}/{fm(s['ext'][1]):>7} {fm(s['live']):>9} "
                  f"{fp_(s['grad']):>5} {fp_(s['ruin']):>5}", flush=True)
        print(f"  {'─'*146}")
    print("  wealth no-credit = cash extracted - all external $ (prop balances = $0); "
          "50%-credit adds 0.5 x funded prop profit-above-start.")
    print("  fleet/death/ext = median funded alive / median cum deaths / external $ p50 & worst. "
          "live/grad/ruin: policy C personal account.")
    for name, *_ in POL:
        if G[name]["grad_m"] is not None:
            print(f"  {name}: median graduation month = {G[name]['grad_m']}")

    # ── IS/OOS rank check
    print(f"\n  IS/OOS (no-credit p50): {'policy':<13}"
          f"{'m6 2024-starts':>16} {'m6 2025+':>10} {'m12 2024':>10} {'m12 2025+':>11}")
    for name, *_ in POL:
        g = G[name]
        print(f"  {'':<24}{name:<13}"
              f"{fm(g['is6'][0]):>10} (N={g['is6'][1]:>2}) "
              f"{fm(g['oos6'][0]):>9} (N={g['oos6'][1]:>2}) "
              f"{fm(g['is12'][0]):>9} (N={g['is12'][1]:>2}) "
              f"{fm(g['oos12'][0]):>9} (N={g['oos12'][1]:>2})", flush=True)
    for m in (6, 12):
        r_is = sorted(POL, key=lambda p: -(G[p[0]][f"is{m}"][0] or -1e18))
        r_oos = sorted(POL, key=lambda p: -(G[p[0]][f"oos{m}"][0] or -1e18))
        flip = [p[0] for p in POL if r_is.index(p) != r_oos.index(p)]
        print(f"  m{m} ranking IS {[p[0][:6] for p in r_is]} vs OOS {[p[0][:6] for p in r_oos]}"
              f"{'  ** RANK FLIP: ' + ', '.join(flip) + ' **' if flip else '  (stable)'}")

    # ═══ PART 3 — graduation-trigger sweep + verdict ═════════════════════════
    print(f"\n{'═'*112}")
    print("  PART 3a — GRADUATION TRIGGER SWEEP (policy C, KEEP=1000)")
    print(f"{'═'*112}")
    print(f"  {'trigger':>9} | {'m12 nc p50':>11} {'m12 hc p50':>11} | "
          f"{'m24 nc p50':>11} {'m24 hc p50':>11} | {'grad@24':>8} {'ruin@24':>8} {'live@24 p50':>12}")
    trig_res = {}
    for trig in (10000.0, 20000.0, 30000.0):
        res = [sim_fleet(seq, i, "C", 1000.0, trig) for i in starts_i]
        g = agg_fleet(res, starts_d)
        trig_res[trig] = g
        s12, s24 = g[12], g[24]
        print(f"  {trig:>9,.0f} | {fm(s12['nc'][1]):>11} {fm(s12['hc'][1]):>11} | "
              f"{fm(s24['nc'][1]):>11} {fm(s24['hc'][1]):>11} | "
              f"{fp_(s24['grad']):>8} {fp_(s24['ruin']):>8} {fm(s24['live']):>12}", flush=True)

    def winner(m, line):
        vals = {name: (G[name][m][line][1] if G[name][m]["n"] else None) for name, *_ in POL}
        return max((v for v in vals.items() if v[1] is not None),
                   key=lambda x: x[1], default=(None, None))

    print(f"\n{'═'*112}")
    print("  PART 3b — VERDICT")
    print(f"{'═'*112}")
    for m in (12, 24):
        wn, wv = winner(m, "nc")
        wh, hv = winner(m, "hc")
        print(f"  month {m}: no-credit winner {wn} (p50 {fm(wv)}), "
              f"50%-credit winner {wh} (p50 {fm(hv)})")
    l5, l1 = part1[("2024+", 5000.0)], part1[("2024+", 1000.0)]
    ln = part1[("2024+", None)]
    print(f"  lifespan: KEEP 1000 {'>' if l1['cens'] else ''}{l1['km']}sd -> "
          f"KEEP 5000 {'>' if l5['cens'] else ''}{l5['km']}sd -> "
          f"NEVER {'>' if ln['cens'] else ''}{ln['km']}sd; "
          f"12-mo cash: KEEP 1000 p50 ${l1['wd12_p50']:,.0f} vs KEEP 5000 p50 ${l5['wd12_p50']:,.0f}")
    for m in (12, 24):
        b1 = G["B HARVESTER"][m]["nc"][1]
        b5 = G["S B+5KBUF"][m]["nc"][1]
        c1 = G["C GRADUATE"][m]["nc"][1]
        c5 = G["S C+5KBUF"][m]["nc"][1]
        if b1 is not None and b5 is not None:
            print(f"  buffer inside the fleet (nc p50, m{m}): "
                  f"B 1k {fm(b1)} vs 5k {fm(b5)} (delta {fm(b5-b1)}) | "
                  f"C 1k {fm(c1)} vs 5k {fm(c5)} (delta {fm(c5-c1)})")
    best_t = max(trig_res, key=lambda t: (trig_res[t][24]["nc"][1] or -1e18))
    print(f"  graduation trigger sweet spot (m24 nc p50): ${best_t:,.0f} "
          f"(differences across 10k/20k/30k are small — the fleet's cash flow "
          f"crosses all three within weeks)")
    print(f"\n  Regime caveat: 2025-26 is the hot stretch for this edge; rolling starts overlap, "
          f"so worlds are not independent.")
    print(f"{'═'*112}\n  buffer_policy done in {time.time()-t_all:,.0f}s.\n{'═'*112}")
