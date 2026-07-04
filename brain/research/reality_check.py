"""
brain/research/reality_check.py

STATISTICAL AUDIT of the v12 stack (morning ORB / VWAP rejection / PM ORB /
Asia gap on NQ 1-min, data/nq_full.csv, composed per empire_rulemap.build_seq).

Three audits:
  A1. SYNTHETIC / PERMUTATION TEST — ~20 synthetic histories. Within each
      session-day, 1-min log-returns are shuffled in 15-minute blocks
      (quarter-hour aligned), OHLC paths rebuilt from the shuffled returns,
      volumes travel with their blocks. Anchors: the day's first RTH bar
      (9:30) and the first evening bar (18:00) keep their real OHLC, so the
      overnight gap and the CME halt gap — cross-session "coarse character" —
      are preserved while intra-session sequencing is destroyed. Segment
      structure: [9:30..16:00] shuffled, (16:00..17:00) untouched,
      [18:00..21:00] shuffled. Each segment's net return is invariant, so
      day closes are preserved. The FULL stack pipeline (engine morning ORB
      via yearly-bankroll protocol + portfolio_policy day-sims + build_seq
      composition) is run on every synthetic world.
  A2. STATIONARY BOOTSTRAP (Politis-Romano, mean block 10 trading days,
      B=10,000) — p-values for mean daily P&L > 0, full period and OOS
      (>= 2025-01-01), for the composed stack and each leg's composed
      contribution. Plus OOS Sharpe (zero-filled over all OOS RTH days)
      with bootstrap CI.
  A3. SELECTION-BIAS DEFLATION —
      (a) Bonferroni-style bound using search-grid widths verified by
          reading the research scripts (counts cited in-code below);
      (b) RC-lite (White's Reality Check spirit) on the one reconstructible
          family: the morning-ORB stop x target x confidence-threshold grid
          (3x3x3 around the live config). Joint stationary bootstrap of the
          demeaned family, max-statistic null, p = P(max* >= live OOS mean).

REPLICATION PROTOCOL (validated in Phase 1 before being trusted):
  The canonical morning-ORB stream is portfolio_policy.run_year_morning
  (fresh bankroll per year, market-state warmup over all prior bars). That
  is O(sum-of-prior) slow, so synthetic worlds / grid configs use a chained
  equivalent (one pass, market state carried across year boundaries, regime
  window carried as the spec 14-day deque). Phase 1 proves the chained
  trades are IDENTICAL to the canonical ones on real data, and that the
  rebuilt composed stream matches the cached data/v12_daily_stream.csv.

NOTE (2026-07-03): portfolio_policy's regime-ATR transplant was fixed from an
  unbounded list (expanding mean) to the spec 14-day deque, and
  run_morning_chained here now mirrors the FIXED canonical. Reality-check
  results recorded before this date (incl. the LEDGER "PASS w/ flags" row)
  were computed on the pre-fix stream — re-run against the regenerated
  data/v12_daily_stream.csv (brain/firmcard.py --rebuild) before citing.

HARD SAFETY: alerts forcibly disabled in-process; no file in the repo is
modified; all randomness is explicitly seeded.

Run (from repo root):
    python3 brain/research/reality_check.py            # full audit (~20-30 min)
    python3 brain/research/reality_check.py --smoke    # fast wiring check
"""

import sys, os, time as _clock
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

import csv
import numpy as np
from datetime import date, datetime, time as dt_time
from collections import defaultdict, deque

import config
# ── kill all outbound comms before anything else touches the engine ─────────
config.TELEGRAM_ALERTS_ENABLED = False
config.DISCORD_ALERTS_ENABLED  = False

import portfolio_policy as pp          # sets SKIP_FRIDAYS=False, PYRAMIDING off,
                                       # PARTIAL off, EVAL off at import (canonical
                                       # v12-stream config)
import _eb_snap4 as eb                 # snapshot of eval_boost (shell-copied);
                                       # canonical build_components() fallback

DATA_CSV   = os.path.join(_ROOT, "data", "nq_full.csv")
CACHE_CSV  = os.path.join(_ROOT, "data", "v12_daily_stream.csv")
SCRATCH    = os.environ.get("RC_SCRATCH",
             "/private/tmp/claude-502/-Users-Cruz-Desktop-nq-bot-final-main/"
             "3c7889f3-5f4d-48b1-9594-81405ad58030/scratchpad")
NPZ_PATH   = os.path.join(SCRATCH, "nq_window_arrays.npz")

OOS_START  = date(2025, 1, 1)
LEGS       = ("ORB", "REJ", "PM", "ASIA")
RISK1      = {"ORB": 565.0, "REJ": 415.0, "PM": 455.0, "ASIA": 515.0}  # build_seq
DLL        = 500.0                                                     # build_seq

# seeds (all explicit)
SYN_SEEDS  = list(range(101, 121))     # 20 synthetic worlds
BOOT_SEED  = 20260703
RC_SEED    = 777

# live morning-ORB config + RC-lite family (3x3x3 around it)
LIVE_STOP, LIVE_RR, LIVE_SKIP = 22.0, 3.0, 3
GRID_STOPS = [17.0, 22.0, 27.0]
GRID_RRS   = [2.5, 3.0, 3.5]
GRID_SKIPS = [2, 3, 4]

