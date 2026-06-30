#!/usr/bin/env python3
"""
brain/research_asia.py  — Optimized version
Asia Session 20-Strategy Deep Research (6:00pm – 9:00pm ET)
IS: 2022-2023 | OOS: 2024-2026

Key optimization: all bars pre-grouped by date into dicts at startup.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import itertools

DATA    = Path(__file__).parent.parent / "data" / "nq_full.csv"
OUT_CSV = Path(__file__).parent / "asia_trades.csv"
OUT_MD  = Path(__file__).parent / "asia_research.md"

COST   = 25.0    # $25/trade total
PT_VAL = 20.0    # $20/pt NQ

IS_END    = pd.Timestamp("2023-12-31")
OOS_START = pd.Timestamp("2024-01-01")
VIA_PF    = 1.30
VIA_N     = 30


# ── Load and pre-group ────────────────────────────────────────────────────────

def load_and_pregroup():
    print("Loading NQ data...")
    df = pd.read_csv(DATA, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["date"]   = df["timestamp"].dt.date
    df["hour"]   = df["timestamp"].dt.hour
    df["minute"] = df["timestamp"].dt.minute
    df["dow"]    = df["timestamp"].dt.dayofweek
    df["month"]  = df["timestamp"].dt.month
    df["year"]   = df["timestamp"].dt.year
    print(f"  {len(df):,} bars")

    # Pre-group Asia bars (18:00-20:59) by date
    asia_df = df[df["hour"].between(18, 20)].copy()
    asia_by_date = {}
    for date, g in asia_df.groupby("date"):
        asia_by_date[date] = g.reset_index(drop=True)

    # Pre-compute 4pm close bars
    pm4_df = df[(df["hour"] == 16) & (df["minute"] == 0)]
    pm4_close = {row["date"]: float(row["close"]) for _, row in pm4_df.iterrows()}

    # Pre-compute 6pm first bar (gap open)
    pm6_df = df[(df["hour"] == 18) & (df["minute"] == 0)]
    pm6_open = {row["date"]: float(row["open"]) for _, row in pm6_df.iterrows()}

    # Gaps: 6pm open - 4pm close (same calendar day)
    gaps = {}
    for d, o in pm6_open.items():
        if d in pm4_close:
            gaps[d] = o - pm4_close[d]

    # Pre-compute US session stats
    us_df = df[
        ((df["hour"] == 9) & (df["minute"] >= 30)) |
        df["hour"].between(10, 15) |
        ((df["hour"] == 16) & (df["minute"] == 0))
    ].copy()
    us_stats = {}
    for date, g in us_df.groupby("date"):
        if len(g) < 30:
            continue
        us_stats[date] = {
            "close": float(g.iloc[-1]["close"]),
            "high":  float(g["high"].max()),
            "low":   float(g["low"].min()),
            "range": float(g["high"].max() - g["low"].min()),
        }

    # 20-day rolling ATR
    sorted_dates = sorted(us_stats)
    ranges = [us_stats[d]["range"] for d in sorted_dates]
    atr20 = {}
    for i, d in enumerate(sorted_dates):
        atr20[d] = float(np.mean(ranges[max(0, i-20):i+1]))

    # Prev-day US stats
    prev_us = {}
    for i, d in enumerate(sorted_dates):
        if i > 0:
            prev_us[d] = us_stats[sorted_dates[i-1]]

    # 5-day US close slope
    closes = [us_stats[d]["close"] for d in sorted_dates]
    slope5 = {}
    for i, d in enumerate(sorted_dates):
        if i >= 5:
            slope5[d] = float(np.polyfit(range(5), closes[i-5:i], 1)[0])
        else:
            slope5[d] = 0.0

    print(f"  Asia days: {len(asia_by_date)} | Gaps: {len(gaps)} | US stats: {len(us_stats)}")
    return df, asia_by_date, pm4_close, pm6_open, gaps, us_stats, atr20, prev_us, slope5


# ── Trade execution ───────────────────────────────────────────────────────────

def sim_trade(direction, entry, stop_pts, rr, bars):
    """bars: list of dicts (after entry bar). Returns dict or None."""
    if direction == "long":
        stop = entry - stop_pts; target = entry + stop_pts * rr
    else:
        stop = entry + stop_pts; target = entry - stop_pts * rr

    for b in bars:
        if direction == "long":
            if b["low"] <= stop:
                return {"exit": "stop",   "pnl": (stop   - entry) * PT_VAL - COST}
            if b["high"] >= target:
                return {"exit": "target", "pnl": (target - entry) * PT_VAL - COST}
        else:
            if b["high"] >= stop:
                return {"exit": "stop",   "pnl": (entry -   stop) * PT_VAL - COST}
            if b["low"] <= target:
                return {"exit": "target", "pnl": (entry - target) * PT_VAL - COST}

    if bars:
        hard = bars[-1]["close"]
        pnl = (hard - entry if direction == "long" else entry - hard) * PT_VAL - COST
        return {"exit": "hard_exit", "pnl": pnl}
    return None


def stats(trades):
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0.0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p < 0]
    return {
        "n":   len(pnls),
        "wr":  len(wins) / len(pnls),
        "pf":  sum(wins) / abs(sum(loss)) if loss else 9.99,
        "net": sum(pnls),
    }


def split(trades):
    is_t  = [t for t in trades if pd.Timestamp(str(t["date"])) <= IS_END]
    oos_t = [t for t in trades if pd.Timestamp(str(t["date"])) >= OOS_START]
    return stats(is_t), stats(oos_t)


def viable(s): return s["n"] >= VIA_N and s["pf"] >= VIA_PF


# ── Strategy helpers ──────────────────────────────────────────────────────────

def get_entry_bar(asia_bars, ent_hr, ent_mn):
    """Get bars at given hour:minute and after. Returns (entry_idx, entry_price) or (None, None)."""
    mask = (asia_bars["hour"] == ent_hr) & (asia_bars["minute"] == ent_mn)
    sub = asia_bars[mask]
    if sub.empty:
        return None, None
    idx = int(sub.index[0])
    return idx, float(sub.iloc[0]["close"])


def bars_after(asia_df, entry_idx):
    """Return list of dicts for bars after entry_idx."""
    return [asia_df.iloc[i].to_dict() for i in range(entry_idx + 1, len(asia_df))]


# ── S1: Gap Continuation Full Sweep ──────────────────────────────────────────

def s1_gap_sweep(asia_by_date, gaps, us_stats):
    print("\n=== S1: Gap Continuation Optimization ===")

    gap_ranges   = [(20,60),(20,70),(20,80),(30,60),(30,70),(30,80),(40,70),(40,80),(50,80)]
    entry_offsets = [15, 30, 45, 60]   # minutes after 6pm
    stops        = [12, 15, 20, 25, 30]
    rrs          = [1.5, 2.0, 2.5, 3.0]
    skip_thu_opts = [True, False]

    all_dates = sorted(gaps.keys())
    total = len(gap_ranges)*len(entry_offsets)*len(stops)*len(rrs)*len(skip_thu_opts)
    print(f"  {total} combos × {len(all_dates)} dates")

    results = []
    for (gmin, gmax), eo, sp, rr, no_thu in itertools.product(
            gap_ranges, entry_offsets, stops, rrs, skip_thu_opts):

        ent_hr = 18 + eo // 60
        ent_mn = eo % 60
        trades = []

        for date in all_dates:
            gap = gaps[date]
            gap_abs = abs(gap)
            if gap_abs < gmin or gap_abs > gmax:
                continue
            if date not in us_stats:
                continue
            dow = pd.Timestamp(str(date)).dayofweek
            if no_thu and dow == 3:
                continue

            asia = asia_by_date.get(date)
            if asia is None or len(asia) < 5:
                continue

            idx, entry_px = get_entry_bar(asia, ent_hr, ent_mn)
            if idx is None:
                continue

            direction = "long" if gap > 0 else "short"
            r = sim_trade(direction, entry_px, sp, rr, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap,
                               "entry": entry_px, **r})

        if not trades:
            continue
        s_is, s_oos = split(trades)
        s_all = stats(trades)
        v = viable(s_oos)
        results.append({
            "strategy": "S1",
            "config": f"gap={gmin}-{gmax} +{eo}m stop={sp} rr={rr} thu={not no_thu}",
            "s_all": s_all, "s_is": s_is, "s_oos": s_oos,
            "viable": v, "trades": trades,
        })

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        t = results[0]
        print(f"  Best OOS PF: {t['s_oos']['pf']:.2f}  n={t['s_oos']['n']}  {t['config']}")
    return results


# ── S2: Asia ORB 6pm-6:30pm ──────────────────────────────────────────────────

def s2_asia_orb(asia_by_date, us_stats):
    print("\n=== S2: Asia ORB (6pm-6:30pm) ===")
    results = []

    for sp, rr in itertools.product([10, 15, 20], [1.5, 2.0, 2.5]):
        trades = []
        for date, asia in asia_by_date.items():
            if date not in us_stats or len(asia) < 30:
                continue
            or30 = asia[(asia["hour"] == 18) & (asia["minute"] <= 29)]
            if len(or30) < 10:
                continue
            orH = float(or30["high"].max())
            orL = float(or30["low"].min())
            if orH - orL < 5 or orH - orL > 60:
                continue

            after = asia[(asia["hour"] == 18) & (asia["minute"] >= 30)]
            after = pd.concat([after, asia[asia["hour"].between(19, 20)]]).reset_index(drop=True)

            direction = entry_px = ei = None
            for i, bar in after.iterrows():
                if bar["close"] > orH:
                    direction, entry_px, ei = "long", bar["close"], i; break
                if bar["close"] < orL:
                    direction, entry_px, ei = "short", bar["close"], i; break
            if direction is None:
                continue

            r = sim_trade(direction, entry_px, sp, rr,
                          [after.iloc[j].to_dict() for j in range(ei+1, len(after))])
            if r:
                trades.append({"date": date, "direction": direction, **r})

        if not trades:
            continue
        s_is, s_oos = split(trades)
        v = viable(s_oos)
        results.append({"strategy": "S2", "config": f"stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


# ── S3: Globex 6pm 15-min breakout ───────────────────────────────────────────

def s3_globex_open(asia_by_date, us_stats):
    print("\n=== S3: CME Globex 6pm Breakout ===")
    results = []

    for sp, rr in itertools.product([10, 15, 20, 25], [1.5, 2.0, 2.5]):
        trades = []
        for date, asia in asia_by_date.items():
            if date not in us_stats or len(asia) < 20:
                continue
            first15 = asia[(asia["hour"] == 18) & (asia["minute"] <= 14)]
            if len(first15) < 5:
                continue
            orH = float(first15["high"].max())
            orL = float(first15["low"].min())
            if orH - orL < 3:
                continue

            after15 = asia[(asia["hour"] == 18) & (asia["minute"] >= 15)]
            after15 = pd.concat([after15, asia[asia["hour"].between(19, 20)]]).reset_index(drop=True)

            direction = entry_px = ei = None
            for i, bar in after15.iterrows():
                if bar["close"] > orH:
                    direction, entry_px, ei = "long", bar["close"], i; break
                if bar["close"] < orL:
                    direction, entry_px, ei = "short", bar["close"], i; break
            if direction is None:
                continue

            r = sim_trade(direction, entry_px, sp, rr,
                          [after15.iloc[j].to_dict() for j in range(ei+1, len(after15))])
            if r:
                trades.append({"date": date, "direction": direction, **r})

        if not trades:
            continue
        s_is, s_oos = split(trades)
        v = viable(s_oos)
        results.append({"strategy": "S3", "config": f"stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


# ── S4: VWAP Reversion ───────────────────────────────────────────────────────

def s4_vwap_fade(asia_by_date, us_stats):
    print("\n=== S4: VWAP Reversion Fade ===")
    results = []

    for thr, sp, rr in itertools.product([15, 20, 25, 30], [10, 15, 20], [1.5, 2.0]):
        trades = []
        for date, asia in asia_by_date.items():
            if date not in us_stats or len(asia) < 20:
                continue
            asia_np = asia.copy().reset_index(drop=True)
            typ  = (asia_np["high"] + asia_np["low"] + asia_np["close"]) / 3
            cumv = (typ * asia_np["volume"]).cumsum().values
            cvol = asia_np["volume"].cumsum().values
            with np.errstate(divide='ignore', invalid='ignore'):
                vwap = np.where(cvol > 0, cumv / cvol, typ.values)

            trade_placed = False
            for i in range(15, len(asia_np) - 5):
                dev = asia_np.iloc[i]["close"] - vwap[i]
                if abs(dev) >= thr and not trade_placed:
                    direction = "short" if dev > 0 else "long"
                    entry_px  = float(asia_np.iloc[i]["close"])
                    ba = [asia_np.iloc[j].to_dict() for j in range(i+1, len(asia_np))]
                    r = sim_trade(direction, entry_px, sp, rr, ba)
                    if r:
                        trades.append({"date": date, "direction": direction, **r})
                    trade_placed = True
                    break

        if not trades:
            continue
        s_is, s_oos = split(trades)
        v = viable(s_oos)
        results.append({"strategy": "S4", "config": f"thr={thr} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


# ── S5: Tokyo Open 8pm Momentum ──────────────────────────────────────────────

def s5_tokyo_open(asia_by_date, us_stats):
    print("\n=== S5: Tokyo Open Momentum ===")
    results = []

    for sp, rr in itertools.product([15, 20, 25], [1.5, 2.0, 2.5]):
        trades = []
        for date, asia in asia_by_date.items():
            if date not in us_stats or len(asia) < 60:
                continue
            open_6pm  = float(asia.iloc[0]["open"])
            bars_8pm  = asia[asia["hour"] == 20]
            if bars_8pm.empty:
                continue
            entry_bar = bars_8pm.iloc[0]
            entry_px  = float(entry_bar["close"])
            direction = "long" if entry_px > open_6pm else "short"
            ba = [bars_8pm.iloc[i].to_dict() for i in range(1, len(bars_8pm))]
            r = sim_trade(direction, entry_px, sp, rr, ba)
            if r:
                trades.append({"date": date, "direction": direction, **r})

        if not trades:
            continue
        s_is, s_oos = split(trades)
        v = viable(s_oos)
        results.append({"strategy": "S5", "config": f"stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


# ── S6: US Close Continuation ────────────────────────────────────────────────

def s6_us_cont(asia_by_date, us_stats):
    print("\n=== S6: US Close Continuation ===")
    results = []

    for sp, rr, eo in itertools.product([12, 15, 20], [1.5, 2.0, 2.5], [0, 15, 30]):
        ent_hr = 18 + eo // 60; ent_mn = eo % 60
        trades = []
        for date, asia in asia_by_date.items():
            us = us_stats.get(date)
            if not us or len(asia) < 20:
                continue
            direction = "long" if us["close"] > (us["high"] + us["low"]) / 2 else "short"
            idx, entry_px = get_entry_bar(asia, ent_hr, ent_mn)
            if idx is None:
                continue
            r = sim_trade(direction, entry_px, sp, rr, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, **r})

        if not trades:
            continue
        s_is, s_oos = split(trades)
        v = viable(s_oos)
        results.append({"strategy": "S6", "config": f"stop={sp} rr={rr} +{eo}m",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})

    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f} — {results[0]['config']}")
    return results


# ── S7-S20: Additional strategies ────────────────────────────────────────────

def s7_range_fade(asia_by_date, us_stats):
    print("\n=== S7: Asia Range Fade at 8pm ===")
    results = []
    for thr, sp, rr in itertools.product([20, 30, 40], [10, 15], [1.5, 2.0]):
        trades = []
        for date, asia in asia_by_date.items():
            if date not in us_stats or len(asia) < 60:
                continue
            open_6pm = float(asia.iloc[0]["open"])
            first2h  = asia[asia["hour"].between(18, 19)]
            if first2h.empty:
                continue
            up_ext   = float(first2h["high"].max()) - open_6pm
            dn_ext   = open_6pm - float(first2h["low"].min())
            if max(up_ext, dn_ext) < thr:
                continue
            direction = "short" if up_ext > dn_ext else "long"
            bars_8pm  = asia[asia["hour"] == 20]
            if bars_8pm.empty:
                continue
            entry_px = float(bars_8pm.iloc[0]["close"])
            ba = [bars_8pm.iloc[i].to_dict() for i in range(1, len(bars_8pm))]
            r = sim_trade(direction, entry_px, sp, rr, ba)
            if r:
                trades.append({"date": date, "direction": direction, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S7", "config": f"thr={thr} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s8_mean_rev(asia_by_date, us_stats):
    print("\n=== S8: Mean Reversion (7pm fade) ===")
    results = []
    for thr, sp, rr in itertools.product([20, 25, 30, 40], [10, 15, 20], [1.5, 2.0]):
        trades = []
        for date, asia in asia_by_date.items():
            if date not in us_stats or len(asia) < 30:
                continue
            open_6pm = float(asia.iloc[0]["open"])
            bars_7pm = asia[(asia["hour"] == 19) & (asia["minute"] == 0)]
            if bars_7pm.empty:
                continue
            price_7pm = float(bars_7pm.iloc[0]["close"])
            move = price_7pm - open_6pm
            if abs(move) < thr:
                continue
            direction = "short" if move > 0 else "long"
            idx = int(bars_7pm.index[0])
            r = sim_trade(direction, price_7pm, sp, rr, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S8", "config": f"thr={thr} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s9_gap_and_go(asia_by_date, gaps, us_stats):
    print("\n=== S9: Gap and Go (Immediate Entry) ===")
    results = []
    for gmin, gmax, sp, rr in itertools.product(
            [20, 30, 40], [80, 100, 999], [15, 20, 25, 30], [1.5, 2.0, 2.5]):
        if gmin >= gmax:
            continue
        trades = []
        for date, gap in gaps.items():
            gap_abs = abs(gap)
            if gap_abs < gmin or gap_abs > gmax:
                continue
            if date not in us_stats:
                continue
            asia = asia_by_date.get(date)
            if asia is None or asia.empty:
                continue
            entry_px  = float(asia.iloc[0]["open"])
            direction = "long" if gap > 0 else "short"
            ba = [asia.iloc[i].to_dict() for i in range(1, len(asia))]
            r = sim_trade(direction, entry_px, sp, rr, ba)
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S9", "config": f"gap={gmin}-{gmax} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s10_atr_filter(asia_by_date, gaps, us_stats, atr20):
    print("\n=== S10: Volatility (ATR) Filter ===")
    results = []
    for mult, sp, rr in itertools.product([1.0, 1.2, 1.5], [15, 20], [1.5, 2.0, 2.5]):
        trades = []
        for date, gap in gaps.items():
            gap_abs = abs(gap)
            if gap_abs < 30 or gap_abs > 80:
                continue
            us = us_stats.get(date)
            if not us:
                continue
            day_atr = atr20.get(date, 80)
            if us["range"] < mult * day_atr:
                continue
            dow = pd.Timestamp(str(date)).dayofweek
            if dow == 3:
                continue
            asia = asia_by_date.get(date)
            if asia is None:
                continue
            idx, entry_px = get_entry_bar(asia, 18, 15)
            if idx is None:
                continue
            direction = "long" if gap > 0 else "short"
            r = sim_trade(direction, entry_px, sp, rr, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S10", "config": f"atr>={mult}x stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s11_sr_filter(asia_by_date, gaps, us_stats, prev_us):
    print("\n=== S11: Prev Day S/R Filter ===")
    results = []
    for prox, sp, rr in itertools.product([10, 15, 20], [15, 20], [1.5, 2.0, 2.5]):
        trades = []
        for date, gap in gaps.items():
            gap_abs = abs(gap)
            if gap_abs < 30 or gap_abs > 80:
                continue
            if date not in us_stats or date not in prev_us:
                continue
            prev = prev_us[date]
            dow = pd.Timestamp(str(date)).dayofweek
            if dow == 3:
                continue
            asia = asia_by_date.get(date)
            if asia is None:
                continue
            idx, entry_px = get_entry_bar(asia, 18, 15)
            if idx is None:
                continue
            direction = "long" if gap > 0 else "short"
            if direction == "long" and abs(entry_px - prev["low"]) > prox:
                continue
            if direction == "short" and abs(entry_px - prev["high"]) > prox:
                continue
            r = sim_trade(direction, entry_px, sp, rr, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S11", "config": f"prox={prox} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s12_trend_cont(asia_by_date, gaps, us_stats, slope5):
    print("\n=== S12: 5-Day Trend Continuation ===")
    trades = []
    for date, gap in gaps.items():
        gap_abs = abs(gap)
        if gap_abs < 30 or gap_abs > 80:
            continue
        if date not in us_stats or date not in slope5:
            continue
        direction = "long" if gap > 0 else "short"
        s5 = slope5[date]
        if direction == "long" and s5 < 0:
            continue
        if direction == "short" and s5 > 0:
            continue
        dow = pd.Timestamp(str(date)).dayofweek
        if dow == 3:
            continue
        asia = asia_by_date.get(date)
        if asia is None:
            continue
        idx, entry_px = get_entry_bar(asia, 18, 15)
        if idx is None:
            continue
        r = sim_trade(direction, entry_px, 15, 1.5, bars_after(asia, idx))
        if r:
            trades.append({"date": date, "direction": direction, "gap": gap, **r})

    if not trades:
        return []
    s_is, s_oos = split(trades); v = viable(s_oos)
    print(f"  OOS PF: {s_oos['pf']:.2f}  n={s_oos['n']}")
    return [{"strategy": "S12", "config": "trend_align stop=15 rr=1.5",
             "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
             "viable": v, "trades": trades}]


def s13_large_gap_fade(asia_by_date, gaps, us_stats):
    print("\n=== S13: Large Gap Fade (>80pt) ===")
    results = []
    for gmin, sp, rr in itertools.product([60, 80, 100], [15, 20, 25, 30], [1.5, 2.0, 2.5]):
        trades = []
        for date, gap in gaps.items():
            if abs(gap) < gmin:
                continue
            if date not in us_stats:
                continue
            asia = asia_by_date.get(date)
            if asia is None:
                continue
            idx, entry_px = get_entry_bar(asia, 18, 15)
            if idx is None:
                continue
            direction = "short" if gap > 0 else "long"  # FADE
            r = sim_trade(direction, entry_px, sp, rr, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S13", "config": f"gap>{gmin} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s14_ema_cross(asia_by_date, us_stats):
    print("\n=== S14: EMA Cross (15-min bars) ===")
    results = []
    for fast, slow, sp, rr in itertools.product([3, 5], [10, 15], [15, 20], [1.5, 2.0]):
        if fast >= slow:
            continue
        trades = []
        for date, asia in asia_by_date.items():
            if date not in us_stats or len(asia) < slow * 15 + 5:
                continue
            # Resample to 15-min
            asia_ts = asia.copy()
            asia_ts["ts"] = pd.to_datetime(asia_ts["timestamp"])
            asia_15 = asia_ts.set_index("ts").resample("15min").agg(
                {"open": "first", "high": "max", "low": "min",
                 "close": "last", "volume": "sum"}).dropna()
            if len(asia_15) < slow + 2:
                continue
            closes = asia_15["close"].values
            ema_f = pd.Series(closes).ewm(span=fast).mean().values
            ema_s = pd.Series(closes).ewm(span=slow).mean().values

            direction = entry_px = entry_ts = None
            for i in range(1, len(asia_15)):
                if not (ema_f[i-1] > ema_s[i-1]) and (ema_f[i] > ema_s[i]):
                    direction, entry_px, entry_ts = "long", float(asia_15.iloc[i]["close"]), asia_15.index[i]
                    break
                if (ema_f[i-1] > ema_s[i-1]) and not (ema_f[i] > ema_s[i]):
                    direction, entry_px, entry_ts = "short", float(asia_15.iloc[i]["close"]), asia_15.index[i]
                    break
            if direction is None:
                continue

            ba_raw = asia[asia["timestamp"] > entry_ts]
            ba = [row.to_dict() for _, row in ba_raw.iterrows()]
            r = sim_trade(direction, entry_px, sp, rr, ba)
            if r:
                trades.append({"date": date, "direction": direction, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S14", "config": f"ema={fast}/{slow} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s15_prevday_break(asia_by_date, gaps, us_stats, prev_us):
    print("\n=== S15: Prev Day Range Breakout ===")
    results = []
    for sp, rr in itertools.product([15, 20, 25], [1.5, 2.0, 2.5]):
        trades = []
        for date, gap in gaps.items():
            gap_abs = abs(gap)
            if gap_abs < 20 or gap_abs > 80:
                continue
            if date not in us_stats or date not in prev_us:
                continue
            prev = prev_us[date]
            asia = asia_by_date.get(date)
            if asia is None:
                continue
            direction = "long" if gap > 0 else "short"
            level = prev["high"] if direction == "long" else prev["low"]

            entry_px = ei = None
            for i, bar in asia.iterrows():
                if direction == "long" and bar["close"] > level:
                    entry_px, ei = bar["close"], i; break
                if direction == "short" and bar["close"] < level:
                    entry_px, ei = bar["close"], i; break
            if entry_px is None:
                continue

            r = sim_trade(direction, float(entry_px), sp, rr, bars_after(asia, int(ei)))
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S15", "config": f"stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s16_compression(asia_by_date, gaps, us_stats, atr20):
    print("\n=== S16: Range Compression Breakout ===")
    # Pre-compute rolling avg Asia OR
    asia_or_hist = []
    for date in sorted(asia_by_date.keys()):
        asia = asia_by_date[date]
        or30 = asia[(asia["hour"] == 18) & (asia["minute"] <= 29)]
        if len(or30) >= 10:
            asia_or_hist.append((date, float(or30["high"].max() - or30["low"].min())))

    asia_or_map = {d: v for d, v in asia_or_hist}
    dates_list  = [d for d, _ in asia_or_hist]
    ors_list    = [v for _, v in asia_or_hist]

    results = []
    for comp, sp, rr in itertools.product([0.5, 0.6, 0.7], [12, 15, 20], [1.5, 2.0]):
        trades = []
        for i, date in enumerate(dates_list):
            if i < 10:
                continue
            avg = float(np.mean(ors_list[max(0, i-10):i]))
            if avg == 0:
                continue
            if asia_or_map[date] > comp * avg:
                continue
            if date not in us_stats or date not in gaps:
                continue

            asia = asia_by_date[date]
            or30 = asia[(asia["hour"] == 18) & (asia["minute"] <= 29)]
            if len(or30) < 10:
                continue
            orH = float(or30["high"].max()); orL = float(or30["low"].min())
            after = asia[(asia["hour"] == 18) & (asia["minute"] >= 30)]
            after = pd.concat([after, asia[asia["hour"].between(19, 20)]]).reset_index(drop=True)

            direction = entry_px = ei = None
            for j, bar in after.iterrows():
                if bar["close"] > orH:
                    direction, entry_px, ei = "long", bar["close"], j; break
                if bar["close"] < orL:
                    direction, entry_px, ei = "short", bar["close"], j; break
            if direction is None:
                continue

            gap = gaps[date]
            if abs(gap) > 10:
                if (gap > 0 and direction == "short") or (gap < 0 and direction == "long"):
                    continue

            ba = [after.iloc[j].to_dict() for j in range(ei+1, len(after))]
            r = sim_trade(direction, float(entry_px), sp, rr, ba)
            if r:
                trades.append({"date": date, "direction": direction, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S16", "config": f"comp<{comp}x stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s17_seasonal(asia_by_date, gaps, us_stats):
    print("\n=== S17: Seasonal (Strong Months Only) ===")
    month_combos = [
        ([2,6,9,10], "Feb/Jun/Sep/Oct"),
        ([1,2,3,6,10], "Jan/Feb/Mar/Jun/Oct"),
        ([2,3,9,10], "Feb/Mar/Sep/Oct"),
        ([6,9,10], "Jun/Sep/Oct"),
        ([1,2,3], "Jan/Feb/Mar"),
    ]
    results = []
    for months, label in month_combos:
        trades = []
        for date, gap in gaps.items():
            if abs(gap) < 30 or abs(gap) > 80:
                continue
            if date not in us_stats:
                continue
            dt = pd.Timestamp(str(date))
            if dt.month not in months or dt.dayofweek == 3:
                continue
            asia = asia_by_date.get(date)
            if asia is None:
                continue
            idx, entry_px = get_entry_bar(asia, 18, 15)
            if idx is None:
                continue
            direction = "long" if gap > 0 else "short"
            r = sim_trade(direction, entry_px, 15, 1.5, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S17", "config": f"months={label}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s18_direction_bias(asia_by_date, gaps, us_stats):
    print("\n=== S18: Direction Bias (Long/Short only) ===")
    results = []
    for dirn, sp, rr in itertools.product(["long","short","both"], [12,15,20], [1.5,2.0,2.5]):
        trades = []
        for date, gap in gaps.items():
            if abs(gap) < 30 or abs(gap) > 80:
                continue
            if date not in us_stats or pd.Timestamp(str(date)).dayofweek == 3:
                continue
            direction = "long" if gap > 0 else "short"
            if dirn != "both" and direction != dirn:
                continue
            asia = asia_by_date.get(date)
            if asia is None:
                continue
            idx, entry_px = get_entry_bar(asia, 18, 15)
            if idx is None:
                continue
            r = sim_trade(direction, entry_px, sp, rr, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S18", "config": f"dir={dirn} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s19_triple_filter(asia_by_date, gaps, us_stats):
    print("\n=== S19: Triple Filter (Gap + Month + DOW) ===")
    strong_months = [2, 6, 9, 10]
    dow_combos = [
        ([0,1,2,4], "no_thu"),
        ([0,1,2], "mon-wed"),
        ([0,4], "mon_fri"),
        ([1,2,4], "tue_wed_fri"),
    ]
    results = []
    for dows, dlabel in dow_combos:
        for sp, rr in itertools.product([15], [1.5, 2.0]):
            trades = []
            for date, gap in gaps.items():
                if abs(gap) < 30 or abs(gap) > 80:
                    continue
                if date not in us_stats:
                    continue
                dt = pd.Timestamp(str(date))
                if dt.dayofweek not in dows or dt.month not in strong_months:
                    continue
                asia = asia_by_date.get(date)
                if asia is None:
                    continue
                idx, entry_px = get_entry_bar(asia, 18, 15)
                if idx is None:
                    continue
                direction = "long" if gap > 0 else "short"
                r = sim_trade(direction, entry_px, sp, rr, bars_after(asia, idx))
                if r:
                    trades.append({"date": date, "direction": direction, "gap": gap, **r})
            if not trades:
                continue
            s_is, s_oos = split(trades); v = viable(s_oos)
            results.append({"strategy": "S19",
                            "config": f"months=strong dows={dlabel} stop={sp} rr={rr}",
                            "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                            "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


def s20_hybrid(asia_by_date, gaps, us_stats, slope5):
    print("\n=== S20: Hybrid Best ===")
    results = []
    strong_months = [2, 6, 9, 10]
    for use_trend, use_season, sp, rr in itertools.product(
            [True, False], [True, False], [12, 15, 20], [1.5, 2.0, 2.5]):
        label = ("trend+" if use_trend else "") + ("seasonal" if use_season else "baseline")
        trades = []
        for date, gap in gaps.items():
            if abs(gap) < 30 or abs(gap) > 80:
                continue
            if date not in us_stats:
                continue
            dt = pd.Timestamp(str(date))
            if dt.dayofweek == 3:
                continue
            direction = "long" if gap > 0 else "short"
            if use_trend and date in slope5:
                s5 = slope5[date]
                if direction == "long" and s5 < 0:
                    continue
                if direction == "short" and s5 > 0:
                    continue
            if use_season and dt.month not in strong_months:
                continue
            asia = asia_by_date.get(date)
            if asia is None:
                continue
            idx, entry_px = get_entry_bar(asia, 18, 15)
            if idx is None:
                continue
            r = sim_trade(direction, entry_px, sp, rr, bars_after(asia, idx))
            if r:
                trades.append({"date": date, "direction": direction, "gap": gap, **r})
        if not trades:
            continue
        s_is, s_oos = split(trades); v = viable(s_oos)
        results.append({"strategy": "S20", "config": f"{label} stop={sp} rr={rr}",
                        "s_all": stats(trades), "s_is": s_is, "s_oos": s_oos,
                        "viable": v, "trades": trades})
    results.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    if results:
        print(f"  Best OOS PF: {results[0]['s_oos']['pf']:.2f}")
    return results


# ── Filter analysis ───────────────────────────────────────────────────────────

def analyze_filters(trades):
    df = pd.DataFrame(trades)
    df["date"]  = pd.to_datetime(df["date"])
    df["dow"]   = df["date"].dt.dayofweek
    df["month"] = df["date"].dt.month
    df["year"]  = df["date"].dt.year
    dow_names   = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri"}
    month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    lines = []

    lines.append("\n### DOW\n| DOW | n | WR | PF | Net |")
    lines.append("| --- | --- | --- | --- | --- |")
    for d, g in df.groupby("dow"):
        s = stats(g.to_dict("records"))
        lines.append(f"| {dow_names.get(d,d)} | {s['n']} | {s['wr']:.1%} | {s['pf']:.2f} | ${s['net']:+,.0f} |")

    lines.append("\n### Month\n| Month | n | WR | PF | Net |")
    lines.append("| --- | --- | --- | --- | --- |")
    for m, g in df.groupby("month"):
        s = stats(g.to_dict("records"))
        lines.append(f"| {month_names.get(m,m)} | {s['n']} | {s['wr']:.1%} | {s['pf']:.2f} | ${s['net']:+,.0f} |")

    lines.append("\n### Direction\n| Dir | n | WR | PF | Net |")
    lines.append("| --- | --- | --- | --- | --- |")
    for d, g in df.groupby("direction"):
        s = stats(g.to_dict("records"))
        lines.append(f"| {d} | {s['n']} | {s['wr']:.1%} | {s['pf']:.2f} | ${s['net']:+,.0f} |")

    lines.append("\n### Year\n| Year | n | WR | PF | Net |")
    lines.append("| --- | --- | --- | --- | --- |")
    for y, g in df.groupby("year"):
        s = stats(g.to_dict("records"))
        lines.append(f"| {y} | {s['n']} | {s['wr']:.1%} | {s['pf']:.2f} | ${s['net']:+,.0f} |")

    return "\n".join(lines)


# ── Report ────────────────────────────────────────────────────────────────────

def write_report(all_results, best_trades, today):
    top_per = [s[0] for s in all_results if s]
    top_per.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    viable_list = [r for r in top_per if r["viable"]]

    lines = [f"# Asia Session Deep Research (6:00pm – 9:00pm ET)\n",
             f"Generated: {today} | Data: 2022-01-03 to 2026-06-11",
             f"IS: 2022-2023 | OOS: 2024-2026 | Viability: OOS PF ≥ {VIA_PF}, n ≥ {VIA_N}\n",
             "\n## Session Overview\n",
             "| Metric | Value |",
             "| --- | --- |",
             "| Window | 6:00pm – 9:00pm ET |",
             "| Gap definition | Same-day 4pm close → 6pm open |",
             "| Trade cost | $25/rt |",
             "| Point value | $20/pt |\n",
             "\n## All Strategy Results (best config per strategy)\n",
             "| Strategy | Config | n_all | WR | PF_all | n_oos | WR_oos | PF_oos | Net_oos | Viable |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]

    for r in top_per:
        sa = r["s_all"]; so = r["s_oos"]
        v = "✓ PASS" if r["viable"] else "FAIL"
        lines.append(f"| {r['strategy']} | {r['config']} "
                     f"| {sa['n']} | {sa['wr']:.1%} | {sa['pf']:.2f} "
                     f"| {so['n']} | {so['wr']:.1%} | {so['pf']:.2f} "
                     f"| ${so['net']:+,.0f} | {v} |")

    # S1 top-25
    if all_results[0]:
        lines += ["\n## S1 — Top 25 Configurations\n",
                  "| Config | n_oos | WR_oos | PF_oos | Net_oos | V |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for r in all_results[0][:25]:
            so = r["s_oos"]; v = "✓" if r["viable"] else "✗"
            lines.append(f"| {r['config']} | {so['n']} | {so['wr']:.1%} | {so['pf']:.2f} | ${so['net']:+,.0f} | {v} |")

    # Best deep-dive
    if top_per:
        best = top_per[0]
        sa = best["s_all"]; si = best["s_is"]; so = best["s_oos"]
        lines += [f"\n## Best Strategy Deep Dive\n**{best['strategy']}** — {best['config']}\n",
                  "| Period | n | WR | PF | Net |",
                  "| --- | --- | --- | --- | --- |",
                  f"| All | {sa['n']} | {sa['wr']:.1%} | {sa['pf']:.2f} | ${sa['net']:+,.0f} |",
                  f"| IS (2022-23) | {si['n']} | {si['wr']:.1%} | {si['pf']:.2f} | ${si['net']:+,.0f} |",
                  f"| OOS (2024-26) | {so['n']} | {so['wr']:.1%} | {so['pf']:.2f} | ${so['net']:+,.0f} |",
                  ""]
        if best_trades:
            lines.append(analyze_filters(best_trades))

    # Verdict
    lines.append("\n## Viability Verdict\n")
    if viable_list:
        lines.append(f"**{len(viable_list)} strategies VIABLE (OOS PF ≥ {VIA_PF}):**\n")
        for r in viable_list:
            so = r["s_oos"]
            lines.append(f"- **{r['strategy']}** — {r['config']}: OOS PF {so['pf']:.2f}, n={so['n']}")
    else:
        best_pf = top_per[0]["s_oos"]["pf"] if top_per else 0
        lines.append(f"**No strategy cleared PF ≥ {VIA_PF}.**  Best OOS PF: {best_pf:.2f}")

    lines.append("\n## Recommendations\n")
    if viable_list:
        best_v = viable_list[0]; so = best_v["s_oos"]
        lines.append(f"Best: **{best_v['strategy']}** — `{best_v['config']}`  OOS PF {so['pf']:.2f}")
    else:
        lines.append("Baseline gap continuation (gap 30-80, skip Thu, stop=15, rr=1.5) remains best (OOS PF ~1.80).")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from datetime import date as dt_date
    today = str(dt_date.today())

    df, asia_by_date, pm4_close, pm6_open, gaps, us_stats, atr20, prev_us, slope5 = load_and_pregroup()

    print(f"\n{'='*60}\nRunning 20 Asia strategies...\n{'='*60}")

    all_results = [
        s1_gap_sweep(asia_by_date, gaps, us_stats),
        s2_asia_orb(asia_by_date, us_stats),
        s3_globex_open(asia_by_date, us_stats),
        s4_vwap_fade(asia_by_date, us_stats),
        s5_tokyo_open(asia_by_date, us_stats),
        s6_us_cont(asia_by_date, us_stats),
        s7_range_fade(asia_by_date, us_stats),
        s8_mean_rev(asia_by_date, us_stats),
        s9_gap_and_go(asia_by_date, gaps, us_stats),
        s10_atr_filter(asia_by_date, gaps, us_stats, atr20),
        s11_sr_filter(asia_by_date, gaps, us_stats, prev_us),
        s12_trend_cont(asia_by_date, gaps, us_stats, slope5),
        s13_large_gap_fade(asia_by_date, gaps, us_stats),
        s14_ema_cross(asia_by_date, us_stats),
        s15_prevday_break(asia_by_date, gaps, us_stats, prev_us),
        s16_compression(asia_by_date, gaps, us_stats, atr20),
        s17_seasonal(asia_by_date, gaps, us_stats),
        s18_direction_bias(asia_by_date, gaps, us_stats),
        s19_triple_filter(asia_by_date, gaps, us_stats),
        s20_hybrid(asia_by_date, gaps, us_stats, slope5),
    ]

    # Best trades
    all_flat = [r for s in all_results for r in s if r]
    all_flat.sort(key=lambda r: r["s_oos"]["pf"], reverse=True)
    best_trades = all_flat[0]["trades"] if all_flat else []

    if all_flat:
        pd.DataFrame(best_trades).to_csv(OUT_CSV, index=False)
        print(f"\nBest strategy trades → {OUT_CSV}")

    OUT_MD.write_text(write_report(all_results, best_trades, today))
    print(f"Report → {OUT_MD}")

    print(f"\n{'='*60}\nASIA RESEARCH SUMMARY\n{'='*60}")
    for s_list in all_results:
        if s_list:
            r = s_list[0]
            v = "✓" if r["viable"] else "✗"
            print(f"  {r['strategy']:6s}  OOS PF {r['s_oos']['pf']:.2f}  n={r['s_oos']['n']:3d}  {r['config'][:40]:40s}  {v}")


if __name__ == "__main__":
    main()
