"""
brain/research/etf_eval_config.py

ELITE TRADER FUNDING 50K STATIC — EVAL-CONFIG OPTIMIZER

Card: $99/attempt, PASS at cum >= +$4,000, FAIL if balance ever touches
-$2,000 (STATIC floor, never trails), $177 activation on pass.

The prior 73%-pass / ~13-day estimate (capital_plan.py validate()) used the
FUNDED full-stack stream (morning ORB at 3R + REJ + PM + ASIA) as a proxy.
But v12's EvalMode actually trades: morning ORB at 2R (not 3R), REJ, PM,
and NO Asia (funded-only).  This script models the REAL eval-mode streams
and asks which NT8 toggle set minimizes expected cost & time per funded acct.

Configs (morning component = ORB2R in all EVAL_* streams):
  EVAL_FULL          ORB2R + REJ + PM      (v12 EvalMode as shipped)
  EVAL_NO_PM         ORB2R + REJ           (PmOrbEnabled = false)
  EVAL_MORNING_ONLY  ORB2R                 (hypothetical morning-only)
  REFERENCE          ORB3 + REJ + PM + ASIA (funded stream = old-estimate
                     proxy; sanity anchor, must land ~73% / ~11-13 d on
                     2024+ starts and match the capital_plan stream cache)

Day composition — verbatim empire_rulemap.build_seq() rules:
  sort by entry time; REJ skipped if any ORB signal that day; PM skipped if
  morning traded AND lost; one position at a time; stop the day at -$500
  cumulative; per-strategy room gate (1150 + day_pnl_so_far) >= RISK1[strat].

Eval sim: from EVERY stream day as a start (rolling, step 1 signal-day),
walk cum P&L; FAIL checked before PASS each day (conservative, matches
capital_plan.validate()).  Days-to-resolution are SIGNAL-DAYS (days in the
stream, i.e. days with >=1 signal for that config) — NOT calendar days;
calendar days are reported separately from actual dates.  Pass rate uses
resolved runs only (censored end-of-data runs excluded, counted).

IS/OOS law: IS starts 2022-2024, OOS starts 2025-2026; ranking flips flagged.

Run:  cd <repo root> && python3 brain/research/etf_eval_config.py
      (~1-8 min: builds morning ORB twice — 3R funded + 2R eval — via the
       real backtest engine, then everything else is arithmetic)
"""

import sys, os, json, time, tempfile, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date
from collections import defaultdict

import config

# ── BUG FIX (found 2026-07-03): eval_boost's ORB2R leg runs the engine with
# config.EVAL_MODE=True, and bankroll.can_trade() PERMANENTLY HALTS the account
# once yearly profit >= EVAL_PROFIT_TARGET ($3,000, "EVAL PASSED") or balance
# <= start - EVAL_MAX_LOSS ($2,000).  run_year_morning() treats each year as
# one account, so the 2R morning stream got truncated to 54 signal-days vs 150
# for the 3R funded run — NOT "the same days at 2R".  Both gates are guarded
# by "> 0" checks, so we disarm them here (from THIS file only) to get the
# unconditional 2R stream; the ETF pass/fail mechanics are applied by OUR
# rolling-start sim below, not by the engine's internal Tradeify state machine.
config.EVAL_PROFIT_TARGET = 0.0        # disarm engine's "EVAL PASSED" halt
config.EVAL_MAX_LOSS      = 10_000_000.0  # disarm engine's "EVAL FAILED" halt

from eval_boost import build_components

# ── card + composition constants ─────────────────────────────────────────────
TARGET, FLOOR   = 4000.0, -2000.0
FEE, ACTIVATION = 99.0, 177.0
RISK1 = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}  # empire_rulemap
DLL   = 500.0
ROOM  = 1150.0

CONFIGS = [
    ("EVAL_FULL",         ("ORB2R", "REJ", "PM")),
    ("EVAL_NO_PM",        ("ORB2R", "REJ")),
    ("EVAL_MORNING_ONLY", ("ORB2R",)),
    ("REFERENCE",         ("ORB3", "REJ", "PM", "ASIA")),
]

