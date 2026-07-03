"""
brain/research/price_action_search.py

Pure price-action edge search — NO date/DOW/month filters.
If the edge can't survive all days and all months it isn't real.

1. OR Retest Entry         — price breaks OR, retests the boundary, continue
2. Volatility Compression  — NQ compresses tight then explodes
3. Trend Day Pullback      — large initial move, fade first deep pullback
4. Failed Breakout Reversal— false OR break traps players, reverse it

All use 1 contract. Walk-forward: IS=2024 / OOS=2025-Jun 2026.
"""

import csv, math
from datetime import datetime, timedelta
from collections import defaultdict

PV   = 20.0
COST = 9.50

def load_bars(path="data/nq_1min.csv"):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            dt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            rows.append({"dt": dt,
                         "open":  float(r["open"]),
                         "high":  float(r["high"]),
                         "low":   float(r["low"]),
                         "close": float(r["close"]),
                         "vol":   float(r.get("volume", 1))})
    rows.sort(key=lambda x: x["dt"])
    return rows

def session_date(dt):
    return (dt + timedelta(days=1)).date() if dt.hour >= 18 else dt.date()

def build_sessions(bars):
    d = defaultdict(list)
    for b in bars:
        d[session_date(b["dt"])].append(b)
    return dict(d)

def us_bars(session_bars, sd):
    return [b for b in session_bars
            if b["dt"].date() == sd
            and "09:30" <= b["dt"].strftime("%H:%M") < "16:00"]

def sim(bars_after, entry, stop_px, target_px, is_long, flatten_dt):
    for b in bars_after:
        if b["dt"] >= flatten_dt:
            pnl = (b["open"] - entry) if is_long else (entry - b["open"])
            return pnl, "flatten"
        if is_long:
            if b["low"]  <= stop_px:   return stop_px   - entry, "stop"
            if b["high"] >= target_px: return target_px - entry, "target"
        else:
            if b["high"] >= stop_px:   return entry - stop_px,   "stop"
            if b["low"]  <= target_px: return entry - target_px, "target"
    return 0, "eod"

def trade(sd, name, is_long, pnl_pts, outcome):
    return {"date": sd, "strat": name,
            "dir": "L" if is_long else "S",
            "pnl_pts": pnl_pts,
            "net": round(pnl_pts * PV - COST, 2),
            "outcome": outcome}

# ─────────────────────────────────────────────────────────────────────────────
# 1. OR Retest Entry
#
#    Setup: ORB signal fires (close > OR_hi + 4pt or < OR_lo - 4pt)
#    Wait:  price pulls back and TOUCHES the OR boundary (within 2pt)
#    Enter: on close above/below the OR level again (confirmation)
#    Stop:  10pt (tight — the OR level is the invalidation)
#    Target: 3R = 30pt
#    Logic:  the broken OR level becomes S/R. Institutional money re-enters
#            here. Higher WR than initial breakout because direction confirmed.
# ─────────────────────────────────────────────────────────────────────────────

