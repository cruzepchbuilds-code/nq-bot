"""
brain/research/noise_mc.py

EXECUTION-NOISE MONTE CARLO on the composed v12 stack (ORB3 + REJ + PM + ASIA,
1 contract, NQ $5/tick $20/pt, stream already carries ~$14.50/trade of
commission+slip). 10,000 paths per scenario, IS 2022-2024 / OOS 2025-2026.

WHAT IT DOES
  1. Rebuilds the per-day component trades via _eb_snap3.build_components()
     (shell snapshot of eval_boost.py -- the live file may be mid-edit).
  2. Replicates empire_rulemap.build_seq() day-composition rules VERBATIM:
       - REJ skipped if morning ORB traded
       - PM skipped if morning ORB traded and morning P&L < 0
       - one open position at a time (entry < open_until -> skip)
       - internal daily halt: no new entry once day P&L <= -$500
       - risk-headroom gate: no entry if (1150 + dayPnL) < RISK1[strat]
     Zero-noise composition is verified 1:1 against data/v12_daily_stream.csv.
     NOTE (documented modeling choice): gates key off EXECUTED trades, not
     signals -- if the morning ORB fill is MISSED, the bot state says "no
     morning trade", so REJ/PM unlock. On the clean stream this is identical
     to build_seq (the first ORB of a day always executes).
  3. Scenarios (noise applied per trade BEFORE composition, so gate flips,
     DLL hits and position-overlap effects propagate):
       SLIPPAGE  base:   per SIDE extra adverse ticks {0,1,2} w.p. {.50,.35,.15}
                         (mean 0.65t/side = 1.30t/trade = $6.50/trade)
                 stress: per SIDE {0,1,2,3,4} w.p. {.30,.30,.20,.12,.08}
                         (mean 1.38t/side = 2.76t/trade = $13.80/trade)
                 Applied to ALL exits incl. limit targets (conservative).
       MISSED    each would-be-executed trade skipped w.p. p in {5,10,15}%
                 (no P&L, no state change -- models disconnects/freezes).
                 ADVERSE variant: only clean-winners can be missed (worst-case
                 fill selection: you only miss the good fills).
       LATENCY   deterministic alternative stream: every entry delayed one
                 1-min bar, entry price = NEXT bar close, original stop/target
                 DISTANCES re-anchored to the delayed entry, re-outcomed
                 bar-by-bar. Exact bar-level replay for ALL four legs:
                   ORB : delayed fill = next close +/- engine slip (0.5pt),
                         stop 27pt / target 81pt (config), engine exit order
                         stop -> target -> 15:55 flatten, $5 commission.
                   REJ/PM/ASIA: the exact portfolio_policy day machines with
                         the fill moved one bar later (distances re-anchored).
                 Approximation + bias: bar-granularity replay resolves a bar
                 that spans both stop and target as STOP-FIRST (pessimistic,
                 same convention as the engine); entry at next bar CLOSE gives
                 breakout entries a full minute of adverse drift, so this
                 measures a HARSH 1-minute freeze, not a 1-2s latency.
       COMBINED  latency stream + base slippage + 5% missed, 10,000 paths.
  4. Break-even: systematic extra ticks/trade (every trade) at which the
     FULL-PERIOD trade-level PF hits 1.00 (bisection; gates re-evaluated).
  5. Fragility: per leg, fraction of per-trade expectancy lost per 1 tick/trade
     of systematic slippage on that leg only (composed, so interactions count).

DETERMINISM: every random draw comes from numpy default_rng with fixed
per-scenario seeds (base seed 20260703). No unseeded randomness anywhere.

RUN (from repo root; first run builds a ~15 min cache, later runs ~1 min):
    python3 brain/research/noise_mc.py                 # full 10,000 paths
    python3 brain/research/noise_mc.py --paths 2000    # quick pass
    python3 brain/research/noise_mc.py --rebuild       # force cache rebuild
Cache: $NOISE_MC_CACHE or the session scratchpad (safe to delete).

NOTE (2026-07-03): upstream fix — portfolio_policy.run_year_morning's
regime-ATR window is now a bounded 14-day deque (was unbounded list ->
expanding mean). This script inherits the fix via import; results recorded
BEFORE this date used the buggy morning stream (composed v12 stream deltas:
710 -> 713 trading days, full-period net -0.08%, OOS 2025-26 net +2.4%) —
re-run before citing absolute numbers. Its pickle cache is also pre-fix,
but the 1:1 assert vs the regenerated data/v12_daily_stream.csv will
catch it — use --rebuild on first re-run.
"""

import sys, os, csv, math, pickle, time as clock
from datetime import datetime, date, time as dtime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
os.chdir(REPO)  # portfolio_policy uses repo-relative data paths

import numpy as np

# ── constants (RISK1 copied verbatim from empire_rulemap.py; do not import
#    empire_rulemap -- it imports the possibly-mid-edit eval_boost.py) ────────
TICK   = 5.0                      # $ per tick, 1 contract NQ
PT     = 20.0                     # $ per point
COST   = 14.50                    # already inside the stream's P&L
LEGS   = ("ORB", "REJ", "PM", "ASIA")
SID    = {"ORB": 0, "REJ": 1, "PM": 2, "ASIA": 3}
RISK1V = np.array([565.0, 415.0, 455.0, 515.0])   # empire_rulemap.RISK1
DLL    = 500.0
HEADRM = 1150.0
SEED   = 20260703
PATHS  = 10000

SCRATCH = ("/private/tmp/claude-502/-Users-Cruz-Desktop-nq-bot-final-main/"
           "3c7889f3-5f4d-48b1-9594-81405ad58030/scratchpad")