IS_YEARS   = (2022, 2023, 2024)
OOS_YEARS  = (2025, 2026)
Y2024P     = (2024, 2025, 2026)
ALL_YEARS  = IS_YEARS + OOS_YEARS

CACHE = os.path.join(tempfile.gettempdir(), "capital_plan_stream_v12.json")


# ── day composition (empire_rulemap.build_seq pattern, generic components) ────

def compose(comp, keys):
    """-> (seq [(date, day_pnl, n_exec)], taken {strat: n executed})."""
    all_days = sorted(set().union(*[set(comp[k]) for k in keys]))
    taken = defaultdict(int)
    seq = []
    for d in all_days:
        lst = []
        for k in keys:
            lst.extend(comp[k].get(d, []))   # tuples already labeled ORB/REJ/PM/ASIA
        lst.sort(key=lambda x: x[1])
        pnl_day = morning = 0.0
        has_orb = any(s == "ORB" for s, *_ in lst)
        open_until = None
        n_exec = 0
        for strat, e_t, x_t, p in lst:
            if strat == "REJ" and has_orb: continue
            if strat == "PM" and has_orb and morning < 0: continue
            if open_until is not None and e_t < open_until: continue
            if pnl_day <= -DLL: continue
            if (ROOM + pnl_day) < RISK1[strat]: continue
            pnl_day += p
            if strat == "ORB": morning += p
            open_until = x_t
            taken[strat] += 1
            n_exec += 1
        seq.append((d, pnl_day, n_exec))
    return seq, dict(taken)


# ── eval simulation ───────────────────────────────────────────────────────────

def run_eval(seq, i0):
    """One eval attempt from stream index i0.
    -> (outcome, signal_days, calendar_days); FAIL checked before PASS."""
    cum = 0.0
    for k in range(i0, len(seq)):
        cum += seq[k][1]
        n = k - i0 + 1
        cal = (seq[k][0] - seq[i0][0]).days + 1
        if cum <= FLOOR:  return ("fail", n, cal)
        if cum >= TARGET: return ("pass", n, cal)
    return ("censored", len(seq) - i0, (seq[-1][0] - seq[i0][0]).days + 1)


def sim_bucket(seq, years):
    """All rolling starts whose start-date year is in `years`."""
    p_sd, p_cd, f_sd, f_cd, cens = [], [], [], [], 0
    for i, (d, _, _) in enumerate(seq):
        if d.year not in years:
            continue
        out, n, cal = run_eval(seq, i)
        if   out == "pass": p_sd.append(n); p_cd.append(cal)
        elif out == "fail": f_sd.append(n); f_cd.append(cal)
        else:               cens += 1
    return p_sd, p_cd, f_sd, f_cd, cens


def med(v):
    return statistics.median(v) if v else float("nan")


def bucket_stats(seq, years):
    p_sd, p_cd, f_sd, f_cd, cens = sim_bucket(seq, years)
    n_res = len(p_sd) + len(f_sd)
    if n_res == 0:
        return None
    pr = len(p_sd) / n_res
    res_sd = sorted(p_sd + f_sd)
    st = {
        "starts": n_res + cens, "resolved": n_res, "censored": cens,
        "pass": pr,
        "med_pass_sd": med(p_sd), "med_fail_sd": med(f_sd),
        "med_res_sd": med(res_sd),
        "med_pass_cd": med(p_cd), "med_fail_cd": med(f_cd),
        "p90_pass_sd": sorted(p_sd)[int(len(p_sd) * 0.9)] if p_sd else float("nan"),
    }
    if pr > 0:
        att = 1.0 / pr
        st["attempts"]  = att
        st["spend"]     = FEE * att
        st["total"]     = FEE * att + ACTIVATION
        # task's rough formula (signal-days) + refined decomposition
        st["time_rough_sd"]   = att * st["med_res_sd"]
        st["time_refined_sd"] = (att - 1.0) * (st["med_fail_sd"] if f_sd else 0.0) + st["med_pass_sd"]
        st["time_refined_cd"] = (att - 1.0) * (st["med_fail_cd"] if f_cd else 0.0) + st["med_pass_cd"]
    return st


# ── reporting ─────────────────────────────────────────────────────────────────

