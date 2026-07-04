"""
brain/research/index_structures.py

MEAN-REVERSION / AUCTION / CLOSE-DRIVEN structures on ES / RTY / NQ.
A different family from everything the desk has tested (all breakout/continuation).

Families:
  F1  Opening gap FADE at 9:30    — fade toward prior RTH close, bucketed by
                                    gap size as % of 14-day avg RTH range.
  F2  Lunch reversion 12:00-13:30 — fade the morning extreme / stretch from the
                                    11:30 level, target back to lunch level/mid.
  F3  Close-hour 15:00-15:55      — (a) trend-day continuation on 15:00+ break
                                    of the 13:00-15:00 range in day direction;
                                    (b) MOC fade of stretched days. Flat 15:58.
  F4  First-hour extreme sweep-fail — after 10:30, break of 9:30-10:30 H/L that
                                    closes back inside within 3 bars -> fade to
                                    the first-hour midpoint. (INTRADAY level —
                                    distinct from the killed PDH/PDL daily one.)
  F5  Prior-day-close magnet      — small-gap days: fade stretches away from
                                    prior close before 11:00, target prior close.

HOUSE LAW: IS 2022-2024, OOS 2025-2026. Configs picked on IS only, OOS revealed
after. N >= ~80 full period. Year-by-year coherence required. Kill tests:
double slippage, drop-best-year, neighbor-config surface.

COSTS (per round turn, 1 contract, applied to every trade):
  NQ : $20/pt, tick 0.25=$5 ;  $3.10 comm + 2 ticks slip = $13.10  (dbl $23.10)
  ES : $50/pt, tick 0.25=$12.50; $3.10 comm + 2 ticks slip = $28.10 (dbl $53.10)
  RTY: $50/pt, tick 0.10=$5 ;  $3.10 comm + 2 ticks slip = $13.10  (dbl $23.10)
  RTY specs verified from data (min close increment 0.10) + CME E-mini R2K.

Conservative fills: entries at signal-bar close (gap fade: 9:30 bar open);
if stop & target both touched in one bar -> STOP first; time exits at the open
of the first bar past the exit minute.

Run:  python3 brain/research/index_structures.py          (stage 1: grids + kill tests)
      python3 brain/research/index_structures.py deep     (stage 2: surfaces, sides, risk caps)
      python3 brain/research/index_structures.py stage3   (stage 3: bootstrap, delay tests)
Data: data/nq_full.csv, data/es_1min.csv, data/rty_1min.csv (1-min, ET wall
      clock; tz suffixes stripped). RTY ends 2026-06-12 (shorter OOS — noted).

CAVEAT: continuous-contract files — quarterly roll can inject artificial
overnight gaps ~4x/yr. Top-gap days are printed for eyeball; days with
|gap| > 1.5 x avg14 range are excluded from gap-driven families (F1/F5).

════════════════════════════════════════════════════════════════════════════
RESULTS (2026-07-03) — 1 SURVIVOR / 14 of 15 family-instrument combos KILLED
════════════════════════════════════════════════════════════════════════════
F1 GAP FADE      KILLED x3. ES-small-gap passed stage-1 (IS 1.27/OOS 1.27) but
                 is a FILL ARTIFACT: entering 9:31 close instead of the 9:30
                 open print -> PF 1.02/1.01. Whole edge lives in the first-60s
                 auction print you cannot get filled at. Sub-slices era-
                 incoherent; bootstrap P(OOS<=1)=0.18. NQ pick died OOS 0.72;
                 RTY IS 1.12 underpowered.
F2 LUNCH REVERT  KILLED x3. No IS-viable config on NQ/ES (best IS PF 0.96/0.81).
                 Indices do not fade the morning extreme at lunch — they trend.
F3 CLOSE HOUR    KILLED x3. MOC-fade DOA (IS PF 0.59-0.84 everywhere).
                 Continuation/NQ th=0.5: OOS 1.47 but IS is 100%% 2022
                 (2023 PF 0.90, 2024 PF 0.50) — regime artifact, not structure.
                 The collision-free 15:00-15:55 window stays empty.
F4 FH SWEEP-FAIL KILLED x3. Uniformly negative (IS PF 0.71-0.91, all configs,
                 all instruments). Intraday first-hour sweep-reject fails just
                 like the desk's killed daily PDH/PDL version.
F5 PC MAGNET     NQ KILLED: IS profit = 2022 longs ($16.7k of $19.9k); shorts
                 flat/neg 3 straight IS years; DLL-fitting vol-capped form
                 decays to OOS 1.11 (2026: 1.03). RTY KILLED: IS 1.06.
                 ES SURVIVES (1-trade/day form):
                   entry: |open-prevclose| <= 15%% of 14d avg range; first bar
                   close 9:31-10:59 stretched >= 20%% of avg range from prior
                   close -> fade toward prior close; target = prior close;
                   stop = 10%% of avg range; flat 12:00; max 1 trade/day.
                   IS  PF 1.22  N186  $40/tr   | OOS PF 1.40  N87  $86/tr
                   2022 1.20 | 2023 1.26 | 2024 1.23 | 2025 1.41 | 2026 1.39
                   2x-slip OOS 1.27; next-bar-open entry unchanged (1.22/1.41);
                   bootstrap P(OOS<=1)=0.091; both sides + both eras;
                   worst day -$1,030 (fits $1,200 DLL @1c); maxDD $4,769;
                   corr vs v12 daily P&L -0.04; ~$3.3k/yr @1c. Caveats: z=0.15
                   neighbor IS-negative (low-side surface edge); P(IS<=1)=0.11;
                   entries 9:31-10:59 overlap v12's NQ AM window on margin only.
════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import math
import numpy as np
import pandas as pd
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPECS = {
    "NQ":  {"file": "nq_full.csv",  "pv": 20.0, "tick": 0.25},
    "ES":  {"file": "es_1min.csv",  "pv": 50.0, "tick": 0.25},
    "RTY": {"file": "rty_1min.csv", "pv": 50.0, "tick": 0.10},
}
for s in SPECS.values():
    s["cost"]  = 3.10 + 2 * s["tick"] * s["pv"]   # $3.10 RT comm + 1 tick/side slip
    s["cost2"] = 3.10 + 4 * s["tick"] * s["pv"]   # double-slippage kill test

IS_YEARS  = (2022, 2023, 2024)
OOS_YEARS = (2025, 2026)

# v12 live-book entry windows (minutes since midnight) for collision analysis
V12_WINDOWS = [(9*60+46, 10*60+30, "AM 9:46-10:30"),
               (11*60,   13*60,    "Lunch 11:00-13:00"),
               (13*60+15, 14*60,   "PM 13:15-14:00"),
               (18*60+10, 18*60+30, "Asia ~18:15")]

M0930, M1030, M1100, M1130, M1200, M1300, M1330, M1400, M1430, M1500 = \
    570, 630, 660, 690, 720, 780, 810, 840, 870, 900
M1545, M1555, M1600 = 945, 955, 960


# ──────────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────────

class Day:
    __slots__ = ("date", "m", "o", "h", "l", "c", "pc", "avg14", "year")
    def __init__(self, date, m, o, h, l, c):
        self.date, self.m, self.o, self.h, self.l, self.c = date, m, o, h, l, c
        self.pc = None      # prior RTH close
        self.avg14 = None   # mean of prior <=14 daily RTH ranges (>=5 required)
        self.year = date.year

    def idx(self, minute):
        return int(np.searchsorted(self.m, minute))


def load_days(sym):
    spec = SPECS[sym]
    path = os.path.join(BASE, "data", spec["file"])
    if not os.path.exists(path):
        print(f"  [{sym}] MISSING FILE {path} — skipping instrument")
        return []
    print(f"  [{sym}] loading {spec['file']} ...", flush=True)
    df = pd.read_csv(path, usecols=["timestamp", "open", "high", "low", "close"],
                     dtype={"timestamp": str})
    ts = df["timestamp"].str.slice(0, 19)
    dt = pd.to_datetime(ts, format="%Y-%m-%d %H:%M:%S")
    mins = dt.dt.hour.to_numpy() * 60 + dt.dt.minute.to_numpy()
    keep = (mins >= M0930) & (mins < M1600)
    df = df.loc[keep].copy()
    df["dt"] = dt[keep]
    df["m"] = mins[keep]
    df = df.drop_duplicates(subset="dt").sort_values("dt")
    df["d"] = df["dt"].dt.date

    days = []
    for d, g in df.groupby("d", sort=True):
        m = g["m"].to_numpy()
        if len(m) < 100 or m[0] > M0930 + 5:      # need a real open + full-ish session
            continue
        days.append(Day(d, m,
                        g["open"].to_numpy(float), g["high"].to_numpy(float),
                        g["low"].to_numpy(float), g["close"].to_numpy(float)))
    # link prior close + rolling 14d range
    ranges = []
    prev_close = None
    for day in days:
        day.pc = prev_close
        if len(ranges) >= 5:
            day.avg14 = float(np.mean(ranges[-14:]))
        prev_close = float(day.c[-1])
        ranges.append(float(day.h.max() - day.l.min()))
    days = [d for d in days if d.pc is not None and d.avg14 is not None]
    print(f"  [{sym}] {len(days)} RTH days  {days[0].date} → {days[-1].date}", flush=True)
    # eyeball roll contamination: top-5 |gap| days
    gaps = sorted(((abs(float(d.o[0]) - d.pc), d.date) for d in days), reverse=True)[:5]
    print(f"  [{sym}] top |open-prevclose| gaps: " +
          ", ".join(f"{dt}:{g:.1f}" for g, dt in gaps), flush=True)
    return days


# ──────────────────────────────────────────────────────────────────────────────
# Simulator + metrics
# ──────────────────────────────────────────────────────────────────────────────

def simulate(day, i_start, side, entry, stop, target, exit_min, check_first_bar):
    """Walk bars; conservative stop-first on ambiguous bars. side=+1 long,-1 short.
    target may be None (time/MOC exit only). Returns (exit_px, reason)."""
    h, l, o, c, m = day.h, day.l, day.o, day.c, day.m
    n = len(m)
    i = i_start if check_first_bar else i_start + 1
    while i < n:
        if m[i] >= exit_min:
            return float(o[i]), "time"
        if side < 0:
            hit_s = h[i] >= stop
            hit_t = target is not None and l[i] <= target
        else:
            hit_s = l[i] <= stop
            hit_t = target is not None and h[i] >= target
        if hit_s:
            return float(stop), "stop"           # stop-first (conservative)
        if hit_t:
            return float(target), "target"
        i += 1
    return float(c[-1]), "eod"


def trade(sym, day, i_start, side, entry, stop, target, exit_min, check_first_bar=False):
    px, reason = simulate(day, i_start, side, entry, stop, target, exit_min, check_first_bar)
    pts = (px - entry) * side
    spec = SPECS[sym]
    return {"date": day.date, "year": day.year, "m": int(day.m[min(i_start, len(day.m)-1)]),
            "side": side, "pts": pts, "net": pts * spec["pv"] - spec["cost"],
            "net2": pts * spec["pv"] - spec["cost2"], "reason": reason}


def pf(vals):
    w = sum(v for v in vals if v > 0)
    l = abs(sum(v for v in vals if v <= 0))
    return (w / l) if l > 0 else (99.0 if w > 0 else 0.0)


def metrics(trades, key="net"):
    if not trades:
        return {"n": 0, "pf": 0.0, "net": 0.0, "wr": 0.0, "avg": 0.0,
                "maxdd": 0.0, "worst_day": 0.0}
    v = [t[key] for t in trades]
    daily = defaultdict(float)
    for t in trades:
        daily[t["date"]] += t[key]
    eq = mx = dd = 0.0
    for d in sorted(daily):
        eq += daily[d]
        mx = max(mx, eq)
        dd = max(dd, mx - eq)
    return {"n": len(v), "pf": pf(v), "net": sum(v),
            "wr": 100.0 * sum(1 for x in v if x > 0) / len(v),
            "avg": sum(v) / len(v), "maxdd": dd,
            "worst_day": min(daily.values()) if daily else 0.0}


def split_eras(trades):
    is_t = [t for t in trades if t["year"] in IS_YEARS]
    oos_t = [t for t in trades if t["year"] in OOS_YEARS]
    return is_t, oos_t


def year_table(trades, key="net"):
    rows = []
    for y in (2022, 2023, 2024, 2025, 2026):
        yt = [t for t in trades if t["year"] == y]
        m = metrics(yt, key)
        rows.append((y, m))
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# F1 — Opening gap fade
# ──────────────────────────────────────────────────────────────────────────────

F1_BUCKETS = {"S": (0.05, 0.15), "M": (0.15, 0.40), "L": (0.40, 1.50), "ALL": (0.05, 1.50)}
F1_GRID = [{"stop_mult": sm, "target": tg, "texit": tx}
           for sm in (1.0, 2.0) for tg in ("full", "half") for tx in (M1030, M1130)]

def run_f1(sym, days, bucket, cfg):
    lo, hi = F1_BUCKETS[bucket]
    out = []
    for day in days:
        entry = float(day.o[0])
        gap = entry - day.pc
        gp = abs(gap) / day.avg14
        if not (lo <= gp < hi):
            continue
        side = -1 if gap > 0 else 1                      # fade toward prior close
        stop = entry - side * cfg["stop_mult"] * abs(gap)
        tgt = day.pc if cfg["target"] == "full" else entry + side * 0.5 * abs(gap)
        out.append(trade(sym, day, 0, side, entry, stop, tgt, cfg["texit"],
                         check_first_bar=True))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# F2 — Lunch reversion 12:00-13:30 (flat 14:00)
# ──────────────────────────────────────────────────────────────────────────────

F2_GRID = ([{"var": "edge", "sbuf": sb, "target": tg}
            for sb in (0.10, 0.20) for tg in ("mid", "l1130")] +
           [{"var": "stretch", "x": x} for x in (0.20, 0.30)])

def run_f2(sym, days, cfg):
    out = []
    for day in days:
        i_lunch, i_end = day.idx(M1200), day.idx(M1330)
        i_1130 = day.idx(M1130)
        if i_lunch <= 5 or i_1130 < 10 or i_lunch >= len(day.m):
            continue
        mh = float(day.h[:i_1130].max()); ml = float(day.l[:i_1130].min())
        mid = (mh + ml) / 2.0
        c1130 = float(day.c[i_1130 - 1])
        r = day.avg14
        done_s = done_l = False
        for i in range(i_lunch, min(i_end, len(day.m))):
            if cfg["var"] == "edge":
                if not done_s and day.h[i] >= mh:
                    done_s = True
                    e = float(day.c[i]); stop = mh + cfg["sbuf"] * r
                    tgt = mid if cfg["target"] == "mid" else c1130
                    if e < stop and e > tgt:
                        out.append(trade(sym, day, i, -1, e, stop, tgt, M1400))
                if not done_l and day.l[i] <= ml:
                    done_l = True
                    e = float(day.c[i]); stop = ml - cfg["sbuf"] * r
                    tgt = mid if cfg["target"] == "mid" else c1130
                    if e > stop and e < tgt:
                        out.append(trade(sym, day, i, 1, e, stop, tgt, M1400))
            else:
                st = float(day.c[i]) - c1130
                if not done_s and st >= cfg["x"] * r:
                    done_s = True
                    e = float(day.c[i])
                    out.append(trade(sym, day, i, -1, e, e + 0.5 * cfg["x"] * r,
                                     c1130, M1400))
                if not done_l and -st >= cfg["x"] * r:
                    done_l = True
                    e = float(day.c[i])
                    out.append(trade(sym, day, i, 1, e, e - 0.5 * cfg["x"] * r,
                                     c1130, M1400))
            if done_s and done_l:
                break
    return out


# ──────────────────────────────────────────────────────────────────────────────
# F3 — Close hour 15:00-15:55, flat by 15:58
# ──────────────────────────────────────────────────────────────────────────────

F3_GRID = ([{"var": "cont", "th": th, "sbuf": sb}
            for th in (0.3, 0.5) for sb in (0.10, 0.15)] +
           [{"var": "fade", "th": th, "sbuf": sb}
            for th in (0.5, 0.7) for sb in (0.15, 0.25)])

def run_f3(sym, days, cfg):
    out = []
    for day in days:
        i15 = day.idx(M1500)
        if i15 >= len(day.m) or day.m[-1] < M1555:      # half days out
            continue
        i13 = day.idx(M1300)
        if i15 - i13 < 60:
            continue
        pmh = float(day.h[i13:i15].max()); pml = float(day.l[i13:i15].min())
        o930 = float(day.o[0]); c1500 = float(day.c[i15 - 1])
        dm = (c1500 - o930) / day.avg14
        r = day.avg14
        if abs(dm) < cfg["th"]:
            continue
        if cfg["var"] == "cont":
            i_stop = day.idx(M1545)
            for i in range(i15, min(i_stop, len(day.m))):
                if dm > 0 and day.c[i] > pmh:
                    e = float(day.c[i])
                    out.append(trade(sym, day, i, 1, e, e - cfg["sbuf"] * r, None, M1555))
                    break
                if dm < 0 and day.c[i] < pml:
                    e = float(day.c[i])
                    out.append(trade(sym, day, i, -1, e, e + cfg["sbuf"] * r, None, M1555))
                    break
        else:  # MOC fade: enter at 15:00 close against the day
            side = -1 if dm > 0 else 1
            e = c1500
            out.append(trade(sym, day, i15 - 1, side, e, e - side * cfg["sbuf"] * r,
                             None, M1555))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# F4 — First-hour extreme sweep-fail (detect 10:30-14:30, flat 15:00)
# ──────────────────────────────────────────────────────────────────────────────

F4_GRID = [{"sbuf": sb, "fr": fr} for sb in (0.05, 0.10) for fr in (0.0, 0.25)]

def run_f4(sym, days, cfg):
    out = []
    for day in days:
        i_fh = day.idx(M1030)
        if i_fh < 30:
            continue
        fhh = float(day.h[:i_fh].max()); fhl = float(day.l[:i_fh].min())
        mid = (fhh + fhl) / 2.0
        r = day.avg14
        if (fhh - fhl) < cfg["fr"] * r:
            continue
        i_end = day.idx(M1430)
        done_h = done_l = False
        i = i_fh
        n = min(i_end, len(day.m))
        while i < n and not (done_h and done_l):
            if not done_h and day.h[i] > fhh:
                done_h = True                            # first break only
                sweep = float(day.h[i]); entry_j = -1
                for j in range(i, min(i + 3, n)):
                    sweep = max(sweep, float(day.h[j]))
                    if day.c[j] < fhh:
                        entry_j = j
                        break
                if entry_j >= 0:
                    e = float(day.c[entry_j])
                    if e > mid:
                        out.append(trade(sym, day, entry_j, -1, e,
                                         sweep + cfg["sbuf"] * r, mid, M1500))
            if not done_l and day.l[i] < fhl:
                done_l = True
                sweep = float(day.l[i]); entry_j = -1
                for j in range(i, min(i + 3, n)):
                    sweep = min(sweep, float(day.l[j]))
                    if day.c[j] > fhl:
                        entry_j = j
                        break
                if entry_j >= 0:
                    e = float(day.c[entry_j])
                    if e < mid:
                        out.append(trade(sym, day, entry_j, 1, e,
                                         sweep - cfg["sbuf"] * r, mid, M1500))
            i += 1
    return out


# ──────────────────────────────────────────────────────────────────────────────
# F5 — Prior-day-close magnet (small-gap days; entries 9:31-10:59, flat 12:00)
# ──────────────────────────────────────────────────────────────────────────────

F5_GRID = [{"y": y, "z": z} for y in (0.10, 0.15) for z in (0.20, 0.30)]

def run_f5(sym, days, cfg):
    out = []
    for day in days:
        gap = float(day.o[0]) - day.pc
        r = day.avg14
        if abs(gap) > cfg["y"] * r:
            continue
        pc = day.pc
        i_end = day.idx(M1100)
        done_s = done_l = False
        for i in range(1, min(i_end, len(day.m))):
            st = float(day.c[i]) - pc
            if not done_s and st >= cfg["z"] * r:
                done_s = True
                e = float(day.c[i])
                out.append(trade(sym, day, i, -1, e, e + 0.5 * cfg["z"] * r, pc, M1200))
            if not done_l and -st >= cfg["z"] * r:
                done_l = True
                e = float(day.c[i])
                out.append(trade(sym, day, i, 1, e, e - 0.5 * cfg["z"] * r, pc, M1200))
            if done_s and done_l:
                break
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Grid runner + selection + kill tests
# ──────────────────────────────────────────────────────────────────────────────

def fmt(m):
    return (f"N={m['n']:>4}  PF={m['pf']:>5.2f}  WR={m['wr']:>4.1f}%  "
            f"net=${m['net']:>9,.0f}  $/tr={m['avg']:>7.1f}")

def cfg_str(cfg, bucket=None):
    s = " ".join(f"{k}={v}" for k, v in cfg.items())
    return (f"[{bucket}] " if bucket else "") + s


def eval_config(trades):
    is_t, oos_t = split_eras(trades)
    return metrics(is_t), metrics(oos_t), metrics(trades)


def run_family(sym, days, fam_name, runner, grid, buckets=None):
    """Run full grid, print table, select best on IS (N_IS>=40, best IS PF)."""
    print(f"\n  ── {fam_name} on {sym} " + "─" * max(1, 58 - len(fam_name) - len(sym)))
    results = []
    combos = [(b, cfg) for b in (buckets or [None]) for cfg in grid]
    for b, cfg in combos:
        trades = runner(sym, days, b, cfg) if b else runner(sym, days, cfg)
        mi, mo, mf = eval_config(trades)
        results.append({"bucket": b, "cfg": cfg, "trades": trades,
                        "is": mi, "oos": mo, "full": mf})
    results.sort(key=lambda r: -r["is"]["pf"])
    for r in results:
        print(f"    IS  {fmt(r['is'])} | OOS {fmt(r['oos'])}  <- {cfg_str(r['cfg'], r['bucket'])}")
    eligible = [r for r in results if r["is"]["n"] >= 40 and r["is"]["pf"] > 1.0]
    if not eligible:
        print(f"    -> no config with N_IS>=40 and IS PF>1.0 — family DEAD on {sym}")
        return None
    best = eligible[0]
    print(f"    -> IS pick: {cfg_str(best['cfg'], best['bucket'])}")
    return best


def kill_tests(sym, fam_name, best):
    """Return (verdict, reasons). Survivor must pass ALL."""
    t = best["trades"]
    mi, mo, mf = best["is"], best["oos"], best["full"]
    reasons = []
    if mf["n"] < 80:
        reasons.append(f"N_full={mf['n']}<80")
    if mi["pf"] < 1.15:
        reasons.append(f"IS PF {mi['pf']:.2f}<1.15")
    if mo["n"] < 20:
        reasons.append(f"N_OOS={mo['n']}<20")
    if mo["pf"] < 1.10:
        reasons.append(f"OOS PF {mo['pf']:.2f}<1.10")
    # year coherence
    yt = year_table(t)
    bad = [y for y, m in yt if m["n"] >= 5 and m["pf"] < 0.70]
    sub1 = [y for y, m in yt if m["n"] >= 5 and m["pf"] < 1.0]
    if bad:
        reasons.append(f"year(s) PF<0.70: {bad}")
    if len(sub1) > 1:
        reasons.append(f">1 year PF<1.0: {sub1}")
    # double slippage
    _, mo2, mf2 = eval_config([dict(x, net=x["net2"]) for x in t])
    if mo2["pf"] < 1.00:
        reasons.append(f"dies at 2x slippage (OOS PF {mo2['pf']:.2f})")
    # drop best year
    ybest = max(yt, key=lambda r: r[1]["net"])[0]
    rest = [x["net"] for x in t if x["year"] != ybest]
    pf_rest = pf(rest)
    if pf_rest < 1.05:
        reasons.append(f"drop-{ybest} PF {pf_rest:.2f}<1.05 (one year carries it)")
    print(f"\n    KILL TESTS {fam_name}/{sym}: "
          + ("PASS (survivor)" if not reasons else "KILLED — " + "; ".join(reasons)))
    print(f"      year-by-year: " + " | ".join(
        f"{y}: N={m['n']} PF={m['pf']:.2f} ${m['net']:,.0f}" for y, m in yt))
    print(f"      2x-slip OOS PF={mo2['pf']:.2f}  full PF={mf2['pf']:.2f}; "
          f"drop-best-year({ybest}) PF={pf_rest:.2f}")
    return (len(reasons) == 0), reasons


# ──────────────────────────────────────────────────────────────────────────────
# Survivor extras: collision, correlation, risk fit
# ──────────────────────────────────────────────────────────────────────────────

def load_v12_stream():
    p = os.path.join(BASE, "data", "v12_daily_stream.csv")
    if not os.path.exists(p):
        return {}
    df = pd.read_csv(p)
    return {pd.Timestamp(d).date(): float(v) for d, v in zip(df["date"], df["pnl"])}


def survivor_report(sym, fam_name, best, v12):
    t = best["trades"]
    # collision
    n = len(t)
    coll = []
    for lo, hi, name in V12_WINDOWS:
        k = sum(1 for x in t if lo <= x["m"] < hi)
        if k:
            coll.append(f"{name}: {100.0*k/n:.0f}%")
    print(f"      collision vs v12 windows: " + (", ".join(coll) if coll else "NONE (clean)"))
    # correlation vs v12 daily stream
    daily = defaultdict(float)
    for x in t:
        daily[x["date"]] += x["net"]
    common = sorted(set(daily) & set(v12))
    if len(common) >= 30:
        a = np.array([daily[d] for d in common]); b = np.array([v12[d] for d in common])
        r_inner = float(np.corrcoef(a, b)[0, 1])
    else:
        r_inner = float("nan")
    union = sorted(set(daily) | set(v12))
    a = np.array([daily.get(d, 0.0) for d in union])
    b = np.array([v12.get(d, 0.0) for d in union])
    r_union = float(np.corrcoef(a, b)[0, 1]) if len(union) >= 30 else float("nan")
    print(f"      corr vs v12 daily P&L: inner-join r={r_inner:.3f} "
          f"(n={len(common)}), union r={r_union:.3f}")
    # risk fit vs $1,200 DLL (1 contract)
    dv = sorted(daily.values())
    worst = dv[0] if dv else 0.0
    p95 = dv[max(0, int(0.05 * len(dv)) - 1)] if dv else 0.0
    fit = "FITS" if worst > -1200 else "BREACHES"
    print(f"      risk vs $1,200 DLL @1c: worst day ${worst:,.0f}, "
          f"p5 day ${p95:,.0f} -> {fit}")


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — adversarial deep-dive on stage-1 survivors (run: ... deep)
#   * parameter-island check (extended F5 surface, F1 bucket x stop surface)
#   * side split, monthly consistency, t-stat on OOS mean
#   * risk-capped variants for $1,200 DLL fit (F1 stop$ cap; F5 1 trade/day)
#   * cross-correlation between survivors (portfolio redundancy)
# ──────────────────────────────────────────────────────────────────────────────

def tstat(vals):
    if len(vals) < 3:
        return 0.0
    m = float(np.mean(vals)); s = float(np.std(vals, ddof=1))
    return m / (s / math.sqrt(len(vals))) if s > 0 else 0.0


def daily_pnl(trades, key="net"):
    d = defaultdict(float)
    for t in trades:
        d[t["date"]] += t[key]
    return d


def deep_report(sym, name, trades):
    is_t, oos_t = split_eras(trades)
    mi, mo, mf = metrics(is_t), metrics(oos_t), metrics(trades)
    print(f"\n  ═══ DEEP {name}/{sym} "
          f"IS PF {mi['pf']:.2f} N{mi['n']} | OOS PF {mo['pf']:.2f} N{mo['n']} ═══")
    print(f"    full: {fmt(mf)}  maxDD ${mf['maxdd']:,.0f}  worst day ${mf['worst_day']:,.0f}")
    print(f"    t-stat per-trade: IS {tstat([t['net'] for t in is_t]):.2f} | "
          f"OOS {tstat([t['net'] for t in oos_t]):.2f}")
    for side_name, sgn in (("SHORT", -1), ("LONG", 1)):
        st = [t for t in trades if t.get("side", 0) == sgn]
        if st:
            si, so = split_eras(st)
            print(f"    {side_name}: IS {fmt(metrics(si))} | OOS {fmt(metrics(so))}")
    # monthly hit rate: % of months positive
    mon = defaultdict(float)
    for t in trades:
        mon[(t["date"].year, t["date"].month)] += t["net"]
    pos = sum(1 for v in mon.values() if v > 0)
    print(f"    months positive: {pos}/{len(mon)} ({100.0*pos/len(mon):.0f}%)")
    dv = sorted(daily_pnl(trades).values())
    print(f"    daily: worst ${dv[0]:,.0f}  p5 ${dv[max(0,int(0.05*len(dv))-1)]:,.0f}  "
          f"days traded {len(dv)}")


def stage2(data, v12):
    print("\n" + "=" * 78)
    print("STAGE 2 — ADVERSARIAL DEEP-DIVE ON PROVISIONAL SURVIVORS")
    print("=" * 78, flush=True)

    # ---- F5 parameter-island check: extended surface (KILL test only) -------
    print("\n  F5 EXTENDED SURFACE (island check; IS PF / OOS PF):")
    for sym in ("NQ", "ES"):
        if sym not in data:
            continue
        days = data[sym]
        print(f"    {sym}:  z->", "  ".join(f"{z:.2f}" for z in (0.15, 0.20, 0.25, 0.30)))
        for y in (0.10, 0.15, 0.20):
            cells = []
            for z in (0.15, 0.20, 0.25, 0.30):
                t = run_f5(sym, days, {"y": y, "z": z})
                mi, mo, _ = eval_config(t)
                cells.append(f"{mi['pf']:.2f}/{mo['pf']:.2f}(N{mi['n']+mo['n']})")
            print(f"      y={y:.2f}:  " + "  ".join(cells))

    # ---- F1/ES bucket-edge island check: gap% sub-slices ---------------------
    if "ES" in data:
        print("\n  F1/ES SUB-SLICE (S bucket halves + neighbors; stop2.0/full/630):")
        for lo, hi in ((0.03, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.25)):
            F1_BUCKETS["_tmp"] = (lo, hi)
            t = run_f1("ES", data["ES"], "_tmp",
                       {"stop_mult": 2.0, "target": "full", "texit": M1030})
            mi, mo, _ = eval_config(t)
            print(f"      gap {lo:.2f}-{hi:.2f}: IS PF {mi['pf']:.2f} N{mi['n']} | "
                  f"OOS PF {mo['pf']:.2f} N{mo['n']}")
        del F1_BUCKETS["_tmp"]

    # ---- survivors with side tags + deep reports ------------------------------
    surv = {}
    if "ES" in data:
        t = run_f1("ES", data["ES"], "S",
                   {"stop_mult": 2.0, "target": "full", "texit": M1030})
        surv["F1_ES"] = t
        deep_report("ES", "F1 GAP FADE (S,2.0,full,10:30)", t)
    for sym in ("NQ", "ES"):
        if sym in data:
            t = run_f5(sym, data[sym], {"y": 0.15, "z": 0.2})
            surv[f"F5_{sym}"] = t
            deep_report(sym, "F5 PC MAGNET (y0.15,z0.2)", t)

    # ---- risk-capped variants -------------------------------------------------
    print("\n  RISK-CAPPED VARIANTS (fit $1,200 DLL @1c):")
    if "ES" in data:
        # F1/ES: skip days where stop risk (2x gap) > $1,100
        base = surv["F1_ES"]
        capped = []
        for day in data["ES"]:
            entry = float(day.o[0]); gap = entry - day.pc
            gp = abs(gap) / day.avg14
            if not (0.05 <= gp < 0.15):
                continue
            if 2.0 * abs(gap) * SPECS["ES"]["pv"] > 1100.0:
                continue
            side = -1 if gap > 0 else 1
            capped.append(trade("ES", day, 0, side, entry,
                                entry - side * 2.0 * abs(gap), day.pc, M1030,
                                check_first_bar=True))
        mi, mo, mf = eval_config(capped)
        dv = sorted(daily_pnl(capped).values())
        print(f"    F1/ES stop$<=1100: IS {fmt(mi)} | OOS {fmt(mo)} | "
              f"worst day ${dv[0]:,.0f} -> {'FITS' if dv[0] > -1200 else 'BREACHES'}")
    for sym in ("NQ", "ES"):
        key = f"F5_{sym}"
        if key not in surv:
            continue
        # max 1 trade/day (first trigger only) + stop$ cap 1,100
        seen = set(); capped = []
        for t in sorted(surv[key], key=lambda x: (x["date"], x["m"])):
            if t["date"] in seen:
                continue
            seen.add(t["date"])
            capped.append(t)
        # apply stop cap by recomputing risk: stop dist = 0.5*z*avg14
        mi, mo, mf = eval_config(capped)
        dv = sorted(daily_pnl(capped).values())
        print(f"    {key} 1-trade/day: IS {fmt(mi)} | OOS {fmt(mo)} | "
              f"worst day ${dv[0]:,.0f} -> {'FITS' if dv[0] > -1200 else 'BREACHES'}")

    # ---- cross-correlations between survivors + vs v12 ------------------------
    print("\n  CROSS-CORRELATION (daily P&L, union 0-fill):")
    keys = list(surv)
    streams = {k: daily_pnl(surv[k]) for k in keys}
    streams["v12"] = v12
    ks = list(streams)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            a, b = streams[ks[i]], streams[ks[j]]
            u = sorted(set(a) | set(b))
            if len(u) < 30:
                continue
            va = np.array([a.get(d, 0.0) for d in u])
            vb = np.array([b.get(d, 0.0) for d in u])
            r = float(np.corrcoef(va, vb)[0, 1])
            print(f"    {ks[i]:>6} vs {ks[j]:>6}: r={r:+.3f}")

    # ---- collision detail ------------------------------------------------------
    print("\n  ENTRY-TIME DISTRIBUTION (survivors):")
    for k in keys:
        ms = [t["m"] for t in surv[k]]
        h, _ = np.histogram(ms, bins=[570, 586, 630, 660, 690, 720])
        tot = len(ms)
        print(f"    {k}: 9:30-9:45 {100*h[0]//tot}% | 9:46-10:29 {100*h[1]//tot}% | "
              f"10:30-10:59 {100*h[2]//tot}% | 11:00-11:29 {100*h[3]//tot}% | "
              f"11:30+ {100*h[4]//tot}%")


# ──────────────────────────────────────────────────────────────────────────────
# Stage 3 — final falsification round (run: ... stage3)
#   * bootstrap P(OOS PF <= 1.0) for each provisional survivor (10k resamples)
#   * F1/ES entry-delay test: enter 9:31 close instead of 9:30 open
#   * F5/NQ year x side table (is IS long-dominance a bear-2022 artifact?)
#   * F5/NQ vol-cap (skip days where stop risk > $1,100) -> DLL fit
# ──────────────────────────────────────────────────────────────────────────────

def boot_pf(trades, n_boot=10000, seed=7):
    rng = np.random.default_rng(seed)
    v = np.array([t["net"] for t in trades])
    if len(v) == 0:
        return 1.0
    idx = rng.integers(0, len(v), size=(n_boot, len(v)))
    s = v[idx]
    wins = np.where(s > 0, s, 0.0).sum(axis=1)
    loss = np.abs(np.where(s <= 0, s, 0.0).sum(axis=1))
    pfs = np.where(loss > 0, wins / np.maximum(loss, 1e-9), 99.0)
    return float((pfs <= 1.0).mean())


def one_per_day(trades):
    seen, out = set(), []
    for t in sorted(trades, key=lambda x: (x["date"], x["m"])):
        if t["date"] in seen:
            continue
        seen.add(t["date"])
        out.append(t)
    return out


def stage3(data, v12):
    print("\n" + "=" * 78)
    print("STAGE 3 — FINAL FALSIFICATION")
    print("=" * 78, flush=True)

    # rebuild survivors
    f1_es = run_f1("ES", data["ES"], "S",
                   {"stop_mult": 2.0, "target": "full", "texit": M1030})
    f5_nq = one_per_day(run_f5("NQ", data["NQ"], {"y": 0.15, "z": 0.2}))
    f5_es = one_per_day(run_f5("ES", data["ES"], {"y": 0.15, "z": 0.2}))

    print("\n  BOOTSTRAP P(OOS PF <= 1.0)  [10k resamples; want < 0.10]:")
    for name, t in (("F1_ES", f1_es), ("F5_NQ 1/day", f5_nq), ("F5_ES 1/day", f5_es)):
        _, oos = split_eras(t)
        p = boot_pf(oos)
        p_is = boot_pf([x for x in t if x["year"] in IS_YEARS])
        print(f"    {name:12}: P(IS<=1)={p_is:.3f}  P(OOS<=1)={p:.3f}")

    # F1 entry-delay falsification: enter at close of 9:31 bar (2nd bar)
    print("\n  F1/ES ENTRY-DELAY TEST (enter 9:31 close, not 9:30 open):")
    delayed = []
    for day in data["ES"]:
        o930 = float(day.o[0]); gap = o930 - day.pc
        gp = abs(gap) / day.avg14
        if not (0.05 <= gp < 0.15) or len(day.m) < 3:
            continue
        side = -1 if gap > 0 else 1
        e = float(day.c[1])                       # 9:31 bar close
        stop = o930 - side * 2.0 * abs(gap)       # same structural stop
        if (side < 0 and (e >= stop or e <= day.pc)) or \
           (side > 0 and (e <= stop or e >= day.pc)):
            continue                               # already through stop/target
        delayed.append(trade("ES", day, 2, side, e, stop, day.pc, M1030))
    mi, mo, _ = eval_config(delayed)
    print(f"    delayed: IS {fmt(mi)} | OOS {fmt(mo)}")

    # F5/NQ year x side
    print("\n  F5/NQ (1/day) YEAR x SIDE  (net $ | PF):")
    for y in (2022, 2023, 2024, 2025, 2026):
        row = []
        for sgn, nm in ((1, "LONG"), (-1, "SHORT")):
            tt = [t for t in f5_nq if t["year"] == y and t["side"] == sgn]
            m = metrics(tt)
            row.append(f"{nm} N={m['n']:>3} ${m['net']:>7,.0f} PF={m['pf']:.2f}")
        print(f"    {y}: " + " | ".join(row))

    # F5/NQ vol-cap for DLL: skip if stop risk 0.1*avg14*$20 > $1,100
    print("\n  F5/NQ 1/day + vol-cap (stop$ <= 1,100 i.e. avg14 <= 550pt):")
    capped = [t for t in f5_nq]  # need day avg14 — recompute inline
    by_date_avg = {d.date: d.avg14 for d in data["NQ"]}
    capped = [t for t in f5_nq if 0.5 * 0.2 * by_date_avg[t["date"]] * 20.0 <= 1100.0]
    mi, mo, _ = eval_config(capped)
    yt = year_table(capped)
    dv = sorted(daily_pnl(capped).values())
    print(f"    IS {fmt(mi)} | OOS {fmt(mo)}")
    print(f"    years: " + " | ".join(f"{y}:PF {m['pf']:.2f} N{m['n']}" for y, m in yt))
    print(f"    worst day ${dv[0]:,.0f} -> {'FITS' if dv[0] > -1200 else 'BREACHES'} $1,200 DLL")

    # F5/ES 1/day full deliverable table
    print("\n  F5/ES (1/day) FINAL TABLE:")
    mi, mo, mf = eval_config(f5_es)
    yt = year_table(f5_es)
    dv = sorted(daily_pnl(f5_es).values())
    print(f"    IS {fmt(mi)} | OOS {fmt(mo)} | full {fmt(mf)}")
    print(f"    years: " + " | ".join(f"{y}:PF {m['pf']:.2f} N{m['n']} ${m['net']:,.0f}"
                                      for y, m in yt))
    m2 = metrics([dict(x, net=x["net2"]) for x in f5_es if x["year"] in OOS_YEARS])
    print(f"    2x-slip OOS PF {m2['pf']:.2f}; worst day ${dv[0]:,.0f}; "
          f"maxDD ${mf['maxdd']:,.0f}")

    # F5/ES 1-bar entry delay (enter next bar open; same stop/target levels)
    print("\n  F5/ES ENTRY-DELAY TEST (enter next bar open):")
    delayed = []
    for day in data["ES"]:
        gap = float(day.o[0]) - day.pc
        r = day.avg14
        if abs(gap) > 0.15 * r:
            continue
        pc = day.pc
        i_end = day.idx(M1100)
        got = None
        for i in range(1, min(i_end, len(day.m) - 1)):
            st = float(day.c[i]) - pc
            if st >= 0.2 * r:
                got = (-1, i); break
            if -st >= 0.2 * r:
                got = (1, i); break
        if not got:
            continue
        side, i = got
        e = float(day.o[i + 1])
        sig = float(day.c[i])
        stop = sig - side * 0.5 * 0.2 * r      # structural levels off signal close
        if (side < 0 and (e >= stop or e <= pc)) or (side > 0 and (e <= stop or e >= pc)):
            continue
        delayed.append(trade("ES", day, i + 1, side, e, stop, pc, M1200,
                             check_first_bar=True))
    mi, mo, _ = eval_config(delayed)
    print(f"    delayed: IS {fmt(mi)} | OOS {fmt(mo)}")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("INDEX MEAN-REVERSION / AUCTION / CLOSE STRUCTURES — ES / RTY / NQ")
    print("IS 2022-2024 | OOS 2025-2026 | costs incl. comm + 1 tick/side slip")
    print("=" * 78, flush=True)

    data = {}
    for sym in ("NQ", "ES", "RTY"):
        days = load_days(sym)
        if days:
            data[sym] = days
    v12 = load_v12_stream()
    print(f"  v12 daily stream: {len(v12)} days loaded for correlation", flush=True)

    if len(sys.argv) > 1 and sys.argv[1] == "deep":
        stage2(data, v12)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "stage3":
        stage3(data, v12)
        return

    families = [
        ("F1 GAP FADE",       run_f1, F1_GRID, list(F1_BUCKETS)),
        ("F2 LUNCH REVERT",   run_f2, F2_GRID, None),
        ("F3 CLOSE HOUR",     run_f3, F3_GRID, None),
        ("F4 FH SWEEP-FAIL",  run_f4, F4_GRID, None),
        ("F5 PC MAGNET",      run_f5, F5_GRID, None),
    ]

    survivors, killed = [], []
    for fam_name, runner, grid, buckets in families:
        print("\n" + "=" * 78)
        print(f"{fam_name}")
        print("=" * 78, flush=True)
        for sym, days in data.items():
            best = run_family(sym, days, fam_name, runner, grid, buckets)
            if best is None:
                killed.append((fam_name, sym, "no IS-eligible config"))
                continue
            ok, reasons = kill_tests(sym, fam_name, best)
            if ok:
                survivors.append((fam_name, sym, best))
                survivor_report(sym, fam_name, best, v12)
            else:
                killed.append((fam_name, sym, "; ".join(reasons)))

    print("\n" + "=" * 78)
    print(f"SUMMARY: {len(survivors)} survivor(s), {len(killed)} killed combos")
    print("=" * 78)
    for fam, sym, best in survivors:
        mi, mo = best["is"], best["oos"]
        print(f"  SURVIVOR {fam}/{sym}: {cfg_str(best['cfg'], best['bucket'])}"
              f"  IS PF {mi['pf']:.2f} (N{mi['n']}) | OOS PF {mo['pf']:.2f} (N{mo['n']})"
              f"  $/tr {best['full']['avg']:.0f}")
    for fam, sym, why in killed:
        print(f"  KILLED   {fam}/{sym}: {why}")


if __name__ == "__main__":
    main()