CACHE = os.environ.get("NOISE_MC_CACHE",
                       os.path.join(SCRATCH, "noise_mc_cache_v1.pkl"))

T0 = clock.time()
def log(msg):
    print(f"[{clock.time()-T0:7.1f}s] {msg}", flush=True)

def mmin(t):            # datetime.time -> float minutes
    return t.hour * 60.0 + t.minute

# ═════════════════════════════════════════════════════════════════════════════
#  Bar-level replays.  delay=0 must reproduce portfolio_policy day functions
#  EXACTLY (asserted at build time); delay=1 moves the fill one bar later.
#  Return: (e_t, x_t, pnl, is_long, filled) | None (no signal).
#  filled=False -> signal fired but no fill bar existed (latency-missed).
# ═════════════════════════════════════════════════════════════════════════════

def rejection_replay(bars, delay):
    """stop 20 / 3R(60) / ext 25 / arm >=11:00 / flat 13:00."""
    sum_pv = sum_vol = 0.0
    vwap = None
    was_ext = saw = False
    rec_up = prev_above = None
    entry = sl = tp = e_t = None
    is_long = None
    pend = None
    for i, b in enumerate(bars):
        t = b["t"]
        if t < dtime(9, 30) or t >= dtime(16, 0):
            continue
        sum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        sum_vol += b["v"]
        vwap = sum_pv / sum_vol if sum_vol else None
        if vwap is None:
            continue
        close = b["c"]; above = close > vwap
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (e_t, t, (sl - entry) * PT - COST, is_long, True)
                if b["h"] >= tp: return (e_t, t, (tp - entry) * PT - COST, is_long, True)
            else:
                if b["h"] >= sl: return (e_t, t, (entry - sl) * PT - COST, is_long, True)
                if b["l"] <= tp: return (e_t, t, (entry - tp) * PT - COST, is_long, True)
            if t >= dtime(13, 0):
                pts = (close - entry) if is_long else (entry - close)
                return (e_t, t, pts * PT - COST, is_long, True)
            prev_above = above
            continue
        if pend is not None:
            if i == pend[0]:                       # delayed fill at this close
                is_long = pend[1]
                entry, e_t = close, t
                sl = entry - 20.0 if is_long else entry + 20.0
                tp = entry + 60.0 if is_long else entry - 60.0
            prev_above = above
            continue
        if t < dtime(10, 0):
            prev_above = above
            continue
        if not was_ext and abs(close - vwap) > 25.0:
            was_ext = True
        if was_ext and prev_above is not None and dtime(11, 0) <= t < dtime(13, 0):
            cu = (not prev_above) and above
            cd = prev_above and (not above)
            if not saw:
                if cu:   saw, rec_up = True, True
                elif cd: saw, rec_up = True, False
            else:
                sig = None
                if rec_up and cd:            sig = False
                elif (not rec_up) and cu:    sig = True
                if sig is not None:
                    if delay == 0:
                        entry, is_long, e_t = close, sig, t
                        sl = entry - 20.0 if sig else entry + 20.0
                        tp = entry + 60.0 if sig else entry - 60.0
                    else:
                        pend = (i + 1, sig)
        prev_above = above
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, bars[-1]["t"], pts * PT - COST, is_long, True)
    if pend is not None:
        return (None, None, 0.0, None, False)
    return None


def pm_replay(bars, delay):
    """PM ORB: OR 13:00-13:14 (15-60pt), entry 13:15-14:00, stop 22 / 2.5R(55), flat 15:55."""
    or_hi = or_lo = None
    or_done = False
    entry = sl = tp = e_t = None
    is_long = None
    pend = None
    for i, b in enumerate(bars):
        t = b["t"]
        if t < dtime(13, 0):
            continue
        if t >= dtime(15, 55):
            break
        if t < dtime(13, 15):
            or_hi = b["h"] if or_hi is None else max(or_hi, b["h"])
            or_lo = b["l"] if or_lo is None else min(or_lo, b["l"])
            continue
        if not or_done:
            or_done = True
            if or_hi is None or not (15 <= or_hi - or_lo <= 60):
                return None
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (e_t, t, (sl - entry) * PT - COST, is_long, True)
                if b["h"] >= tp: return (e_t, t, (tp - entry) * PT - COST, is_long, True)
            else:
                if b["h"] >= sl: return (e_t, t, (entry - sl) * PT - COST, is_long, True)
                if b["l"] <= tp: return (e_t, t, (entry - tp) * PT - COST, is_long, True)
            continue
        if pend is not None:
            if i == pend[0]:
                is_long = pend[1]
                entry, e_t = b["c"], t
                sl = entry - 22.0 if is_long else entry + 22.0
                tp = entry + 55.0 if is_long else entry - 55.0
            continue
        if t > dtime(14, 0):
            continue
        if b["c"] > or_hi + 2:
            if delay == 0:
                entry, is_long, e_t = b["c"], True, t
                sl, tp = entry - 22.0, entry + 55.0
            else:
                pend = (i + 1, True)
        elif b["c"] < or_lo - 2:
            if delay == 0:
                entry, is_long, e_t = b["c"], False, t
                sl, tp = entry + 22.0, entry - 55.0
            else:
                pend = (i + 1, False)
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, dtime(15, 55), pts * PT - COST, is_long, True)
    if pend is not None:
        return (None, None, 0.0, None, False)
    return None