def stream_stats(name, seq, taken):
    pnls = [p for _, p, _ in seq]
    ntr  = sum(n for _, _, n in seq)
    pos  = sum(1 for p in pnls if p > 0)
    print(f"  {name:<18} days={len(seq):>3}  trades={ntr:>4} ({ntr/len(seq):.2f}/day)  "
          f"mean=${statistics.mean(pnls):>+7.1f}  med=${med(pnls):>+7.1f}  "
          f"sd=${statistics.pstdev(pnls):>6.0f}  win%={pos/len(pnls):.0%}  "
          f"best=${max(pnls):>+6.0f}  worst=${min(pnls):>+6.0f}  net=${sum(pnls):>+9,.0f}")
    print(f"  {'':<18} executed by strat: {taken}")


def fmt_row(name, st):
    if st is None:
        return f"  {name:<18} (no starts)"
    return (f"  {name:<18} {st['pass']:>6.1%} {st['med_pass_sd']:>5.0f} {st['med_fail_sd']:>5.0f} "
            f"{st['attempts']:>5.2f} {st['spend']:>7.0f} {st['total']:>7.0f} "
            f"{st['time_rough_sd']:>7.1f} {st['time_refined_sd']:>7.1f} {st['time_refined_cd']:>7.0f} "
            f"{st['resolved']:>5}/{st['censored']:<4}")


HDR = (f"  {'config':<18} {'pass%':>6} {'mPas':>5} {'mFai':>5} "
       f"{'att':>5} {'spend$':>7} {'total$':>7} "
       f"{'sd~':>7} {'sd*':>7} {'cal*':>7} {'res/cens':>10}")
LEGEND = ("  mPas/mFai = median signal-days to pass/fail | att = expected attempts (1/pass)\n"
          "  spend$ = 99*att | total$ = spend + 177 activation | sd~ = att*med_resolution "
          "(task formula, signal-days)\n  sd* = (att-1)*med_fail + med_pass signal-days | "
          "cal* = same in CALENDAR days | res/cens = resolved/censored starts")


