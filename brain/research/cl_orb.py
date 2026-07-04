"""
brain/research/cl_orb.py — CL (crude oil) 9:00 ET pit-open ORB: THE definitive study.

════════════════════════════════════════════════════════════════════════════════
RESULT (2026-07-03): KILL — LEDGER ITEM CLOSED. DO NOT DEPLOY, DO NOT REVISIT
WITHOUT NEW EVIDENCE.
  0 / 1008 native configs reach even IS PF 1.0 (max 0.95, grid median 0.82).
  Frictionless (zero comm/slip, touch fills): IS max PF 1.07 -> 0.87 OOS.
  There is NO GROSS EDGE — the kill is not a cost story. A 5-15 min OR at the
  9:00 pit open breaks on 99% of days: the "breakout" carries no information
  (same structural finding as the GC kill — no equity-style opening drive).
  Wednesday/EIA handling sign-flips across eras (Wed IS PF 1.04 -> OOS 0.80);
  OR-size, direction, and Wed-skip pockets all die in at least one era.
  Details: run this file (~3 min) or see research_log.md Failed Strategies.
════════════════════════════════════════════════════════════════════════════════

Ledger item: "CL ORB at 9:00 ET open — 1.4M bars available, completely
uncorrelated with NQ" (project_state.md, Optional future research).
Prior art: counterbalance.py tested CL with NQ-TRANSPLANTED params (gap filter,
NQ-scaled stops) -> edgeless but anti-correlated (-0.20). This study is the
NATIVE CL parameter sweep, run under house law.

HOUSE LAW
  IS = 2022-2024, OOS = 2025-2026 (data ends 2026-06-12).
  Select on IS ONLY -> confirm on OOS. Year-by-year table required.
  Edge must be positive + coherent in BOTH eras. N >= ~80 total or reject.

PATTERN (base grid — no OR-size filter, no gap filter, no DOW filter):
  OR built from 9:00 ET open, windows {5, 10, 15} min.
  Entry: first 1-min CLOSE beyond OR extreme (+buffer {0.00, 0.05}), after OR
  completes, up to cutoff {10:30, 11:00}. One trade/day, first signal wins.
  Stop: fixed {0.10 .. 0.50} CL pts ($100-$500). Target: {1.5, 2, 2.5, 3} R.
  Hard flatten {11:30, 12:30, 14:00}.
  Grid = 3*2*7*4*2*3 = 1008 configs, evaluated per day once via caching.

COST / FILL MODEL (stated per mission):
  CL: $1.55/side commission = $3.10 RT. Slippage: 1 tick ($10) adverse on every
  market fill (entry at signal-bar close, stop-outs, flattens). Targets are
  limit orders: fill AT target only if the bar trades >= 1 tick THROUGH it
  (touch alone doesn't fill). Same-bar stop+target ambiguity -> STOP wins.
  Gap-through-stop -> filled at bar open (worse than stop), 1 tick adverse.
  Net effect ~ $23.10/RT typical — consistent with the desk's flat $24.50 CL
  cost in counterbalance.py.
  MCL fallback: $0.85/side comm + 2 ticks/side slippage ($1/tick):
  pnl_mcl = pts*100 - 2.00(extra slip) - 1.70  (pts already carry CL 1-tick model).

CONTRACT FACTS (CME standard):
  CL  = 1,000 bbl, tick 0.01 = $10  -> $1,000 per 1.00 pt.
  MCL =   100 bbl, tick 0.01 = $1   -> $100 per 1.00 pt.

STRUCTURAL FACTORS TESTED HONESTLY (both eras, finalist-level):
  EIA petroleum status report ~10:30 ET most Wednesdays (holiday weeks shift):
  variants = no-special-case / skip-Wed / Wed-entry-only-after-10:35.
  Roll days: trades are strictly intraday on a spliced continuous series, so
  roll splices (between sessions) never touch a position. Verified empirically
  via a drop-|session-gap|>$1 sensitivity line.

Run:  python3 brain/research/cl_orb.py     (from repo root, ~1-2 min)
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import date, datetime

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CL_CSV = os.path.join(BASE, "data", "cl_1min.csv")
V12_CSV = os.path.join(BASE, "data", "v12_daily_stream.csv")

TICK = 0.01
PT = 1000.0          # CL $/pt
COMM = 3.10          # CL round-trip commission
MCL_PT = 100.0
MCL_EXTRA = 2.00 + 1.70   # extra slippage (2 ticks/side vs 1) + MCL RT comm

IS_Y = (2022, 2023, 2024)
OOS_Y = (2025, 2026)
ALL_Y = (2022, 2023, 2024, 2025, 2026)

# grid (minutes-since-midnight for speed)
OR_WINDOWS = (5, 10, 15)                 # OR = [9:00, 9:00+w)
BUFFERS = (0.00, 0.05)
STOPS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
TMULTS = (1.5, 2.0, 2.5, 3.0)
CUTOFFS = (630, 660)                     # 10:30, 11:00 last entry
FLATS = (690, 750, 840)                  # 11:30, 12:30, 14:00
OPEN_M = 540                             # 9:00
MAX_CUTOFF = max(CUTOFFS)

# IS survivor bar (selection uses IS ONLY)
MIN_N_IS = 100
MIN_PF_IS = 1.15
MIN_AVG_IS = 15.0        # $/trade after costs
MIN_YR_NET = -1500.0     # no IS year worse than this
MIN_POS_YRS = 2          # of 3 IS years net > 0

NQ_RISK = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}


def mm(t_str):
    return int(t_str[:2]) * 60 + int(t_str[3:5])


def fmt_t(m):
    return f"{m // 60:02d}:{m % 60:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# 1. LOAD + QC
# ─────────────────────────────────────────────────────────────────────────────
def load_cl():
    """Bars 8:00-14:59 ET per date + last close of EVERY session (roll/gap QC)."""
    days = defaultdict(list)
    sess_last_close = {}          # date-str -> last close of that calendar day
    n = 0
    with open(CL_CSV) as f:
        next(f)  # header: timestamp,open,high,low,close,volume
        for line in f:
            ts, o, h, l, c, v = line.rstrip("\n").split(",")
            n += 1
            cf = float(c)
            d = ts[:10]
            sess_last_close[d] = cf
            hh = int(ts[11:13])
            if hh < 8 or hh > 14:
                continue
            m = hh * 60 + int(ts[14:16])
            days[d].append((m, float(o), float(h), float(l), cf))
    print(f"  parsed {n:,} rows -> {len(days)} dates with 8:00-14:59 bars", flush=True)
    return days, sess_last_close


def qc(days, sess_last_close):
    dates = sorted(days)
    usable, skipped = [], 0
    prior_close = {}
    prev_d = None
    for d in sorted(sess_last_close):
        if prev_d is not None:
            prior_close[d] = sess_last_close[prev_d]
        prev_d = d
    for d in dates:
        wd = date.fromisoformat(d).weekday()
        if wd >= 5:
            skipped += 1
            continue
        bars = days[d]
        or15 = [b for b in bars if OPEN_M <= b[0] < OPEN_M + 15]
        post = [b for b in bars if OPEN_M + 15 <= b[0] <= 750]
        if len(or15) < 13 or len(post) < 60:
            skipped += 1
            continue
        usable.append(d)
    gaps = []
    for d in usable:
        op = next(b for b in days[d] if b[0] >= OPEN_M)[1]
        pc = prior_close.get(d)
        if pc:
            gaps.append((abs(op - pc), d))
    gaps.sort(reverse=True)
    big = [g for g in gaps if g[0] > 1.0]
    print(f"  usable weekdays with full OR+session: {len(usable)}  (skipped {skipped})")
    per_y = defaultdict(int)
    for d in usable:
        per_y[int(d[:4])] += 1
    print("  days/year:", dict(sorted(per_y.items())))
    print(f"  |9:00-open vs prior-session-close| > $1.00: {len(big)} days "
          f"(top: {[(d, round(g, 2)) for g, d in gaps[:5]]})")
    return usable, prior_close


# ─────────────────────────────────────────────────────────────────────────────
# 2. PER-DAY PRIMITIVES (cached once, reused by all 1008 configs)
# ─────────────────────────────────────────────────────────────────────────────
def or_levels(bars, w):
    hi = lo = None
    for m, o, h, l, c in bars:
        if m < OPEN_M:
            continue
        if m >= OPEN_M + w:
            break
        hi = h if hi is None else max(hi, h)
        lo = l if lo is None else min(lo, l)
    return hi, lo


def first_break(bars, w, orh, orl, buf, start_m, end_m):
    """First close beyond OR+/-buf in [start_m, end_m]. -> (dir, minute, close) | None"""
    up, dn = orh + buf, orl - buf
    for m, o, h, l, c in bars:
        if m < start_m:
            continue
        if m > end_m:
            return None
        if c > up:
            return (1, m, c)
        if c < dn:
            return (-1, m, c)
    return None


def sim_exit(bars, i0, direction, efill, stop_pts, tgt_pts, slip=TICK, touch=False):
    """Scan bars after entry bar. -> (kind, minute, fill) kind in {'stop','tgt'} | None.
       Conservative: gap-through-stop fills at open-slip; same-bar stop wins;
       target needs 1-tick trade-through (unless touch=True: optimistic touch fill)."""
    sl = efill - stop_pts if direction == 1 else efill + stop_pts
    tp = efill + tgt_pts if direction == 1 else efill - tgt_pts
    thru = 0.0 if touch else TICK
    for j in range(i0 + 1, len(bars)):
        m, o, h, l, c = bars[j]
        if direction == 1:
            if o <= sl:
                return ("stop", m, o - slip)
            if l <= sl:
                return ("stop", m, sl - slip)
            if o >= tp + thru:
                return ("tgt", m, o)
            if h >= tp + thru:
                return ("tgt", m, tp)
        else:
            if o >= sl:
                return ("stop", m, o + slip)
            if h >= sl:
                return ("stop", m, sl + slip)
            if o <= tp - thru:
                return ("tgt", m, o)
            if l <= tp - thru:
                return ("tgt", m, tp)
    return None


def day_pack(bars):
    """Everything reusable for one day."""
    idx = {b[0]: i for i, b in enumerate(bars)}
    flat_fill = {}
    for f in FLATS:
        px = None
        for m, o, h, l, c in bars:
            if m >= f:
                px = (o, m)     # market at open of first bar >= flatten time
                break
        if px is None:
            lb = bars[-1]
            px = (lb[4], lb[0])  # half day: last close
        flat_fill[f] = px
    ors = {w: or_levels(bars, w) for w in OR_WINDOWS}
    return idx, flat_fill, ors


# ─────────────────────────────────────────────────────────────────────────────
# 3. SWEEP — evaluate all configs from cached per-day sims
# ─────────────────────────────────────────────────────────────────────────────
def sweep(days, usable, wed_mode="none", comm=COMM, slip=TICK, touch=False):
    """wed_mode: 'none' | 'skip' | 'after' (Wed entries only from 10:35).
       comm/slip/touch: cost model (comm=0, slip=0, touch=True = frictionless).
       Returns cfg -> {yr: [gw, gl, n, net, wins]}."""
    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0, 0.0, 0]))
    for k, d in enumerate(usable):
        if k % 400 == 0:
            print(f"    day {k}/{len(usable)}", flush=True)
        bars = days[d]
        yr = int(d[:4])
        wd = date.fromisoformat(d).weekday()
        if wed_mode == "skip" and wd == 2:
            continue
        idx, flat_fill, ors = day_pack(bars)
        start_floor = 635 if (wed_mode == "after" and wd == 2) else None
        for w in OR_WINDOWS:
            orh, orl = ors[w]
            if orh is None:
                continue
            for buf in BUFFERS:
                st = max(OPEN_M + w, start_floor) if start_floor else OPEN_M + w
                sig = first_break(bars, w, orh, orl, buf, st, MAX_CUTOFF)
                if sig is None:
                    continue
                dirn, em, eclose = sig
                efill = eclose + slip * dirn
                i0 = idx[em]
                for stop in STOPS:
                    for tm in TMULTS:
                        hit = sim_exit(bars, i0, dirn, efill, stop, stop * tm,
                                       slip=slip, touch=touch)
                        for cut in CUTOFFS:
                            if em > cut:
                                continue
                            for f in FLATS:
                                if hit is not None and hit[1] < f:
                                    xfill = hit[2]
                                else:
                                    fp, fm = flat_fill[f]
                                    xfill = fp - slip * dirn
                                pnl = (xfill - efill) * dirn * PT - comm
                                a = agg[(w, buf, cut, stop, tm, f)][yr]
                                a[0] += pnl if pnl > 0 else 0.0
                                a[1] += -pnl if pnl <= 0 else 0.0
                                a[2] += 1
                                a[3] += pnl
                                a[4] += 1 if pnl > 0 else 0
    return agg


def grid_health(agg, label):
    """Population-level honesty stats for a sweep result."""
    is_pfs, oos_pfs = [], []
    best = (None, -1.0, None)
    for cfg, a in agg.items():
        i, o = era(a, IS_Y), era(a, OOS_Y)
        is_pfs.append(i["pf"])
        oos_pfs.append(o["pf"])
        if i["pf"] > best[1]:
            best = (cfg, i["pf"], o)
    is_pfs.sort(); oos_pfs.sort()
    q = lambda v, p: v[int(p * (len(v) - 1))]
    print(f"  {label}:")
    print(f"    IS  PF q25/med/q75 = {q(is_pfs,.25):.2f}/{q(is_pfs,.5):.2f}/"
          f"{q(is_pfs,.75):.2f}  max={is_pfs[-1]:.2f}  share>1.0: "
          f"{sum(1 for x in is_pfs if x > 1)/len(is_pfs):.1%}")
    print(f"    OOS PF q25/med/q75 = {q(oos_pfs,.25):.2f}/{q(oos_pfs,.5):.2f}/"
          f"{q(oos_pfs,.75):.2f}  max={oos_pfs[-1]:.2f}  share>1.0: "
          f"{sum(1 for x in oos_pfs if x > 1)/len(oos_pfs):.1%}")
    print(f"    best-IS cfg: {cfg_str(best[0])} -> IS PF {best[1]:.2f}, "
          f"OOS PF {best[2]['pf']:.2f}")
    return best


def era(aggcfg, yrs):
    gw = sum(aggcfg[y][0] for y in yrs if y in aggcfg)
    gl = sum(aggcfg[y][1] for y in yrs if y in aggcfg)
    n = sum(aggcfg[y][2] for y in yrs if y in aggcfg)
    net = sum(aggcfg[y][3] for y in yrs if y in aggcfg)
    wins = sum(aggcfg[y][4] for y in yrs if y in aggcfg)
    pf = gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0)
    return dict(pf=pf, n=n, net=net, avg=(net / n if n else 0.0),
                wr=(100.0 * wins / n if n else 0.0))


def cfg_str(c):
    w, buf, cut, stop, tm, f = c
    return (f"OR{w:>2} buf{buf:.2f} cut{fmt_t(cut)} stop{stop:.2f} "
            f"tgt{tm:.1f}R flat{fmt_t(f)}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. FINALIST DETAIL (single-config re-run with per-trade records)
# ─────────────────────────────────────────────────────────────────────────────
def run_config(days, usable, cfg, wed_mode="none"):
    w, buf, cut, stop, tm, f = cfg
    trades = []
    for d in usable:
        wd = date.fromisoformat(d).weekday()
        if wed_mode == "skip" and wd == 2:
            continue
        bars = days[d]
        idx, flat_fill, ors = day_pack(bars)
        orh, orl = ors[w]
        if orh is None:
            continue
        st = OPEN_M + w
        if wed_mode == "after" and wd == 2:
            st = max(st, 635)
        sig = first_break(bars, w, orh, orl, buf, st, cut)
        if sig is None:
            continue
        dirn, em, eclose = sig
        efill = eclose + TICK * dirn
        hit = sim_exit(bars, idx[em], dirn, efill, stop, stop * tm)
        if hit is not None and hit[1] < f:
            kind, xm, xfill = hit
        else:
            fp, fm = flat_fill[f]
            kind, xm, xfill = "flat", fm, fp - TICK * dirn
        pnl = (xfill - efill) * dirn * PT - COMM
        trades.append(dict(d=d, yr=int(d[:4]), wd=wd, mo=int(d[5:7]), dirn=dirn,
                           em=em, xm=xm, kind=kind, pnl=pnl,
                           orr=(orh - orl), pts=(xfill - efill) * dirn))
    return trades


def tstats(trs):
    if not trs:
        return dict(pf=0.0, n=0, net=0.0, avg=0.0, wr=0.0)
    gw = sum(t["pnl"] for t in trs if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trs if t["pnl"] <= 0)
    net = gw - gl
    n = len(trs)
    return dict(pf=(gw / gl if gl > 0 else 99.0), n=n, net=net, avg=net / n,
                wr=100.0 * sum(1 for t in trs if t["pnl"] > 0) / n)


def line(label, s):
    print(f"  {label:<26} N={s['n']:>4}  WR={s['wr']:>5.1f}%  PF={s['pf']:>5.2f}  "
          f"net=${s['net']:>+10,.0f}  avg=${s['avg']:>+7.0f}")


def max_dd_worst(trs):
    by_day = defaultdict(float)
    for t in trs:
        by_day[t["d"]] += t["pnl"]
    eq = peak = 0.0
    mdd = 0.0
    worst = (0.0, "-")
    for d in sorted(by_day):
        eq += by_day[d]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        if by_day[d] < worst[0]:
            worst = (by_day[d], d)
    return mdd, worst


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 78)
    print("CL 9:00 ET ORB — definitive study (IS 2022-24 select, OOS 2025-26 confirm)")
    print("=" * 78)
    print("[1/7] Loading data/cl_1min.csv ...", flush=True)
    days, slc = load_cl()
    usable, prior_close = qc(days, slc)

    print("\n[2/7] Grid sweep on all days (1008 configs, cached per-day sims)...",
          flush=True)
    agg = sweep(days, usable)

    rows = []
    for cfg, a in agg.items():
        is_s = era(a, IS_Y)
        oos_s = era(a, OOS_Y)
        yr_nets = {y: (a[y][3] if y in a else 0.0) for y in IS_Y}
        rows.append((cfg, is_s, oos_s, yr_nets))

    # honesty stat: whole-grid distribution BEFORE any selection
    import statistics as st
    is_pfs = sorted(r[1]["pf"] for r in rows)
    oos_pfs = sorted(r[2]["pf"] for r in rows)
    q = lambda v, p: v[int(p * (len(v) - 1))]
    print(f"\n  GRID HONESTY (all {len(rows)} configs, no selection):")
    print(f"    IS  PF quartiles: {q(is_pfs,.25):.2f} / {q(is_pfs,.5):.2f} / "
          f"{q(is_pfs,.75):.2f}   share PF>1: {sum(1 for x in is_pfs if x > 1)/len(is_pfs):.0%}")
    print(f"    OOS PF quartiles: {q(oos_pfs,.25):.2f} / {q(oos_pfs,.5):.2f} / "
          f"{q(oos_pfs,.75):.2f}   share PF>1: {sum(1 for x in oos_pfs if x > 1)/len(oos_pfs):.0%}")

    print("\n[3/7] IS survivor filter + neighborhood-robust selection (IS ONLY)...")
    surv = []
    for cfg, is_s, oos_s, yr_nets in rows:
        pos_yrs = sum(1 for y in IS_Y if yr_nets[y] > 0)
        if (is_s["n"] >= MIN_N_IS and is_s["pf"] >= MIN_PF_IS
                and is_s["avg"] >= MIN_AVG_IS and pos_yrs >= MIN_POS_YRS
                and min(yr_nets.values()) > MIN_YR_NET):
            surv.append((cfg, is_s, oos_s))
    print(f"  survivors of IS bar (N>={MIN_N_IS}, PF>={MIN_PF_IS}, avg>=${MIN_AVG_IS:.0f}, "
          f">={MIN_POS_YRS}/3 yrs pos, min-yr>{MIN_YR_NET:.0f}): {len(surv)} / {len(rows)}")

    if not surv:
        print("\n  NO CONFIG SURVIVES THE IS BAR — running KILL-AUDIT battery")
        print("  (try to kill the kill before signing it).")
        best10 = sorted(rows, key=lambda r: r[1]["pf"], reverse=True)[:10]
        print("\n  Top-10 raw IS PF (all fail the bar) — for the record:")
        for cfg, is_s, oos_s, _ in best10:
            print(f"    {cfg_str(cfg)}  IS PF={is_s['pf']:.2f} N={is_s['n']} "
                  f"avg=${is_s['avg']:+.0f} | OOS PF={oos_s['pf']:.2f} N={oos_s['n']} "
                  f"avg=${oos_s['avg']:+.0f}")

        # ── audit A: FRICTIONLESS grid (comm=0, slip=0, touch fills) ─────────
        print("\n  [A] Frictionless sweep — is the kill just a cost story?")
        agg0 = sweep(days, usable, comm=0.0, slip=0.0, touch=True)
        grid_health(agg0, "zero-cost, zero-slip, touch-fill grid (most generous)")

        # ── audit B: Wednesday / EIA handling at population level ────────────
        print("\n  [B] EIA/Wednesday handling (population level, real costs):")
        for mode, nm in (("skip", "skip-Wednesday"),
                         ("after", "Wed-entry-only->=10:35")):
            aggw = sweep(days, usable, wed_mode=mode)
            grid_health(aggw, nm)

        # ── audit C: OR-size filter + direction + Wed splits on best configs ─
        print("\n  [C] Best-IS configs — OR-size bands / direction / Wednesday:")
        for cfg, is_s, oos_s, _ in best10[:3]:
            trs = run_config(days, usable, cfg)
            i_t = [t for t in trs if t["yr"] in IS_Y]
            o_t = [t for t in trs if t["yr"] in OOS_Y]
            print(f"    {cfg_str(cfg)}")
            for band, lo, hi in (("OR<0.10", 0, 0.10), ("0.10-0.20", 0.10, 0.20),
                                 ("0.20-0.40", 0.20, 0.40), ("OR>0.40", 0.40, 9e9)):
                bi = tstats([t for t in i_t if lo <= t["orr"] < hi])
                bo = tstats([t for t in o_t if lo <= t["orr"] < hi])
                print(f"      {band:<10} IS PF={bi['pf']:.2f}/N{bi['n']:<4} "
                      f"OOS PF={bo['pf']:.2f}/N{bo['n']}")
            for nm, sel in (("long", 1), ("short", -1)):
                bi = tstats([t for t in i_t if t["dirn"] == sel])
                bo = tstats([t for t in o_t if t["dirn"] == sel])
                print(f"      {nm:<10} IS PF={bi['pf']:.2f}/N{bi['n']:<4} "
                      f"OOS PF={bo['pf']:.2f}/N{bo['n']}")
            wi = tstats([t for t in i_t if t["wd"] == 2])
            wo = tstats([t for t in o_t if t["wd"] == 2])
            ni = tstats([t for t in i_t if t["wd"] != 2])
            no = tstats([t for t in o_t if t["wd"] != 2])
            print(f"      Wed-only   IS PF={wi['pf']:.2f}/N{wi['n']:<4} "
                  f"OOS PF={wo['pf']:.2f}/N{wo['n']}   "
                  f"non-Wed IS PF={ni['pf']:.2f} OOS PF={no['pf']:.2f}")

        # ── audit D: year-by-year table for the best-IS config (house law) ──
        print("\n  [D] Year-by-year, best-IS config (required table):")
        bcfg = best10[0][0]
        trs = run_config(days, usable, bcfg)
        print(f"    {cfg_str(bcfg)}")
        for y in ALL_Y:
            yt = [t for t in trs if t["yr"] == y]
            if yt:
                s = tstats(yt)
                print(f"      {y}{' (thru 6/12)' if y == 2026 else '':<13} "
                      f"N={s['n']:>3}  WR={s['wr']:>5.1f}%  PF={s['pf']:>5.2f}  "
                      f"net=${s['net']:>+9,.0f}  avg=${s['avg']:>+6.0f}")
        mdd, worst = max_dd_worst(trs)
        sig_rate = len(trs) / len(usable)
        print(f"      maxDD ${mdd:,.0f}  worst day ${worst[0]:,.0f} ({worst[1]})  "
              f"signal rate {sig_rate:.0%} of days")

        # ── audit E: engine spot-trace, one real day ─────────────────────────
        print("\n  [E] Engine spot-trace (verify sim against raw bars):")
        td = trs[len(trs) // 2]
        bars = days[td["d"]]
        w, buf, cut, stop, tm, f = bcfg
        orh, orl = or_levels(bars, w)
        print(f"    {td['d']}  OR[9:00-{fmt_t(OPEN_M+w)}) H={orh:.2f} L={orl:.2f}  "
              f"{'LONG' if td['dirn']==1 else 'SHORT'} entry {fmt_t(td['em'])} "
              f"exit {fmt_t(td['xm'])} ({td['kind']})  pts={td['pts']:+.2f}  "
              f"pnl=${td['pnl']:+,.0f}")
        for m_, o_, h_, l_, c_ in bars:
            if td["em"] - 2 <= m_ <= td["em"] + 1 or td["xm"] - 1 <= m_ <= td["xm"]:
                print(f"      bar {fmt_t(m_)}  O={o_:.2f} H={h_:.2f} L={l_:.2f} C={c_:.2f}")

        print("\n" + "=" * 78)
        print("VERDICT: KILL — no IS-coherent CL ORB configuration exists in a "
              "1008-point\nnative sweep; see audit battery above. Ledger item closed.")
        print("=" * 78)
        sys.exit(0)

    ismap = {cfg: is_s["pf"] for cfg, is_s, _ in ((c, i, o) for c, i, o, _ in rows)}
    s_i = {s: i for i, s in enumerate(STOPS)}
    t_i = {t: i for i, t in enumerate(TMULTS)}

    def nbr_score(cfg):
        w, buf, cut, stop, tm, f = cfg
        vals = []
        for ds in (-1, 0, 1):
            for dt in (-1, 0, 1):
                si, ti = s_i[stop] + ds, t_i[tm] + dt
                if 0 <= si < len(STOPS) and 0 <= ti < len(TMULTS):
                    c2 = (w, buf, cut, STOPS[si], TMULTS[ti], f)
                    if c2 in ismap:
                        vals.append(ismap[c2])
        return sum(vals) / len(vals)

    ranked = sorted(surv, key=lambda x: (nbr_score(x[0]), x[1]["pf"]), reverse=True)
    print(f"\n  Top survivors by NEIGHBORHOOD IS PF (anti-isolated-peak):")
    print(f"  {'config':<52}{'nbrPF':>6}{'IS PF':>7}{'N':>5}{'avg$':>7}")
    for cfg, is_s, oos_s in ranked[:12]:
        print(f"  {cfg_str(cfg):<52}{nbr_score(cfg):>6.2f}{is_s['pf']:>7.2f}"
              f"{is_s['n']:>5}{is_s['avg']:>7.0f}")
    oos_pos = sum(1 for _, _, o in surv if o["net"] > 0)
    print(f"  [post-hoc honesty: {oos_pos}/{len(surv)} survivors are OOS-net-positive]")

    CHOSEN = ranked[0][0]
    print(f"\n  CHOSEN ON IS: {cfg_str(CHOSEN)}")

    print("\n[4/7] Finalist evaluation — full period, year-by-year, diagnostics")
    trs = run_config(days, usable, CHOSEN)
    is_t = [t for t in trs if t["yr"] in IS_Y]
    oos_t = [t for t in trs if t["yr"] in OOS_Y]
    line("IS  2022-2024", tstats(is_t))
    line("OOS 2025-2026", tstats(oos_t))
    print("  year-by-year:")
    for y in ALL_Y:
        yt = [t for t in trs if t["yr"] == y]
        if yt:
            line(f"  {y}" + (" (thru 6/12)" if y == 2026 else ""), tstats(yt))
    mdd, worst = max_dd_worst(trs)
    print(f"  maxDD (daily eq, full) = ${mdd:,.0f}   worst day = ${worst[0]:,.0f} ({worst[1]})")
    for lbl, tt in (("IS", is_t), ("OOS", oos_t)):
        md, wd_ = max_dd_worst(tt)
        print(f"  {lbl} maxDD ${md:,.0f}, worst day ${wd_[0]:,.0f} ({wd_[1]})")
    # monthly concentration
    for lbl, tt in (("IS", is_t), ("OOS", oos_t)):
        bym = defaultdict(float)
        for t in tt:
            bym[t["d"][:7]] += t["pnl"]
        net = sum(bym.values())
        top = max(bym.items(), key=lambda x: x[1])
        posm = sum(1 for v in bym.values() if v > 0)
        print(f"  {lbl} monthly: top {top[0]} ${top[1]:+,.0f} "
              f"({(top[1]/net*100 if net > 0 else float('nan')):.0f}% of net), "
              f"{posm}/{len(bym)} months positive")
    # DOW + direction + exits
    for lbl, tt in (("IS", is_t), ("OOS", oos_t)):
        print(f"  {lbl} DOW: ", end="")
        for wdd, nm in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri")):
            s = tstats([t for t in tt if t["wd"] == wdd])
            print(f"{nm} PF{s['pf']:.2f}/N{s['n']}", end="  ")
        print()
        L = tstats([t for t in tt if t["dirn"] == 1])
        S = tstats([t for t in tt if t["dirn"] == -1])
        k = defaultdict(int)
        for t in tt:
            k[t["kind"]] += 1
        print(f"  {lbl} long PF{L['pf']:.2f}/N{L['n']}  short PF{S['pf']:.2f}/N{S['n']}"
              f"  exits {dict(k)}")
    # OR-size quartile diagnostic (info only, not selection)
    orr = sorted(t["orr"] for t in trs)
    cutq = [orr[len(orr)//4], orr[len(orr)//2], orr[3*len(orr)//4]]
    print(f"  OR-size quartile cuts: {[round(c,3) for c in cutq]}")
    for lbl, tt in (("IS", is_t), ("OOS", oos_t)):
        qs = []
        for lo, hi in [(0, cutq[0]), (cutq[0], cutq[1]), (cutq[1], cutq[2]), (cutq[2], 9e9)]:
            s = tstats([t for t in tt if lo <= t["orr"] < hi])
            qs.append(f"PF{s['pf']:.2f}/N{s['n']}")
        print(f"  {lbl} by OR-size quartile: {'  '.join(qs)}")

    print("\n[5/7] EIA / Wednesday handling (finalist, BOTH eras)")
    for lbl, tt in (("IS", is_t), ("OOS", oos_t)):
        wed = tstats([t for t in tt if t["wd"] == 2])
        non = tstats([t for t in tt if t["wd"] != 2])
        print(f"  {lbl}: Wed-only PF={wed['pf']:.2f} N={wed['n']} avg=${wed['avg']:+.0f} | "
              f"non-Wed PF={non['pf']:.2f} N={non['n']} avg=${non['avg']:+.0f}")
    for mode, nm in (("skip", "skip-Wednesday"), ("after", "Wed-entry>=10:35")):
        tv = run_config(days, usable, CHOSEN, wed_mode=mode)
        iv = tstats([t for t in tv if t["yr"] in IS_Y])
        ov = tstats([t for t in tv if t["yr"] in OOS_Y])
        print(f"  variant {nm:<18} IS PF={iv['pf']:.2f} N={iv['n']} net=${iv['net']:+,.0f} | "
              f"OOS PF={ov['pf']:.2f} N={ov['n']} net=${ov['net']:+,.0f}")

    print("\n[6/7] Roll/gap sensitivity + MCL economics")
    keep = [t for t in trs
            if not (t["d"] in prior_close
                    and abs(next(b for b in days[t["d"]] if b[0] >= OPEN_M)[1]
                            - prior_close[t["d"]]) > 1.0)]
    ik = tstats([t for t in keep if t["yr"] in IS_Y])
    ok = tstats([t for t in keep if t["yr"] in OOS_Y])
    print(f"  drop |session gap|>$1 days ({len(trs)-len(keep)} trades removed): "
          f"IS PF={ik['pf']:.2f}  OOS PF={ok['pf']:.2f}")
    mcl = [dict(t, pnl=t["pts"] * MCL_PT - MCL_EXTRA) for t in trs]
    im = tstats([t for t in mcl if t["yr"] in IS_Y])
    om = tstats([t for t in mcl if t["yr"] in OOS_Y])
    print(f"  MCL 1-lot: IS PF={im['pf']:.2f} avg=${im['avg']:+.2f} | "
          f"OOS PF={om['pf']:.2f} avg=${om['avg']:+.2f}  (per contract)")

    print("\n[7/7] NQ v12 correlation, overlap, risk-room, time-collision")
    nq = {}
    with open(V12_CSV) as f:
        for row in csv.DictReader(f):
            nq[row["date"]] = float(row["pnl"])
    cl_day = defaultdict(float)
    for t in trs:
        cl_day[t["d"]] += t["pnl"]
    joint = [(nq[d], cl_day[d]) for d in cl_day if d in nq]
    def pearson(pairs):
        if len(pairs) < 30:
            return float("nan")
        xs, ys = zip(*pairs)
        mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
        cov = sum((a-mx)*(b-my) for a, b in pairs)
        vx = (sum((a-mx)**2 for a in xs))**.5
        vy = (sum((b-my)**2 for b in ys))**.5
        return cov/(vx*vy) if vx*vy else 0.0
    union_dates = set(nq) | set(cl_day)
    u = [(nq.get(d, 0.0), cl_day.get(d, 0.0)) for d in union_dates]
    onloss = [cl_day[d] for d in cl_day if d in nq and nq[d] < 0]
    gw = sum(x for x in onloss if x > 0); gl = -sum(x for x in onloss if x <= 0)
    print(f"  daily P&L corr (joint active days, N={len(joint)}): {pearson(joint):+.3f}")
    print(f"  daily P&L corr (union, 0-filled, N={len(u)}): {pearson(u):+.3f}")
    print(f"  CL on NQ-LOSS days: N={len(onloss)}  PF={gw/gl if gl else 99:.2f}  "
          f"avg=${(sum(onloss)/len(onloss)) if onloss else 0:+,.0f}")
    ov_days = sum(1 for d in cl_day if d in nq)
    print(f"  overlap: {ov_days}/{len(cl_day)} CL trade days are NQ-active days "
          f"({ov_days/len(cl_day):.0%}) ~= {ov_days/4.5:.0f}/yr")
    # collision: CL holding interval vs NQ morning envelope (9:46-12:00)
    coll = sum(1 for t in trs if t["em"] <= 720 and t["xm"] >= 586)
    med_e = sorted(t["em"] for t in trs)[len(trs)//2]
    med_x = sorted(t["xm"] for t in trs)[len(trs)//2]
    print(f"  CL holding window: median {fmt_t(med_e)} -> {fmt_t(med_x)}; "
          f"{coll/len(trs):.0%} of CL trades overlap the NQ morning-ORB envelope (9:46-12:00)")
    print(f"  => simultaneous-position days ~= {ov_days/4.5*coll/len(trs):.0f}/yr "
          f"(needs own account or shared-DLL design — NOT solved here)")
    w_, buf_, cut_, stop_, tm_, f_ = CHOSEN
    theo = stop_ * PT + 10 + COMM
    worst_tr = min(t["pnl"] for t in trs)
    print(f"  risk room: theoretical worst loss = ${theo:,.0f}  "
          f"(stop {stop_:.2f} + 1-tick slip + comm); realized worst trade = ${worst_tr:,.0f}")
    print(f"  vs NQ per-trade risk $415-565 and DLL $1,200 (standalone) / $900 (stacked):")
    print(f"    NQ ORB loss $565 + CL loss ${theo:,.0f} = ${565+theo:,.0f} "
          f"{'> $1,200 DLL BREACH' if 565+theo > 1200 else '<= $1,200 ok standalone-DLL'}"
          f"{' and > $900 stacked limit' if 565+theo > 900 else ''}")
    n_mcl = int((1200 - 565) // (theo / 10))
    print(f"    MCL alternative: worst loss ~${theo/10:,.0f}/contract -> "
          f"{n_mcl} MCL fit next to a worst-case NQ day under $1,200")

    print("\n" + "=" * 78)
    o = tstats(oos_t); i = tstats(is_t)
    y25 = tstats([t for t in trs if t["yr"] == 2025])
    ok_edge = (o["pf"] >= 1.05 and o["net"] > 0 and y25["net"] > 0 and i["pf"] >= MIN_PF_IS)
    print(f"VERDICT: {'DEPLOY-CANDIDATE' if ok_edge else 'KILL'} — {cfg_str(CHOSEN)}")
    print(f"  IS PF {i['pf']:.2f} (N={i['n']}, ${i['net']:+,.0f})  "
          f"OOS PF {o['pf']:.2f} (N={o['n']}, ${o['net']:+,.0f})  "
          f"expectancy ${tstats(trs)['avg']:+,.0f}/trade after costs")
    print("=" * 78)