def asia_replay(bars, delay):
    """Asia gap: ref close pre-17:00, 18:15 bar, gap 30-80, stop 25 / 3R(75), flat end."""
    cme = None
    entry = sl = tp = e_t = None
    is_long = None
    pend = None
    for i, b in enumerate(bars):
        t = b["t"]
        if t < dtime(17, 0):
            cme = b["c"]
            continue
        if t < dtime(18, 15):
            continue
        if pend is not None and entry is None:
            if i == pend[0]:
                is_long = pend[1]
                entry, e_t = b["c"], t
                sl = entry - 25 if is_long else entry + 25
                tp = entry + 75 if is_long else entry - 75
            continue
        if entry is None:
            if t >= dtime(18, 16) or cme is None:
                return None
            gap = b["c"] - cme
            if not (30 <= abs(gap) <= 80):
                return None
            if delay == 0:
                entry, is_long, e_t = b["c"], gap > 0, t
                sl = entry - 25 if is_long else entry + 25
                tp = entry + 75 if is_long else entry - 75
            else:
                pend = (i + 1, gap > 0)
            continue
        if t >= dtime(21, 0):
            pts = (b["c"] - entry) if is_long else (entry - b["c"])
            return (e_t, t, pts * PT - COST, is_long, True)
        if is_long:
            if b["l"] <= sl: return (e_t, t, (sl - entry) * PT - COST, is_long, True)
            if b["h"] >= tp: return (e_t, t, (tp - entry) * PT - COST, is_long, True)
        else:
            if b["h"] >= sl: return (e_t, t, (entry - sl) * PT - COST, is_long, True)
            if b["l"] <= tp: return (e_t, t, (entry - tp) * PT - COST, is_long, True)
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, bars[-1]["t"], pts * PT - COST, is_long, True)
    if pend is not None:
        return (None, None, 0.0, None, False)
    return None


def orb_replay_delayed(bars, i0, is_long, stop_d, tgt_d, slip_pt, comm_rt):
    """Engine-mirror delayed ORB: fill = close(bars[i0+1]) +/- slip, stop/target
    distances re-anchored to that close, exit order stop -> target -> 15:55
    flatten at close (engine convention: stop checked first = pessimistic on
    ambiguous bars). Returns (e_t, x_t, pnl_1c, filled)."""
    if i0 + 1 >= len(bars):
        return (None, None, 0.0, False)
    fb = bars[i0 + 1]
    sig = fb["c"]
    if is_long:
        fill, stop, tgt = sig + slip_pt, sig - stop_d, sig + tgt_d
    else:
        fill, stop, tgt = sig - slip_pt, sig + stop_d, sig - tgt_d
    e_t = fb["t"]
    exit_p = x_t = None
    last = fb
    j = i0 + 2
    while j < len(bars):
        b = bars[j]; t = b["t"]; last = b
        if is_long:
            if b["l"] <= stop:   exit_p, x_t = stop - slip_pt, t; break
            if b["h"] >= tgt:    exit_p, x_t = tgt, t; break
        else:
            if b["h"] >= stop:   exit_p, x_t = stop + slip_pt, t; break
            if b["l"] <= tgt:    exit_p, x_t = tgt, t; break
        if t >= dtime(15, 55):
            exit_p, x_t = b["c"], t; break
        j += 1
    if exit_p is None:                       # half-day / data-end fallback
        exit_p, x_t = last["c"], last["t"]
    pts = (exit_p - fill) if is_long else (fill - exit_p)
    return (e_t, x_t, pts * PT - comm_rt, True)


# ═════════════════════════════════════════════════════════════════════════════
#  Cache build: components via the eval_boost snapshot + one instrumented
#  morning run (for ORB direction/entry-bar), verification, latency replay.
# ═════════════════════════════════════════════════════════════════════════════

