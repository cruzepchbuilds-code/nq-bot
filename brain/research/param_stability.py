"""
brain/research/param_stability.py

PARAMETER ROBUSTNESS AUDIT for the live v12 stack (NQ 1-min, data/nq_full.csv).
House law: IS 2022-2024 / OOS 2025-2026.

Four live legs, audited IN ISOLATION (each leg's P&L is generated with only that
leg active — morning via the real engine with all other engine legs disabled;
REJ / PM / ASIA are standalone day-sims by construction, cloned from
brain/research/portfolio_policy.py and verified cell-for-cell against it):

  Morning ORB   : entries 9:46-10:30, OR 55-110pt, gap>20 aligned,
                  regime OR >= 0.18 x 14d avg range, breakout vol >= 200,
                  confidence score >= 3, stop 27 (22+5 buffer), 3R funded (81pt)
  VWAP Rejection: extension >= 25pt, first cross >= 11:00, entries 11:00-13:00,
                  stop 20, 3R (60pt)         [+ fixed gates: skip Mon, skip months 4,5,6,9,12]
  PM ORB        : OR 13:00-13:14 (15-60pt), entries 13:15-14:00, stop 22, 2.5R (55pt)
                  [+ fixed gates: skip Mon+Fri]
  Asia Gap      : 18:15 entry, |halt gap| 30-80pt, stop 25, 3R (75pt)
                  [+ fixed gates: skip Thu, skip months 8,11]

For every scalar parameter: 1-D sweep +/-40% around live in ~7 steps
(5 steps for confidence score / integer params). 2-D heat maps:
(stop, target-R) for all four legs; (OR min, OR max) for morning + PM;
(gap threshold, regime threshold) for morning.

Verdicts (keyed on OOS PF, one-sided — a neighbor materially BETTER is not
a robustness failure, a neighbor >15% WORSE is a cliff):
  PLATEAU : live and both +/-1-step neighbors within 15% of live OOS PF
  EDGE    : one immediate neighbor is a cliff (direction noted)
  SPIKE   : both immediate neighbors are cliffs — isolated peak = overfit red flag

HARNESS NOTES (all overrides are IN-MEMORY module attributes; config.py untouched):
  * Morning leg runs the real engine (backtest.Backtester) over RTH bars,
    chained per-year with a fresh bankroll each January (same warm state
    carry as portfolio_policy.run_year_morning: last close, regime ranges,
    OR-volume history, prev day mode) — regime history kept as a 14-day
    deque per regime.py spec. NOTE: portfolio_policy used to transplant it
    as a plain list, silently turning the 14-day ATR into an expanding mean
    (live-params effect: OOS N 52 PF 2.84 vs deque OOS N 64 PF 2.35 full-neutral).
    FIXED 2026-07-03 in portfolio_policy + 13 research scripts that copied
    the pattern (this file was already spec-correct); deltas in their headers.
  * Bankroll gates fully neutralized (career DD halt, Apex floor, daily/weekly
    loss limits, profit lock, max trades/losses per day, consecutive-losing-day
    pause). The live NT8 leg (CruzCapitalNQ_v10_4.cs) has no in-strategy profit
    lock or DLL — those live at the account-policy layer, which is out of scope
    for signal-parameter stability.
  * config.MIN_RR forced to 0 so low-R sweep cells exist (live MIN_RR=1.9 only
    binds below R=1.9, which live never uses).
  * Morning P&L normalized per contract (pnl / contracts), matching
    eval_boost.build_components — sizing noise is not signal quality.
  * Morning cost model = engine (2 ticks slippage/side + $2.50/side commission);
    REJ/PM/ASIA = flat $14.50 round trip, no slippage points (pp convention).
  * REGIME_FADE_THRESHOLD is swept in lockstep with REGIME_BREAKOUT_THRESHOLD
    (equal thresholds = fade mode disabled, the live invariant).

Run from repo root:  python3 brain/research/param_stability.py
Runtime: ~5-8 min (about 160 engine runs + ~300 fast day-sims). Prints progress.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time as clock_mod
from datetime import datetime, time, date
from collections import defaultdict, deque

import config
from backtest import Backtester, load_csv

NQ_PT, COST = 20.0, 14.50
DATA   = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "nq_full.csv")
YEARS  = [2022, 2023, 2024, 2025, 2026]
IS_MAX_YEAR = 2024          # IS = <=2024, OOS = >=2025

T0 = clock_mod.time()
def el():
    return f"[{clock_mod.time()-T0:6.0f}s]"

# ─────────────────────────────────────────────────────────────────────────────
# Morning ORB — real engine, in-memory config, per-year chained, neutral bankroll
# ─────────────────────────────────────────────────────────────────────────────

MORNING_BASE = {
    # live v12 morning leg
    "SKIP_MONDAYS": True, "SKIP_FRIDAYS": False,
    "ORB_FIXED_STOP_POINTS": 22.0, "ORB_STOP_BUFFER_POINTS": 5.0,   # eff stop 27
    "ORB_BREAKOUT_BUFFER_POINTS": 4.0, "ORB_FUNDED_RR_TARGET": 3.0,
    "ORB_MIN_RANGE_POINTS": 55.0, "ORB_MAX_RANGE_POINTS": 110.0,
    "GAP_FILTER_POINTS": 20.0, "BREAKOUT_MIN_VOLUME": 200,
    "REGIME_BREAKOUT_THRESHOLD": 0.18, "REGIME_FADE_THRESHOLD": 0.18,
    "CONFIDENCE_SCORE_ENABLED": True, "CONFIDENCE_SCORE_SKIP_BELOW": 3,
    "CONFIDENCE_SCORE_DOUBLE_AT": 99, "ORB_SKIP_FIRST_BAR": True,
    "LAST_ENTRY_TIME": "10:30",
    "SECOND_BREAKOUT_ENABLED": True, "SECOND_BREAKOUT_MIN_TIME": "10:00",
    # other engine legs OFF (isolation)
    "LONDON_ENABLED": False, "ASIA_ENABLED": False, "PM_VWAP_ENABLED": False,
    "GAP_FILL_ENABLED": False, "VWAP_PULLBACK_ENABLED": False,
    "PYRAMIDING_ENABLED": False, "PARTIAL_EXIT_ENABLED": False, "EVAL_MODE": False,
    # disabled research gates at live values
    "GAP_EXCLUDE_MIN": 0.0, "GAP_EXCLUDE_MAX": 0.0,
    "BREAKOUT_MIN_OR_VOLUME_RATIO": 0.0, "BREAKOUT_MAX_OR_VOLUME_RATIO": 0.0,
    "SKIP_MONTHS": [],
    # harness neutralizations (see module docstring)
    "MIN_RR": 0.0,
    "MAX_TOTAL_DRAWDOWN_PCT": 9.0, "ENFORCE_APEX_RULES": False,
    "MAX_CONSECUTIVE_LOSING_DAYS": 10**9,
    "DAILY_LOSS_LIMIT_PCT": 9.0, "DAILY_PROFIT_LOCK_PCT": 9.0,
    "WEEKLY_LOSS_LIMIT_PCT": 9.0, "MAX_TRADES_PER_DAY": 99, "MAX_LOSSES_PER_DAY": 99,
    "TELEGRAM_ALERTS_ENABLED": False,
}

def hhmm(total_min):
    return f"{total_min // 60:02d}:{total_min % 60:02d}"

def _apply_morning(over):
    for k, v in MORNING_BASE.items():
        setattr(config, k, v)
    for k, v in over.items():
        if   k == "stop":   config.ORB_FIXED_STOP_POINTS = v - MORNING_BASE["ORB_STOP_BUFFER_POINTS"]
        elif k == "rr":     config.ORB_FUNDED_RR_TARGET = v
        elif k == "ormin":  config.ORB_MIN_RANGE_POINTS = float(v)
        elif k == "ormax":  config.ORB_MAX_RANGE_POINTS = float(v)
        elif k == "gap":    config.GAP_FILTER_POINTS = float(v)
        elif k == "regime": config.REGIME_BREAKOUT_THRESHOLD = v; config.REGIME_FADE_THRESHOLD = v
        elif k == "vol":    config.BREAKOUT_MIN_VOLUME = v
        elif k == "score":  config.CONFIDENCE_SCORE_SKIP_BELOW = v
        elif k == "wend":   config.LAST_ENTRY_TIME = hhmm(9 * 60 + 45 + v)
        else: raise KeyError(k)

RTH_BY_YEAR = {}
_MCACHE = {}
_ENGINE_RUNS = [0]

def run_morning(**over):
    key = tuple(sorted(over.items()))
    if key in _MCACHE:
        return _MCACHE[key]
    _apply_morning(over)
    state = None
    out = []
    for y in YEARS:
        bt = Backtester()
        if state is not None:
            bt._last_close, dr, ovh, bt.prev_day_mode = state
            bt.regime.daily_ranges = deque(dr, maxlen=config.REGIME_ATR_PERIOD)
            bt.or_volume_history = list(ovh)
        bt.run(RTH_BY_YEAR.get(y, []), silent=True)
        for t in bt.bank.trade_log:
            if t.get("mode") == "breakout":
                out.append((date.fromisoformat(t["date"]),
                            t["pnl"] / max(1, t.get("contracts", 1))))
        state = (bt._last_close, list(bt.regime.daily_ranges),
                 list(bt.or_volume_history), bt.prev_day_mode)
    _MCACHE[key] = out
    _ENGINE_RUNS[0] += 1
    return out

# ─────────────────────────────────────────────────────────────────────────────
# REJ / PM / ASIA — parameterized clones of portfolio_policy day-sims
# (verified against the originals at live params in verify_clones())
# ─────────────────────────────────────────────────────────────────────────────

def rejection_day_p(bars, stop=20.0, rr=3.0, ext=25.0, arm=time(11, 0), wend=time(13, 0)):
    """Clone of pp.rejection_day with sweepable stop/rr/ext/arm/window-end."""
    tgt = stop * rr
    sum_pv = sum_vol = 0.0
    vwap = None
    was_ext = saw = False
    rec_up = prev_above = None
    entry = sl = tp = e_t = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(9, 30) or t >= time(16, 0):
            continue
        sum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        sum_vol += b["v"]
        vwap = sum_pv / sum_vol if sum_vol else None
        if vwap is None:
            continue
        close = b["c"]; above = close > vwap
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (e_t, t, (sl - entry) * NQ_PT - COST)
                if b["h"] >= tp: return (e_t, t, (tp - entry) * NQ_PT - COST)
            else:
                if b["h"] >= sl: return (e_t, t, (entry - sl) * NQ_PT - COST)
                if b["l"] <= tp: return (e_t, t, (entry - tp) * NQ_PT - COST)
            if t >= wend:
                pts = (close - entry) if is_long else (entry - close)
                return (e_t, t, pts * NQ_PT - COST)
            prev_above = above
            continue
        if t < time(10, 0):
            prev_above = above
            continue
        if not was_ext and abs(close - vwap) > ext:
            was_ext = True
        if was_ext and prev_above is not None and arm <= t < wend:
            cu = (not prev_above) and above
            cd = prev_above and (not above)
            if not saw:
                if cu:   saw, rec_up = True, True
                elif cd: saw, rec_up = True, False
            else:
                if rec_up and cd:
                    entry, is_long, e_t = close, False, t
                    sl, tp = close + stop, close - tgt
                elif (not rec_up) and cu:
                    entry, is_long, e_t = close, True, t
                    sl, tp = close - stop, close + tgt
        prev_above = above
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, bars[-1]["t"], pts * NQ_PT - COST)
    return None


def pm_day_p(bars, or_min=15.0, or_max=60.0, cutoff=time(14, 0), stop=22.0, rr=2.5):
    """Clone of pp.pm_day with sweepable OR bounds / entry cutoff / stop / rr."""
    tgt = stop * rr
    or_hi = or_lo = None
    or_done = False
    entry = sl = tp = e_t = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(13, 0):
            continue
        if t >= time(15, 55):
            break
        if t < time(13, 15):
            or_hi = b["h"] if or_hi is None else max(or_hi, b["h"])
            or_lo = b["l"] if or_lo is None else min(or_lo, b["l"])
            continue
        if not or_done:
            or_done = True
            if or_hi is None or not (or_min <= or_hi - or_lo <= or_max):
                return None
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (e_t, t, (sl - entry) * NQ_PT - COST)
                if b["h"] >= tp: return (e_t, t, (tp - entry) * NQ_PT - COST)
            else:
                if b["h"] >= sl: return (e_t, t, (entry - sl) * NQ_PT - COST)
                if b["l"] <= tp: return (e_t, t, (entry - tp) * NQ_PT - COST)
            continue
        if t > cutoff:
            continue
        if b["c"] > or_hi + 2:
            entry, is_long, e_t = b["c"], True, t
            sl, tp = entry - stop, entry + tgt
        elif b["c"] < or_lo - 2:
            entry, is_long, e_t = b["c"], False, t
            sl, tp = entry + stop, entry - tgt
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, time(15, 55), pts * NQ_PT - COST)
    return None


def asia_day_p(bars, gap_min=30.0, gap_max=80.0, stop=25.0, rr=3.0, entry_min=15):
    """Clone of pp.asia_day with sweepable gap bounds / stop / rr / entry minute."""
    tgt = stop * rr
    t_entry = time(18, entry_min)
    t_after = time(18, entry_min + 1)
    cme = None
    entry = sl = tp = e_t = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(17, 0):
            cme = b["c"]
            continue
        if t < t_entry:
            continue
        if entry is None:
            if t >= t_after or cme is None:
                return None
            gap = b["c"] - cme
            if not (gap_min <= abs(gap) <= gap_max):
                return None
            entry, is_long, e_t = b["c"], gap > 0, t
            sl = entry - stop if is_long else entry + stop
            tp = entry + tgt if is_long else entry - tgt
            continue
        if t >= time(21, 0):
            pts = (b["c"] - entry) if is_long else (entry - b["c"])
            return (e_t, t, pts * NQ_PT - COST)
        if is_long:
            if b["l"] <= sl: return (e_t, t, (sl - entry) * NQ_PT - COST)
            if b["h"] >= tp: return (e_t, t, (tp - entry) * NQ_PT - COST)
        else:
            if b["h"] >= sl: return (e_t, t, (entry - sl) * NQ_PT - COST)
            if b["l"] <= tp: return (e_t, t, (entry - tp) * NQ_PT - COST)
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, bars[-1]["t"], pts * NQ_PT - COST)
    return None

# ── leg runners: fixed live DOW/month gates, sweepable scalars ───────────────

RTH_DAYS, EVE_DAYS = {}, {}
_RCACHE, _PCACHE, _ACACHE = {}, {}, {}

def run_rej(stop=20.0, rr=3.0, ext=25.0, arm_min=60, end_min=120):
    key = (stop, rr, ext, arm_min, end_min)
    if key in _RCACHE:
        return _RCACHE[key]
    arm  = time(*divmod(10 * 60 + arm_min, 60))
    wend = time(*divmod(11 * 60 + end_min, 60))
    out = []
    for d in sorted(RTH_DAYS):
        if d.weekday() == 0 or d.month in (4, 5, 6, 9, 12):
            continue
        r = rejection_day_p(RTH_DAYS[d], stop=stop, rr=rr, ext=ext, arm=arm, wend=wend)
        if r:
            out.append((d, r[2]))
    _RCACHE[key] = out
    return out

def run_pm(stop=22.0, rr=2.5, ormin=15.0, ormax=60.0, cut_min=45):
    key = (stop, rr, ormin, ormax, cut_min)
    if key in _PCACHE:
        return _PCACHE[key]
    cutoff = time(*divmod(13 * 60 + 15 + cut_min, 60))
    out = []
    for d in sorted(RTH_DAYS):
        if d.weekday() in (0, 4):
            continue
        r = pm_day_p(RTH_DAYS[d], or_min=ormin, or_max=ormax, cutoff=cutoff, stop=stop, rr=rr)
        if r:
            out.append((d, r[2]))
    _PCACHE[key] = out
    return out

def run_asia(stop=25.0, rr=3.0, gmin=30.0, gmax=80.0, entry_min=15):
    key = (stop, rr, gmin, gmax, entry_min)
    if key in _ACACHE:
        return _ACACHE[key]
    out = []
    for d in sorted(EVE_DAYS):
        if d.weekday() == 3 or d.month in (8, 11):
            continue
        r = asia_day_p(EVE_DAYS[d], gap_min=gmin, gap_max=gmax, stop=stop, rr=rr, entry_min=entry_min)
        if r:
            out.append((d, r[2]))
    _ACACHE[key] = out
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Metrics / verdicts / printing
# ─────────────────────────────────────────────────────────────────────────────

def metr(rows):
    def pf(x):
        w = sum(p for p in x if p > 0)
        l = abs(sum(p for p in x if p <= 0))
        if l > 0:
            return w / l
        return 99.0 if w > 0 else 0.0
    is_ = [p for d, p in rows if d.year <= IS_MAX_YEAR]
    oos = [p for d, p in rows if d.year > IS_MAX_YEAR]
    return {"n": len(rows), "net": sum(p for _, p in rows),
            "ispf": pf(is_), "oospf": pf(oos), "isn": len(is_), "oosn": len(oos)}

SUMMARY = []   # rows: (leg, param, live_str, swept_str, stable_str, verdict, live_oospf, worst_nb)

def classify(values, live_i, cells, fmt=lambda v: str(v), tol=0.15):
    pfs = [c["oospf"] for c in cells]
    live = pfs[live_i]
    if live <= 0:
        return "N/A(live OOS PF<=0)", "-", 0.0
    ok = [p >= live * (1 - tol) for p in pfs]
    lo = hi = live_i
    while lo > 0 and ok[lo - 1]:
        lo -= 1
    while hi < len(ok) - 1 and ok[hi + 1]:
        hi += 1
    left_cliff  = live_i > 0 and not ok[live_i - 1]
    right_cliff = live_i < len(ok) - 1 and not ok[live_i + 1]
    if left_cliff and right_cliff:
        v = "SPIKE"
    elif left_cliff:
        v = "EDGE(cliff just below live)"
    elif right_cliff:
        v = "EDGE(cliff just above live)"
    else:
        v = "PLATEAU"
    thin = []
    for j in (live_i - 1, live_i + 1):
        if 0 <= j < len(cells) and cells[j]["oosn"] < 8:
            thin.append(j)
    if thin:
        v += " (thin-N neighbor)"
    nb = [pfs[j] for j in (live_i - 1, live_i + 1) if 0 <= j < len(pfs)]
    return v, f"{fmt(values[lo])}..{fmt(values[hi])}", min(nb) if nb else live

def fmtv(v):
    if isinstance(v, float):
        s = f"{v:g}"
        return s
    return str(v)

def sweep_1d(leg, pname, key, values, live_i, runner, fmt=fmtv, cells=None):
    print(f"\n  -- {leg} :: {pname} (live={fmt(values[live_i])}) --", flush=True)
    if cells is None:
        cells = []
        for v in values:
            cells.append(metr(runner(**{key: v})))
    for v, m in zip(values, cells):
        mark = "*" if v == values[live_i] else " "
        print(f"   {mark}{fmt(v):>8}   N={m['n']:>3} (IS {m['isn']:>3}/OOS {m['oosn']:>3})"
              f"   net ${m['net']:>+10,.0f}   IS PF {min(m['ispf'],99):5.2f}   OOS PF {min(m['oospf'],99):5.2f}",
              flush=True)
    vdt, rng, worst_nb = classify(values, live_i, cells, fmt)
    live_pf = cells[live_i]["oospf"]
    print(f"   -> verdict: {vdt}   stable range: {rng}   live OOS PF {min(live_pf,99):.2f}"
          f"   worst +/-1 neighbor OOS PF {min(worst_nb,99):.2f}", flush=True)
    SUMMARY.append((leg, pname, fmt(values[live_i]), f"{fmt(values[0])}..{fmt(values[-1])}",
                    rng, vdt, live_pf, worst_nb))
    return cells

def grid_2d(leg, tag, runner, kx, xs, ky, ys, live_ix, live_iy,
            fx=fmtv, fy=fmtv, invalid=None, print_ispf=False):
    """xs = rows, ys = cols. Returns cells dict {(i,j): metr or None}."""
    print(f"\n  == {leg} :: 2-D grid {tag} ==", flush=True)
    cells = {}
    for i, x in enumerate(xs):
        t0 = clock_mod.time()
        for j, y in enumerate(ys):
            if invalid and invalid(x, y):
                cells[(i, j)] = None
                continue
            cells[(i, j)] = metr(runner(**{kx: x, ky: y}))
        print(f"    {el()} row {kx}={fx(x)} done ({clock_mod.time()-t0:4.1f}s)", flush=True)

    def block(metric, title, scale=1.0, fmt_cell="{:6.2f}"):
        print(f"\n    {title}   (rows: {kx}, cols: {ky}; * = live cell)")
        print("    " + f"{'':>8}" + "".join(f"{fy(y):>8}" for y in ys))
        for i, x in enumerate(xs):
            row = f"    {fx(x):>8}"
            for j, y in enumerate(ys):
                c = cells[(i, j)]
                if c is None:
                    row += f"{'n/a':>8}"
                else:
                    val = min(c[metric] * scale, 99) if metric != "net" else c[metric] * scale
                    mark = "*" if (i == live_ix and j == live_iy) else " "
                    row += f"{fmt_cell.format(val):>7}{mark}"
            print(row, flush=True)

    block("oospf", f"OOS PF (2025-26) — {leg} {tag}")
    if print_ispf:
        block("ispf", f"IS PF (2022-24) — {leg} {tag}")
    block("net", f"Full-period net $k (2022-26) — {leg} {tag}", scale=1e-3, fmt_cell="{:+6.1f}")
    return cells

def cross_sections(cells, xs, ys, live_ix, live_iy):
    row = [cells[(live_ix, j)] for j in range(len(ys))]   # vary y at live x
    col = [cells[(i, live_iy)] for i in range(len(xs))]   # vary x at live y
    return col, row

# ─────────────────────────────────────────────────────────────────────────────
# Verification of clones vs portfolio_policy originals
# ─────────────────────────────────────────────────────────────────────────────

def verify_clones():
    print(f"\n{el()} Verifying parameterized clones vs portfolio_policy originals "
          f"(live params, every day)...", flush=True)
    try:
        import portfolio_policy as pp
    except Exception as e:
        print(f"  WARNING: cannot import portfolio_policy ({e}) — skipping verification.", flush=True)
        return
    bad = 0
    for d in sorted(RTH_DAYS):
        if rejection_day_p(RTH_DAYS[d]) != pp.rejection_day(RTH_DAYS[d]):
            bad += 1
    print(f"  rejection_day : {'OK — identical on all days' if bad == 0 else f'MISMATCH on {bad} days!'}", flush=True)
    bad = 0
    for d in sorted(RTH_DAYS):
        if pm_day_p(RTH_DAYS[d]) != pp.pm_day(RTH_DAYS[d]):
            bad += 1
    print(f"  pm_day        : {'OK — identical on all days' if bad == 0 else f'MISMATCH on {bad} days!'}", flush=True)
    bad = 0
    for d in sorted(EVE_DAYS):
        if asia_day_p(EVE_DAYS[d]) != pp.asia_day(EVE_DAYS[d]):
            bad += 1
    print(f"  asia_day      : {'OK — identical on all days' if bad == 0 else f'MISMATCH on {bad} days!'}", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"{el()} Loading {DATA} ...", flush=True)
    bars = load_csv(DATA)
    print(f"{el()}  {len(bars):,} bars", flush=True)

    # engine bars: RTH only (identical trade stream — engine skips non-RTH when
    # London/Asia legs are disabled; day rollover fires on first in-window bar)
    for b in bars:
        ts = b["timestamp"]
        t = ts.time()
        if time(9, 30) <= t <= time(16, 0):
            RTH_BY_YEAR.setdefault(ts.year, []).append(b)

    # day dicts for the standalone sims (same construction as pp.load_days)
    for b in bars:
        ts = b["timestamp"]
        h = ts.hour
        row = {"t": ts.time(), "h": b["high"], "l": b["low"], "c": b["close"], "v": b["volume"]}
        if 9 <= h < 16:
            RTH_DAYS.setdefault(ts.date(), []).append(row)
        elif 16 <= h < 21:
            EVE_DAYS.setdefault(ts.date(), []).append(row)
    del bars
    print(f"{el()}  RTH days: {len(RTH_DAYS)}   EVE days: {len(EVE_DAYS)}   "
          f"engine RTH bars: {sum(len(v) for v in RTH_BY_YEAR.values()):,}", flush=True)

    verify_clones()

    # ── live baselines ──────────────────────────────────────────────────────
    print(f"\n{el()} {'='*96}\n  LIVE BASELINES (isolated legs, audit harness)\n  {'='*96}", flush=True)
    base = {
        "Morning ORB": metr(run_morning()),
        "VWAP REJ":    metr(run_rej()),
        "PM ORB":      metr(run_pm()),
        "Asia Gap":    metr(run_asia()),
    }
    for leg, m in base.items():
        print(f"  {leg:<12} N={m['n']:>4} (IS {m['isn']:>3}/OOS {m['oosn']:>3})   "
              f"net ${m['net']:>+10,.0f}   IS PF {m['ispf']:5.2f}   OOS PF {m['oospf']:5.2f}", flush=True)

    # ══ MORNING ORB ══════════════════════════════════════════════════════════
    print(f"\n{el()} {'#'*96}\n  LEG 1: MORNING ORB (engine sweeps — ~160 engine runs)\n  {'#'*96}", flush=True)

    M_STOP  = [16.2, 19.8, 23.4, 27.0, 30.6, 34.2, 37.8]
    M_RR    = [1.8, 2.2, 2.6, 3.0, 3.4, 3.8, 4.2]
    M_ORMIN = [33, 40, 48, 55, 62, 70, 77]
    M_ORMAX = [66, 81, 95, 110, 125, 139, 154]
    M_GAP   = [12, 14.7, 17.3, 20, 22.7, 25.3, 28]
    M_REG   = [0.108, 0.132, 0.156, 0.18, 0.204, 0.228, 0.252]
    M_VOL   = [120, 147, 173, 200, 227, 253, 280]
    M_SCORE = [0, 1, 2, 3, 4]
    M_WEND  = [27, 33, 39, 45, 51, 57, 63]          # minutes after 9:45
    f_wend  = lambda m: hhmm(9 * 60 + 45 + m)

    g = grid_2d("Morning", "(stop, target R)", run_morning, "stop", M_STOP, "rr", M_RR,
                3, 3, print_ispf=True)
    col, row = cross_sections(g, M_STOP, M_RR, 3, 3)
    sweep_1d("Morning", "stop (eff pts)", "stop", M_STOP, 3, run_morning, cells=col)
    sweep_1d("Morning", "target R", "rr", M_RR, 3, run_morning, cells=row)

    g = grid_2d("Morning", "(OR min, OR max)", run_morning, "ormin", M_ORMIN, "ormax", M_ORMAX,
                3, 3, invalid=lambda a, b: a >= b)
    col, row = cross_sections(g, M_ORMIN, M_ORMAX, 3, 3)
    sweep_1d("Morning", "OR min (pts)", "ormin", M_ORMIN, 3, run_morning, cells=col)
    sweep_1d("Morning", "OR max (pts)", "ormax", M_ORMAX, 3, run_morning, cells=row)

    g = grid_2d("Morning", "(gap filter, regime thr)", run_morning, "gap", M_GAP, "regime", M_REG,
                3, 3)
    col, row = cross_sections(g, M_GAP, M_REG, 3, 3)
    sweep_1d("Morning", "gap filter (pts)", "gap", M_GAP, 3, run_morning, cells=col)
    sweep_1d("Morning", "regime threshold", "regime", M_REG, 3, run_morning, cells=row)

    sweep_1d("Morning", "breakout min volume", "vol", M_VOL, 3, run_morning)
    sweep_1d("Morning", "confidence score >=", "score", M_SCORE, 3, run_morning)
    sweep_1d("Morning", "entry window end", "wend", M_WEND, 3, run_morning, fmt=f_wend)

    print(f"\n{el()}  morning engine runs so far: {_ENGINE_RUNS[0]}", flush=True)

    # ══ VWAP REJECTION ═══════════════════════════════════════════════════════
    print(f"\n{el()} {'#'*96}\n  LEG 2: VWAP REJECTION (11:00-13:00)\n  {'#'*96}", flush=True)

    R_STOP = [12, 14.7, 17.3, 20, 22.7, 25.3, 28]
    R_RR   = [1.8, 2.2, 2.6, 3.0, 3.4, 3.8, 4.2]
    R_EXT  = [15, 18.3, 21.7, 25, 28.3, 31.7, 35]
    R_ARM  = [36, 44, 52, 60, 68, 76, 84]            # minutes after 10:00
    R_END  = [72, 88, 104, 120, 136, 152, 168]       # minutes after 11:00
    f_arm  = lambda m: hhmm(10 * 60 + m)
    f_rend = lambda m: hhmm(11 * 60 + m)

    g = grid_2d("VWAP REJ", "(stop, target R)", run_rej, "stop", R_STOP, "rr", R_RR,
                3, 3, print_ispf=True)
    col, row = cross_sections(g, R_STOP, R_RR, 3, 3)
    sweep_1d("VWAP REJ", "stop (pts)", "stop", R_STOP, 3, run_rej, cells=col)
    sweep_1d("VWAP REJ", "target R", "rr", R_RR, 3, run_rej, cells=row)
    sweep_1d("VWAP REJ", "extension (pts)", "ext", R_EXT, 3, run_rej)
    sweep_1d("VWAP REJ", "cross-arm start", "arm_min", R_ARM, 3, run_rej, fmt=f_arm)
    sweep_1d("VWAP REJ", "window end/flat", "end_min", R_END, 3, run_rej, fmt=f_rend)

    # ══ PM ORB ═══════════════════════════════════════════════════════════════
    print(f"\n{el()} {'#'*96}\n  LEG 3: PM ORB (13:00-13:14 OR)\n  {'#'*96}", flush=True)

    P_STOP  = [13.2, 16.1, 19.1, 22.0, 24.9, 27.9, 30.8]
    P_RR    = [1.5, 1.83, 2.17, 2.5, 2.83, 3.17, 3.5]
    P_ORMIN = [9, 11, 13, 15, 17, 19, 21]
    P_ORMAX = [36, 44, 52, 60, 68, 76, 84]
    P_CUT   = [27, 33, 39, 45, 51, 57, 63]           # minutes after 13:15
    f_cut   = lambda m: hhmm(13 * 60 + 15 + m)

    g = grid_2d("PM ORB", "(stop, target R)", run_pm, "stop", P_STOP, "rr", P_RR,
                3, 3, print_ispf=True)
    col, row = cross_sections(g, P_STOP, P_RR, 3, 3)
    sweep_1d("PM ORB", "stop (pts)", "stop", P_STOP, 3, run_pm, cells=col)
    sweep_1d("PM ORB", "target R", "rr", P_RR, 3, run_pm, cells=row)

    g = grid_2d("PM ORB", "(OR min, OR max)", run_pm, "ormin", P_ORMIN, "ormax", P_ORMAX,
                3, 3, invalid=lambda a, b: a >= b)
    col, row = cross_sections(g, P_ORMIN, P_ORMAX, 3, 3)
    sweep_1d("PM ORB", "OR min (pts)", "ormin", P_ORMIN, 3, run_pm, cells=col)
    sweep_1d("PM ORB", "OR max (pts)", "ormax", P_ORMAX, 3, run_pm, cells=row)

    sweep_1d("PM ORB", "entry cutoff", "cut_min", P_CUT, 3, run_pm, fmt=f_cut)

    # ══ ASIA GAP ═════════════════════════════════════════════════════════════
    print(f"\n{el()} {'#'*96}\n  LEG 4: ASIA GAP (18:15)\n  {'#'*96}", flush=True)

    A_STOP  = [15, 18.3, 21.7, 25, 28.3, 31.7, 35]
    A_RR    = [1.8, 2.2, 2.6, 3.0, 3.4, 3.8, 4.2]
    A_GMIN  = [18, 22, 26, 30, 34, 38, 42]
    A_GMAX  = [48, 59, 69, 80, 91, 101, 112]
    A_ENTRY = [9, 11, 13, 15, 17, 19, 21]            # minutes after 18:00
    f_asia  = lambda m: hhmm(18 * 60 + m)

    g = grid_2d("Asia Gap", "(stop, target R)", run_asia, "stop", A_STOP, "rr", A_RR,
                3, 3, print_ispf=True)
    col, row = cross_sections(g, A_STOP, A_RR, 3, 3)
    sweep_1d("Asia Gap", "stop (pts)", "stop", A_STOP, 3, run_asia, cells=col)
    sweep_1d("Asia Gap", "target R", "rr", A_RR, 3, run_asia, cells=row)
    sweep_1d("Asia Gap", "gap min (pts)", "gmin", A_GMIN, 3, run_asia)
    sweep_1d("Asia Gap", "gap max (pts)", "gmax", A_GMAX, 3, run_asia)
    sweep_1d("Asia Gap", "entry time", "entry_min", A_ENTRY, 3, run_asia, fmt=f_asia)

    # ══ SUMMARY ══════════════════════════════════════════════════════════════
    print(f"\n{el()} {'='*118}\n  PARAMETER STABILITY SUMMARY  "
          f"(verdict on OOS PF 2025-26, 15% one-sided tolerance, +/-1-step cliff test)\n  {'='*118}", flush=True)
    print(f"  {'leg':<10} {'parameter':<22} {'live':>8} {'swept':>16} {'stable range':>16}   "
          f"{'liveOOSPF':>9} {'worstNbr':>8}  verdict")
    print(f"  {'-'*114}")
    counts = defaultdict(int)
    for leg, p, live, swept, stable, vdt, livepf, nb in SUMMARY:
        counts[vdt.split(" ")[0].split("(")[0]] += 1
        print(f"  {leg:<10} {p:<22} {live:>8} {swept:>16} {stable:>16}   "
              f"{min(livepf,99):>9.2f} {min(nb,99):>8.2f}  {vdt}", flush=True)
    n = len(SUMMARY)
    print(f"\n  OVERALL: {counts.get('PLATEAU',0)}/{n} PLATEAU | "
          f"{counts.get('EDGE',0)}/{n} EDGE | {counts.get('SPIKE',0)}/{n} SPIKE | "
          f"{counts.get('N/A',0)}/{n} N/A", flush=True)
    edges  = [f"{l}:{p}" for l, p, *_, v, _pf, _nb in [(r[0], r[1], r[5], r[6], r[7]) for r in SUMMARY] if v.startswith("EDGE")]
    # simpler re-scan for names
    edges  = [f"{r[0]}::{r[1]} {r[5][r[5].find('('):] if '(' in r[5] else ''}".strip()
              for r in SUMMARY if r[5].startswith("EDGE")]
    spikes = [f"{r[0]}::{r[1]}" for r in SUMMARY if r[5].startswith("SPIKE")]
    print(f"  EDGEs : {edges if edges else 'none'}")
    print(f"  SPIKEs: {spikes if spikes else 'none'}")
    print(f"\n{el()}  total morning engine runs: {_ENGINE_RUNS[0]}   "
          f"REJ sims: {len(_RCACHE)}   PM sims: {len(_PCACHE)}   ASIA sims: {len(_ACACHE)}")
    print(f"{el()}  param_stability done.", flush=True)


if __name__ == "__main__":
    main()
