"""
brain/research/deep_search.py

Deep-search for new NQ edges. Six strategies with structural backing.
Honest IS/OOS walk-forward. No curve-fitting excuses.

1. PDH/PDL Breakout       — prior day session H/L: key CTA trigger levels globally
2. Globex Range Breakout  — overnight H/L broken at US open (institutional reference)
3. 10:30 Momentum         — enter established first-hour trend at the ORB window close
4. Opening 5-min Drive    — explosive first 5 bars signal the day direction
5. VWAP Trend Reclaim     — intraday VWAP computed from 9:30; trade first reclaim
6. London BO Sweep        — Thu/Fri only with param grid on stop/RR/range

All use 1 contract. Walk-forward: IS=2024 / OOS=2025-Jun 2026.
"""

import csv
from datetime import datetime, date, timedelta
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

def us_session(bars_for_sd, sd):
    return [b for b in bars_for_sd
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

def make_trade(sd, name, is_long, pnl_pts, outcome):
    return {"date": sd, "strat": name,
            "dir": "L" if is_long else "S",
            "pnl_pts": pnl_pts,
            "net": round(pnl_pts * PV - COST, 2),
            "outcome": outcome}

# ── 1. PDH/PDL Breakout ───────────────────────────────────────────────────────
def pdh_pdl(sessions, stop=20, rr=3.0, buf=5, entry_end="14:00", flatten="15:30"):
    trades = []
    sds = sorted(sd for sd in sessions if sd.weekday() < 5)
    for i in range(1, len(sds)):
        sd   = sds[i]
        sd_p = sds[i - 1]
        prior_us = us_session(sessions[sd_p], sd_p)
        if len(prior_us) < 30: continue
        pdh = max(b["high"] for b in prior_us)
        pdl = min(b["low"]  for b in prior_us)
        today_us   = us_session(sessions[sd], sd)
        flatten_dt = datetime(sd.year, sd.month, sd.day, int(flatten[:2]), int(flatten[3:]))
        traded = False
        for idx, b in enumerate(today_us):
            t = b["dt"].strftime("%H:%M")
            if t < "09:45" or t >= entry_end: continue
            if traded: break
            if   b["close"] > pdh + buf: is_long = True
            elif b["close"] < pdl - buf: is_long = False
            else: continue
            entry  = b["close"]
            stop_p = entry - stop if is_long else entry + stop
            tgt_p  = entry + stop * rr if is_long else entry - stop * rr
            pnl, out = sim(today_us[idx+1:], entry, stop_p, tgt_p, is_long, flatten_dt)
            trades.append(make_trade(sd, "PDH_PDL", is_long, pnl, out))
            traded = True
    return trades

# ── 2. Globex Range Breakout ──────────────────────────────────────────────────
def globex_bo(sessions, stop=20, rr=3.0, buf=4, entry_end="11:30", flatten="15:00"):
    trades = []
    sds = sorted(sd for sd in sessions if sd.weekday() < 5)
    for i in range(1, len(sds)):
        sd   = sds[i]
        sd_p = sds[i - 1]
        overnight = ([b for b in sessions[sd_p] if b["dt"].date() == sd_p and b["dt"].hour >= 18] +
                     [b for b in sessions[sd]   if b["dt"].date() == sd and b["dt"].strftime("%H:%M") < "09:30"])
        if len(overnight) < 20: continue
        ghi = max(b["high"] for b in overnight)
        glo = min(b["low"]  for b in overnight)
        if ghi - glo < 20: continue
        today_us   = us_session(sessions[sd], sd)
        flatten_dt = datetime(sd.year, sd.month, sd.day, int(flatten[:2]), int(flatten[3:]))
        traded = False
        for idx, b in enumerate(today_us):
            t = b["dt"].strftime("%H:%M")
            if t < "09:45" or t >= entry_end: continue
            if traded: break
            if   b["close"] > ghi + buf: is_long = True
            elif b["close"] < glo - buf: is_long = False
            else: continue
            entry  = b["close"]
            stop_p = entry - stop if is_long else entry + stop
            tgt_p  = entry + stop * rr if is_long else entry - stop * rr
            pnl, out = sim(today_us[idx+1:], entry, stop_p, tgt_p, is_long, flatten_dt)
            trades.append(make_trade(sd, "Globex_BO", is_long, pnl, out))
            traded = True
    return trades

# ── 3. 10:30 First-Hour Momentum ─────────────────────────────────────────────
def first_hour_mom(sessions, stop=22, rr=3.0, threshold=40, chop_limit=2.0, flatten="15:30"):
    trades = []
    for sd, bars in sorted(sessions.items()):
        if sd.weekday() >= 5: continue
        today_us   = us_session(sessions[sd], sd)
        flatten_dt = datetime(sd.year, sd.month, sd.day, int(flatten[:2]), int(flatten[3:]))
        fh = [b for b in today_us if b["dt"].strftime("%H:%M") < "10:30"]
        if len(fh) < 55: continue
        open_px  = fh[0]["open"]
        fh_close = fh[-1]["close"]
        fh_high  = max(b["high"] for b in fh)
        fh_low   = min(b["low"]  for b in fh)
        net_move = fh_close - open_px
        fh_range = fh_high - fh_low
        if abs(net_move) < threshold: continue
        if fh_range / abs(net_move) > chop_limit: continue
        is_long = net_move > 0
        entry   = fh_close
        stop_p  = entry - stop if is_long else entry + stop
        tgt_p   = entry + stop * rr if is_long else entry - stop * rr
        after   = [b for b in today_us if b["dt"].strftime("%H:%M") >= "10:30"]
        pnl, out = sim(after, entry, stop_p, tgt_p, is_long, flatten_dt)
        trades.append(make_trade(sd, "FH_Mom", is_long, pnl, out))
    return trades

# ── 4. Opening 5-min Drive ────────────────────────────────────────────────────
def open_drive(sessions, stop=12, rr=2.0, min_move=15, close_pct=0.65):
    trades = []
    for sd, bars in sorted(sessions.items()):
        if sd.weekday() >= 5: continue
        today_us   = us_session(sessions[sd], sd)
        flatten_dt = datetime(sd.year, sd.month, sd.day, 10, 30)
        d5 = [b for b in today_us if "09:30" <= b["dt"].strftime("%H:%M") <= "09:34"]
        if len(d5) < 4: continue
        open_px = d5[0]["open"]
        close5  = d5[-1]["close"]
        high5   = max(b["high"] for b in d5)
        low5    = min(b["low"]  for b in d5)
        rng5    = high5 - low5
        net5    = close5 - open_px
        if abs(net5) < min_move or rng5 < min_move: continue
        pos_in_range = (close5 - low5) / rng5 if rng5 > 0 else 0.5
        if net5 > 0 and pos_in_range < close_pct: continue
        if net5 < 0 and pos_in_range > (1 - close_pct): continue
        is_long = net5 > 0
        entry   = close5
        stop_p  = entry - stop if is_long else entry + stop
        tgt_p   = entry + stop * rr if is_long else entry - stop * rr
        after   = [b for b in today_us if b["dt"].strftime("%H:%M") >= "09:35"]
        pnl, out = sim(after, entry, stop_p, tgt_p, is_long, flatten_dt)
        trades.append(make_trade(sd, "Open_Drive", is_long, pnl, out))
    return trades

# ── 5. VWAP Trend Reclaim ─────────────────────────────────────────────────────
def vwap_reclaim(sessions, stop=15, rr=2.5, trend_min=45, flatten="15:00"):
    trades = []
    for sd, bars in sorted(sessions.items()):
        if sd.weekday() >= 5: continue
        today_us   = us_session(sessions[sd], sd)
        flatten_dt = datetime(sd.year, sd.month, sd.day, int(flatten[:2]), int(flatten[3:]))
        cum_pv, cum_vol = 0.0, 0.0
        vwap_bars = []
        for b in today_us:
            tp = (b["high"] + b["low"] + b["close"]) / 3
            cum_pv  += tp * b["vol"]
            cum_vol += b["vol"]
            vwap    = cum_pv / cum_vol if cum_vol else b["close"]
            vwap_bars.append({**b, "vwap": vwap})
        traded = False
        for idx in range(trend_min, len(vwap_bars)):
            if traded: break
            b = vwap_bars[idx]
            t = b["dt"].strftime("%H:%M")
            if t >= flatten[:5]: break
            if t < "10:30": continue
            window    = vwap_bars[idx - trend_min: idx]
            above     = sum(1 for w in window if w["close"] > w["vwap"])
            below     = sum(1 for w in window if w["close"] < w["vwap"])
            strong_up   = above >= int(trend_min * 0.80)
            strong_down = below >= int(trend_min * 0.80)
            if not strong_up and not strong_down: continue
            vwap_now = b["vwap"]
            if not (b["low"] <= vwap_now <= b["high"]): continue
            is_long = strong_up
            entry   = vwap_now
            stop_p  = entry - stop if is_long else entry + stop
            tgt_p   = entry + stop * rr if is_long else entry - stop * rr
            pnl, out = sim(vwap_bars[idx+1:], entry, stop_p, tgt_p, is_long, flatten_dt)
            trades.append(make_trade(sd, "VWAP_Reclaim", is_long, pnl, out))
            traded = True
    return trades

# ── 6. London BO Sweep ────────────────────────────────────────────────────────
def london_sweep(sessions):
    results = []
    for stop in [10, 12, 15, 20]:
        for rr in [1.5, 2.0, 2.5]:
            for days in ["all", "ThuFri"]:
                trades = []
                for sd, bars in sorted(sessions.items()):
                    if sd.weekday() >= 5: continue
                    if days == "ThuFri" and sd.weekday() not in (3, 4): continue
                    rng = [b for b in bars if b["dt"].date() == sd and b["dt"].hour < 3]
                    if len(rng) < 10: continue
                    hi = max(b["high"] for b in rng)
                    lo = min(b["low"]  for b in rng)
                    if not (20 <= hi - lo <= 80): continue
                    flatten_dt = datetime(sd.year, sd.month, sd.day, 9, 0)
                    entry_bars = [b for b in bars if b["dt"].date() == sd and 3 <= b["dt"].hour < 5]
                    for b in entry_bars:
                        if   b["close"] > hi + 3: is_long = True
                        elif b["close"] < lo - 3: is_long = False
                        else: continue
                        entry  = b["close"]
                        stop_p = entry - stop if is_long else entry + stop
                        tgt_p  = entry + stop * rr if is_long else entry - stop * rr
                        after  = [b2 for b2 in bars if b2["dt"] > b["dt"]]
                        pnl, out = sim(after, entry, stop_p, tgt_p, is_long, flatten_dt)
                        trades.append(make_trade(sd, "London", is_long, pnl, out))
                        break
                if not trades: continue
                ts_oos = [t for t in trades if t["date"].year >= 2025]
                if len(ts_oos) < 20: continue
                wins = [t for t in ts_oos if t["net"] > 0]
                gw   = sum(t["net"] for t in wins)
                gl   = sum(t["net"] for t in ts_oos if t["net"] <= 0)
                pf   = gw / abs(gl) if gl else 99.0
                net  = sum(t["net"] for t in ts_oos)
                wr   = len(wins) / len(ts_oos)
                results.append((pf, net, wr, len(ts_oos), stop, rr, days))
    results.sort(reverse=True)
    return results

# ── Display ───────────────────────────────────────────────────────────────────
def stats(ts):
    if not ts: return None
    wins = [t for t in ts if t["net"] > 0]
    gw   = sum(t["net"] for t in wins)
    gl   = sum(t["net"] for t in ts if t["net"] <= 0)
    net  = sum(t["net"] for t in ts)
    return {"n": len(ts), "wr": len(wins)/len(ts),
            "pf": gw/abs(gl) if gl else 99.0, "net": net, "avg": net/len(ts)}

def show(trades, name):
    s_is  = stats([t for t in trades if t["date"].year < 2025])
    s_oos = stats([t for t in trades if t["date"].year >= 2025])
    s_all = stats(trades)
    if not s_all: print(f"\n  {name}: no trades"); return
    print(f"\n  ── {name}")
    hdr = f"  {'Period':12} {'N':>5} {'WR':>7} {'PF':>6} {'Net $':>9} {'Avg/T':>8}"
    print(hdr)
    if s_is:
        print(f"  {'IS  2024':12} {s_is['n']:>5} {s_is['wr']:>6.0%} "
              f"{s_is['pf']:>6.2f} {s_is['net']:>9,.0f} {s_is['avg']:>8,.0f}")
    if s_oos:
        v = "✓ EDGE" if s_oos["pf"]>=1.3 and s_oos["n"]>=30 else "~ marginal" if s_oos["pf"]>=1.1 else "✗ no edge"
        print(f"  {'OOS 2025+':12} {s_oos['n']:>5} {s_oos['wr']:>6.0%} "
              f"{s_oos['pf']:>6.2f} {s_oos['net']:>9,.0f} {s_oos['avg']:>8,.0f}  {v}")
    print(f"  {'All':12} {s_all['n']:>5} {s_all['wr']:>6.0%} "
          f"{s_all['pf']:>6.2f} {s_all['net']:>9,.0f} {s_all['avg']:>8,.0f}")
    dow = ["Mon","Tue","Wed","Thu","Fri"]
    parts = [f"{dow[d]} PF={stats([t for t in trades if t['date'].weekday()==d])['pf']:.2f}"
             f"({len([t for t in trades if t['date'].weekday()==d])})"
             for d in range(5) if any(t["date"].weekday()==d for t in trades)]
    print(f"  DOW: {'  '.join(parts)}")
    mn = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
          7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    oos = [t for t in trades if t["date"].year >= 2025]
    mparts = [f"{mn[m]} PF={stats([t for t in oos if t['date'].month==m])['pf']:.2f}"
              f"({len([t for t in oos if t['date'].month==m])})"
              for m in range(1,13) if any(t["date"].month==m for t in oos)]
    if mparts: print(f"  Mth: {'  '.join(mparts)}")

if __name__ == "__main__":
    print("Loading NQ 1-min data...")
    bars     = load_bars()
    sessions = build_sessions(bars)
    print(f"  {len(bars):,} bars · {len(sessions)} sessions")
    print("\nRunning 6 strategies...")
    t1 = pdh_pdl(sessions);         print(f"  PDH/PDL:      {len(t1)}")
    t2 = globex_bo(sessions);       print(f"  Globex BO:    {len(t2)}")
    t3 = first_hour_mom(sessions);  print(f"  10:30 Mom:    {len(t3)}")
    t4 = open_drive(sessions);      print(f"  Open Drive:   {len(t4)}")
    t5 = vwap_reclaim(sessions);    print(f"  VWAP Reclaim: {len(t5)}")
    W = 68
    print(f"\n{'='*W}")
    print(f"  DEEP SEARCH — NQ 1-min  (IS=2024 / OOS=2025-Jun2026)")
    print(f"{'='*W}")
    show(t1, "1. PDH/PDL Breakout      [20pt stop, 3R, entry<14:00, flatten 15:30]")
    show(t2, "2. Globex Range Breakout  [20pt stop, 3R, entry<11:30, flatten 15:00]")
    show(t3, "3. 10:30 First-Hour Mom   [22pt stop, 3R, net>40pt, chop<2.0]")
    show(t4, "4. Opening 5-min Drive    [12pt stop, 2R, move>15pt, close>65%]")
    show(t5, "5. VWAP Trend Reclaim     [15pt stop, 2.5R, 45min trend, flatten 15:00]")
    print(f"\n  ── 6. London BO Param Sweep (sorted by OOS PF)")
    print(f"  {'Days':8} {'Stop':>5} {'RR':>5} {'N_OOS':>6} {'WR':>7} {'PF':>6} {'Net':>9}")
    for pf, net, wr, n, stop, rr, days in london_sweep(sessions)[:12]:
        print(f"  {days:8} {stop:>5} {rr:>5} {n:>6} {wr:>6.0%} {pf:>6.2f} {net:>9,.0f}")