def build_cache():
    import config
    config.TELEGRAM_ALERTS_ENABLED = False          # belt and braces: no I/O
    log("importing snapshot _eb_snap3 (copy of eval_boost.py) ...")
    import _eb_snap3 as eb
    import portfolio_policy as pp                   # read-only import
    from vwap_fulldata import load_days as vwap_load

    log("build_components() via snapshot -- two engine configs, ~8 min ...")
    comp = eb.build_components()
    config.TELEGRAM_ALERTS_ENABLED = False
    for k in ("ORB3", "REJ", "PM", "ASIA"):
        n = sum(len(v) for v in comp[k].values())
        log(f"  comp[{k}]: {n} trades on {len(comp[k])} days")

    # ── instrumented morning run: full trade dicts (dir, entry bar) ─────────
    log("extra morning engine pass for ORB direction/entry-bar metadata ...")
    config.EVAL_MODE = False                        # ORB3 = funded 3R config
    from backtest import load_csv
    bars_full = load_csv(pp.DATA)
    morning = []
    for y in pp.YEARS:
        morning.extend(pp.run_year_morning(bars_full, y))
        log(f"  morning {y} done (cum {len(morning)} trades)")
    del bars_full
    config.EVAL_MODE = False

    # transform exactly as eval_boost does and verify vs comp["ORB3"]
    orb_meta = defaultdict(list)      # d -> [(e_t, x_t, pnl_1c, is_long)]
    for t in morning:
        d = date.fromisoformat(t["date"])
        e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
        x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
        c = max(1, t.get("contracts", 1))
        orb_meta[d].append((e, x, t["pnl"] / c, t["dir"] == "long"))
    mism = 0
    for d in set(comp["ORB3"]) | set(orb_meta):
        a = sorted((mmin(e), mmin(x), round(p, 4)) for _, e, x, p in comp["ORB3"].get(d, []))
        b = sorted((mmin(e), mmin(x), round(p, 4)) for e, x, p, _ in orb_meta.get(d, []))
        if a != b:
            mism += 1
    log(f"  ORB metadata vs comp[ORB3]: {mism} day mismatches "
        f"({'PASS' if mism == 0 else 'FAIL'})")
    assert mism == 0, "instrumented morning run does not match snapshot ORB3"

    # ── bars for standalone replays ─────────────────────────────────────────
    log("loading RTH (9-16h) and evening (16-21h) day bars ...")
    rth = vwap_load(pp.DATA)
    eve = pp.load_days(pp.DATA, 16, 21)

    # verify delay=0 transcriptions reproduce comp exactly (bijection on days)
    for key, src, fn in (("REJ", rth, rejection_replay),
                         ("PM", rth, pm_replay),
                         ("ASIA", eve, asia_replay)):
        bad = extra = 0
        days = comp[key]
        for d, lst in days.items():
            r = fn(src[d], 0)
            if r is None or not r[4]:
                bad += 1; continue
            _, e, x, p = lst[0]
            if not (r[0] == e and r[1] == x and abs(r[2] - p) < 1e-6):
                bad += 1
        # days where replay fires but comp has nothing (filters equal -> none)
        cal = rth if src is rth else eve
        for d in cal:
            if d in days:
                continue
            wd, mo = d.weekday(), d.month
            if key == "REJ"  and (wd == 0 or mo in (4, 5, 6, 9, 12)): continue
            if key == "PM"   and wd in (0, 4): continue
            if key == "ASIA" and (wd == 3 or mo in (8, 11)): continue
            if fn(cal[d], 0) is not None:
                extra += 1
        log(f"  {key} replay(delay=0) vs comp: {bad} mismatches, "
            f"{extra} spurious ({'PASS' if bad == 0 and extra == 0 else 'FAIL'})")
        assert bad == 0 and extra == 0, f"{key} transcription broken"

    # ── latency stream ───────────────────────────────────────────────────────
    stop_d  = config.ORB_FIXED_STOP_POINTS + config.ORB_STOP_BUFFER_POINTS
    tgt_d   = stop_d * config.ORB_FUNDED_RR_TARGET
    slip_pt = config.SLIPPAGE_TICKS * config.TICK_SIZE
    comm_rt = config.COMMISSION_PER_SIDE * 2
    log(f"latency replay (ORB stop {stop_d}pt / target {tgt_d}pt, engine slip "
        f"{slip_pt}pt, comm ${comm_rt}/RT) ...")

    # minute index for ORB entry-bar lookup within RTH day bars
    rth_idx = {d: {mmin(b["t"]): i for i, b in enumerate(v)} for d, v in rth.items()}

    def orb_lat(d, e_t, is_long):
        """Locate entry bar on trade date (engine 'date' = exit date; fall back
        1-3 calendar days for the rare overnight-flatten artifact)."""
        for dd in (d, date.fromordinal(d.toordinal() - 1),
                   date.fromordinal(d.toordinal() - 2),
                   date.fromordinal(d.toordinal() - 3)):
            if dd in rth_idx and mmin(e_t) in rth_idx[dd]:
                return orb_replay_delayed(rth[dd], rth_idx[dd][mmin(e_t)],
                                          is_long, stop_d, tgt_d, slip_pt, comm_rt)
        return None                               # entry bar not found

    days_all = sorted(set(comp["ORB3"]) | set(comp["REJ"])
                      | set(comp["PM"]) | set(comp["ASIA"]))
    clean, lat = {}, {}
    diag = defaultdict(int)
    for d in days_all:
        cl, lt = [], []
        # build in the same order as empire_rulemap.build_seq, then stable-sort
        for key in ("ORB3", "REJ", "PM", "ASIA"):
            leg = "ORB" if key == "ORB3" else key
            sid = SID[leg]
            for j, tup in enumerate(comp[key].get(d, [])):
                _, e, x, p = tup
                cl.append((sid, mmin(e), mmin(x), float(p)))
                if leg == "ORB":
                    r = orb_lat(d, e, orb_meta[d][j][3]) if j < len(orb_meta[d]) else None
                    if r is None:
                        lt.append((sid, mmin(e), mmin(x), float(p)))
                        diag["orb_unreplayed"] += 1
                    elif not r[3]:
                        diag["orb_nofill"] += 1
                    else:
                        lt.append((sid, mmin(r[0]), mmin(r[1]), float(r[2])))
                        diag["orb_replayed"] += 1
                else:
                    fn = {"REJ": rejection_replay, "PM": pm_replay,
                          "ASIA": asia_replay}[leg]
                    src = eve[d] if leg == "ASIA" else rth[d]
                    r = fn(src, 1)
                    if r is None:
                        # signal existed clean but not in delayed machine: keep
                        # clean (cannot happen -- signal logic unchanged)
                        lt.append((sid, mmin(e), mmin(x), float(p)))
                        diag[f"{leg.lower()}_nosig"] += 1
                    elif not r[4]:
                        diag[f"{leg.lower()}_nofill"] += 1
                    else:
                        lt.append((sid, mmin(r[0]), mmin(r[1]), float(r[2])))
                        diag[f"{leg.lower()}_replayed"] += 1
        cl.sort(key=lambda r: r[1])               # stable, same as build_seq
        lt.sort(key=lambda r: r[1])
        clean[d], lat[d] = cl, lt
    log(f"  latency diag: {dict(diag)}")

    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump({"days": days_all, "clean": clean, "lat": lat,
                     "diag": dict(diag),
                     "cfg": {"stop_d": stop_d, "tgt_d": tgt_d}}, f)
    log(f"cache written: {CACHE}")
    return days_all, clean, lat, dict(diag)


