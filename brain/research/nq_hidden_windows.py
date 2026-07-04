"""
brain/research/nq_hidden_windows.py

NQ HIDDEN-WINDOW SWEEP — windows the live v12 system does NOT trade.
v12 occupies: 9:46-10:30, 11:00-13:00, 13:15-14:00 (PM ORB), 18:15 (Asia gap,
Mon-Thu only: needs same-day 16:00 close, so never Sunday).

Windows tested here:
  W1  8:30 ET economic-release impulse (continuation + fade), flat 9:25
  W2a 15:00-16:00 trend-day continuation, flat 15:58
  W2b 15:30 mini-ORB, flat 15:58
  W3a Monday weekend-gap fade/go at 9:30, flat 10:55
  W3b Sunday 18:00 reopen drift 18:00-20:00 (v12 Asia leg is dark on Sunday)
  W4  Midnight Globex 00:00-02:00: overnight-range break + drift
  W5  14:00-15:00 continuation of PM ORB direction (CORRELATES with v12 PM leg
      by construction — interaction reported honestly)

House law: IS 2022-2024, OOS 2025-2026-06. Configs chosen on IS, confirmed on
OOS. N >= ~80 full period. Costs $14.50/RT all-in, $20/pt. Fills: signal on bar
close -> entry at NEXT bar open (except stated open-print entries); stop checked
BEFORE target inside a bar (conservative); time-flatten at bar open.

Run:  python3 brain/research/nq_hidden_windows.py            (from repo root)
"""

import os
import sys
import time as _time
from collections import defaultdict
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data", "nq_full.csv")
V12_STREAM = os.path.join(ROOT, "data", "v12_daily_stream.csv")

POINT_VALUE = 20.0
COST_RT = 14.50          # all-in round trip, house convention
IS_START, IS_END = date(2022, 1, 1), date(2024, 12, 31)
OOS_START, OOS_END = date(2025, 1, 1), date(2026, 6, 30)
MIN_FULL_N = 80          # house floor
MIN_IS_N = 45            # need enough IS trades to pick a config at all

T0 = _time.time()


