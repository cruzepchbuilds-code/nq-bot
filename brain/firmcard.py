#!/usr/bin/env python3
"""
brain/firmcard.py — price ANY prop-firm rule card against the live v12 stream
in seconds.

WHAT IT DOES
  1. Cache: on first run (or --rebuild) imports build_seq() from
     brain/research/empire_rulemap — the exact live day-composition producing
     the canonical daily P&L sequence — (minutes: loads the 88MB 1-min CSV and
     simulates) and writes data/v12_daily_stream.csv (date,pnl). Every later
     run loads that cache instantly.
  2. Funded sim: a generalized empire_rulemap.run_cell — floor type/size,
     consistency, payout cadence AND payout cap all come from flags instead of
     hardcoded pairs (5day = eligible every 5 trading days, monthly = every 21).
  3. Optional eval phase: --eval-target/--eval-cost/--activation simulate the
     eval on the same stream from each rolling start (same floor rules unless
     --eval-floor/--eval-floor-type given). Reaching +target = pass, hitting
     the floor = fail. Funded extraction then starts the day AFTER the pass.
  4. Report: expected extraction per account-year (mean over 2024+ rolling
     starts, 252-day horizon — identical methodology to empire_rulemap so
     numbers reconcile), death %, paid %, rank vs the recomputed 108-cell rule
     map, ROI vs effective price, one-line verdict.

METHODOLOGY CONSTANTS (identical to empire_rulemap)
  start $50,000; keep a $1,000 buffer above start; first payout no earlier
  than relative day 5; rolling starts = every trading day from 2024-01-01
  with >= 60 days of data remaining; horizon 252 trading days; expectation =
  mean over ALL rolling starts (dead worlds count with what they extracted).

EXAMPLES
  python3 brain/firmcard.py --name "Lucid 50K Direct" --floor-type trail_lock \
    --floor 2000 --consistency 0.20 --payout 5day --payout-cap 3000 --price 364
  python3 brain/firmcard.py --name "Tradeify 50K Daily" --floor-type trail_lock \
    --floor 2000 --consistency 0 --payout daily --payout-cap 1000 --price 260
  python3 brain/firmcard.py --name "ETF 50K Static" --floor-type fixed \
    --floor 2000 --consistency 0 --payout 5day --payout-cap 1250 --price 99 \
    --eval-target 4000 --eval-cost 99 --activation 177
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import date
from itertools import product

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "v12_daily_stream.csv")

# ── methodology constants (mirror empire_rulemap exactly) ────────────────────
START, KEEP = 50_000.0, 1_000.0
HORIZON = 252          # trading days per simulated account-year
START_YEAR = 2024      # rolling-start worlds begin 2024+
MIN_TAIL = 60          # a start needs >= 60 days of data remaining
WARMUP = 5             # first payout no earlier than relative day 5

CADENCE = {"daily": 1, "5day": 5, "monthly": 21}          # eligibility gap (td)
CANON_CAP = {"daily": 1000.0, "5day": 3000.0, "monthly": float("inf")}
FLOOR_TYPES = ("fixed", "trail_lock", "trail_pure")


# ── cache layer ───────────────────────────────────────────────────────────────
def build_cache():
    """One-time build: import the live day-composition and persist (date,pnl)."""
    print("[firmcard] cache miss — building v12 daily stream from "
          "empire_rulemap.build_seq() (one-time, several minutes)...", flush=True)
    t0 = time.time()

    # replicate the sys.path insertion the brain/research scripts do
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "brain", "research"))
    os.chdir(ROOT)  # research code loads data/nq_full.csv via relative path

    # heartbeat so long builds show progress
    import threading
    stop = threading.Event()

    def beat():
        while not stop.wait(30):
            print(f"[firmcard]   ...still building ({time.time() - t0:,.0f}s "
                  f"elapsed; loads the 1-min CSV twice + simulates)", flush=True)

    hb = threading.Thread(target=beat, daemon=True)
    hb.start()
    try:
        from empire_rulemap import build_seq
        print("[firmcard]   imported empire_rulemap — running build_seq()", flush=True)
        seq = build_seq()
    finally:
        stop.set()

    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "pnl"])
        for d, p in seq:
            w.writerow([d.isoformat(), repr(float(p))])
    os.replace(tmp, CACHE)
    print(f"[firmcard] built {len(seq)} trading days in {time.time() - t0:,.0f}s "
          f"→ {CACHE}", flush=True)
    return seq


def load_stream(rebuild=False):
    if rebuild or not os.path.exists(CACHE):
        return build_cache()
    with open(CACHE) as f:
        r = csv.reader(f)
        next(r)  # header
        seq = [(date.fromisoformat(d), float(p)) for d, p in r]
    print(f"[firmcard] loaded cached stream: {len(seq)} trading days "
          f"({seq[0][0]} → {seq[-1][0]}) from {os.path.relpath(CACHE, ROOT)}",
          flush=True)
    return seq


# ── funded-phase simulator (generalized empire_rulemap.run_cell) ─────────────
def run_funded(pnls, i0, floor_type, floor_amt, cons_pct, cadence, cap,
               horizon=HORIZON):
    """One account-year. Returns (withdrawn, died). Math is op-for-op identical
    to empire_rulemap.run_cell; only cadence/cap are parameters now."""
    bal = peak = START
    floor = START - floor_amt
    tp = best = wd = 0.0
    last_pay = -99
    for k in range(i0, min(i0 + horizon, len(pnls))):
        p = pnls[k]
        bal += p
        tp += p
        if p > best:
            best = p
        if bal <= floor:
            return wd, True
        rel = k - i0
        ok_cons = (cons_pct == 0) or (tp > 0 and tp >= best / cons_pct)
        can = True if cadence <= 1 else (rel - last_pay >= cadence)
        if ok_cons and can and rel >= WARMUP:
            avail = bal - (START + KEEP)
            if avail > cap:
                avail = cap
            if avail > 0:
                bal -= avail
                wd += avail
                last_pay = rel
        if bal > peak:
            peak = bal
        if floor_type == "trail_lock":
            nf = peak - floor_amt
            if nf > START:
                nf = START
            if nf > floor:
                floor = nf
        elif floor_type == "trail_pure":
            nf = peak - floor_amt
            if nf > floor:
                floor = nf
    return wd, False


# ── eval-phase simulator ─────────────────────────────────────────────────────
def run_eval(pnls, i0, target, floor_type, floor_amt):
    """Simulate one eval attempt from i0 on the same stream.
    Returns (status, days, k_last): status pass|die|open (open = data ran out),
    k_last = index of the day the eval resolved."""
    cum = hwm = 0.0
    floor = -floor_amt
    n = 0
    for k in range(i0, len(pnls)):
        cum += pnls[k]
        n += 1
        if cum <= floor:
            return "die", n, k
        if cum >= target:
            return "pass", n, k
        if cum > hwm:
            hwm = cum
        if floor_type == "trail_lock":
            nf = hwm - floor_amt
            if nf > 0.0:
                nf = 0.0
            if nf > floor:
                floor = nf
        elif floor_type == "trail_pure":
            nf = hwm - floor_amt
            if nf > floor:
                floor = nf
    return "open", n, None


# ── 108-cell rule map (recomputed from cache — cheap) ────────────────────────
def rolling_starts(seq):
    return [i for i, (d, _) in enumerate(seq)
            if d.year >= START_YEAR and len(seq) - i >= MIN_TAIL]


def build_map(pnls, idx):
    print(f"[firmcard] recomputing 108-cell rule map "
          f"({len(idx)} rolling-start worlds each)...", flush=True)
    t0 = time.time()
    rows = []
    combos = list(product(FLOOR_TYPES, (1500, 2000, 2500, 3000),
                          (0, 0.20, 0.40), ("daily", "5day", "monthly")))
    for n, (ft, fa, cp, po) in enumerate(combos, 1):
        cad, cap = CADENCE[po], CANON_CAP[po]
        wd_sum = died = paid = 0
        for i in idx:
            wd, dead = run_funded(pnls, i, ft, fa, cp, cad, cap)
            wd_sum += wd
            died += dead
            paid += wd > 0
        m = len(idx)
        rows.append((wd_sum / m, died / m, paid / m, ft, fa, cp, po))
        if n % 27 == 0:
            print(f"[firmcard]   {n}/108 cells ({time.time() - t0:,.0f}s)",
                  flush=True)
    print(f"[firmcard] map done in {time.time() - t0:,.1f}s", flush=True)
    return rows


def map_rank(rows, extraction):
    return sum(1 for r in rows if r[0] > extraction) + 1


# ── verdict ──────────────────────────────────────────────────────────────────
def verdict(rank, total, roi, death):
    pct = rank / total
    r = roi if roi is not None else 0.0
    if pct <= 0.15 and r >= 20:
        v = "GOLD CELL — buy"
    elif pct <= 0.35 and r >= 10:
        v = "strong card — buy"
    elif pct <= 0.35:
        v = "good rules, pricey — negotiate"
    elif pct <= 0.60 and r >= 10:
        v = "playable — fair value"
    elif pct <= 0.60:
        v = "middling — skip unless discounted"
    else:
        v = "mediocre — skip"
    if death >= 0.60 and "skip" not in v:
        v += " (high mortality — budget for re-buys)"
    return v


# ── card pricing ─────────────────────────────────────────────────────────────
def price_card(seq, a):
    pnls = [p for _, p in seq]
    idx = rolling_starts(seq)
    cap = a.payout_cap if a.payout_cap is not None else CANON_CAP[a.payout]
    cad = CADENCE[a.payout]

    out = {"name": a.name, "floor_type": a.floor_type, "floor": a.floor,
           "consistency": a.consistency, "payout": a.payout,
           "payout_cap": None if cap == float("inf") else cap}

    # ── eval phase (optional) ──
    eval_block = None
    funded_starts = idx
    if a.eval_target is not None:
        eft = a.eval_floor_type or a.floor_type
        efa = a.eval_floor if a.eval_floor is not None else a.floor
        res = [run_eval(pnls, i, a.eval_target, eft, efa) for i in idx]
        passes = [(n, k) for s, n, k in res if s == "pass"]
        dies = sum(1 for s, _, _ in res if s == "die")
        resolved = len(passes) + dies
        pass_rate = len(passes) / resolved if resolved else 0.0
        days = sorted(n for n, _ in passes)
        med_days = days[len(days) // 2] if days else -1
        attempts = (1.0 / pass_rate) if pass_rate > 0 else float("inf")
        ecost = a.eval_cost if a.eval_cost is not None else (a.price or 0.0)
        total_cost = ecost * attempts + (a.activation or 0.0)
        funded_starts = [k + 1 for _, k in passes if len(pnls) - (k + 1) >= MIN_TAIL]
        eval_block = {"target": a.eval_target, "floor_type": eft, "floor": efa,
                      "pass_rate": pass_rate, "median_days": med_days,
                      "attempts": attempts, "eval_cost": ecost,
                      "activation": a.activation or 0.0,
                      "cost_per_funded": total_cost,
                      "resolved": resolved, "funded_worlds": len(funded_starts)}
        out["eval"] = eval_block

    # ── funded phase ──
    res = [run_funded(pnls, i, a.floor_type, a.floor, a.consistency, cad, cap)
           for i in funded_starts]
    n = len(res)
    extraction = sum(r[0] for r in res) / n if n else 0.0
    death = sum(1 for r in res if r[1]) / n if n else 0.0
    paid = sum(1 for r in res if r[0] > 0) / n if n else 0.0

    # cold-start reference for eval cards (plain rolling starts, map-comparable)
    cold = None
    if eval_block is not None:
        cres = [run_funded(pnls, i, a.floor_type, a.floor, a.consistency, cad, cap)
                for i in idx]
        cold = sum(r[0] for r in cres) / len(cres)

    # ── rank vs the 108-cell map ──
    rows = build_map(pnls, idx)
    rank = map_rank(rows, extraction)

    # ── ROI ──
    if eval_block is not None:
        eff_price = eval_block["cost_per_funded"]
        price_note = (f"${eval_block['eval_cost']:,.0f} × "
                      f"{eval_block['attempts']:.2f} attempts + "
                      f"${eval_block['activation']:,.0f} activation")
    else:
        eff_price = a.price
        price_note = "sticker"
    roi = (extraction / eff_price) if eff_price else None
    vd = verdict(rank, len(rows), roi, death)

    out.update({"extraction_yr": extraction, "death": death, "paid": paid,
                "rank": rank, "cells": len(rows), "roi": roi,
                "effective_price": eff_price, "verdict": vd,
                "worlds": n, "cold_start_extraction": cold})

    # ── report block ──
    cap_s = "uncapped" if cap == float("inf") else f"cap ${cap:,.0f}"
    cons_s = "none" if a.consistency == 0 else f"{a.consistency:.0%}"
    W = 70
    print()
    print("═" * W)
    print(f"  FIRM CARD — {a.name}")
    print("═" * W)
    print(f"  rules  : {a.floor_type} ${a.floor:,.0f} floor | consistency "
          f"{cons_s} | payout {a.payout} ({cap_s})")
    print(f"  stream : v12 daily P&L, {len(seq)} days "
          f"({seq[0][0]} → {seq[-1][0]})")
    print(f"  worlds : {len(idx)} rolling starts (2024+, {HORIZON}-day horizon)")
    if eval_block:
        e = eval_block
        print(f"\n  EVAL PHASE  (target +${e['target']:,.0f}, "
              f"{e['floor_type']} ${e['floor']:,.0f} floor, "
              f"{e['resolved']} resolved attempts)")
        print(f"    pass rate               : {e['pass_rate']:.0%}")
        print(f"    median days to pass     : {e['median_days']} td")
        print(f"    expected attempts       : {e['attempts']:.2f}")
        print(f"    cost per funded account : ${e['eval_cost']:,.0f} × "
              f"{e['attempts']:.2f} + ${e['activation']:,.0f} = "
              f"${e['cost_per_funded']:,.0f}")
        print(f"\n  FUNDED PHASE  (starts day after pass; "
              f"{n} worlds with ≥{MIN_TAIL}d of data)")
    else:
        print(f"\n  FUNDED PHASE")
    print(f"    extraction / account-year : ${extraction:,.0f}")
    print(f"    death: {death:.0%}   paid at least once: {paid:.0%}")
    if cold is not None:
        print(f"    (cold-start reference, plain rolling starts: ${cold:,.0f})")
    print(f"\n  rank vs 108-cell rule map : {rank}/{len(rows)}  "
          f"(top {rank / len(rows):.0%})")
    if roi is not None:
        print(f"  ROI    : {roi:,.1f}×   (${extraction:,.0f} extraction / "
              f"${eff_price:,.0f} effective price [{price_note}])")
    else:
        print(f"  ROI    : n/a (no --price given)")
    print(f"  VERDICT: {vd}")
    print("═" * W)
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Price any prop-firm rule card against the live v12 stream.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--name", default="Unnamed card", help="card label")
    ap.add_argument("--floor-type", choices=FLOOR_TYPES, default="trail_lock",
                    help="drawdown floor style")
    ap.add_argument("--floor", type=float, default=2000.0,
                    help="floor size, $ below start (or below peak if trailing)")
    ap.add_argument("--consistency", type=float, default=0.0,
                    help="payout needs total >= best_day/pct; 0 = none (e.g. 0.20)")
    ap.add_argument("--payout", choices=("daily", "5day", "monthly"),
                    default="5day", help="payout cadence (5day = eligible every "
                    "5 trading days, monthly = every 21)")
    ap.add_argument("--payout-cap", type=float, default=None,
                    help="max $ per payout (default: canonical cap for the "
                    "cadence — daily $1k, 5day $3k, monthly uncapped)")
    ap.add_argument("--price", type=float, default=None,
                    help="sticker price $ (funded/direct account)")
    ap.add_argument("--eval-target", type=float, default=None,
                    help="eval profit target $; presence turns on the eval phase")
    ap.add_argument("--eval-cost", type=float, default=None,
                    help="cost per eval attempt $ (default: --price)")
    ap.add_argument("--activation", type=float, default=None,
                    help="one-time activation fee $ on passing the eval")
    ap.add_argument("--eval-floor", type=float, default=None,
                    help="eval floor size $ (default: --floor)")
    ap.add_argument("--eval-floor-type", choices=FLOOR_TYPES, default=None,
                    help="eval floor style (default: --floor-type)")
    ap.add_argument("--rebuild", action="store_true",
                    help="force rebuild of the cached daily stream")
    ap.add_argument("--json", metavar="PATH", default=None,
                    help="append a one-line JSON summary to PATH")
    a = ap.parse_args()

    t0 = time.time()
    seq = load_stream(rebuild=a.rebuild)
    out = price_card(seq, a)
    print(f"\n[firmcard] total {time.time() - t0:,.1f}s")

    if a.json:
        with open(a.json, "a") as f:
            f.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