# ── Audit 3a: search widths VERIFIED BY READING the research scripts ────────
# (config counts confirmed in the named files; these are lower bounds — the
#  true program width is larger, so the Bonferroni bound is LENIENT.)
BONF = {
    "ORB":  (64,  "threshold_sweep.py 4x4=16 + nq_param_sweep.py 7+6+5+5+5+5+5=38"
                  " + v9_optimization_test 10 (config.py v7 notes)"),
    "REJ":  (96,  "rejection_expanded_is.py 3x4x4=48 + vwap_fulldata.py S3 19"
                  " + final_sweep.py NQ-VWAP 5x4+9=29"),
    "PM":   (48,  "pm_sweep.py 6x4=24 + pm_stop_rr_sweep.py 6x4=24"),
    "ASIA": (489, "strategy_asia.py docstring 'tested across 464 configs'"
                  " + multi_probe.py P1 5x5=25"),
    "STACK": (2400, "legs 697 + cl_orb.py 1008 (documented 3*2*7*4*2*3)"
                    " + gc_structures.py >=540 (216+216+108)"
                    " + nq_hidden_windows.py 95 + multi_probe P2/P3 42"),
}

# ═════════════════════════════════════════════════════════════════════════════
# 0. Data loading -> compact numpy arrays (only windows the pipeline reads)
# ═════════════════════════════════════════════════════════════════════════════

def load_arrays():
    """Parse nq_full.csv once. Keep bars with wall-clock minute-of-day in
    [9:30..16:59] u [18:00..21:00] — everything the engine/day-sims read."""
    ymd, mins, o, h, l, c, v = [], [], [], [], [], [], []
    with open(DATA_CSV) as f:
        rdr = csv.reader(f)
        header = next(rdr)
        for row in rdr:
            s = row[0]
            mm = int(s[11:13]) * 60 + int(s[14:16])
            if not (570 <= mm <= 1019 or 1080 <= mm <= 1260):
                continue
            ymd.append(int(s[0:4]) * 10000 + int(s[5:7]) * 100 + int(s[8:10]))
            mins.append(mm)
            o.append(float(row[1])); h.append(float(row[2]))
            l.append(float(row[3])); c.append(float(row[4]))
            v.append(float(row[5]))
    a = {
        "ymd":  np.asarray(ymd,  dtype=np.int32),
        "mins": np.asarray(mins, dtype=np.int16),
        "o": np.asarray(o), "h": np.asarray(h),
        "l": np.asarray(l), "c": np.asarray(c), "v": np.asarray(v),
    }
    order = np.lexsort((a["mins"], a["ymd"]))
    for k in a:
        a[k] = a[k][order]
    return a


