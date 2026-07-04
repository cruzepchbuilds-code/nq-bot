"""
brain/research/extended_hours.py

Extended-hours strategy research — NQ 1-min data (full 23-hr sessions).
Tests four strategies that run outside the core 9:30–10:30 ORB window.

1. London Open Breakout    — midnight–3 AM range, 3 AM breakout, 15pt stop, 2R
2. Overnight Mean Reversion — fade overnight drift >60pt from prior US close
3. Pre-Market Momentum     — 9 AM pre-mkt push >30pt, hold into US open
4. Asia Range Fade         — fade range extremes 10 PM–2 AM back to midpoint

Walk-forward: IS=2024 / OOS=2025-Jun2026
"""

import csv
from datetime import datetime, date, timedelta
from collections import defaultdict

PV   = 20.0   # NQ $20/point
COST = 9.50   # round-trip commission

# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────

def load_bars(path="data/nq_1min.csv"):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            dt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            rows.append({"dt": dt,
                         "open":  float(r["open"]),
                         "high":  float(r["high"]),
                         "low":   float(r["low"]),
                         "close": float(r["close"])})
    rows.sort(key=lambda x: x["dt"])
    return rows

def session_date(dt):
    """US calendar date this bar's session belongs to."""
    return (dt + timedelta(days=1)).date() if dt.hour >= 18 else dt.date()

def build_sessions(bars):
    d = defaultdict(list)
    for b in bars:
        d[session_date(b["dt"])].append(b)
    return dict(d)

# ─────────────────────────────────────────────────────────────────────────────
# Trade simulator
# ─────────────────────────────────────────────────────────────────────────────

def sim(bars_after, entry, stop_px, target_px, is_long, flatten_dt):
    """Simulate bar-by-bar; return (pnl_points, outcome)."""
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

def make_trade(sd, name, is_long, pnl_pts, outcome):
    return {"date": sd, "strat": name,
            "dir":  "L" if is_long else "S",
            "pnl_pts": pnl_pts,
            "net": pnl_pts * PV - COST,
            "outcome": outcome}

# ─────────────────────────────────────────────────────────────────────────────
# 1. London Open Breakout
#    Range: midnight–3 AM ET  |  Entry: 3–5 AM ET  |  Flatten: 9 AM ET
#    Stop: 15pt fixed  |  Target: 2R (30pt)  |  OR filter: 20–80pt
# ─────────────────────────────────────────────────────────────────────────────

def london_bo(sessions):
    trades = []
    for sd, bars in sorted(sessions.items()):
        if sd.weekday() >= 5: continue

        # Range bars: 00:00–02:59 ET on session date
        rng = [b for b in bars
               if b["dt"].date() == sd and b["dt"].hour < 3]
        if len(rng) < 10: continue

        hi = max(b["high"] for b in rng)
        lo = min(b["low"]  for b in rng)
        if not (20 <= hi - lo <= 80): continue

        flatten_dt = datetime(sd.year, sd.month, sd.day, 9, 0)
        entry_bars = [b for b in bars
                      if b["dt"].date() == sd and 3 <= b["dt"].hour < 5]

        for b in entry_bars:
            if   b["close"] > hi + 3: is_long = True
            elif b["close"] < lo - 3: is_long = False
            else: continue

            entry  = b["close"]
            stop_p = entry - 15 if is_long else entry + 15
            tgt_p  = entry + 30 if is_long else entry - 30

            after = [b2 for b2 in bars if b2["dt"] > b["dt"]]
            pnl, out = sim(after, entry, stop_p, tgt_p, is_long, flatten_dt)
            trades.append(make_trade(sd, "London_BO", is_long, pnl, out))
            break

    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 2. Overnight Mean Reversion
#    Measure at 8 AM ET: drift from prior US close (4 PM prior day)
#    Fade if drift > 60pt  |  Stop: 20pt  |  Target: 2R  |  Flatten: 9:30 AM
# ─────────────────────────────────────────────────────────────────────────────