def or_retest(sessions, stop=10, rr=3.0, retest_zone=2.0):
    trades = []

    for sd, bars in sorted(sessions.items()):
        today = us_bars(bars, sd)
        if not today: continue

        # Build OR
        or_bars = [b for b in today if b["dt"].strftime("%H:%M") < "09:45"]
        if len(or_bars) < 10: continue
        or_hi = max(b["high"] for b in or_bars)
        or_lo = min(b["low"]  for b in or_bars)
        or_rng = or_hi - or_lo
        if not (55 <= or_rng <= 110): continue   # same filter as main ORB

        BRK = 4.0
        flatten_dt = datetime(sd.year, sd.month, sd.day, 15, 30)

        # Phase 1: find initial breakout
        entry_bars = [b for b in today
                      if "09:45" <= b["dt"].strftime("%H:%M") < "10:30"]
        initial_dir = None
        breakout_bar_idx = None
        for i, b in enumerate(today):
            t = b["dt"].strftime("%H:%M")
            if t < "09:45" or t >= "10:30": continue
            if b["close"] > or_hi + BRK:
                initial_dir = "long"; breakout_bar_idx = i; break
            elif b["close"] < or_lo - BRK:
                initial_dir = "short"; breakout_bar_idx = i; break

        if not initial_dir: continue

        # Phase 2: wait for retest of OR level
        is_long = initial_dir == "long"
        level   = or_hi if is_long else or_lo
        retest_found = False
        retest_idx   = None

        for i in range(breakout_bar_idx + 1, len(today)):
            b = today[i]
            t = b["dt"].strftime("%H:%M")
            if t >= "14:00": break   # too late

            if is_long:
                # Price must pull back to within retest_zone of or_hi
                if b["low"] <= level + retest_zone:
                    retest_found = True; retest_idx = i; break
            else:
                if b["high"] >= level - retest_zone:
                    retest_found = True; retest_idx = i; break

        if not retest_found: continue

        # Phase 3: entry confirmation — next bar that closes back on the breakout side
        entry_px = None
        entry_idx = None
        for i in range(retest_idx + 1, len(today)):
            b = today[i]
            t = b["dt"].strftime("%H:%M")
            if t >= "14:00": break
            if is_long  and b["close"] > level + 1.0:
                entry_px = b["close"]; entry_idx = i; break
            if not is_long and b["close"] < level - 1.0:
                entry_px = b["close"]; entry_idx = i; break

        if entry_px is None: continue

        stop_p = entry_px - stop if is_long else entry_px + stop
        tgt_p  = entry_px + stop * rr if is_long else entry_px - stop * rr

        pnl, out = sim(today[entry_idx+1:], entry_px, stop_p, tgt_p, is_long, flatten_dt)
        trades.append(trade(sd, "OR_Retest", is_long, pnl, out))

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# 2. Volatility Compression Breakout
#
#    Setup: NQ's 20-bar ATR drops below X% of its 60-bar baseline
#           (the market is coiling — about to move)
#    Enter: when price closes outside the compression channel
#    Stop:  half the compression range
#    Target: 2.5R
#    Logic:  volatility mean-reverts. Low vol leads to high vol.
#            Institutional algorithms detect this and add directional exposure.
# ─────────────────────────────────────────────────────────────────────────────

def vol_compression(sessions, compression_ratio=0.5, rr=2.5, flatten_hhmm="15:30"):
    trades = []

    for sd, bars in sorted(sessions.items()):
        today = us_bars(bars, sd)
        if len(today) < 80: continue

        flatten_dt = datetime(sd.year, sd.month, sd.day,
                              int(flatten_hhmm[:2]), int(flatten_hhmm[3:]))
        traded = False

        for idx in range(60, len(today)):
            if traded: break
            b = today[idx]
            t = b["dt"].strftime("%H:%M")
            if t < "10:00" or t >= "14:30": continue

            # ATR over last 20 bars
            window20 = today[idx-20:idx]
            atr20 = sum(max(w["high"]-w["low"],
                            abs(w["high"]-today[idx-20+j-1]["close"]) if idx-20+j > 0 else 0,
                            abs(w["low"] -today[idx-20+j-1]["close"]) if idx-20+j > 0 else 0)
                        for j, w in enumerate(window20)) / 20

            # Baseline ATR over last 60 bars
            window60 = today[idx-60:idx]
            atr60 = sum(max(w["high"]-w["low"],
                            abs(w["high"]-today[idx-60+j-1]["close"]) if idx-60+j > 0 else 0,
                            abs(w["low"] -today[idx-60+j-1]["close"]) if idx-60+j > 0 else 0)
                        for j, w in enumerate(window60)) / 60

            if atr60 < 1 or atr20 / atr60 > compression_ratio: continue

            # Price must break out of the 20-bar channel
            chan_hi = max(w["high"] for w in window20)
            chan_lo = min(w["low"]  for w in window20)
            chan_mid = (chan_hi + chan_lo) / 2

            if   b["close"] > chan_hi: is_long = True
            elif b["close"] < chan_lo: is_long = False
            else: continue

            half_range = (chan_hi - chan_lo) / 2
            if half_range < 5: continue   # channel too tight to be meaningful

            entry  = b["close"]
            stop_p = entry - half_range if is_long else entry + half_range
            tgt_p  = entry + half_range * rr if is_long else entry - half_range * rr

            pnl, out = sim(today[idx+1:], entry, stop_p, tgt_p, is_long, flatten_dt)
            trades.append(trade(sd, "Vol_Comp", is_long, pnl, out))
            traded = True

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# 3. Trend Day First Pullback
#
#    Setup: NQ drives 50pt+ in first 30 min from open (trend day signal)
#    Entry: when price pulls back 15-25pt from the extreme
#    Stop:  20pt beyond the pullback low/high
#    Target: 3R
#    Logic:  on genuine trend days, the first pullback is shallow and
#            the trend resumes. Institutions use the dip to add.
#            Key filter: trend must be ONE-DIRECTIONAL (not volatile).
# ─────────────────────────────────────────────────────────────────────────────