def log(msg):
    print(f"[{_time.time()-T0:7.1f}s] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Data loading: per-calendar-date numpy arrays (minute-of-day, OHLC)
# ─────────────────────────────────────────────────────────────────────────────
def load_days():
    log("loading nq_full.csv ...")
    df = pd.read_csv(DATA)
    ts = pd.to_datetime(df["timestamp"].str.slice(0, 19), format="%Y-%m-%d %H:%M:%S")
    df["m"] = (ts.dt.hour * 60 + ts.dt.minute).astype(np.int32)
    df["d"] = ts.dt.date
    df = df.sort_values(["d", "m"], kind="mergesort")
    log(f"rows={len(df):,}  span {df['d'].iloc[0]} -> {df['d'].iloc[-1]}")

    hrs = sorted((df["m"] // 60).unique().tolist())
    missing = [h for h in range(24) if h not in hrs]
    log(f"hour coverage check: present={hrs}")
    log(f"                    missing={missing}  (expect [17] — CME halt)")

    days = {}
    for d, g in df.groupby("d", sort=True):
        days[d] = dict(
            m=g["m"].to_numpy(np.int32),
            o=g["open"].to_numpy(np.float64),
            h=g["high"].to_numpy(np.float64),
            l=g["low"].to_numpy(np.float64),
            c=g["close"].to_numpy(np.float64),
        )
    log(f"calendar dates with bars: {len(days)}")
    return days


def win(day, m0, m1):
    """index slice [i0,i1) covering minutes [m0, m1)."""
    i0 = int(np.searchsorted(day["m"], m0, "left"))
    i1 = int(np.searchsorted(day["m"], m1, "left"))
    return i0, i1


def bar_at(day, minute):
    i = int(np.searchsorted(day["m"], minute, "left"))
    if i < len(day["m"]) and day["m"][i] == minute:
        return i
    return None


def first_bar_at_or_after(day, minute, before=None):
    i = int(np.searchsorted(day["m"], minute, "left"))
    if i < len(day["m"]) and (before is None or day["m"][i] < before):
        return i
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Bracket simulator
# ─────────────────────────────────────────────────────────────────────────────
def sim_bracket(day, ei, side, entry, stop, tgt, flat_minute):
    """Entry at open of bar ei. Stop before target inside a bar. Flatten at open
    of first bar >= flat_minute; if day data ends first, exit last close."""
    m, h, l, o, c = day["m"], day["h"], day["l"], day["o"], day["c"]
    n = len(m)
    for i in range(ei, n):
        if m[i] >= flat_minute:
            return o[i], "flat", m[i]
        if side > 0:
            if l[i] <= stop:
                return stop, "stop", m[i]
            if tgt is not None and h[i] >= tgt:
                return tgt, "target", m[i]
        else:
            if h[i] >= stop:
                return stop, "stop", m[i]
            if tgt is not None and l[i] <= tgt:
                return tgt, "target", m[i]
    return c[n - 1], "eod", m[n - 1]


def make_trade(d, side, entry, exitpx, tag):
    pts = (exitpx - entry) * side
    return dict(date=d, side=side, pts=pts, usd=pts * POINT_VALUE - COST_RT, tag=tag)


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────
def summarize(trades):
    if not trades:
        return dict(N=0, WR=0.0, PF=0.0, net=0.0, avg=0.0, dd=0.0, worst=0.0)
    usd = np.array([t["usd"] for t in trades])
    gw = usd[usd > 0].sum()
    gl = -usd[usd < 0].sum()
    pf = gw / gl if gl > 0 else float("inf")
    daily = defaultdict(float)
    for t in trades:
        daily[t["date"]] += t["usd"]
    eq = np.cumsum([daily[k] for k in sorted(daily)])
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    return dict(N=len(usd), WR=100.0 * (usd > 0).mean(), PF=pf,
                net=float(usd.sum()), avg=float(usd.mean()),
                dd=dd, worst=float(min(daily.values())))


def split_eras(trades):
    is_t = [t for t in trades if IS_START <= t["date"] <= IS_END]
    oos_t = [t for t in trades if OOS_START <= t["date"] <= OOS_END]
    return is_t, oos_t


def fmt(s):
    pf = f"{s['PF']:5.2f}" if s["PF"] != float("inf") else "  inf"
    return (f"N={s['N']:>4}  WR={s['WR']:5.1f}%  PF={pf}  "
            f"net=${s['net']:>9,.0f}  avg=${s['avg']:>6,.0f}")


def year_table(trades, label):
    print(f"    year-by-year — {label}")
    by = defaultdict(list)
    for t in trades:
        by[t["date"].year].append(t)
    print(f"      {'yr':<5} {'N':>4} {'WR%':>6} {'PF':>6} {'net$':>10} {'avg$':>7} {'maxDD$':>8} {'worst$':>8}")
    for yr in sorted(by):
        s = summarize(by[yr])
        pf = f"{s['PF']:.2f}" if s["PF"] != float("inf") else "inf"
        print(f"      {yr:<5} {s['N']:>4} {s['WR']:>6.1f} {pf:>6} {s['net']:>10,.0f} "
              f"{s['avg']:>7,.0f} {s['dd']:>8,.0f} {s['worst']:>8,.0f}")


def config_row(name, trades):
    is_t, oos_t = split_eras(trades)
    si, so = summarize(is_t), summarize(oos_t)
    pfi = f"{si['PF']:5.2f}" if si["PF"] != float("inf") else "  inf"
    pfo = f"{so['PF']:5.2f}" if so["PF"] != float("inf") else "  inf"
    print(f"    {name:<42} IS: N={si['N']:>4} PF={pfi} ${si['net']:>8,.0f} | "
          f"OOS: N={so['N']:>4} PF={pfo} ${so['net']:>8,.0f}")
    return si, so


# ─────────────────────────────────────────────────────────────────────────────
# Rolling per-date context metrics
# ─────────────────────────────────────────────────────────────────────────────
def build_context(days):
    log("building per-date context (8:30 ranges, RTH ranges, rolling medians)...")
    dates = sorted(days)
    ctx = {}
    hist_830, hist_830w, hist_rng = [], [], []
    prev_rth_close, prev_rth_date = None, None
    prev_date = None
    for d in dates:
        day = days[d]
        e = dict(prev_date=prev_date,
                 prev_rth_close=prev_rth_close, prev_rth_date=prev_rth_date)

        i = bar_at(day, 510)                      # 8:30 single bar
        e["r830"] = (day["h"][i] - day["l"][i]) if i is not None else None
        e["med830"] = float(np.median(hist_830[-20:])) if len(hist_830) >= 20 else None

        i0, i1 = win(day, 510, 515)               # 8:30-8:34 composite
        e["r830w"] = (day["h"][i0:i1].max() - day["l"][i0:i1].min()) if i1 - i0 == 5 else None
        e["med830w"] = float(np.median(hist_830w[-20:])) if len(hist_830w) >= 20 else None

        i0, i1 = win(day, 570, 960)               # RTH 9:30-16:00
        rng = None
        if i1 - i0 >= 300:                        # full-ish RTH day
            rng = day["h"][i0:i1].max() - day["l"][i0:i1].min()
        e["rth_rng"] = rng
        e["med_rng"] = float(np.median(hist_rng[-20:])) if len(hist_rng) >= 20 else None

        ctx[d] = e
        if e["r830"] is not None:
            hist_830.append(e["r830"])
        if e["r830w"] is not None:
            hist_830w.append(e["r830w"])
        if rng is not None:
            hist_rng.append(rng)
        if i1 - i0 > 0:                           # last RTH close (any RTH bars)
            prev_rth_close = day["c"][i1 - 1]
            prev_rth_date = d
        prev_date = d
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# W1 — 8:30 economic-release impulse, flat 9:25
# ─────────────────────────────────────────────────────────────────────────────
def w1_impulse(days, ctx, det, k, rr, mode):
    """det: 'A' 1-min bar (enter 8:31) | 'B' 5-min composite (enter 8:35).
    mode: 'cont' | 'fade'.  Stop at impulse base/extreme, target rr*risk, flat 9:25."""
    trades = []
    for d in sorted(days):
        if d.weekday() > 4:
            continue
        day, e = days[d], ctx[d]
        if det == "A":
            if e["r830"] is None or e["med830"] is None or e["med830"] <= 0:
                continue
            if e["r830"] < k * e["med830"]:
                continue
            i = bar_at(day, 510)
            imp_dir = np.sign(day["c"][i] - day["o"][i])
            imp_hi, imp_lo = day["h"][i], day["l"][i]
            ei = first_bar_at_or_after(day, 511, before=565)
        else:
            if e["r830w"] is None or e["med830w"] is None or e["med830w"] <= 0:
                continue
            if e["r830w"] < k * e["med830w"]:
                continue
            i0, i1 = win(day, 510, 515)
            imp_dir = np.sign(day["c"][i1 - 1] - day["o"][i0])
            imp_hi, imp_lo = day["h"][i0:i1].max(), day["l"][i0:i1].min()
            ei = first_bar_at_or_after(day, 515, before=565)
        if imp_dir == 0 or ei is None:
            continue
        side = int(imp_dir) if mode == "cont" else -int(imp_dir)
        entry = day["o"][ei]
        stop = imp_lo if side > 0 else imp_hi
        risk = (entry - stop) * side
        if risk < 2.0:
            continue
        tgt = entry + side * rr * risk
        px, tag, _ = sim_bracket(day, ei, side, entry, stop, tgt, 565)
        trades.append(make_trade(d, side, entry, px, tag))
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# W2a — close-hour trend-day continuation, entry 15:00, flat 15:58
# ─────────────────────────────────────────────────────────────────────────────
def w2a_trend(days, ctx, k, stop_spec, rr=None, pos_th=0.75, entry_m=900):
    """stop_spec: ('frac', x) stop = x*day_range | ('fix', pts) fixed points."""
    trades = []
    for d in sorted(days):
        if d.weekday() > 4:
            continue
        day, e = days[d], ctx[d]
        if e["med_rng"] is None:
            continue
        i0, i1 = win(day, 570, entry_m)           # 9:30 -> entry time
        if i1 - i0 < 300:
            continue
        hi, lo = day["h"][i0:i1].max(), day["l"][i0:i1].min()
        rng = hi - lo
        if rng < k * e["med_rng"] or rng <= 0:
            continue
        ei = first_bar_at_or_after(day, entry_m, before=958)
        if ei is None:
            continue
        entry = day["o"][ei]
        pos = (entry - lo) / rng
        if pos >= pos_th:
            side = 1
        elif pos <= 1.0 - pos_th:
            side = -1
        else:
            continue
        spts = stop_spec[1] * rng if stop_spec[0] == "frac" else stop_spec[1]
        stop = entry - side * spts
        tgt = entry + side * rr * spts if rr else None
        px, tag, _ = sim_bracket(day, ei, side, entry, stop, tgt, 958)
        trades.append(make_trade(d, side, entry, px, tag))
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# W2b — 15:30 mini-ORB (OR 15:30-15:34, entry 15:35-15:50, flat 15:58)
# ─────────────────────────────────────────────────────────────────────────────
def w2b_miniorb(days, buf, stop_mode, rr):
    trades = []
    for d in sorted(days):
        if d.weekday() > 4:
            continue
        day = days[d]
        i0, i1 = win(day, 930, 935)
        if i1 - i0 < 5:
            continue
        orh, orl = day["h"][i0:i1].max(), day["l"][i0:i1].min()
        j0, j1 = win(day, 935, 951)
        for j in range(j0, j1):
            cl = day["c"][j]
            side = 1 if cl > orh + buf else (-1 if cl < orl - buf else 0)
            if side == 0 or j + 1 >= len(day["m"]):
                continue
            ei = j + 1
            entry = day["o"][ei]
            if stop_mode == "or":
                stop = orl if side > 0 else orh
                if (entry - stop) * side > 20.0:
                    stop = entry - side * 20.0
                if (entry - stop) * side < 3.0:
                    stop = entry - side * 3.0
            else:
                stop = entry - side * 15.0
            risk = (entry - stop) * side
            tgt = entry + side * rr * risk
            px, tag, _ = sim_bracket(day, ei, side, entry, stop, tgt, 958)
            trades.append(make_trade(d, side, entry, px, tag))
            break
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# W3a — Monday weekend gap at 9:30 open (fade or go), flat 10:55
# ─────────────────────────────────────────────────────────────────────────────
def w3a_monday_gap(days, ctx, gmin_rel, mode, stop_x, tgt_x, stop_fix=None):
    """gap = Mon 9:30 open - prior RTH close (Friday). |gap| >= gmin_rel*med20(RTH rng).
    fade: toward fill, target tgt_x*|gap| (1.0 = full fill), stop stop_x*|gap|
    (or fixed points if stop_fix is given — deployable-risk variant)."""
    trades = []
    for d in sorted(days):
        if d.weekday() != 0:
            continue
        day, e = days[d], ctx[d]
        if e["prev_rth_close"] is None or e["med_rng"] is None:
            continue
        if e["prev_rth_date"] is not None and (d - e["prev_rth_date"]).days > 4:
            continue
        i = bar_at(day, 570)
        if i is None:
            continue
        entry = day["o"][i]
        gap = entry - e["prev_rth_close"]
        if abs(gap) < gmin_rel * e["med_rng"] or abs(gap) < 5.0:
            continue
        gdir = 1 if gap > 0 else -1
        side = -gdir if mode == "fade" else gdir
        spts = stop_fix if stop_fix is not None else stop_x * abs(gap)
        stop = entry - side * spts
        tgt = entry + side * tgt_x * abs(gap)
        px, tag, _ = sim_bracket(day, i, side, entry, stop, tgt, 655)
        trades.append(make_trade(d, side, entry, px, tag))
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# W3b — Sunday 18:00 reopen drift 18:00-20:00
# ─────────────────────────────────────────────────────────────────────────────
def w3b_sunday(days, ctx, sig, mode, mmin, stop_pt, flat_minute=1200):
    """sig 'm15': first-15-min move, enter 18:15. sig 'gap': reopen gap vs Friday
    RTH close, enter 18:01. mode cont|fade. No target, flat 20:00."""
    trades = []
    for d in sorted(days):
        if d.weekday() != 6:
            continue
        day, e = days[d], ctx[d]
        if sig == "m15":
            i0 = bar_at(day, 1080)
            i1 = bar_at(day, 1094)
            if i0 is None or i1 is None:
                continue
            mv = day["c"][i1] - day["o"][i0]
            ei = first_bar_at_or_after(day, 1095, before=flat_minute)
        else:
            if e["prev_rth_close"] is None:
                continue
            i0 = bar_at(day, 1080)
            if i0 is None:
                continue
            mv = day["o"][i0] - e["prev_rth_close"]
            ei = first_bar_at_or_after(day, 1081, before=flat_minute)
        if ei is None or abs(mv) < mmin:
            continue
        side = (1 if mv > 0 else -1) * (1 if mode == "cont" else -1)
        entry = day["o"][ei]
        stop = entry - side * stop_pt
        px, tag, _ = sim_bracket(day, ei, side, entry, stop, None, flat_minute)
        trades.append(make_trade(d, side, entry, px, tag))
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# W4 — Midnight Globex 00:00-02:00: ON-range break + drift
# ─────────────────────────────────────────────────────────────────────────────
def w4_break(days, buf, stop_pt, rr):
    """ON range = prev calendar session 18:00-23:59. First close beyond +-buf in
    00:00-02:00 -> enter next bar open. rr None => no target, flat 02:00."""
    trades = []
    dates = sorted(days)
    for idx in range(1, len(dates)):
        d = dates[idx]
        if d.weekday() > 4:
            continue
        p = dates[idx - 1]
        if (d - p).days > 1:
            continue
        pday = days[p]
        i0, i1 = win(pday, 1080, 1440)
        if i1 - i0 < 180:
            continue
        onh, onl = pday["h"][i0:i1].max(), pday["l"][i0:i1].min()
        day = days[d]
        j0, j1 = win(day, 0, 120)
        for j in range(j0, j1):
            cl = day["c"][j]
            side = 1 if cl > onh + buf else (-1 if cl < onl - buf else 0)
            if side == 0 or j + 1 >= len(day["m"]):
                continue
            ei = j + 1
            entry = day["o"][ei]
            stop = entry - side * stop_pt
            tgt = entry + side * rr * stop_pt if rr else None
            px, tag, _ = sim_bracket(day, ei, side, entry, stop, tgt, 120)
            trades.append(make_trade(d, side, entry, px, tag))
            break
    return trades


def w4_drift(days, mode, mmin, stop_pt):
    """Evening drift 18:00->00:00; enter 00:01 cont/fade, no target, flat 02:00."""
    trades = []
    dates = sorted(days)
    for idx in range(1, len(dates)):
        d = dates[idx]
        if d.weekday() > 4:
            continue
        p = dates[idx - 1]
        if (d - p).days > 1:
            continue
        pi = bar_at(days[p], 1080)
        di = bar_at(days[d], 0)
        if pi is None or di is None:
            continue
        mv = days[d]["o"][di] - days[p]["o"][pi]
        if abs(mv) < mmin:
            continue
        side = (1 if mv > 0 else -1) * (1 if mode == "cont" else -1)
        day = days[d]
        ei = first_bar_at_or_after(day, 1, before=120)
        if ei is None:
            continue
        entry = day["o"][ei]
        stop = entry - side * stop_pt
        px, tag, _ = sim_bracket(day, ei, side, entry, stop, None, 120)
        trades.append(make_trade(d, side, entry, px, tag))
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# W5 — PM ORB (v12-approx) + 14:00-15:00 continuation
# ─────────────────────────────────────────────────────────────────────────────
def sim_pm_orb_v12(days):
    """Approx of live PM leg: OR 13:00-13:14, entry close +-2pt 13:15-14:00,
    OR range <= 60pt, stop 22pt, target 2.5R (55pt), flat 15:55."""
    out = {}
    for d in sorted(days):
        if d.weekday() > 4:
            continue
        day = days[d]
        i0, i1 = win(day, 780, 795)
        if i1 - i0 < 15:
            continue
        orh, orl = day["h"][i0:i1].max(), day["l"][i0:i1].min()
        if orh - orl > 60.0:
            continue
        j0, j1 = win(day, 795, 841)
        for j in range(j0, j1):
            cl = day["c"][j]
            side = 1 if cl > orh + 2.0 else (-1 if cl < orl - 2.0 else 0)
            if side == 0 or j + 1 >= len(day["m"]):
                continue
            ei = j + 1
            entry = day["o"][ei]
            stop = entry - side * 22.0
            tgt = entry + side * 55.0
            px, tag, xm = sim_bracket(day, ei, side, entry, stop, tgt, 955)
            t = make_trade(d, side, entry, px, tag)
            t["entry_m"], t["exit_m"] = int(day["m"][j] + 1), int(xm)
            out[d] = t
            break
    return out


def w5_cont(days, pm, variant, rr):
    """On PM-trigger days only, trade PM direction after 14:00.
    variant 'open14': enter 14:00 open, stop 22, no tgt, flat 15:55.
    variant 'mini':   14:00-14:14 range, break in PM dir +2pt 14:15-15:00,
                      stop 18, target rr*R, flat 15:55."""
    trades = []
    for d, pmt in sorted(pm.items()):
        day = days[d]
        side = pmt["side"]
        if variant == "open14":
            ei = first_bar_at_or_after(day, 840, before=955)
            if ei is None:
                continue
            entry = day["o"][ei]
            stop = entry - side * 22.0
            px, tag, _ = sim_bracket(day, ei, side, entry, stop, None, 955)
            trades.append(make_trade(d, side, entry, px, tag))
        else:
            i0, i1 = win(day, 840, 855)
            if i1 - i0 < 15:
                continue
            mh, ml = day["h"][i0:i1].max(), day["l"][i0:i1].min()
            j0, j1 = win(day, 855, 901)
            for j in range(j0, j1):
                cl = day["c"][j]
                trig = (side > 0 and cl > mh + 2.0) or (side < 0 and cl < ml - 2.0)
                if not trig or j + 1 >= len(day["m"]):
                    continue
                ei = j + 1
                entry = day["o"][ei]
                stop = entry - side * 18.0
                tgt = entry + side * rr * 18.0
                px, tag, _ = sim_bracket(day, ei, side, entry, stop, tgt, 955)
                trades.append(make_trade(d, side, entry, px, tag))
                break
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# v12 daily-stream correlation
# ─────────────────────────────────────────────────────────────────────────────
def v12_correlation(trades, label):
    v = pd.read_csv(V12_STREAM, parse_dates=["date"])
    v["date"] = v["date"].dt.date
    v12 = v.set_index("date")["pnl"]
    daily = defaultdict(float)
    for t in trades:
        daily[t["date"]] += t["usd"]
    s = pd.Series(daily).sort_index()
    if len(s) < 3:
        print(f"    {label}: too few days for correlation")
        return
    joint = s.index.intersection(v12.index)
    all_days = s.index.union(v12.index)
    su = s.reindex(all_days, fill_value=0.0)
    vu = v12.reindex(all_days, fill_value=0.0)
    r_joint = np.corrcoef(s.reindex(joint), v12.reindex(joint))[0, 1] if len(joint) >= 3 else float("nan")
    r_union = np.corrcoef(su, vu)[0, 1]
    print(f"    {label}: corr vs v12 stream — joint days r={r_joint:+.3f} "
          f"(n={len(joint)}), union(fill0) r={r_union:+.3f}; "
          f"{len(joint)}/{len(s)} of its days are v12 trade days")


# ─────────────────────────────────────────────────────────────────────────────
# Sweep driver: IS-pick -> OOS-confirm
# ─────────────────────────────────────────────────────────────────────────────
def run_family(title, configs, note=""):
    """configs: list of (name, trades). Prints full IS/OOS grid, picks best IS PF
    (subject to N floors), prints year-by-year + DD for the pick."""
    print("\n" + "=" * 96)
    print(f"  {title}")
    if note:
        print(f"  {note}")
    print("=" * 96)
    rows = []
    for name, trades in configs:
        si, so = config_row(name, trades)
        rows.append((name, trades, si, so))
    eligible = [r for r in rows if r[2]["N"] >= MIN_IS_N and (r[2]["N"] + r[3]["N"]) >= MIN_FULL_N]
    if not eligible:
        print("  -> no config meets N floors (IS>=%d, full>=%d). FAMILY DEAD ON ARRIVAL." % (MIN_IS_N, MIN_FULL_N))
        return None
    best = max(eligible, key=lambda r: r[2]["PF"] if r[2]["PF"] != float("inf") else 99.0)
    name, trades, si, so = best
    print(f"\n  IS-pick: {name}")
    print(f"    IS : {fmt(si)}  maxDD=${si['dd']:,.0f}  worst=${si['worst']:,.0f}")
    print(f"    OOS: {fmt(so)}  maxDD=${so['dd']:,.0f}  worst=${so['worst']:,.0f}")
    year_table(trades, name)
    neg_is_years = count_neg_is_years(trades)
    survive = (si["PF"] >= 1.15 and so["PF"] >= 1.10 and si["net"] > 0 and so["net"] > 0
               and (si["N"] + so["N"]) >= MIN_FULL_N and neg_is_years <= 1)
    reason = f"; IS year-coherence FAIL ({neg_is_years}/3 IS years negative — carried-by-one-year mirage)" \
        if neg_is_years >= 2 else ""
    print(f"  -> {'SURVIVOR CANDIDATE' if survive else 'KILLED'} "
          f"(gate: IS PF>=1.15 & OOS PF>=1.10 & both nets>0 & N>={MIN_FULL_N} & <=1 negative IS year{reason})")
    return (name, trades, si, so, survive)


def count_neg_is_years(trades):
    by = defaultdict(float)
    for t in trades:
        by[t["date"].year] += t["usd"]
    return sum(1 for y in (2022, 2023, 2024) if by.get(y, 0.0) < 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# PART 2 — W2a deployable-risk refinement (the only structurally coherent family)
# Pre-registered rule: among FIXED-stop configs (house risk band 20-28pt =
# $400-560), pick max IS PF with IS N>=45 & full N>=80; survivor gate unchanged.
# ─────────────────────────────────────────────────────────────────────────────
def w2a_deepdive(days, ctx, pm):
    print("\n" + "=" * 96)
    print("  PART 2 — W2a DEPLOYABLE-RISK REFINEMENT (fixed stops inside the $415-565 house band)")
    print("=" * 96)
    grid = []
    for k in (1.25, 1.5):
        for spts in (20.0, 25.0, 28.0):
            grid.append((f"k={k:.2f} stop=fix{spts:.0f} hold->15:58", k, ("fix", spts)))
        grid.append((f"k={k:.2f} stop=0.25xrng (reference, NOT deployable)", k, ("frac", 0.25)))
    rows = []
    for name, k, spec in grid:
        tr = w2a_trend(days, ctx, k, spec, None)
        si, so = config_row(name, tr)
        rows.append((name, k, spec, tr, si, so))

    fixed = [r for r in rows if r[2][0] == "fix" and r[4]["N"] >= MIN_IS_N
             and (r[4]["N"] + r[5]["N"]) >= MIN_FULL_N]
    if not fixed:
        print("  -> no fixed-stop config meets N floors. W2a NOT DEPLOYABLE within risk budget.")
        return None
    best = max(fixed, key=lambda r: r[4]["PF"])

    def battery(name, k, spec, tr, si, so):
        print(f"\n  ── {name} " + "─" * max(1, 80 - len(name)))
        print(f"    IS : {fmt(si)}  maxDD=${si['dd']:,.0f}  worst=${si['worst']:,.0f}")
        print(f"    OOS: {fmt(so)}  maxDD=${so['dd']:,.0f}  worst=${so['worst']:,.0f}")
        year_table(tr, name)
        spts = spec[1]
        print(f"    risk: stop {spts:.0f}pt -> ${spts*POINT_VALUE+COST_RT:,.1f} all-in per stop-out "
              f"(house NQ band $415-565; v12 legs carry $414.5-565)")

        b = [dict(t, usd=t["usd"] - 10.0) for t in tr]
        bi, bo = split_eras(b)
        print(f"    +$10/RT slippage:       IS PF {si['PF']:.2f}->{summarize(bi)['PF']:.2f}, "
              f"OOS PF {so['PF']:.2f}->{summarize(bo)['PF']:.2f}")

        tr505 = w2a_trend(days, ctx, k, spec, None, entry_m=905)
        di, do = split_eras(tr505)
        sdi, sdo = summarize(di), summarize(do)
        print(f"    entry 15:05 not 15:00:  IS PF {sdi['PF']:.2f} (N={sdi['N']}), OOS PF {sdo['PF']:.2f} (N={sdo['N']})")

        tr80 = w2a_trend(days, ctx, k, spec, None, pos_th=0.80)
        ei_, eo_ = split_eras(tr80)
        sei, seo = summarize(ei_), summarize(eo_)
        print(f"    quartile 0.80 not 0.75: IS PF {sei['PF']:.2f} (N={sei['N']}), OOS PF {seo['PF']:.2f} (N={seo['N']})")

        for lbl, side in (("longs (up-trend days)", 1), ("shorts (down-trend days)", -1)):
            st = [t for t in tr if t["side"] == side]
            i2, o2 = split_eras(st)
            s2i, s2o = summarize(i2), summarize(o2)
            print(f"    {lbl:<24} IS: N={s2i['N']:>3} PF={s2i['PF']:.2f} ${s2i['net']:>7,.0f} | "
                  f"OOS: N={s2o['N']:>3} PF={s2o['PF']:.2f} ${s2o['net']:>7,.0f}")

        dows = defaultdict(list)
        for t in tr:
            dows[t["date"].weekday()].append(t)
        dn = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
        print("    DOW (info only, no filter): " + "; ".join(
            f"{dn[dw]} N={summarize(dows[dw])['N']} PF={summarize(dows[dw])['PF']:.2f}"
            for dw in sorted(dows)))

        usd = sorted((t["usd"] for t in tr), reverse=True)
        top5, net = sum(usd[:5]), sum(usd)
        sx = summarize(sorted(tr, key=lambda t: -t["usd"])[5:])
        print(f"    concentration (blunt):  top-5 winners ${top5:,.0f} = "
              f"{100*top5/net if net else 0:.0f}% of net; PF excl top-5 = {sx['PF']:.2f}")
        is_t, oos_t = split_eras(tr)
        sxi = summarize(sorted(is_t, key=lambda t: -t["usd"])[3:])
        sxo = summarize(sorted(oos_t, key=lambda t: -t["usd"])[3:])
        print(f"    tail replicability:     IS PF excl its top-3 = {sxi['PF']:.2f}; "
              f"OOS PF excl its top-3 = {sxo['PF']:.2f} "
              f"(hold-to-close designs are tail-harvesters; demand tail in BOTH eras)")
        print("    top-5 winning trades:")
        for t in sorted(tr, key=lambda t: -t["usd"])[:5]:
            print(f"      {t['date']}  {'SHORT' if t['side']<0 else 'LONG ':5}  "
                  f"{t['pts']:+7.1f}pt  ${t['usd']:+8,.0f}")

        w2a_days = set(t["date"] for t in tr)
        pm_open_15 = sum(1 for d in w2a_days if d in pm and pm[d]["exit_m"] > 900)
        pm_days = sum(1 for d in w2a_days if d in pm)
        print(f"    collision: {pm_days}/{len(w2a_days)} W2a days had a PM-ORB trade; "
              f"PM position still open at 15:00 on {pm_open_15} ({100*pm_open_15/len(w2a_days):.0f}% of W2a days)")
        pmd = {d: t["usd"] for d, t in pm.items()}
        w2d = defaultdict(float)
        for t in tr:
            w2d[t["date"]] += t["usd"]
        j = sorted(set(pmd) & set(w2d))
        if len(j) >= 3:
            r = np.corrcoef([pmd[d] for d in j], [w2d[d] for d in j])[0, 1]
            print(f"    W2a vs PM-leg daily P&L corr on co-fire days: r={r:+.3f} (n={len(j)})")
        v12_correlation(tr, name)

        # shorts-only cut (post-hoc split, flagged as such — structural rationale:
        # close-hour continuation is historically stronger on liquidation days)
        sh = [t for t in tr if t["side"] < 0]
        shi, sho = split_eras(sh)
        print(f"    SHORT-ONLY cut (post-hoc, would need pre-registration):")
        print(f"      IS : {fmt(summarize(shi))}")
        print(f"      OOS: {fmt(summarize(sho))}")
        year_table(sh, "short-only")

    name, k, spec, tr, si, so = best
    survive = (si["PF"] >= 1.15 and so["PF"] >= 1.10 and si["net"] > 0 and so["net"] > 0
               and count_neg_is_years(tr) <= 1)
    print(f"\n  IS-pick (fixed-stop only): {name}  -> {'SURVIVOR' if survive else 'KILLED'} at deployable risk")
    if spec[1] * POINT_VALUE + COST_RT > 565.0:
        print(f"  NOTE: pick's all-in stop-out ${spec[1]*POINT_VALUE+COST_RT:.1f} EXCEEDS the $565 band top;")
        print(f"        band-compliant twin (stop=25pt, $514.5 all-in) battery-tested below as the")
        print(f"        deployment recommendation — risk-constraint override, NOT a performance pick")
        print(f"        (its IS PF is lower).")
    battery(name, k, spec, tr, si, so)

    for want, label in (("k=1.50 stop=fix25", "BAND-COMPLIANT TWIN ($514.5 all-in) — deployment recommendation if survivor"),
                        ("k=1.25 stop=fix25", "HIGHER-N SIBLING (band-compliant, ~2x trade count)")):
        alt = next((r for r in rows if want in r[0]), None)
        if alt is not None and alt[0] != name:
            print(f"\n  {label}:")
            battery(alt[0], alt[1], alt[2], alt[3], alt[4], alt[5])
    return (name, tr, si, so, survive)


def main():
    days = load_days()
    ctx = build_context(days)

    dcount = defaultdict(int)
    for d in days:
        dcount[d.weekday()] += 1
    log(f"weekday date counts: Mon={dcount[0]} Tue={dcount[1]} Wed={dcount[2]} "
        f"Thu={dcount[3]} Fri={dcount[4]} Sun={dcount[6]}")

    results = {}

    # ── W1 ──────────────────────────────────────────────────────────────────
    log("W1: 8:30 release impulse sweep ...")
    cfgs = []
    for det in ("A", "B"):
        for k in (2.0, 3.0, 4.0):
            for mode in ("cont", "fade"):
                for rr in (1.5, 2.0, 3.0):
                    cfgs.append((f"det{det} k={k:.0f} {mode} tgt={rr}R",
                                 w1_impulse(days, ctx, det, k, rr, mode)))
    results["W1"] = run_family(
        "W1 — 8:30 ECONOMIC-RELEASE IMPULSE (flat 9:25; zero overlap with v12 9:46 start)",
        cfgs, "spike day: 8:30 range >= k x 20-day median; detA=1-min bar entry 8:31, detB=5-min entry 8:35")

    # ── W2a ─────────────────────────────────────────────────────────────────
    log("W2a: close-hour trend continuation sweep ...")
    cfgs = []
    for k in (1.0, 1.25, 1.5):
        for sf in (0.15, 0.25):
            cfgs.append((f"rng>={k:.2f}xmed stop={sf:.2f}xrng hold->15:58",
                         w2a_trend(days, ctx, k, ("frac", sf), None)))
    cfgs.append(("rng>=1.25xmed stop=0.25xrng tgt=1R",
                 w2a_trend(days, ctx, 1.25, ("frac", 0.25), 1.0)))
    results["W2a"] = run_family(
        "W2a — CLOSE-HOUR TREND-DAY CONTINUATION (enter 15:00 in day direction, flat 15:58)",
        cfgs, "day range 9:30-15:00 vs 20-day median RTH range; price in top/bottom quartile at 15:00")

    # ── W2b ─────────────────────────────────────────────────────────────────
    log("W2b: 15:30 mini-ORB sweep ...")
    cfgs = []
    for buf in (1.0, 2.0):
        for sm in ("or", "fx15"):
            for rr in (1.5, 2.0):
                cfgs.append((f"buf={buf:.0f} stop={sm} tgt={rr}R",
                             w2b_miniorb(days, buf, sm, rr)))
    results["W2b"] = run_family(
        "W2b — 15:30 MINI-ORB (OR 15:30-15:34, entry 15:35-15:50, flat 15:58)", cfgs)

    # ── W3a ─────────────────────────────────────────────────────────────────
    log("W3a: Monday weekend-gap sweep ...")
    cfgs = []
    for mode in ("fade", "go"):
        for g in (0.15, 0.30):
            for sx in (0.5, 1.0):
                for tx in (0.5, 1.0):
                    cfgs.append((f"{mode} gap>={g:.2f}xmed stop={sx}xgap tgt={tx}xgap",
                                 w3a_monday_gap(days, ctx, g, mode, sx, tx)))
    # deployable-risk variants: fixed stop inside the house $415-565 band
    for spts in (25.0, 28.0):
        for tx in (0.5, 1.0):
            cfgs.append((f"fade gap>=0.30xmed stop=fix{spts:.0f} tgt={tx}xgap",
                         w3a_monday_gap(days, ctx, 0.30, "fade", None, tx, stop_fix=spts)))
    results["W3a"] = run_family(
        "W3a — MONDAY WEEKEND-GAP 9:30 (fade vs go; flat 10:55; v12 skips Monday mornings)",
        cfgs, "gap = Mon 9:30 open - Fri RTH close; threshold relative to 20-day median RTH range")

    # ── W3b ─────────────────────────────────────────────────────────────────
    log("W3b: Sunday reopen drift sweep ...")
    cfgs = []
    for sig in ("m15", "gap"):
        for mode in ("cont", "fade"):
            for mmin in (0.0, 5.0) if sig == "m15" else (10.0, 20.0):
                cfgs.append((f"sig={sig} {mode} |mv|>={mmin:.0f}pt stop=20 flat20:00",
                             w3b_sunday(days, ctx, sig, mode, mmin, 20.0)))
    results["W3b"] = run_family(
        "W3b — SUNDAY 18:00 REOPEN DRIFT 18:00-20:00 (v12 Asia leg needs same-day 16:00 close -> dark on Sunday)",
        cfgs)

    # ── W4 ──────────────────────────────────────────────────────────────────
    log("W4: midnight Globex sweep ...")
    cfgs = []
    for stop in (15.0, 25.0):
        for rr in (1.0, 1.5, None):
            tag = f"tgt={rr}R" if rr else "no-tgt flat02"
            cfgs.append((f"ONbreak buf=2 stop={stop:.0f} {tag}",
                         w4_break(days, 2.0, stop, rr)))
    for mode in ("cont", "fade"):
        cfgs.append((f"drift18->00 {mode} |mv|>=10 stop=20", w4_drift(days, mode, 10.0, 20.0)))
    results["W4"] = run_family(
        "W4 — MIDNIGHT GLOBEX 00:00-02:00 (ON range 18:00-23:59 break; evening-drift continuation/fade)",
        cfgs)

    # ── W5 ──────────────────────────────────────────────────────────────────
    log("W5: post-14:00 PM continuation ...")
    pm = sim_pm_orb_v12(days)
    pm_trades = list(pm.values())
    print("\n" + "=" * 96)
    print("  W5 — 14:00-15:00 CONTINUATION OF PM ORB DIRECTION  (fires ONLY on v12-PM trigger days)")
    print("=" * 96)
    print("  reference: v12-approx PM ORB itself (OR 13:00-13:14, +-2pt 13:15-14:00, ORmax60, 22pt/2.5R):")
    config_row("PM-ORB v12-approx (reference only)", pm_trades)
    cfgs = [("open14: enter 14:00 open, stop 22, hold->15:55", w5_cont(days, pm, "open14", None)),
            ("mini: 14:00-14:14 range-break PMdir, stop 18, 1.5R", w5_cont(days, pm, "mini", 1.5)),
            ("mini: 14:00-14:14 range-break PMdir, stop 18, 2.0R", w5_cont(days, pm, "mini", 2.0))]
    results["W5"] = run_family("W5 grid (interaction with live PM leg reported below regardless)", cfgs)
    # Interaction is structural: report it for the best W5 config even if killed.
    if results["W5"]:
        name, trades, *_ = results["W5"]
        both = set(t["date"] for t in trades) & set(pm.keys())
        print(f"\n  W5 INTERACTION: {len(both)}/{len(trades)} W5 trades occur on days the live PM leg traded (=100% by construction).")
        pm_daily = {d: t["usd"] for d, t in pm.items()}
        w5_daily = defaultdict(float)
        for t in trades:
            w5_daily[t["date"]] += t["usd"]
        j = sorted(set(pm_daily) & set(w5_daily))
        if len(j) >= 3:
            r = np.corrcoef([pm_daily[d] for d in j], [w5_daily[d] for d in j])[0, 1]
            print(f"  W5 vs PM-leg daily P&L correlation on co-fire days: r={r:+.3f} (n={len(j)})")
        v12_correlation(trades, f"W5 [{name}]")

    # ── Survivor deep-dive: correlation + slippage robustness ───────────────
    print("\n" + "=" * 96)
    print("  SURVIVOR DEEP-DIVE (v12-stream correlation, +1-tick slippage robustness, risk fit)")
    print("=" * 96)
    any_surv = False
    for wname, res in results.items():
        if res is None or not res[4]:
            continue
        any_surv = True
        name, trades, si, so, _ = res
        print(f"\n  {wname} [{name}]")
        v12_correlation(trades, f"{wname}")
        bumped = [dict(t, usd=t["usd"] - 10.0) for t in trades]  # +1 tick/side = $10/RT
        bi, bo = split_eras(bumped)
        sbi, sbo = summarize(bi), summarize(bo)
        print(f"    +$10/RT slippage stress: IS PF {si['PF']:.2f}->{sbi['PF']:.2f}, "
              f"OOS PF {so['PF']:.2f}->{sbo['PF']:.2f}")
        risks = [abs(t["pts"]) for t in trades if t["tag"] == "stop"]
        mr = max(risks) if risks else 0.0
        print(f"    per-trade stop-outs: worst {mr:.1f}pt = ${mr*POINT_VALUE:,.0f} + costs; "
              f"house NQ risk constants $415-565; -$500 day-halt; $1,200 DLL")
    if not any_surv:
        print("\n  (no survivors flagged by the gate)")

    # ── Part 2: W2a at deployable risk ───────────────────────────────────────
    log("PART 2: W2a deployable-risk refinement ...")
    w2a_deepdive(days, ctx, pm)

    log("done.")


if __name__ == "__main__":
    main()