def load_cache(rebuild=False):
    if not rebuild and os.path.exists(CACHE):
        log(f"loading cache {CACHE}")
        with open(CACHE, "rb") as f:
            c = pickle.load(f)
        return c["days"], c["clean"], c["lat"], c["diag"]
    return build_cache()


# ═════════════════════════════════════════════════════════════════════════════
#  Composition: scalar (exact, for baseline / break-even / fragility) and
#  vectorized-across-paths (for the Monte Carlo).
# ═════════════════════════════════════════════════════════════════════════════

def compose_scalar(days, recs, sys_ticks=0.0, leg_ticks=None):
    """empire_rulemap.build_seq day rules, executed-based gating.
    Returns ([(date, day_pnl)], [(date, sid, eff_pnl)])."""
    daily, taken = [], []
    lt = leg_ticks or {}
    for d in days:
        pnl_day = morning = 0.0
        orb_exec = False
        open_until = -1.0
        for s, e, x, p in recs[d]:
            if s == 1 and orb_exec:                       continue
            if s == 2 and orb_exec and morning < 0:       continue
            if e < open_until:                            continue
            if pnl_day <= -DLL:                           continue
            if HEADRM + pnl_day < RISK1V[s]:              continue
            eff = p - (sys_ticks + lt.get(s, 0.0)) * TICK
            pnl_day += eff
            if s == 0:
                morning += eff
                orb_exec = True
            open_until = x
            taken.append((d, s, eff))
        daily.append((d, pnl_day))
    return daily, taken


def flatten(days, recs):
    """Flat arrays + per-day slices for the vector composer."""
    sid, e, x, p, day_ix = [], [], [], [], []
    starts = [0]
    for i, d in enumerate(days):
        for r in recs[d]:
            sid.append(r[0]); e.append(r[1]); x.append(r[2]); p.append(r[3])
            day_ix.append(i)
        starts.append(len(sid))
    years = np.array([d.year for d in days])
    return (np.array(sid), np.array(e), np.array(x), np.array(p),
            np.array(starts), years)


def draw_side_ticks(rng, P, kind):
    u = rng.random(P)
    if kind == "base":     # 0/1/2 w.p. .50/.35/.15
        return (u > 0.50).astype(np.float64) + (u > 0.85)
    if kind == "stress":   # 0..4 w.p. .30/.30/.20/.12/.08
        return ((u > 0.30).astype(np.float64) + (u > 0.60)
                + (u > 0.80) + (u > 0.92))
    raise ValueError(kind)


def mc_run(days, recs, P, seed, slip=None, miss_p=0.0, winners_only=False):
    """Vectorized composition across P paths. Returns metrics dict."""
    sid, e, x, p, starts, years = flatten(days, recs)
    nd = len(days)
    rng = np.random.default_rng(seed)

    ymap = {y: i for i, y in enumerate(sorted(set(years)))}
    ycol = np.array([ymap[y] for y in years])
    ny = len(ymap)
    is_day = years <= 2024

    ynet = np.zeros((P, ny))
    pos = np.zeros((P, 3)); neg = np.zeros((P, 3)); cnt = np.zeros((P, 3))
    eq = np.zeros((P, 3)); pk = np.zeros((P, 3)); dd = np.zeros((P, 3))
    dsum = np.zeros(P); dsq = np.zeros(P)

    for di in range(nd):
        a, b = starts[di], starts[di + 1]
        if a == b:
            continue
        pnl_day = np.zeros(P)
        morning = np.zeros(P)
        orb_exec = np.zeros(P, dtype=bool)
        open_until = np.full(P, -1.0)
        per = 1 if is_day[di] else 2
        for k in range(a, b):
            s = int(sid[k])
            would = (e[k] >= open_until) & (pnl_day > -DLL) \
                    & (HEADRM + pnl_day >= RISK1V[s])
            if s == 1:
                would &= ~orb_exec
            elif s == 2:
                would &= ~(orb_exec & (morning < 0.0))
            eff = p[k]
            if slip is not None:
                tks = draw_side_ticks(rng, P, slip) + draw_side_ticks(rng, P, slip)
                eff = eff - tks * TICK
            if miss_p > 0.0:
                m = rng.random(P) < miss_p
                if winners_only:
                    m &= (p[k] > 0)
                taken = would & ~m
            else:
                taken = would
            contrib = np.where(taken, eff, 0.0)
            pnl_day += contrib
            if s == 0:
                morning += contrib
                orb_exec |= taken
            open_until = np.where(taken, x[k], open_until)
            gain = np.where(contrib > 0, contrib, 0.0)
            loss = np.where(contrib < 0, -contrib, 0.0)
            for pp_ in (0, per):
                pos[:, pp_] += gain; neg[:, pp_] += loss; cnt[:, pp_] += taken
        ynet[:, ycol[di]] += pnl_day
        for pp_ in (0, per):
            eq[:, pp_] += pnl_day
            np.maximum(pk[:, pp_], eq[:, pp_], out=pk[:, pp_])
            np.maximum(dd[:, pp_], pk[:, pp_] - eq[:, pp_], out=dd[:, pp_])
        dsum += pnl_day; dsq += pnl_day ** 2

    ycols = sorted(ymap)
    is_cols = [ymap[y] for y in ycols if y <= 2024]
    oos_cols = [ymap[y] for y in ycols if y >= 2025]
    net = {"full": ynet.sum(1), "is": ynet[:, is_cols].sum(1),
           "oos": ynet[:, oos_cols].sum(1)}
    pf = {}
    for i, k in enumerate(("full", "is", "oos")):
        pf[k] = np.where(neg[:, i] > 0, pos[:, i] / np.maximum(neg[:, i], 1e-9), 99.0)
    pyr = {"full": ynet > 0,
           "is": ynet[:, is_cols] > 0, "oos": ynet[:, oos_cols] > 0}
    dvar = dsq / nd - (dsum / nd) ** 2
    return {"net": net, "pf": pf, "dd": {"full": dd[:, 0], "is": dd[:, 1],
                                         "oos": dd[:, 2]},
            "pyr": {k: float(v.mean()) for k, v in pyr.items()},
            "cnt": {"full": cnt[:, 0], "is": cnt[:, 1], "oos": cnt[:, 2]},
            "dstd": np.sqrt(np.maximum(dvar, 0.0))}