def trend_day_pullback(sessions, initial_move=50, pb_min=12, pb_max=28, stop=20, rr=3.0):
    trades = []

    for sd, bars in sorted(sessions.items()):
        today = us_bars(bars, sd)
        if not today: continue

        flatten_dt = datetime(sd.year, sd.month, sd.day, 15, 30)
        open_px = today[0]["open"]

        # Check 30-min move
        bars_30 = [b for b in today if b["dt"].strftime("%H:%M") < "10:00"]
        if len(bars_30) < 25: continue

        extreme_hi = max(b["high"] for b in bars_30)
        extreme_lo = min(b["low"]  for b in bars_30)
        move_up    = extreme_hi - open_px
        move_dn    = open_px - extreme_lo

        if move_up >= initial_move and move_up > move_dn * 1.5:
            is_long = True;  extreme = extreme_hi
        elif move_dn >= initial_move and move_dn > move_up * 1.5:
            is_long = False; extreme = extreme_lo
        else:
            continue   # not a clean directional day

        # Wait for pullback of pb_min to pb_max points from extreme
        traded = False
        for idx, b in enumerate(today):
            if traded: break
            t = b["dt"].strftime("%H:%M")
            if t < "10:00" or t >= "14:00": continue

            if is_long:
                pb = extreme_hi - b["low"]
            else:
                pb = b["high"] - extreme_lo

            if not (pb_min <= pb <= pb_max): continue

            # Confirmation: close should be moving back in trend direction
            if is_long  and b["close"] < b["open"]: continue  # still dropping
            if not is_long and b["close"] > b["open"]: continue  # still rising

            entry  = b["close"]
            stop_p = entry - stop if is_long else entry + stop
            tgt_p  = entry + stop * rr if is_long else entry - stop * rr

            pnl, out = sim(today[idx+1:], entry, stop_p, tgt_p, is_long, flatten_dt)
            trades.append(trade(sd, "TrendPB", is_long, pnl, out))
            traded = True

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# 4. Failed Breakout Reversal
#
#    Setup: price breaks the OR (long signal) but closes BACK INSIDE
#           within 3 bars → the breakout has failed
#    Enter: fade in the opposite direction of the failed break
#    Stop:  above the failed breakout high (or below low)
#    Target: 2R
#    Logic:  a failed breakout TRAPS traders who chased it. They must exit
#            their losing trade, accelerating the reversal.
#            One of the cleanest institutional patterns.
# ─────────────────────────────────────────────────────────────────────────────

def failed_breakout(sessions, stop=15, rr=2.0, max_bars_to_fail=4):
    trades = []

    for sd, bars in sorted(sessions.items()):
        today = us_bars(bars, sd)
        if not today: continue

        or_bars = [b for b in today if b["dt"].strftime("%H:%M") < "09:45"]
        if len(or_bars) < 10: continue
        or_hi = max(b["high"] for b in or_bars)
        or_lo = min(b["low"]  for b in or_bars)
        if not (40 <= or_hi - or_lo <= 120): continue

        BRK        = 4.0
        flatten_dt = datetime(sd.year, sd.month, sd.day, 14, 0)
        traded     = False

        entry_bars = [b for b in today if "09:45" <= b["dt"].strftime("%H:%M") < "11:00"]

        for i, b in enumerate(entry_bars):
            if traded: break

            broke_high = b["close"] > or_hi + BRK
            broke_low  = b["close"] < or_lo - BRK

            if not broke_high and not broke_low: continue

            # Watch next max_bars_to_fail bars for a close back inside
            failed = False
            fail_bar = None
            for j in range(1, max_bars_to_fail + 1):
                if i + j >= len(entry_bars): break
                nb = entry_bars[i + j]
                if broke_high and nb["close"] < or_hi:
                    failed = True; fail_bar = nb; break
                if broke_low  and nb["close"] > or_lo:
                    failed = True; fail_bar = nb; break

            if not failed or not fail_bar: continue

            # Fade the failed breakout
            is_long = broke_low   # broke low → failed → fade UP (buy)
            entry   = fail_bar["close"]
            extreme = b["high"] if not is_long else b["low"]
            stop_p  = extreme + 2 if not is_long else extreme - 2
            actual_stop = abs(entry - stop_p)
            if actual_stop < 5 or actual_stop > 30: continue
            tgt_p   = entry + actual_stop * rr if is_long else entry - actual_stop * rr

            fail_global = today.index(fail_bar)
            pnl, out = sim(today[fail_global+1:], entry, stop_p, tgt_p, is_long, flatten_dt)
            trades.append(trade(sd, "Failed_BO", is_long, pnl, out))
            traded = True

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────────────────────────────────────