def overnight_mr(sessions):
    trades = []
    sds = sorted(sd for sd in sessions if sd.weekday() < 5)

    for i in range(1, len(sds)):
        sd   = sds[i]
        sd_p = sds[i - 1]
        if sd.weekday() == 0 and (sd - sd_p).days > 3: continue  # skip holiday Mondays

        # Prior US close: last bar at 15:xx ET on sd_p
        prior = [b for b in sessions[sd_p]
                 if b["dt"].date() == sd_p and b["dt"].hour == 15]
        if not prior: continue
        ref = prior[-1]["close"]

        # Measurement: first bar at 08:00 ET on sd
        meas = [b for b in sessions[sd]
                if b["dt"].date() == sd and b["dt"].hour == 8 and b["dt"].minute == 0]
        if not meas: continue

        curr  = meas[0]["close"]
        drift = curr - ref
        if abs(drift) < 60: continue

        is_long    = drift < 0
        entry      = curr
        stop_p     = entry - 20 if is_long else entry + 20
        tgt_p      = entry + 40 if is_long else entry - 40
        flatten_dt = datetime(sd.year, sd.month, sd.day, 9, 30)

        after = [b for b in sessions[sd] if b["dt"] > meas[0]["dt"]]
        pnl, out = sim(after, entry, stop_p, tgt_p, is_long, flatten_dt)
        trades.append(make_trade(sd, "Overnight_MR", is_long, pnl, out))

    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 3. Pre-Market Momentum
#    Measure at 9:00 AM ET: how far has NQ moved from prior close?
#    Enter if move > 30pt in one direction  |  Stop: 15pt  |  Target: 1.5R
#    Flatten: 10 AM ET (30 min into US session)
# ─────────────────────────────────────────────────────────────────────────────

def premarket_mom(sessions):
    trades = []
    sds = sorted(sd for sd in sessions if sd.weekday() < 5)

    for i in range(1, len(sds)):
        sd   = sds[i]
        sd_p = sds[i - 1]

        prior = [b for b in sessions[sd_p]
                 if b["dt"].date() == sd_p and b["dt"].hour == 15]
        if not prior: continue
        ref = prior[-1]["close"]

        # 9:00 AM bar
        pm = [b for b in sessions[sd]
              if b["dt"].date() == sd and b["dt"].hour == 9 and b["dt"].minute == 0]
        if not pm: continue

        curr = pm[0]["close"]
        move = curr - ref
        if abs(move) < 30: continue

        is_long    = move > 0
        entry      = curr
        stop_p     = entry - 15 if is_long else entry + 15
        tgt_p      = entry + 22.5 if is_long else entry - 22.5
        flatten_dt = datetime(sd.year, sd.month, sd.day, 10, 0)

        after = [b for b in sessions[sd] if b["dt"] > pm[0]["dt"]]
        pnl, out = sim(after, entry, stop_p, tgt_p, is_long, flatten_dt)
        trades.append(make_trade(sd, "PreMkt_Mom", is_long, pnl, out))

    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 4. Asia Range Fade
#    Build range: 6–10 PM ET  |  Fade if price breaks beyond range 10 PM–2 AM
#    Target: range midpoint  |  Stop: 10pt  |  Flatten: 3 AM ET
# ─────────────────────────────────────────────────────────────────────────────

def asia_fade(sessions):
    trades = []
    for sd, bars in sorted(sessions.items()):
        if sd.weekday() >= 5: continue
        prev_day = sd - timedelta(days=1)

        # Range: 18:00–21:59 ET on previous calendar day
        rng = [b for b in bars
               if b["dt"].date() == prev_day and 18 <= b["dt"].hour < 22]
        if len(rng) < 10: continue

        hi  = max(b["high"] for b in rng)
        lo  = min(b["low"]  for b in rng)
        mid = (hi + lo) / 2
        if hi - lo < 15: continue

        # Fade window: 22:00 prev_day through 01:59 sd
        fade_bars = [b for b in bars
                     if (b["dt"].date() == prev_day and b["dt"].hour >= 22)
                     or (b["dt"].date() == sd and b["dt"].hour < 2)]

        flatten_dt = datetime(sd.year, sd.month, sd.day, 3, 0)
        traded = False

        for b in fade_bars:
            if traded: break
            if   b["close"] > hi + 3: is_long = False   # fade back down to mid
            elif b["close"] < lo - 3: is_long = True    # fade back up to mid
            else: continue

            if abs(b["close"] - mid) < 8: continue      # too close to target

            entry  = b["close"]
            stop_p = entry + 10 if not is_long else entry - 10
            tgt_p  = mid

            after = [b2 for b2 in bars if b2["dt"] > b["dt"]]
            pnl, out = sim(after, entry, stop_p, tgt_p, is_long, flatten_dt)
            trades.append(make_trade(sd, "Asia_Fade", is_long, pnl, out))
            traded = True

    return trades