# ═════════════════════════════════════════════════════════════════════════════
#  Reporting helpers
# ═════════════════════════════════════════════════════════════════════════════

def stream_metrics(daily, taken):
    """Deterministic metrics from a scalar composition."""
    out = {}
    for k, sel in (("full", lambda y: True), ("is", lambda y: y <= 2024),
                   ("oos", lambda y: y >= 2025)):
        dsel = [(d, v) for d, v in daily if sel(d.year)]
        tsel = [t for t in taken if sel(t[0].year)]
        net = sum(v for _, v in dsel)
        gw = sum(x[2] for x in tsel if x[2] > 0)
        gl = sum(-x[2] for x in tsel if x[2] < 0)
        eqv = pkv = ddv = 0.0
        for _, v in dsel:
            eqv += v; pkv = max(pkv, eqv); ddv = max(ddv, pkv - eqv)
        yrs = ((dsel[-1][0] - dsel[0][0]).days / 365.25) if len(dsel) > 1 else 1.0
        out[k] = {"net": net, "pf": (gw / gl if gl > 0 else 99.0),
                  "dd": ddv, "yrs": yrs, "ann": net / yrs, "n": len(tsel),
                  "gw": gw, "gl": gl}
    return out


def pcts(a):
    return np.percentile(a, [5, 50, 95])


def row(label, m, per, yrs, note=""):
    ann = m["net"][per] / yrs
    a5, a50, a95 = pcts(ann)
    f5, f50, f95 = pcts(m["pf"][per])
    d50, d95 = np.percentile(m["dd"][per], [50, 95])
    dmax = m["dd"][per].max()
    ppf = float((m["pf"][per] > 1.2).mean())
    pyr = float(m["pyr"][per].mean()) if hasattr(m["pyr"][per], "mean") else m["pyr"][per]
    print(f"  {label:<26} {a5:>8,.0f} {a50:>8,.0f} {a95:>8,.0f} |"
          f" {f5:>5.2f} {f50:>5.2f} {f95:>5.2f} | {pyr:>6.1%} {ppf:>7.1%} |"
          f" {d50:>7,.0f} {d95:>7,.0f} {dmax:>7,.0f}{note}")