def stats(ts):
    if not ts: return None
    wins = [t for t in ts if t["net"] > 0]
    gw   = sum(t["net"] for t in wins)
    gl   = sum(t["net"] for t in ts if t["net"] <= 0)
    net  = sum(t["net"] for t in ts)
    return {"n": len(ts), "wr": len(wins)/len(ts),
            "pf": gw/abs(gl) if gl else 99.0,
            "net": net, "avg": net/len(ts)}

def show(trades, name):
    s_is  = stats([t for t in trades if t["date"].year < 2025])
    s_oos = stats([t for t in trades if t["date"].year >= 2025])
    s_all = stats(trades)
    if not s_all: print(f"\n  {name}: no trades"); return

    print(f"\n  ── {name}")
    print(f"  {'Period':12} {'N':>5} {'WR':>7} {'PF':>6} {'Net $':>9} {'Avg/T':>8}")
    if s_is:
        print(f"  {'IS  2024':12} {s_is['n']:>5} {s_is['wr']:>6.0%} "
              f"{s_is['pf']:>6.2f} {s_is['net']:>9,.0f} {s_is['avg']:>8,.0f}")
    if s_oos:
        v = ("✓ EDGE"    if s_oos["pf"] >= 1.4 and s_oos["n"] >= 40 else
             "~ worth it" if s_oos["pf"] >= 1.2 and s_oos["n"] >= 30 else
             "~ marginal" if s_oos["pf"] >= 1.1 else "✗ no edge")
        print(f"  {'OOS 2025+':12} {s_oos['n']:>5} {s_oos['wr']:>6.0%} "
              f"{s_oos['pf']:>6.2f} {s_oos['net']:>9,.0f} {s_oos['avg']:>8,.0f}  {v}")
    print(f"  {'All':12} {s_all['n']:>5} {s_all['wr']:>6.0%} "
          f"{s_all['pf']:>6.2f} {s_all['net']:>9,.0f} {s_all['avg']:>8,.0f}")

    # Outcome breakdown
    oc = defaultdict(int)
    for t in trades: oc[t["outcome"]] += 1
    print(f"  Exits: {dict(sorted(oc.items()))}")


if __name__ == "__main__":
    print("Loading NQ 1-min data...")
    bars     = load_bars()
    sessions = build_sessions(bars)
    print(f"  {len(bars):,} bars · {len(sessions)} sessions")

    print("\nRunning price-action strategies (no date filters)...")
    t1 = or_retest(sessions);            print(f"  OR Retest:       {len(t1)}")
    t2 = vol_compression(sessions);      print(f"  Vol Compression: {len(t2)}")
    t3 = trend_day_pullback(sessions);   print(f"  Trend Pullback:  {len(t3)}")
    t4 = failed_breakout(sessions);      print(f"  Failed BO:       {len(t4)}")

    W = 68
    print(f"\n{'='*W}")
    print(f"  PRICE ACTION SEARCH — NQ 1-min  (IS=2024 / OOS=2025-Jun2026)")
    print(f"  No date filters. No DOW filters. No month filters.")
    print(f"{'='*W}")
    show(t1, "1. OR Retest Entry         [10pt stop, 3R — BRC pattern]")
    show(t2, "2. Volatility Compression  [half-range stop, 2.5R — coil breakout]")
    show(t3, "3. Trend Day First Pullback [20pt stop, 3R — institutional re-entry]")
    show(t4, "4. Failed Breakout Reversal [dynamic stop, 2R — trapped traders]")
    print()