def _date_of(ymd_int):
    return date(ymd_int // 10000, (ymd_int // 100) % 100, ymd_int % 100)


def day_slices(a):
    """[(ymd, start, stop)] contiguous per-day slices."""
    u, idx = np.unique(a["ymd"], return_index=True)
    idx = list(idx) + [len(a["ymd"])]
    return [(int(u[i]), int(idx[i]), int(idx[i + 1])) for i in range(len(u))]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Synthetic-world construction (Audit 1)
# ═════════════════════════════════════════════════════════════════════════════

def _shuffle_segment(o, h, l, c, v, mins, rng):
    """Shuffle quarter-hour blocks of per-bar log-return tuples inside one
    segment. Bar 0 is fully anchored. Volumes travel with their tuples.
    Segment net log-return is invariant -> segment-end close preserved."""
    n = len(o)
    if n < 3:
        return o, h, l, c, v
    prev_c = c[:-1]
    ro = np.log(o[1:] / prev_c)          # gap into bar (vs prior close)
    rh = np.log(h[1:] / o[1:])           # intrabar shape
    rl = np.log(l[1:] / o[1:])
    rc = np.log(c[1:] / o[1:])
    vv = v[1:].copy()
    q = mins[1:] // 15                   # quarter-hour block id
    cuts = np.flatnonzero(np.diff(q) != 0) + 1
    groups = np.split(np.arange(n - 1), cuts)
    if len(groups) > 1:
        order = rng.permutation(len(groups))
        seq = np.concatenate([groups[i] for i in order])
    else:
        seq = np.arange(n - 1)
    ro, rh, rl, rc, vv = ro[seq], rh[seq], rl[seq], rc[seq], vv[seq]
    lc = np.log(c[0]) + np.cumsum(ro + rc)
    lo = lc - rc
    o2 = np.exp(lo); c2 = np.exp(lc)
    h2 = np.exp(lo + rh); l2 = np.exp(lo + rl)
    q4 = lambda x: np.round(x * 4.0) / 4.0        # NQ tick quantization
    o2, c2, h2, l2 = q4(o2), q4(c2), q4(h2), q4(l2)
    h2 = np.maximum(h2, np.maximum(o2, c2))
    l2 = np.minimum(l2, np.minimum(o2, c2))
    on, hn, ln_, cn, vn = o.copy(), h.copy(), l.copy(), c.copy(), v.copy()
    on[1:], hn[1:], ln_[1:], cn[1:], vn[1:] = o2, h2, l2, c2, vv
    return on, hn, ln_, cn, vn


def make_synthetic(a, seed):
    """Return a new array-dict: per day, shuffle RTH [9:30..16:00] and evening
    [18:00..21:00] segments independently; 16:01-16:59 untouched (feeds only
    the Asia 16:59 reference close, which stays real)."""
    rng = np.random.default_rng(seed)
    o, h, l, c, v = (a[k].copy() for k in ("o", "h", "l", "c", "v"))
    mins = a["mins"]
    for ymd, s0, s1 in day_slices(a):
        m = mins[s0:s1]
        for lo_m, hi_m in ((570, 960), (1080, 1260)):
            seg = np.flatnonzero((m >= lo_m) & (m <= hi_m)) + s0
            if len(seg) < 3:
                continue
            i0, i1 = seg[0], seg[-1] + 1     # contiguous within a day
            o[i0:i1], h[i0:i1], l[i0:i1], c[i0:i1], v[i0:i1] = _shuffle_segment(
                o[i0:i1], h[i0:i1], l[i0:i1], c[i0:i1], v[i0:i1],
                mins[i0:i1].astype(np.int64), rng)
    return {"ymd": a["ymd"], "mins": mins, "o": o, "h": h, "l": l, "c": c, "v": v}


# ═════════════════════════════════════════════════════════════════════════════
# 2. Pipeline replication (engine morning + day-sims + composition)
# ═════════════════════════════════════════════════════════════════════════════

def set_live_config():
    """Assert the exact v12-stream configuration (idempotent; called at the
    start of EVERY task so grid mutations never leak)."""
    config.TELEGRAM_ALERTS_ENABLED = False
    config.DISCORD_ALERTS_ENABLED  = False
    config.SKIP_FRIDAYS            = False
    config.PYRAMIDING_ENABLED      = False
    config.PARTIAL_EXIT_ENABLED    = False
    config.EVAL_MODE               = False
    config.ORB_FIXED_STOP_POINTS      = LIVE_STOP
    config.ORB_FUNDED_RR_TARGET       = LIVE_RR
    config.CONFIDENCE_SCORE_SKIP_BELOW = LIVE_SKIP


def build_engine_bars(a):
    """Bar dicts for the Backtester: minutes [9:30..16:00] u [18:00..21:00]."""
    mask = (a["mins"] <= 960) | (a["mins"] >= 1080)
    ymd = a["ymd"][mask].tolist()
    mins = a["mins"][mask].tolist()
    o, h, l, c, v = (a[k][mask].tolist() for k in ("o", "h", "l", "c", "v"))
    bars, cache_ymd, cache_dt = [], None, None
    for i in range(len(ymd)):
        yi = ymd[i]
        if yi != cache_ymd:
            cache_ymd = yi
            cache_dt = datetime(yi // 10000, (yi // 100) % 100, yi % 100)
        m = mins[i]
        bars.append({
            "timestamp": cache_dt.replace(hour=m // 60, minute=m % 60),
            "open": o[i], "high": h[i], "low": l[i], "close": c[i],
            "volume": v[i],
        })
    return bars


def build_day_lists(a):
    """(rth_days, eve_days) matching vwap_fulldata.load_days / pp.load_days(16,21):
    rth = 9:30-15:59 bars (rejection/pm sims ignore <9:30 anyway),
    eve = 16:00-20:59 bars."""
    rth, eve = defaultdict(list), defaultdict(list)
    ymd, mins = a["ymd"].tolist(), a["mins"].tolist()
    h_, l_, c_, v_ = (a[k].tolist() for k in ("h", "l", "c", "v"))
    cache_ymd, cache_d = None, None
    for i in range(len(ymd)):
        m = mins[i]
        if m >= 1260:            # exclude 21:00 bar (pp.load_days is hour<21)
            continue
        yi = ymd[i]
        if yi != cache_ymd:
            cache_ymd, cache_d = yi, _date_of(yi)
        rec = {"t": dt_time(m // 60, m % 60),
               "h": h_[i], "l": l_[i], "c": c_[i], "v": v_[i]}
        if 570 <= m <= 959:
            rth[cache_d].append(rec)
        elif 960 <= m <= 1259:
            eve[cache_d].append(rec)
    return rth, eve


def run_morning_chained(engine_bars):
    """Chained-equivalent of pp.run_year_morning over all years:
    one pass, fresh bankroll per year, market state carried exactly the way
    the canonical warmup produces it (regime window = bounded 14-day deque,
    matching the 2026-07-03 ATR fix in portfolio_policy)."""
    by_year = defaultdict(list)
    for b in engine_bars:
        by_year[b["timestamp"].year].append(b)
    carried = {"lc": None, "rng": [], "ovh": [], "pdm": None}
    out = []
    for y in sorted(by_year):
        bt = pp.TimedBacktester()
        bt._last_close = carried["lc"]
        # canonical warmup hands over a bounded deque(maxlen=REGIME_ATR_PERIOD)
        bt.regime.daily_ranges = deque(carried["rng"],
                                       maxlen=config.REGIME_ATR_PERIOD)
        bt.or_volume_history   = list(carried["ovh"])
        bt.prev_day_mode       = carried["pdm"]
        bt.run(by_year[y], silent=True)
        out.extend(t for t in bt.bank.trade_log if t.get("mode") == "breakout")
        carried = {"lc": bt._last_close, "rng": list(bt.regime.daily_ranges),
                   "ovh": list(bt.or_volume_history), "pdm": bt.prev_day_mode}
    return out


def components_from(morning_trades, rth_days, eve_days):
    """Replicates eval_boost.build_components() (ORB3 branch only) exactly."""
    comp = {"ORB3": defaultdict(list), "REJ": defaultdict(list),
            "PM": defaultdict(list), "ASIA": defaultdict(list)}
    for t in morning_trades:
        d = date.fromisoformat(t["date"])
        e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
        x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
        cc = max(1, t.get("contracts", 1))
        comp["ORB3"][d].append(("ORB", e, x, t["pnl"] / cc))
    for d in sorted(rth_days):
        wd, mo = d.weekday(), d.month
        if wd != 0 and mo not in (4, 5, 6, 9, 12):
            r = pp.rejection_day(rth_days[d])
            if r:
                comp["REJ"][d].append(("REJ", *r))
        if wd not in (0, 4):
            r = pp.pm_day(rth_days[d])
            if r:
                comp["PM"][d].append(("PM", *r))
    for d in sorted(eve_days):
        if d.weekday() != 3 and d.month not in (8, 11):
            r = pp.asia_day(eve_days[d])
            if r:
                comp["ASIA"][d].append(("ASIA", *r))
    return comp


def compose(comp):
    """Verbatim empire_rulemap.build_seq day_pnl rules, plus accepted-trade
    capture. Returns ({date: day_pnl}, [(leg, date, pnl), ...])."""
    all_days = sorted(set().union(*[set(comp[k])
                                    for k in ("ORB3", "REJ", "PM", "ASIA")]))
    daily, accepted = {}, []
    for d in all_days:
        lst = []
        for k in ("ORB3", "REJ", "PM", "ASIA"):
            key = "ORB" if k == "ORB3" else k
            lst.extend((key, *t[1:]) for t in comp[k].get(d, []))
        lst.sort(key=lambda x: x[1])
        pnl_day = morning = 0.0
        has_orb = any(s == "ORB" for s, *_ in lst)
        open_until = None
        for strat, e_t, x_t, p in lst:
            if strat == "REJ" and has_orb:
                continue
            if strat == "PM" and has_orb and morning < 0:
                continue
            if open_until is not None and e_t < open_until:
                continue
            if pnl_day <= -DLL:
                continue
            if (1150 + pnl_day) < RISK1[strat]:
                continue
            pnl_day += p
            if strat == "ORB":
                morning += p
            open_until = x_t
            accepted.append((strat, d, p))
        daily[d] = pnl_day
    return daily, accepted


def run_full_pipeline(a):
    """arrays -> (daily dict, accepted trades). Uses live config."""
    set_live_config()
    engine_bars = build_engine_bars(a)
    trades = run_morning_chained(engine_bars)
    del engine_bars
    rth, eve = build_day_lists(a)
    comp = components_from(trades, rth, eve)
    return compose(comp)


# ═════════════════════════════════════════════════════════════════════════════
# 3. Worker plumbing (multiprocessing, spawn-safe)
# ═════════════════════════════════════════════════════════════════════════════

_G = {}

def _winit(npz_path):
    z = np.load(npz_path)
    _G["arr"] = {k: z[k] for k in z.files}

def _task_synth(seed):
    t0 = _clock.time()
    syn = make_synthetic(_G["arr"], seed)
    daily, accepted = run_full_pipeline(syn)
    return seed, daily, accepted, _clock.time() - t0

def _task_grid(args):
    stop, rr, skip = args
    set_live_config()
    config.ORB_FIXED_STOP_POINTS       = stop
    config.ORB_FUNDED_RR_TARGET        = rr
    config.CONFIDENCE_SCORE_SKIP_BELOW = skip
    if _G.get("engine_bars") is None:
        _G["engine_bars"] = build_engine_bars(_G["arr"])
    trades = run_morning_chained(_G["engine_bars"])
    daily = defaultdict(float)
    for t in trades:
        cc = max(1, t.get("contracts", 1))
        daily[t["date"]] += t["pnl"] / cc          # per-contract, like the stream
    return (stop, rr, skip), dict(daily)


# ═════════════════════════════════════════════════════════════════════════════
# 4. Statistics
# ═════════════════════════════════════════════════════════════════════════════

def pf(pnls):
    w = sum(p for p in pnls if p > 0)
    lo = -sum(p for p in pnls if p <= 0)
    return (w / lo) if lo > 0 else float("inf")


def sb_indices(n, B, rng, mean_block=10.0, chunk=2000):
    """Yield stationary-bootstrap index matrices (chunk, n), circular."""
    p = 1.0 / mean_block
    ar = np.arange(n)
    done = 0
    while done < B:
        b = min(chunk, B - done)
        restart = rng.random((b, n)) < p
        restart[:, 0] = True
        spos = np.where(restart, ar[None, :], 0)
        spos = np.maximum.accumulate(spos, axis=1)
        starts = rng.integers(0, n, size=(b, n))
        sval = np.take_along_axis(starts, spos, axis=1)
        idx = (sval + (ar[None, :] - spos)) % n
        yield idx
        done += b


def boot_pval_mean(x, B, rng, mean_block=10.0):
    """P(mean* >= mean_obs) under the centered (mean-zero) null."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 8:
        return float("nan"), (float(x.mean()) if n else float("nan"))
    mu = x.mean()
    xc = x - mu
    ge = 0
    for idx in sb_indices(n, B, rng, mean_block):
        ge += int((xc[idx].mean(axis=1) >= mu).sum())
    return (1 + ge) / (B + 1), mu


def boot_sharpe(x, B, rng, mean_block=10.0):
    """Annualized Sharpe of daily series x + bootstrap CI + p(Sharpe<=0)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    sd = x.std(ddof=1)
    obs = (x.mean() / sd * np.sqrt(252.0)) if sd > 0 else float("nan")
    sh = []
    for idx in sb_indices(n, B, rng, mean_block):
        xs = x[idx]
        m = xs.mean(axis=1)
        s = xs.std(axis=1, ddof=1)
        ok = s > 0
        sh.append(np.where(ok, m / np.where(ok, s, 1.0) * np.sqrt(252.0), np.nan))
    sh = np.concatenate(sh)
    sh = sh[np.isfinite(sh)]
    lo, hi = np.percentile(sh, [2.5, 97.5])
    p_le0 = (1 + int((sh <= 0).sum())) / (len(sh) + 1)
    return obs, lo, hi, p_le0


def rc_lite(D, live_col, B, rng, mean_block=10.0):
    """White's-RC-style max-statistic test. D: (n_days, K) daily P&L matrix
    (zero-filled). Null: each column demeaned. p = P(max_k mean*_k >= obs_live)."""
    n, K = D.shape
    obs_live = D[:, live_col].mean()
    Dc = D - D.mean(axis=0, keepdims=True)
    ge = 0
    tot = 0
    for idx in sb_indices(n, B, rng, mean_block, chunk=1000):
        sample = Dc[idx]                       # (b, n, K)
        mx = sample.mean(axis=1).max(axis=1)   # (b,)
        ge += int((mx >= obs_live).sum())
        tot += len(mx)
    return (1 + ge) / (tot + 1), obs_live


def synth_percentile(real, synth_vals):
    s = np.asarray([v for v in synth_vals if np.isfinite(v)], dtype=float)
    if len(s) == 0:
        return float("nan")
    return 100.0 * ((s < real).sum() + 0.5 * (s == real).sum()) / len(s)


# ═════════════════════════════════════════════════════════════════════════════
# 5. Main
# ═════════════════════════════════════════════════════════════════════════════

def _fmt_p(p):
    if not np.isfinite(p):
        return "  n/a"
    return f"{p:.4f}" if p >= 0.0001 else f"{p:.1e}"


def leg_daily(accepted, leg, lo=None, hi=None):
    d = defaultdict(float)
    for s, dd, p in accepted:
        if s == leg and (lo is None or dd >= lo) and (hi is None or dd < hi):
            d[dd] += p
    ks = sorted(d)
    return np.array([d[k] for k in ks]), ks


def main():
    smoke = "--smoke" in sys.argv
    n_worlds = 2 if smoke else len(SYN_SEEDS)
    B = 500 if smoke else 10000
    workers = int(os.environ.get("RC_WORKERS", "4"))
    t_start = _clock.time()
    print(f"REALITY CHECK — v12 stack statistical audit"
          f" ({'SMOKE' if smoke else 'FULL'} mode, {workers} workers, B={B},"
          f" worlds={n_worlds})", flush=True)
    print(f"  seeds: synthetic {SYN_SEEDS[:n_worlds]}, bootstrap {BOOT_SEED},"
          f" RC {RC_SEED}", flush=True)

    # ── PHASE 0: data ────────────────────────────────────────────────────────
    print("\n[P0] Loading nq_full.csv into window arrays...", flush=True)
    a = load_arrays()
    os.makedirs(SCRATCH, exist_ok=True)
    np.savez(NPZ_PATH, **a)
    n_days_all = len(np.unique(a["ymd"]))
    print(f"  {len(a['ymd']):,} window bars, {n_days_all} session days"
          f" ({_date_of(int(a['ymd'][0]))} .. {_date_of(int(a['ymd'][-1]))})"
          f"  [{_clock.time()-t_start:.0f}s]", flush=True)

    # ── PHASE 1: validation of the replication ──────────────────────────────
    set_live_config()
    engine_bars = build_engine_bars(a)
    chained = run_morning_chained(engine_bars)
    canon, engine_ok = None, None
    if not smoke:
        print("\n[P1] VALIDATION — chained engine vs canonical"
              " pp.run_year_morning ...", flush=True)
        set_live_config()
        t0 = _clock.time()
        bars_full = pp.load_csv(pp.DATA)       # canonical path, full CSV
        canon = []
        for y in pp.YEARS:
            canon.extend(pp.run_year_morning(bars_full, y))
            print(f"    canonical {y}: cumulative {len(canon)} trades"
                  f" [{_clock.time()-t0:.0f}s]", flush=True)
        del bars_full
        key = lambda t: (t["date"], t.get("entry_time"), t.get("exit_time"),
                         t.get("contracts"), round(t["pnl"], 2))
        ck, hk = sorted(map(key, canon)), sorted(map(key, chained))
        if ck == hk:
            print(f"  MORNING ENGINE MATCH: {len(canon)} trades identical"
                  f" (chained == canonical). Chained runner is trusted.",
                  flush=True)
            engine_ok = True
        else:
            only_c = set(ck) - set(hk); only_h = set(hk) - set(ck)
            print(f"  !! MORNING MISMATCH: canonical {len(ck)} vs chained"
                  f" {len(hk)} | only-canonical {len(only_c)} only-chained"
                  f" {len(only_h)}", flush=True)
            for x in list(only_c)[:5]: print(f"     canon-only: {x}", flush=True)
            for x in list(only_h)[:5]: print(f"     chain-only: {x}", flush=True)
            engine_ok = False
    else:
        print("\n[P1] VALIDATION — smoke mode: canonical engine comparison"
              " skipped (cache check below still validates end-to-end)",
              flush=True)

    # real components + composed stream
    rth, eve = build_day_lists(a)
    morning_src = canon if engine_ok is False else chained
    comp_real = components_from(morning_src, rth, eve)
    daily_real, accepted_real = compose(comp_real)

    # cache comparison
    cache = {}
    with open(CACHE_CSV) as f:
        for row in csv.DictReader(f):
            cache[date.fromisoformat(row["date"])] = float(row["pnl"])
    common = sorted(set(cache) & set(daily_real))
    diffs = [(d, cache[d], daily_real[d]) for d in common
             if abs(cache[d] - daily_real[d]) > 0.011]
    extra_rebuilt = sorted(set(daily_real) - set(cache))
    missing = sorted(set(cache) - set(daily_real))
    print(f"  CACHE CHECK vs data/v12_daily_stream.csv: {len(common)} common"
          f" days, {len(diffs)} mismatches, rebuilt-extra {len(extra_rebuilt)},"
          f" cache-only {len(missing)}", flush=True)
    cache_ok = (len(diffs) == 0 and len(missing) == 0)
    if not cache_ok:
        for d, cv, rv in diffs[:8]:
            print(f"     {d}: cache {cv:+.1f} vs rebuilt {rv:+.1f}", flush=True)
        if missing[:5]:
            print(f"     cache-only days: {missing[:5]}", flush=True)
        print("  -> falling back to snapshot build_components() for canonical"
              " components (slow, ~8 min)...", flush=True)
        comp_c = eb.build_components()
        comp_real = {k: comp_c[k] for k in ("ORB3", "REJ", "PM", "ASIA")}
        daily_real, accepted_real = compose(comp_real)
        diffs2 = [(d, cache[d], daily_real.get(d)) for d in cache
                  if abs(cache[d] - daily_real.get(d, float("nan"))) > 0.011]
        print(f"     snapshot-canonical rebuild: {len(diffs2)} mismatches vs"
              f" cache", flush=True)
    else:
        print("  STREAM MATCH: rebuilt composed stream reproduces the cached"
              " v12 stream exactly on all cached days"
              + (f" (+{len(extra_rebuilt)} newer days in rebuild)" if extra_rebuilt else ""),
              flush=True)

    # real stack stats
    real_trades = [(s, d, p) for s, d, p in accepted_real]
    real_pf_all = pf([p for _, _, p in real_trades])
    oos_trades = [(s, d, p) for s, d, p in real_trades if d >= OOS_START]
    real_pf_oos = pf([p for _, _, p in oos_trades])
    real_leg_pf_oos = {leg: pf([p for s, d, p in oos_trades if s == leg] or [0.0])
                       for leg in LEGS}
    real_leg_pf_all = {leg: pf([p for s, d, p in real_trades if s == leg] or [0.0])
                       for leg in LEGS}
    print(f"  REAL stack: N={len(real_trades)} trades, PF(full)="
          f"{real_pf_all:.3f}, PF(OOS>=2025)={real_pf_oos:.3f}, "
          f"net(full)=${sum(p for _,_,p in real_trades):+,.0f}", flush=True)
    for leg in LEGS:
        n_o = sum(1 for s, d, _ in oos_trades if s == leg)
        n_a = sum(1 for s, _, _ in real_trades if s == leg)
        print(f"    {leg:<4} N_full={n_a:>3} PF_full={real_leg_pf_all[leg]:>6.3f}"
              f" | N_oos={n_o:>3} PF_oos={real_leg_pf_oos[leg]:>6.3f}", flush=True)

    # ── PHASE 2: synthetic worlds ────────────────────────────────────────────
    print(f"\n[P2] SYNTHETIC PERMUTATION TEST — {n_worlds} shuffled worlds"
          f" (15-min block shuffle within RTH/evening segments)...", flush=True)
    from concurrent.futures import ProcessPoolExecutor
    syn_results = []
    seeds = SYN_SEEDS[:n_worlds]
    with ProcessPoolExecutor(max_workers=workers, initializer=_winit,
                             initargs=(NPZ_PATH,)) as ex:
        for seed, daily, accepted, dt in ex.map(_task_synth, seeds):
            spf_all = pf([p for _, _, p in accepted])
            s_oos = [(s, d, p) for s, d, p in accepted if d >= OOS_START]
            spf_oos = pf([p for _, _, p in s_oos])
            net = sum(p for _, _, p in accepted)
            leg_pf = {leg: pf([p for s, d, p in accepted if s == leg] or [0.0])
                      for leg in LEGS}
            leg_n = {leg: sum(1 for s, _, _ in accepted if s == leg)
                     for leg in LEGS}
            syn_results.append(dict(seed=seed, pf=spf_all, pf_oos=spf_oos,
                                    net=net, n=len(accepted),
                                    leg_pf=leg_pf, leg_n=leg_n, daily=daily))
            print(f"    world seed={seed}: N={len(accepted):>4} "
                  f"PF={spf_all:5.3f} PF_oos={spf_oos:5.3f} net=${net:>+9,.0f}"
                  f"  [{dt:.0f}s]", flush=True)

    syn_pfs = [r["pf"] for r in syn_results]
    stack_pct = synth_percentile(real_pf_all, syn_pfs)
    stack_pct_oos = synth_percentile(real_pf_oos, [r["pf_oos"] for r in syn_results])
    leg_pct = {leg: synth_percentile(real_leg_pf_all[leg],
                                     [r["leg_pf"][leg] for r in syn_results])
               for leg in LEGS}
    print(f"  synthetic stack PF: min={min(syn_pfs):.3f} "
          f"median={np.median(syn_pfs):.3f} max={max(syn_pfs):.3f} | "
          f"REAL PF {real_pf_all:.3f} -> empirical percentile {stack_pct:.0f}"
          f" (OOS-PF percentile {stack_pct_oos:.0f})", flush=True)
    hot = [r for r in syn_results if r["pf"] > 1.15]
    if hot:
        print(f"  !! {len(hot)} synthetic world(s) print PF > 1.15 — possible"
              f" artifact; inspect per-leg PFs below:", flush=True)
        for r in hot:
            print(f"     seed={r['seed']}: " + "  ".join(
                f"{leg} PF={r['leg_pf'][leg]:.2f}(n={r['leg_n'][leg]})"
                for leg in LEGS), flush=True)

    # ── PHASE 3: stationary bootstrap ────────────────────────────────────────
    print(f"\n[P3] STATIONARY BOOTSTRAP (Politis-Romano, mean block 10 days,"
          f" B={B})...", flush=True)
    rng = np.random.default_rng(BOOT_SEED)
    stack_days = sorted(daily_real)
    stack_x = np.array([daily_real[d] for d in stack_days])
    oos_mask = np.array([d >= OOS_START for d in stack_days])
    p_stack_full, mu_f = boot_pval_mean(stack_x, B, rng)
    p_stack_oos, mu_o = boot_pval_mean(stack_x[oos_mask], B, rng)
    print(f"  STACK: full mean ${mu_f:+.0f}/day p={_fmt_p(p_stack_full)} "
          f"(n={len(stack_x)}) | OOS mean ${mu_o:+.0f}/day"
          f" p={_fmt_p(p_stack_oos)} (n={int(oos_mask.sum())})", flush=True)

    leg_p = {}
    for leg in LEGS:
        xf, _ = leg_daily(accepted_real, leg)
        xo, _ = leg_daily(accepted_real, leg, lo=OOS_START)
        pf_, mf = boot_pval_mean(xf, B, rng)
        po_, mo = boot_pval_mean(xo, B, rng)
        leg_p[leg] = dict(p_full=pf_, p_oos=po_, n_full=len(xf), n_oos=len(xo),
                          mu_full=mf, mu_oos=mo)
        print(f"  {leg:<4}: full ${mf:+7.0f}/td p={_fmt_p(pf_)} (n={len(xf):>3})"
              f" | OOS ${mo:+7.0f}/td p={_fmt_p(po_)} (n={len(xo):>3})", flush=True)

    # OOS Sharpe on zero-filled calendar of all OOS RTH trading days
    rth_dates = sorted(d for d in rth if d >= OOS_START and d.weekday() < 5)
    z = np.array([daily_real.get(d, 0.0) for d in rth_dates])
    sh, sh_lo, sh_hi, p_sh = boot_sharpe(z, B, rng)
    print(f"  OOS Sharpe (zero-filled, {len(z)} days): {sh:.2f} "
          f"[95% CI {sh_lo:.2f} .. {sh_hi:.2f}], P(Sharpe<=0)={_fmt_p(p_sh)}",
          flush=True)

    # ── PHASE 4: selection-bias deflation ────────────────────────────────────
    print(f"\n[P4] RC-LITE — morning-ORB family "
          f"{len(GRID_STOPS)}x{len(GRID_RRS)}x{len(GRID_SKIPS)} "
          f"(stop x targetRR x conf-threshold), live=({LIVE_STOP:.0f},"
          f"{LIVE_RR},{LIVE_SKIP})...", flush=True)
    grid = [(s, r, k) for s in GRID_STOPS for r in GRID_RRS for k in GRID_SKIPS]
    if smoke:
        grid = [(LIVE_STOP, LIVE_RR, LIVE_SKIP), (17.0, 2.5, 2), (27.0, 3.5, 4)]
    grid_daily = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_winit,
                             initargs=(NPZ_PATH,)) as ex:
        done = 0
        for cfg, daily in ex.map(_task_grid, grid):
            grid_daily[cfg] = daily
            done += 1
            if done % 6 == 0 or done == len(grid):
                print(f"    grid {done}/{len(grid)} configs done"
                      f" [{_clock.time()-t_start:.0f}s]", flush=True)

    oos_cal = rth_dates                       # zero-filled OOS calendar
    K = len(grid)
    D = np.zeros((len(oos_cal), K))
    for j, cfg in enumerate(grid):
        dd = grid_daily[cfg]
        # grid task keys dates as ISO strings (from trade dicts)
        for i, d in enumerate(oos_cal):
            D[i, j] = dd.get(str(d), 0.0)
    live_col = grid.index((LIVE_STOP, LIVE_RR, LIVE_SKIP))
    rng_rc = np.random.default_rng(RC_SEED)
    p_rc, mu_live = rc_lite(D, live_col, B, rng_rc)
    col_means = D.mean(axis=0)
    rank = int((col_means > col_means[live_col]).sum()) + 1
    # naive (un-deflated) p for the live column alone, same engine
    p_naive_live, _ = boot_pval_mean(D[:, live_col], B,
                                     np.random.default_rng(RC_SEED + 1))
    print(f"  live morning config OOS: mean ${mu_live:+.2f}/day over"
          f" {len(oos_cal)} days (rank {rank}/{K} in family; family mean-of-"
          f"means ${col_means.mean():+.2f})", flush=True)
    print(f"  naive OOS p (live alone): {_fmt_p(p_naive_live)}   |   "
          f"RC-lite max-of-family p: {_fmt_p(p_rc)}", flush=True)
    top = np.argsort(-col_means)[:5]
    for j in top:
        s, r, k = grid[j]
        print(f"    family top-5: stop={s:.0f} rr={r} skip>={k}: "
              f"${col_means[j]:+.2f}/day", flush=True)

    print("\n  BONFERRONI LAYER (verified search widths — lower bounds):",
          flush=True)
    for k_, (m_, src) in BONF.items():
        print(f"    {k_:<5} M={m_:<5} [{src}]", flush=True)

    # ── PHASE 5: audit cards ─────────────────────────────────────────────────
    print(f"\n{'='*84}\n  STATISTICAL AUDIT CARDS  (OOS = {OOS_START} onward;"
          f"  B={B}; {n_worlds} synthetic worlds)\n{'='*84}", flush=True)
    print("  verdict rule: SURVIVES  = raw OOS p<0.05 AND deflated p<0.05 AND"
          " synth pct>=90\n"
          "                MARGINAL  = raw OOS p<0.05 AND deflated p<0.25 (or"
          " raw p<0.10 AND synth pct>=75)\n"
          "                else INDISTINGUISHABLE-FROM-SELECTION\n"
          "  deflated p: ORB -> RC-lite max-of-family p;"
          " REJ/PM/ASIA/STACK -> min(1, M x raw OOS p)", flush=True)

    def verdict(p_raw, p_adj, pct):
        if np.isfinite(p_raw) and p_raw < 0.05 and p_adj < 0.05 and pct >= 90:
            return "SURVIVES"
        if (np.isfinite(p_raw) and p_raw < 0.05 and p_adj < 0.25) or \
           (np.isfinite(p_raw) and p_raw < 0.10 and pct >= 75):
            return "MARGINAL"
        return "INDISTINGUISHABLE-FROM-SELECTION"

    cards = {}
    for leg in LEGS:
        p_raw = leg_p[leg]["p_oos"]
        if leg == "ORB":
            p_adj, adj_src = p_rc, "RC-lite"
        else:
            p_adj = min(1.0, BONF[leg][0] * p_raw) if np.isfinite(p_raw) else 1.0
            adj_src = f"Bonf x{BONF[leg][0]}"
        v = verdict(p_raw, p_adj, leg_pct[leg])
        cards[leg] = (p_raw, p_adj, adj_src, leg_pct[leg], v)
    p_stack_adj = min(1.0, BONF["STACK"][0] * p_stack_oos)
    v_stack = verdict(p_stack_oos, p_stack_adj, stack_pct)

    hdr = (f"  {'leg':<6}{'d_oos':>6}{'mu_oos/td':>11}{'OOS boot p':>12}"
           f"{'synth pct':>11}{'deflated p':>18}  verdict"
           f"      (d_oos = OOS trade-DAYS)")
    print(hdr + "\n  " + "-" * 88, flush=True)
    for leg in LEGS:
        p_raw, p_adj, adj_src, pct, v = cards[leg]
        print(f"  {leg:<6}{leg_p[leg]['n_oos']:>6}"
              f"{leg_p[leg]['mu_oos']:>+11.0f}{_fmt_p(p_raw):>12}"
              f"{pct:>10.0f}%{_fmt_p(p_adj):>12} {adj_src:<11} {v}", flush=True)
    print(f"  {'STACK':<6}{int(oos_mask.sum()):>6}{mu_o:>+11.0f}"
          f"{_fmt_p(p_stack_oos):>12}{stack_pct:>10.0f}%"
          f"{_fmt_p(p_stack_adj):>12} Bonf x{BONF['STACK'][0]:<5} {v_stack}",
          flush=True)

    # ── Look-ahead / protocol-defect register ────────────────────────────────
    print(f"\n  DEFECT REGISTER (verified by reading the code):", flush=True)
    print("  [ENGINE] No same-bar entry/exit leak found: entries fill at the"
          " signal bar's CLOSE,\n"
          "           exits are evaluated from the NEXT bar onward; when stop"
          " and target are both\n"
          "           touched within one bar, the STOP is taken first"
          " (conservative). Target fills\n"
          "           assume touch=fill (no queue) — mild optimism, standard.",
          flush=True)
    print("  [LEDGER] build_seq books a trade's full P&L at ENTRY time, but"
          " the one-position rule\n"
          "           (open_until) means every accepted entry occurs after all"
          " prior exits, so the\n"
          "           DLL/morning-loss gates never act on unrealized future"
          " P&L. Checked: no leak.", flush=True)
    print("  [PROTOCOL] House law (select on IS 2022-24, confirm OOS) was"
          " violated for several live\n"
          "           knobs — config.py justifies them by OOS performance,"
          " so the OOS window is\n"
          "           partially a selection surface (this is why the"
          " Bonferroni layer exists):", flush=True)
    for line, txt in [
        (54,  "SKIP_FRIDAYS: 'skip improves OOS PF 3.36->4.94' (morning; OFF"
              " in v12 stream but OOS-chosen)"),
        (96,  "PM_ORB_RR_TARGET=2.5: 'beats 2R on ... OOS PF 1.36v1.35, OOS"
              " +$1,520'"),
        (235, "CONFIDENCE_SCORE_SKIP_BELOW=3: 'PF jumps from 2.48 -> 3.31"
              " OOS' (ACTIVE in stream)"),
        (242, "ORB_SKIP_FIRST_BAR=True: '9:45 entries ... OOS PF 0.839'"
              " (ACTIVE in stream)"),
        (255, "ASIA_RR_TARGET=3.0: 'OOS $8,708 vs $5,033 at old 15/1.5'"
              " (ACTIVE in stream)"),
        (256, "ASIA_SKIP_THURSDAYS: 'Thu OOS PF 0.82' (ACTIVE in stream)"),
    ]:
        print(f"           config.py:{line} — {txt}", flush=True)
    print("           Also: REJ weak-month set {4,5,6,9,12} and PM/ASIA"
          " day-of-week skips were\n"
          "           validated on month/DOW tables computed over ALL years"
          " including OOS\n"
          "           (vwap_fulldata.py S2, pm_orb_dow.py, strategy_asia.py"
          " docstring).", flush=True)

    print(f"\n  SUMMARY LINE:", flush=True)
    print(f"  REALITY CHECK: stack synthetic percentile {stack_pct:.0f};"
          f" OOS bootstrap p={_fmt_p(p_stack_oos)}; deflation verdicts: "
          + ", ".join(f"{leg}: {cards[leg][4]}" for leg in LEGS)
          + f", STACK: {v_stack}", flush=True)
    eq = {True: "PASS", False: "FAIL", None: "SKIPPED(smoke)"}[engine_ok]
    print(f"\n  engine-equivalence: {eq} |"
          f" cache-match: {'PASS' if cache_ok else 'FAIL'} |"
          f" runtime {(_clock.time()-t_start)/60:.1f} min", flush=True)
    print("  reality_check done.", flush=True)


if __name__ == "__main__":
    main()