# ═════════════════════════════════════════════════════════════════════════════
#  Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    global PATHS
    rebuild = "--rebuild" in sys.argv
    if "--paths" in sys.argv:
        PATHS = int(sys.argv[sys.argv.index("--paths") + 1])
    days, clean, lat, diag = load_cache(rebuild)

    # ── clean baseline + verification vs the cached v12 stream ──────────────
    daily0, taken0 = compose_scalar(days, clean)
    v12p = os.path.join(REPO, "data", "v12_daily_stream.csv")
    if os.path.exists(v12p):
        ref = {}
        with open(v12p) as f:
            for r in csv.DictReader(f):
                ref[date.fromisoformat(r["date"])] = float(r["pnl"])
        mine = {d: v for d, v in daily0 if abs(v) > 1e-9}
        common = set(mine) & set(ref)
        diffs = [abs(mine[d] - ref[d]) for d in common]
        log(f"verify vs v12_daily_stream.csv: mine {len(mine)} days / csv "
            f"{len(ref)} days, common {len(common)}, max|diff| "
            f"{max(diffs) if diffs else 0:.4f}, extra-mine "
            f"{len(set(mine)-set(ref))}, extra-csv {len(set(ref)-set(mine))}")
        ok = (len(common) == len(ref) == len(mine)
              and (not diffs or max(diffs) < 0.01))
        log(f"  composition replication: {'PASS' if ok else 'FAIL'}")
        assert ok, "composed stream does not reproduce data/v12_daily_stream.csv"

    # cross-check vectorized composer == scalar at zero noise
    m0 = mc_run(days, clean, 2, SEED, slip=None, miss_p=0.0)
    bm = stream_metrics(daily0, taken0)
    assert abs(m0["net"]["full"][0] - bm["full"]["net"]) < 0.01, "vector != scalar"
    log("vector composer == scalar composer at zero noise: PASS")

    yrs = {k: bm[k]["yrs"] for k in bm}
    print()
    print("=" * 100)
    print("  CLEAN BASELINE (composed v12 stack, 1c, executed-based gating)")
    print("=" * 100)
    for k, lab in (("full", "FULL 2022-2026"), ("is", "IS 2022-2024"),
                   ("oos", "OOS 2025-2026")):
        b = bm[k]
        print(f"  {lab:<15} net ${b['net']:>+10,.0f}  ann ${b['ann']:>+9,.0f}"
              f"  PF {b['pf']:.3f}  trades {b['n']:>4}  maxDD ${b['dd']:>8,.0f}"
              f"  ({b['yrs']:.2f} yrs)")
    ynets = defaultdict(float)
    for d, v in daily0:
        ynets[d.year] += v
    print("  per calendar year: "
          + "  ".join(f"{y}: {v:+,.0f}" for y, v in sorted(ynets.items())))
    base_pyr = {}
    for k, sel in (("full", lambda y: True), ("is", lambda y: y <= 2024),
                   ("oos", lambda y: y >= 2025)):
        ys = [v for y, v in ynets.items() if sel(y)]
        base_pyr[k] = sum(1 for v in ys if v > 0) / len(ys)

    # ── scenarios ────────────────────────────────────────────────────────────
    log(f"running Monte Carlo scenarios ({PATHS} paths each) ...")
    scen = []
    scen.append(("slip base (E$6.5/t)", dict(slip="base"), 1))
    scen.append(("slip stress (E$13.8/t)", dict(slip="stress"), 2))
    scen.append(("miss 5%", dict(miss_p=0.05), 3))
    scen.append(("miss 10%", dict(miss_p=0.10), 4))
    scen.append(("miss 15%", dict(miss_p=0.15), 5))
    scen.append(("miss 5% winners-only", dict(miss_p=0.05, winners_only=True), 6))
    scen.append(("miss 10% winners-only", dict(miss_p=0.10, winners_only=True), 7))
    scen.append(("miss 15% winners-only", dict(miss_p=0.15, winners_only=True), 8))
    results = {}
    for name, kw, s in scen:
        t = clock.time()
        results[name] = mc_run(days, clean, PATHS, SEED * 100 + s, **kw)
        log(f"  {name:<24} done ({clock.time()-t:.1f}s)")

    # latency: deterministic alternative stream
    dailyL, takenL = compose_scalar(days, lat)
    bl = stream_metrics(dailyL, takenL)
    ynetsL = defaultdict(float)
    for d, v in dailyL:
        ynetsL[d.year] += v
    lat_pyr = {}
    for k, sel in (("full", lambda y: True), ("is", lambda y: y <= 2024),
                   ("oos", lambda y: y >= 2025)):
        ys = [v for y, v in ynetsL.items() if sel(y)]
        lat_pyr[k] = sum(1 for v in ys if v > 0) / len(ys)
    # combined realistic: latency stream + base slip + 5% miss
    t = clock.time()
    comb = mc_run(days, lat, PATHS, SEED * 100 + 9, slip="base", miss_p=0.05)
    results["COMBINED lat+slip+5%"] = comb
    log(f"  COMBINED realistic       done ({clock.time()-t:.1f}s)")

    for per, lab in (("full", "FULL 2022-2026"), ("is", "IS 2022-2024"),
                     ("oos", "OOS 2025-2026")):
        print()
        print("=" * 100)
        print(f"  SCENARIO GRID -- {lab} ({yrs[per]:.2f} yrs, {PATHS} paths, "
              f"annualized net $ / trade-level PF / EOD-equity maxDD)")
        print("=" * 100)
        print(f"  {'scenario':<26} {'ann p5':>8} {'ann p50':>8} {'ann p95':>8} |"
              f" {'PF p5':>5} {'p50':>5} {'p95':>5} | {'P(yr>0)':>6} "
              f"{'P(PF>1.2)':>7} | {'DD p50':>7} {'DD p95':>7} {'worst':>7}")
        print("  " + "-" * 96)
        b = bm[per]
        print(f"  {'CLEAN baseline':<26} {b['ann']:>8,.0f} {b['ann']:>8,.0f} "
              f"{b['ann']:>8,.0f} | {b['pf']:>5.2f} {b['pf']:>5.2f} "
              f"{b['pf']:>5.2f} | {base_pyr[per]:>6.1%} "
              f"{1.0 if b['pf'] > 1.2 else 0.0:>7.1%} | {b['dd']:>7,.0f} "
              f"{b['dd']:>7,.0f} {b['dd']:>7,.0f}")
        for name, _, _ in scen:
            row(name, results[name], per, yrs[per])
        bL = bl[per]
        print(f"  {'latency 1-bar (determ.)':<26} {bL['ann']:>8,.0f} "
              f"{bL['ann']:>8,.0f} {bL['ann']:>8,.0f} | {bL['pf']:>5.2f} "
              f"{bL['pf']:>5.2f} {bL['pf']:>5.2f} | {lat_pyr[per]:>6.1%} "
              f"{1.0 if bL['pf'] > 1.2 else 0.0:>7.1%} | {bL['dd']:>7,.0f} "
              f"{bL['dd']:>7,.0f} {bL['dd']:>7,.0f}")
        row("COMBINED lat+slip+5%", comb, per, yrs[per])

    # variance note for the miss asymmetry
    print()
    print("  MISS-SCENARIO VARIANCE (full period, across paths):")
    print(f"  {'scenario':<26} {'std(annNet)':>11} {'med daily std':>13} "
          f"{'med trades':>10}")
    for name in ("slip base (E$6.5/t)", "miss 5%", "miss 10%", "miss 15%",
                 "miss 5% winners-only", "miss 15% winners-only"):
        m = results[name]
        ann = m["net"]["full"] / yrs["full"]
        print(f"  {name:<26} {ann.std():>11,.0f} "
              f"{np.median(m['dstd']):>13,.0f} "
              f"{np.median(m['cnt']['full']):>10,.0f}")
    n_clean = len(taken0)
    dstd0 = np.std([v for _, v in daily0])
    print(f"  {'CLEAN baseline':<26} {0:>11,.0f} {dstd0:>13,.0f} {n_clean:>10,}")

    # ── latency per-leg detail ───────────────────────────────────────────────
    print()
    print("=" * 100)
    print("  LATENCY DETAIL (1-bar delayed entries, deterministic replay)")
    print("=" * 100)
    legnet0 = defaultdict(float); legn0 = defaultdict(int)
    for _, s, v in taken0:
        legnet0[s] += v; legn0[s] += 1
    legnetL = defaultdict(float); legnL = defaultdict(int)
    for _, s, v in takenL:
        legnetL[s] += v; legnL[s] += 1
    for s, leg in enumerate(LEGS):
        print(f"  {leg:<5} clean net ${legnet0[s]:>+10,.0f} ({legn0[s]:>3}t)"
              f"  ->  delayed ${legnetL[s]:>+10,.0f} ({legnL[s]:>3}t)"
              f"   delta ${legnetL[s]-legnet0[s]:>+9,.0f}")
    print(f"  total: clean ${bm['full']['net']:>+10,.0f} -> delayed "
          f"${bl['full']['net']:>+10,.0f}  (delta "
          f"${bl['full']['net']-bm['full']['net']:>+9,.0f}, PF "
          f"{bm['full']['pf']:.3f} -> {bl['full']['pf']:.3f}; IS PF "
          f"{bm['is']['pf']:.3f} -> {bl['is']['pf']:.3f}, OOS PF "
          f"{bm['oos']['pf']:.3f} -> {bl['oos']['pf']:.3f})")
    print(f"  replay diag: {diag}")
    print("  approximation: bar-granularity; ambiguous stop+target bars "
          "resolve STOP-FIRST (pessimistic); entry = next 1-min bar close "
          "(a full minute of drift -- harsher than seconds-level latency).")

    # ── fragility ranking (systematic +1 tick/trade on ONE leg at a time) ───
    print()
    print("=" * 100)
    print("  FRAGILITY -- expectancy lost per +1 tick/trade ($5) of systematic"
          " slippage, per leg (composed)")
    print("=" * 100)
    frag = []
    for s, leg in enumerate(LEGS):
        _, tk = compose_scalar(days, clean, leg_ticks={s: 1.0})
        net1 = sum(v for _, ss, v in tk if ss == s)
        n1 = sum(1 for _, ss, v in tk if ss == s)
        e0 = legnet0[s] / max(legn0[s], 1)
        e1 = net1 / max(n1, 1)
        lost = e0 - e1
        frag.append((lost / e0 if e0 > 0 else float("inf"), leg, e0, lost,
                     e0 / lost if lost > 0 else float("inf")))
    frag.sort(reverse=True)
    print(f"  {'leg':<6} {'E/trade':>9} {'lost/tick':>9} {'%edge/tick':>10} "
          f"{'ticks-to-zero':>13}")
    for pct, leg, e0, lost, t2z in frag:
        print(f"  {leg:<6} {e0:>9,.2f} {lost:>9,.2f} {pct:>10.2%} {t2z:>13.1f}")

    # ── break-even systematic slippage ───────────────────────────────────────
    print()
    print("=" * 100)
    print("  BREAK-EVEN SWEEP -- systematic extra ticks/trade on EVERY trade "
          "(1 tick = $5 = 0.25pt)")
    print("=" * 100)
    print(f"  {'ticks':>5} {'$/trade':>8} | {'PF full':>7} {'PF IS':>7} "
          f"{'PF OOS':>7} | {'ann net full':>12}")

    def pf_at(t, per="full"):
        dly, tk = compose_scalar(days, clean, sys_ticks=t)
        sel = {"full": lambda y: True, "is": lambda y: y <= 2024,
               "oos": lambda y: y >= 2025}[per]
        gw = sum(v for d, _, v in tk if v > 0 and sel(d.year))
        gl = sum(-v for d, _, v in tk if v < 0 and sel(d.year))
        net = sum(v for d, v in dly if sel(d.year))
        return (gw / gl if gl > 0 else 99.0), net

    for t in range(0, 15):
        pf_f, net_f = pf_at(float(t))
        pf_i, _ = pf_at(float(t), "is")
        pf_o, _ = pf_at(float(t), "oos")
        print(f"  {t:>5} {t*5:>8.0f} | {pf_f:>7.3f} {pf_i:>7.3f} {pf_o:>7.3f} |"
              f" {net_f/yrs['full']:>12,.0f}")

    def bisect_pf1(per):
        lo, hi = 0.0, 80.0
        if pf_at(hi, per)[0] > 1.0:
            return float("inf")
        for _ in range(50):
            mid = (lo + hi) / 2
            if pf_at(mid, per)[0] > 1.0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    be_full = bisect_pf1("full")
    be_is = bisect_pf1("is")
    be_oos = bisect_pf1("oos")
    surv12 = 0
    for t in range(0, 81):
        if pf_at(float(t))[0] >= 1.2:
            surv12 = t
        else:
            break
    print(f"\n  BREAK-EVEN (PF=1.00): full {be_full:.1f} ticks/trade "
          f"(${be_full*5:.0f}/trade, {be_full*0.25:.1f} NQ pts)   "
          f"IS {be_is:.1f}   OOS {be_oos:.1f}")
    print(f"  PF stays >= 1.20 (full period) up to {surv12} extra "
          f"ticks/trade systematic.")

    # ── verdict ──────────────────────────────────────────────────────────────
    pyr_comb = comb["pyr"]["full"]
    print()
    print("=" * 100)
    print(f"  NOISE VERDICT: edge survives {surv12} extra ticks/trade "
          f"systematic at PF>=1.2 (break-even {be_full:.1f} ticks, OOS "
          f"break-even {be_oos:.1f}); combined-realistic scenario keeps "
          f"P(year>0) = {pyr_comb:.0%}")
    print("=" * 100)
    log("noise_mc done.")


if __name__ == "__main__":
    main()