if __name__ == "__main__":
    t0 = time.time()
    print("ETF 50K STATIC EVAL-CONFIG STUDY — building components "
          "(morning ORB engine runs twice: funded 3R + eval 2R)...", flush=True)
    comp = build_components()
    print(f"  components built in {time.time()-t0:.0f}s", flush=True)
    for k in ("ORB3", "ORB2R", "REJ", "PM", "ASIA"):
        print(f"    {k:<6} signal-days: {len(comp[k])}")
    d3, d2 = set(comp["ORB3"]), set(comp["ORB2R"])
    print(f"    ORB2R vs ORB3 day sets: {len(d2 & d3)} common | "
          f"{len(d2 - d3)} only-2R | {len(d3 - d2)} only-3R "
          f"{'✓ same days' if d2 == d3 else '⚠ differ (path-dependent engine halts?)'}")

    streams = {}
    for name, keys in CONFIGS:
        seq, taken = compose(comp, keys)
        # HARD GUARANTEE: no Asia leakage into eval-mode streams
        if name != "REFERENCE":
            assert "ASIA" not in keys and "ASIA" not in taken, f"ASIA leaked into {name}"
        streams[name] = (seq, taken)

    # ── sanity anchor 1: REFERENCE must byte-match capital_plan's cached stream
    print(f"\n{'─'*100}\n  SANITY CHECKS\n{'─'*100}")
    ref_seq = streams["REFERENCE"][0]
    if os.path.exists(CACHE):
        raw = json.load(open(CACHE))
        cache = {date.fromisoformat(d): p for d, p in raw}
        ours  = {d: p for d, p, _ in ref_seq}
        common = set(cache) & set(ours)
        max_diff = max(abs(cache[d] - ours[d]) for d in common) if common else float("nan")
        print(f"  vs capital_plan cache: cache {len(cache)}d | ours {len(ours)}d | "
              f"common {len(common)}d | max |P&L diff| ${max_diff:.2f} "
              f"{'✓ MATCH' if max_diff < 1e-6 and len(cache)==len(ours) else '⚠ DIFFERS — investigate'}")
    else:
        print("  capital_plan stream cache not found — skipping byte-match check")

    # ── sanity anchor 2: REFERENCE on 2024+ starts ≈ old 73% / 11-13 d estimate
    ref24 = bucket_stats(ref_seq, Y2024P)
    ok = ref24 and 0.68 <= ref24["pass"] <= 0.78 and 9 <= ref24["med_pass_sd"] <= 14
    print(f"  REFERENCE 2024+ starts: pass {ref24['pass']:.1%}, median {ref24['med_pass_sd']:.0f} "
          f"signal-days to pass (old estimate: 73% / ~11-13 d) "
          f"{'✓ ANCHOR REPRODUCED' if ok else '✗ ANCHOR BROKEN — do not trust results'}")

    # ── stream-level stats
    print(f"\n{'─'*100}\n  STREAM STATS (1 contract, all history; 'days' = signal-days "
          f"— days with >=1 signal for that config)\n{'─'*100}")
    for name, _ in CONFIGS:
        stream_stats(name, *streams[name])

    # ── eval sims per bucket
    BUCKETS = [
        ("ALL STARTS (2022-2026)", ALL_YEARS),
        ("2024+ STARTS (old-estimate basis)", Y2024P),
        ("IS STARTS (2022-2024)", IS_YEARS),
        ("OOS STARTS (2025-2026)", OOS_YEARS),
    ]
    all_stats = {}
    for label, yrs in BUCKETS:
        print(f"\n{'═'*100}\n  {label} — pass at +$4,000, static -$2,000 floor, "
              f"$99/attempt + $177 activation\n{'═'*100}")
        print(HDR)
        print(f"  {'─'*96}")
        for name, _ in CONFIGS:
            st = bucket_stats(streams[name][0], yrs)
            all_stats[(name, label)] = st
            print(fmt_row(name, st))
    print(f"\n{LEGEND}")

    # ── IS vs OOS ranking-flip check (eval configs only, rank by total cost)
    print(f"\n{'─'*100}\n  IS vs OOS RANKING (by expected total $ per funded account; "
          f"eval-legal configs only)\n{'─'*100}")
    evals = [n for n, _ in CONFIGS if n != "REFERENCE"]
    rank = {}
    for label in ("IS STARTS (2022-2024)", "OOS STARTS (2025-2026)"):
        order = sorted(evals, key=lambda n: all_stats[(n, label)]["total"])
        rank[label] = order
        print(f"  {label:<28} " + "  >  ".join(
            f"{n} (${all_stats[(n, label)]['total']:.0f}, {all_stats[(n, label)]['pass']:.0%})"
            for n in order))
    flip = rank["IS STARTS (2022-2024)"] != rank["OOS STARTS (2025-2026)"]
    print(f"  {'⚠ RANKING FLIPS between IS and OOS — see above' if flip else '✓ ranking stable across IS/OOS'}")

    # ── verdict (decide on ALL starts, sanity-check against OOS)
    best = min(evals, key=lambda n: all_stats[(n, 'ALL STARTS (2022-2026)')]["total"])
    b_all = all_stats[(best, "ALL STARTS (2022-2026)")]
    b_oos = all_stats[(best, "OOS STARTS (2025-2026)")]
    f_all = all_stats[("EVAL_FULL", "ALL STARTS (2022-2026)")]
    print(f"\n{'═'*100}\n  VERDICT\n{'═'*100}")
    print(f"  best eval config (all starts): {best} — pass {b_all['pass']:.0%}, "
          f"total ${b_all['total']:.0f}/funded, ~{b_all['time_refined_sd']:.0f} signal-days "
          f"(~{b_all['time_refined_cd']:.0f} calendar days)")
    print(f"  same config OOS-only:          pass {b_oos['pass']:.0%}, total ${b_oos['total']:.0f}, "
          f"~{b_oos['time_refined_sd']:.0f} signal-days")
    if best == "EVAL_FULL" or abs(b_all["total"] - f_all["total"]) < 15:
        print("  -> EVAL_FULL wins or ties: NO SETTINGS CHANGE NEEDED — buy with v12 defaults "
              "(EvalMode on, PmOrbEnabled on).")
    else:
        print(f"  -> switch NT8 toggles to {best} for the eval phase, revert after passing.")
    print(f"\n  done in {time.time()-t0:.0f}s.\n{'═'*100}")