# ─────────────────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────────────────

def stats(ts):
    if not ts: return None
    wins = [t for t in ts if t["net"] > 0]
    gw   = sum(t["net"] for t in wins)
    gl   = sum(t["net"] for t in ts if t["net"] <= 0)
    net  = sum(t["net"] for t in ts)
    return {"n": len(ts), "wr": len(wins) / len(ts),
            "pf": gw / abs(gl) if gl else 99.0,
            "net": net, "avg": net / len(ts)}

def show(trades, name, stop_pt, rr):
    tag = f"stop={stop_pt}pt  RR={rr}"
    print(f"\n  ── {name}  [{tag}]")
    if not trades:
        print("     no trades"); return

    s_is  = stats([t for t in trades if t["date"].year < 2025])
    s_oos = stats([t for t in trades if t["date"].year >= 2025])
    s_all = stats(trades)

    hdr = f"  {'Period':12} {'N':>5} {'WR':>7} {'PF':>6} {'Net $':>9} {'Avg/T':>8}"
    print(hdr)
    if s_is:
        print(f"  {'IS  (2024)':12} {s_is['n']:>5} {s_is['wr']:>6.0%} "
              f"{s_is['pf']:>6.2f} {s_is['net']:>9,.0f} {s_is['avg']:>8,.0f}")
    if s_oos:
        print(f"  {'OOS (2025+)':12} {s_oos['n']:>5} {s_oos['wr']:>6.0%} "
              f"{s_oos['pf']:>6.2f} {s_oos['net']:>9,.0f} {s_oos['avg']:>8,.0f}")
    print(f"  {'All':12} {s_all['n']:>5} {s_all['wr']:>6.0%} "
          f"{s_all['pf']:>6.2f} {s_all['net']:>9,.0f} {s_all['avg']:>8,.0f}")

    # Outcome breakdown
    outcomes = defaultdict(int)
    for t in trades: outcomes[t["outcome"]] += 1
    print(f"  Outcomes: " +
          "  ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))

    # DOW breakdown
    dow = ["Mon","Tue","Wed","Thu","Fri"]
    dow_stats = []
    for d in range(5):
        ts = [t for t in trades if t["date"].weekday() == d]
        if ts:
            s = stats(ts)
            dow_stats.append(f"{dow[d]} WR={s['wr']:.0%} PF={s['pf']:.2f}({s['n']})")
    print(f"  DOW:      " + "  ".join(dow_stats))

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Loading NQ 1-min data...")
    bars     = load_bars()
    sessions = build_sessions(bars)
    first    = min(b["dt"] for b in bars).date()
    last     = max(b["dt"] for b in bars).date()
    print(f"  {len(bars):,} bars · {len(sessions)} sessions · {first} → {last}")

    print("\nRunning strategies...")
    t1 = london_bo(sessions)
    t2 = overnight_mr(sessions)
    t3 = premarket_mom(sessions)
    t4 = asia_fade(sessions)
    print(f"  Trades found: London={len(t1)}  OvernightMR={len(t2)}  "
          f"PreMktMom={len(t3)}  AsiaFade={len(t4)}")

    W = 62
    print(f"\n{'='*W}")
    print(f"  EXTENDED HOURS RESEARCH — NQ 1-min")
    print(f"  Walk-forward: IS=2024  /  OOS=2025–Jun 2026")
    print(f"{'='*W}")

    show(t1, "1. London Open Breakout",     stop_pt=15, rr=2.0)
    show(t2, "2. Overnight Mean Reversion", stop_pt=20, rr=2.0)
    show(t3, "3. Pre-Market Momentum",      stop_pt=15, rr=1.5)
    show(t4, "4. Asia Range Fade",          stop_pt=10, rr="mid")
    print()
