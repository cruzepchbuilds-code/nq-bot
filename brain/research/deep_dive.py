"""
brain/research/deep_dive.py

Comprehensive deep-dive on all 5 CruzCapital strategies:
  ES ORB | NQ VWAP | ES VWAP | NQ Power Hour | ES Power Hour

Analyses run on each strategy:
  1. Monte Carlo stress test (2000 resamplings)
  2. Rolling 60-day walk-forward PF (to spot instability windows)
  3. DOW / month / direction / result / entry-time breakdowns
  4. Max drawdown + consecutive-loss analysis
  5. Edge-improvement search (ATR filter, direction filter, day-range filter)

Usage:
    cd /Users/Cruz/Desktop/nq_bot_final-main
    python3 brain/research/deep_dive.py
"""

import csv, os, sys, random
from datetime import datetime, date, time, timedelta
from collections import defaultdict
from itertools import product

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── instrument constants ─────────────────────────────────────────────────────
NQ_PT   = 20.0
ES_PT   = 50.0
NQ_COST = 9.50
ES_COST = 10.75
WEAK_NQ = {6, 9, 12}
WEAK_ES = {1, 5, 6, 7, 8, 10}

DOW_NAMES = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri"}
MON_NAMES = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
             7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}

# ── data loader ──────────────────────────────────────────────────────────────

def load_bars(path):
    bbd = defaultdict(list)
    prev_closes = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            ts_str = row["timestamp"][:19]
            try: ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except: continue
            if ts.hour < 9 or ts.hour >= 16: continue
            d = ts.date()
            b = {"ts":ts,"t":ts.time(),"o":float(row["open"]),"h":float(row["high"]),
                 "l":float(row["low"]),"c":float(row["close"]),"v":float(row["volume"]),
                 "dow":ts.weekday(),"month":ts.month,"date":d}
            bbd[d].append(b)
    all_days = sorted(bbd.keys())
    # prev_day_range[d] = range of the PREVIOUS session (no lookahead)
    prev_day_range = {}
    for i, d in enumerate(all_days):
        bars = bbd[d]
        if i > 0:
            prev = all_days[i-1]
            prev_bars = bbd[prev]
            if prev_bars:
                prev_closes[d] = prev_bars[-1]["c"]
                prev_day_range[d] = max(b["h"] for b in prev_bars) - min(b["l"] for b in prev_bars)
    return bbd, prev_day_range, prev_closes

def compute_atr(bbd, n=14):
    all_days = sorted(bbd.keys())
    true_ranges = []
    atr_by_day = {}
    prev_c = None
    for d in all_days:
        bars = bbd[d]
        if not bars: prev_c = None; continue
        hi = max(b["h"] for b in bars)
        lo = min(b["l"] for b in bars)
        tr = max(hi - lo,
                 abs(hi - prev_c) if prev_c else hi - lo,
                 abs(lo - prev_c) if prev_c else hi - lo)
        true_ranges.append(tr)
        if len(true_ranges) >= n:
            atr_by_day[d] = sum(true_ranges[-n:]) / n
        prev_c = bars[-1]["c"]
    return atr_by_day

# ── trade collectors ─────────────────────────────────────────────────────────

def collect_es_orb(bbd, prev_closes):
    STOP    = 9.0   # 7pt + 2pt buffer
    RR      = 2.0
    BRK_BUF = 1.0
    MIN_OR  = 5.0
    MAX_OR  = 30.0
    LAST_E  = time(10, 15)
    GAP_MIN = 5.0
    SIG_MIN = 60.0
    SKIP    = {1, 5, 6, 7, 8, 10}
    STRONG  = {2, 4, 11}

    trades = []
    or_vol_hist = []

    for d in sorted(bbd.keys()):
        bars = bbd[d]
        if not bars: continue
        month = bars[0]["month"]; dow = bars[0]["dow"]
        if month in SKIP or dow == 0: continue

        prev_c = prev_closes.get(d)
        or_hi = -1e9; or_lo = 1e9; or_vol = 0; or_built = False
        avg_ov = sum(or_vol_hist[-20:]) / min(len(or_vol_hist), 20) if or_vol_hist else 0
        open_930 = None; gap = None
        traded = in_pos = False
        pos_long = None; entry_px = sl = tp = None; entry_t = None; or_range = 0

        for b in bars:
            t = b["t"]
            if t >= time(15, 55): break
            if t < time(9, 30): continue
            if t >= time(9, 30) and t < time(9, 31) and open_930 is None:
                open_930 = b["o"]
                if prev_c: gap = open_930 - prev_c
            if t < time(9, 45):
                or_hi = max(or_hi, b["h"]); or_lo = min(or_lo, b["l"]); or_vol += b["v"]
                if t >= time(9, 44) and not or_built:
                    or_built = True
                    or_vol_hist.append(or_vol)
                    if len(or_vol_hist) > 20: or_vol_hist.pop(0)
                    avg_ov = sum(or_vol_hist) / len(or_vol_hist)
                continue
            if not or_built: continue
            or_range = or_hi - or_lo
            if or_range < MIN_OR or or_range > MAX_OR: continue
            if t > LAST_E and not in_pos: break
            close = b["c"]
            if in_pos:
                if pos_long:
                    if   b["l"] <= sl:     pnl_p = -STOP;      res = "stop"
                    elif b["h"] >= tp:     pnl_p =  STOP*RR;   res = "target"
                    elif t >= time(15,54): pnl_p = close-entry_px; res = "eod"
                    else: continue
                else:
                    if   b["h"] >= sl:     pnl_p = -STOP;      res = "stop"
                    elif b["l"] <= tp:     pnl_p =  STOP*RR;   res = "target"
                    elif t >= time(15,54): pnl_p = entry_px-close; res = "eod"
                    else: continue
                trades.append({"strat":"ES_ORB","date":d,"dow":dow,"month":month,
                    "dir":"long" if pos_long else "short","entry_t":entry_t,
                    "pnl_pts":round(pnl_p,2),"pnl_usd":round(pnl_p*ES_PT-ES_COST,2),
                    "or_range":round(or_range,2),"gap":round(gap,2) if gap else 0,
                    "result":res})
                in_pos = False; traded = True; continue
            if traded: continue
            go_l = close > or_hi + BRK_BUF; go_s = close < or_lo - BRK_BUF
            if not go_l and not go_s: continue
            sc = 0
            if   t < time(9, 50):  sc += 20
            elif t < time(10,  0): sc += 15
            elif t < time(10, 15): sc += 10
            else:                  sc += 5
            if gap is not None:
                if go_l and gap > GAP_MIN: sc += 25
                elif not go_l and gap < -GAP_MIN: sc += 25
                else: sc += 15
            else: sc += 15
            if avg_ov > 0:
                r = or_vol / avg_ov
                sc += 25 if 0.7 <= r <= 1.5 else (15 if r >= 0.5 else 5)
            else: sc += 15
            if   10 <= or_range <= 20: sc += 20
            elif 20 <  or_range <= 30: sc += 15
            elif  5 <  or_range < 10:  sc += 10
            if month in STRONG: sc += 10
            if sc < SIG_MIN: continue
            if go_l:
                entry_px = close; sl = close-STOP; tp = close+STOP*RR; pos_long = True
            else:
                entry_px = close; sl = close+STOP; tp = close-STOP*RR; pos_long = False
            in_pos = True; entry_t = t

        if in_pos and entry_px and bars:
            lc = bars[-1]["c"]
            pnl_p = (lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"strat":"ES_ORB","date":d,"dow":dow,"month":month,
                "dir":"long" if pos_long else "short","entry_t":entry_t,
                "pnl_pts":round(pnl_p,2),"pnl_usd":round(pnl_p*ES_PT-ES_COST,2),
                "or_range":round(or_range,2),"gap":round(gap,2) if gap else 0,
                "result":"eod"})
        if bars: prev_closes[d] = bars[-1]["c"]
    return trades


def collect_vwap(bbd, pt_val, cost, weak_months, label,
                 stop_pt=20, rr=2.5, min_ext=25, win_end=time(13,0),
                 max_trades=1, trend_aligned=True):
    trades = []
    for d in sorted(bbd.keys()):
        bars = bbd[d]
        if not bars: continue
        month = bars[0]["month"]; dow = bars[0]["dow"]
        if month in weak_months: continue
        sum_pv = sum_vol = 0.0; vwap = None
        open_930 = am_trend = None; was_ext = False
        prev_above = None; trades_today = 0
        in_pos = pos_long = False
        entry_px = sl = tp = entry_t = ext_amount = None

        for b in bars:
            t = b["t"]
            if t >= time(15, 55): break
            if t >= time(9, 30):
                if t < time(9, 31) and open_930 is None: open_930 = b["o"]
                tp2 = (b["h"]+b["l"]+b["c"])/3.0
                sum_pv += tp2 * b["v"]; sum_vol += b["v"]
                if sum_vol > 0: vwap = sum_pv / sum_vol
            if am_trend is None and t >= time(10, 30) and open_930 and vwap:
                am_trend = "bull" if b["c"] > open_930 else "bear"
            if vwap is None or t < time(10, 0):
                prev_above = (b["c"] > vwap) if vwap else None; continue
            close = b["c"]
            if in_pos:
                if pos_long:
                    if   b["l"] <= sl:  pnl_p = sl - entry_px; res = "stop"
                    elif b["h"] >= tp:  pnl_p = tp - entry_px; res = "target"
                    elif t >= win_end:  pnl_p = close - entry_px; res = "eod"
                    else: prev_above = close > vwap; continue
                else:
                    if   b["h"] >= sl:  pnl_p = entry_px - sl; res = "stop"
                    elif b["l"] <= tp:  pnl_p = entry_px - tp; res = "target"
                    elif t >= win_end:  pnl_p = entry_px - close; res = "eod"
                    else: prev_above = close > vwap; continue
                trades.append({"strat":label,"date":d,"dow":dow,"month":month,
                    "dir":"long" if pos_long else "short","entry_t":entry_t,
                    "pnl_pts":round(pnl_p,2),"pnl_usd":round(pnl_p*pt_val-cost,2),
                    "ext_amt":round(ext_amount,2) if ext_amount else 0,"result":res})
                in_pos = False; prev_above = close > vwap; continue
            if trades_today >= max_trades or t >= win_end:
                prev_above = close > vwap; continue
            if not was_ext and abs(close - vwap) > min_ext:
                was_ext = True; ext_amount = abs(close - vwap)
            curr_above = close > vwap
            if was_ext and prev_above is not None:
                can_l = not trend_aligned or am_trend == "bull"
                can_s = not trend_aligned or am_trend == "bear"
                if (not prev_above) and curr_above and can_l:
                    entry_px = close; sl = close-stop_pt; tp = close+stop_pt*rr
                    pos_long = True; in_pos = True; entry_t = t; trades_today += 1; was_ext = False
                elif prev_above and (not curr_above) and can_s:
                    entry_px = close; sl = close+stop_pt; tp = close-stop_pt*rr
                    pos_long = False; in_pos = True; entry_t = t; trades_today += 1; was_ext = False
            prev_above = curr_above

        if in_pos and entry_px and bars:
            lc = bars[-1]["c"]
            pnl_p = (lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"strat":label,"date":d,"dow":dow,"month":month,
                "dir":"long" if pos_long else "short","entry_t":entry_t,
                "pnl_pts":round(pnl_p,2),"pnl_usd":round(pnl_p*pt_val-cost,2),
                "ext_amt":round(ext_amount,2) if ext_amount else 0,"result":"eod"})
    return trades


def collect_ph(bbd, pt_val, cost, weak_months, label,
               stop_pt=20, rr=2.0, last_entry=time(15, 0)):
    trades = []
    for d in sorted(bbd.keys()):
        bars = bbd[d]
        if not bars: continue
        month = bars[0]["month"]; dow = bars[0]["dow"]
        if month in weak_months or dow == 0: continue
        open_930 = ph_hi = ph_lo = ph_trend = None; ph_set = False
        traded = in_pos = False; pos_long = None
        entry_px = sl = tp = entry_t = None

        for b in bars:
            t = b["t"]
            if t >= time(15, 55): break
            if t >= time(9, 30) and t < time(9, 31) and open_930 is None: open_930 = b["o"]
            if t < time(14, 0): continue
            if not ph_set and t < time(14, 1):
                ph_hi = b["h"]; ph_lo = b["l"]
                ph_trend = "bull" if (open_930 and b["c"] > open_930) else "bear"
                ph_set = True; continue
            if not ph_set: continue
            close = b["c"]
            if in_pos:
                if pos_long:
                    if   b["l"] <= sl:  pnl_p = sl - entry_px; res = "stop"
                    elif b["h"] >= tp:  pnl_p = tp - entry_px; res = "target"
                    else: continue
                else:
                    if   b["h"] >= sl:  pnl_p = entry_px - sl; res = "stop"
                    elif b["l"] <= tp:  pnl_p = entry_px - tp; res = "target"
                    else: continue
                trades.append({"strat":label,"date":d,"dow":dow,"month":month,
                    "dir":"long" if pos_long else "short","entry_t":entry_t,
                    "pnl_pts":round(pnl_p,2),"pnl_usd":round(pnl_p*pt_val-cost,2),
                    "ph_range":round(ph_hi-ph_lo,2) if ph_hi and ph_lo else 0,
                    "result":res})
                in_pos = False; traded = True; continue
            if traded or t > last_entry: continue
            if ph_trend == "bull" and close > ph_hi:
                entry_px = close; sl = close-stop_pt; tp = close+stop_pt*rr
                pos_long = True; in_pos = traded = True; entry_t = t
            elif ph_trend == "bear" and close < ph_lo:
                entry_px = close; sl = close+stop_pt; tp = close-stop_pt*rr
                pos_long = False; in_pos = traded = True; entry_t = t

        if in_pos and entry_px and bars:
            lc = bars[-1]["c"]
            pnl_p = (lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"strat":label,"date":d,"dow":dow,"month":month,
                "dir":"long" if pos_long else "short","entry_t":entry_t,
                "pnl_pts":round(pnl_p,2),"pnl_usd":round(pnl_p*pt_val-cost,2),
                "ph_range":round(ph_hi-ph_lo,2) if ph_hi and ph_lo else 0,
                "result":"eod"})
    return trades


# ── Monte Carlo ──────────────────────────────────────────────────────────────

def monte_carlo(trades, start_eq=50000, n_sims=2000, dll=1200.0):
    if not trades:
        return None
    pnls = [t["pnl_usd"] for t in trades]
    n = len(pnls)
    final_eqs, max_dds, breached_dll = [], [], 0

    for _ in range(n_sims):
        sample = random.choices(pnls, k=n)
        eq = start_eq; peak = start_eq; max_dd = 0
        day_pnl = 0; last_date_idx = 0
        dll_hit = False
        for i, p in enumerate(sample):
            day_pnl += p
            # approximate: reset day_pnl every ~1.3 trades (avg frequency)
            # simpler: just track equity and max DD
            eq += p
            if eq > peak: peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd: max_dd = dd
        final_eqs.append(eq); max_dds.append(max_dd)

    final_eqs.sort(); max_dds.sort()
    N = n_sims
    return {
        "n_trades": n,
        "median_final": final_eqs[N//2],
        "p5_final":  final_eqs[N//20],
        "p95_final": final_eqs[19*N//20],
        "pct_profit": sum(1 for e in final_eqs if e > start_eq) / N,
        "median_dd":  max_dds[N//2],
        "p95_dd":     max_dds[19*N//20],
        "worst_dd":   max_dds[-1],
    }


# ── Rolling walk-forward ─────────────────────────────────────────────────────

def rolling_pf(trades, window_days=60, step_days=20):
    if not trades:
        return []
    dated = sorted(trades, key=lambda t: t["date"])
    d_min = dated[0]["date"]
    d_max = dated[-1]["date"]
    results = []
    cur = d_min
    while cur + timedelta(days=window_days) <= d_max:
        win_end = cur + timedelta(days=window_days)
        window_trades = [t for t in dated if cur <= t["date"] < win_end]
        if window_trades:
            wins = [t for t in window_trades if t["pnl_usd"] > 0]
            gw = sum(t["pnl_usd"] for t in wins)
            gl = abs(sum(t["pnl_usd"] for t in window_trades if t["pnl_usd"] <= 0))
            pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0)
            results.append({
                "start": cur, "end": win_end,
                "trades": len(window_trades), "pf": round(min(pf, 9.99), 2),
                "net": sum(t["pnl_usd"] for t in window_trades),
            })
        cur += timedelta(days=step_days)
    return results


# ── Breakdown analysis ────────────────────────────────────────────────────────

def slice_stats(group_key, trades):
    """Return dict: group → {trades, wr, pf, net}."""
    groups = defaultdict(list)
    for t in trades:
        groups[t[group_key]].append(t["pnl_usd"])
    out = {}
    for k, pnls in sorted(groups.items()):
        wins = [p for p in pnls if p > 0]
        gw = sum(wins)
        gl = abs(sum(p for p in pnls if p <= 0))
        out[k] = {
            "n": len(pnls), "wr": len(wins)/len(pnls),
            "pf": gw/gl if gl > 0 else 0,
            "net": sum(pnls),
        }
    return out


def max_drawdown_streak(trades):
    """Compute max consecutive losses and max $ drawdown from equity curve."""
    pnls = [t["pnl_usd"] for t in sorted(trades, key=lambda t: t["date"])]
    max_loss_streak = cur_streak = 0
    eq = 0; peak = 0; max_dd = 0
    for p in pnls:
        eq += p; peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        if p < 0: cur_streak += 1; max_loss_streak = max(max_loss_streak, cur_streak)
        else: cur_streak = 0
    return {"max_loss_streak": max_loss_streak, "max_dd_usd": round(max_dd, 2),
            "max_dd_pct": round(max_dd / 50000, 4)}


# ── Edge-improvement search ───────────────────────────────────────────────────

def edge_atr_filter(trades, atr_by_day, thresholds):
    """Test 'only trade when ATR >= threshold' for each threshold."""
    results = []
    for thresh in thresholds:
        filt = [t for t in trades if atr_by_day.get(t["date"], 0) >= thresh]
        if len(filt) < 10: continue
        wins = [t for t in filt if t["pnl_usd"] > 0]
        gw = sum(t["pnl_usd"] for t in wins)
        gl = abs(sum(t["pnl_usd"] for t in filt if t["pnl_usd"] <= 0))
        pf = gw / gl if gl > 0 else 0
        results.append({"threshold": thresh, "trades": len(filt),
                         "pf": round(pf,2), "wr": round(len(wins)/len(filt),3),
                         "net": round(sum(t["pnl_usd"] for t in filt),0)})
    return sorted(results, key=lambda x: x["pf"], reverse=True)


def edge_direction_filter(trades):
    """Long-only vs short-only vs both."""
    out = {}
    for label, subset in [("all", trades),
                           ("long only", [t for t in trades if t["dir"]=="long"]),
                           ("short only",[t for t in trades if t["dir"]=="short"])]:
        if not subset: continue
        wins = [t for t in subset if t["pnl_usd"] > 0]
        gw = sum(t["pnl_usd"] for t in wins)
        gl = abs(sum(t["pnl_usd"] for t in subset if t["pnl_usd"] <= 0))
        out[label] = {"n": len(subset), "wr": round(len(wins)/len(subset),3),
                      "pf": round(gw/gl,2) if gl > 0 else 0,
                      "net": round(sum(t["pnl_usd"] for t in subset),0)}
    return out


def edge_skip_week(trades, test_dows):
    """Test skipping specific additional days."""
    results = []
    for skip_dow in test_dows:
        filt = [t for t in trades if t["dow"] != skip_dow]
        if len(filt) < 10: continue
        wins = [t for t in filt if t["pnl_usd"] > 0]
        gw = sum(t["pnl_usd"] for t in wins)
        gl = abs(sum(t["pnl_usd"] for t in filt if t["pnl_usd"] <= 0))
        pf = gw / gl if gl > 0 else 0
        results.append({"skip": DOW_NAMES[skip_dow], "trades": len(filt),
                         "pf": round(pf,2), "net": round(sum(t["pnl_usd"] for t in filt),0)})
    return results


def edge_day_range_filter(trades, day_range, thresholds):
    """Only trade on days with range >= N points (high-move days)."""
    results = []
    for thresh in thresholds:
        filt = [t for t in trades if day_range.get(t["date"], 0) >= thresh]
        if len(filt) < 10: continue
        wins = [t for t in filt if t["pnl_usd"] > 0]
        gw = sum(t["pnl_usd"] for t in wins)
        gl = abs(sum(t["pnl_usd"] for t in filt if t["pnl_usd"] <= 0))
        pf = gw / gl if gl > 0 else 0
        results.append({"min_range": thresh, "trades": len(filt),
                         "pf": round(pf,2), "wr": round(len(wins)/len(filt),3),
                         "net": round(sum(t["pnl_usd"] for t in filt),0)})
    return sorted(results, key=lambda x: x["pf"], reverse=True)


# ── Printer helpers ───────────────────────────────────────────────────────────

SEP = "─" * 68

def print_header(name):
    print(f"\n{'='*68}")
    print(f"  {name}")
    print(f"{'='*68}")

def print_section(title):
    print(f"\n  {title}")
    print(f"  {SEP[:60]}")

def pf_bar(pf, width=20):
    filled = min(int(pf * width / 3.0), width)
    bar = "█" * filled + "░" * (width - filled)
    color = "✓" if pf >= 1.2 else ("~" if pf >= 1.0 else "✗")
    return f"[{bar}] {pf:.2f} {color}"

def summary_line(trades):
    if not trades: return "  no trades"
    wins = [t for t in trades if t["pnl_usd"] > 0]
    gw = sum(t["pnl_usd"] for t in wins)
    gl = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] <= 0))
    pf = gw/gl if gl > 0 else 0
    return (f"  {len(trades)} trades | WR {len(wins)/len(trades):.1%} "
            f"| PF {pf:.2f} | Net ${sum(t['pnl_usd'] for t in trades):,.0f}")


def print_full_analysis(label, trades, atr_by_day, day_range,
                        atr_thresholds, range_thresholds):
    if not trades:
        print(f"\n  ✗ No trades for {label}"); return

    print_header(label)
    print(summary_line(trades))

    # ── 1. IS / OOS split ────────────────────────────────────────────────────
    is_cut = date(2025, 1, 1) if "NQ" in label else date(2024, 1, 1)
    is_t  = [t for t in trades if t["date"] < is_cut]
    oos_t = [t for t in trades if t["date"] >= is_cut]
    print_section("IS vs OOS")
    print(f"  IS  {'(2024)'   if 'NQ' in label else '(2022-23)':10s}{summary_line(is_t).strip()}")
    print(f"  OOS {'(2025+)'  if 'NQ' in label else '(2024+)':10s}{summary_line(oos_t).strip()}")

    # ── 2. Monte Carlo ────────────────────────────────────────────────────────
    print_section("Monte Carlo  (2000 resamplings, $50K start)")
    mc = monte_carlo(trades)
    if mc:
        print(f"  Median final eq:  ${mc['median_final']:,.0f}  "
              f"(p5=${mc['p5_final']:,.0f} | p95=${mc['p95_final']:,.0f})")
        print(f"  Prob profitable:  {mc['pct_profit']:.1%}")
        print(f"  Max DD median:    {mc['median_dd']:.1%}   worst: {mc['worst_dd']:.1%}")
        print(f"  Max DD p95:       {mc['p95_dd']:.1%}")

    # ── 3. Rolling walk-forward ───────────────────────────────────────────────
    print_section("Rolling 60-day PF  (step 20 days)")
    rols = rolling_pf(trades)
    if rols:
        below = [r for r in rols if r["pf"] < 1.0]
        min_pf = min(r["pf"] for r in rols)
        max_pf = max(r["pf"] for r in rols)
        avg_pf = sum(r["pf"] for r in rols) / len(rols)
        pct_win = sum(1 for r in rols if r["pf"] >= 1.0) / len(rols)
        print(f"  Windows: {len(rols)} | Profitable windows: {pct_win:.0%}")
        print(f"  PF range: {min_pf:.2f} – {max_pf:.2f} | Average: {avg_pf:.2f}")
        if below:
            print(f"  Losing windows ({len(below)}):")
            for r in sorted(below, key=lambda x: x["pf"])[:5]:
                print(f"    {r['start']} → {r['end']}: PF {r['pf']:.2f}  "
                      f"({r['trades']} trades, ${r['net']:,.0f})")

    # ── 4. Drawdown & streak ──────────────────────────────────────────────────
    print_section("Max Drawdown & Streak")
    ds = max_drawdown_streak(trades)
    print(f"  Max consecutive losses: {ds['max_loss_streak']}")
    print(f"  Max $ drawdown (actual trade sequence): ${ds['max_dd_usd']:,.0f}  "
          f"({ds['max_dd_pct']:.1%} of $50K)")

    # ── 5. DOW breakdown ──────────────────────────────────────────────────────
    print_section("Day-of-week breakdown")
    dow_s = slice_stats("dow", trades)
    print(f"  {'Day':4s} {'Trades':>7} {'WR':>7} {'PF':>6} {'Net $':>10}  PF bar")
    for dow, s in dow_s.items():
        bar = pf_bar(s["pf"])
        print(f"  {DOW_NAMES[dow]:4s} {s['n']:>7} {s['wr']:>7.1%} {s['pf']:>6.2f} "
              f"{s['net']:>10,.0f}  {bar}")

    # ── 6. Monthly breakdown ──────────────────────────────────────────────────
    print_section("Monthly breakdown")
    mon_s = slice_stats("month", trades)
    print(f"  {'Mon':4s} {'Trades':>7} {'WR':>7} {'PF':>6} {'Net $':>10}")
    for m, s in mon_s.items():
        flag = " ← SKIP" if s["pf"] < 0.9 else (" ← STRONG" if s["pf"] >= 1.5 else "")
        print(f"  {MON_NAMES[m]:4s} {s['n']:>7} {s['wr']:>7.1%} {s['pf']:>6.2f} "
              f"{s['net']:>10,.0f}{flag}")

    # ── 7. Direction breakdown ────────────────────────────────────────────────
    print_section("Long vs short breakdown")
    dir_s = edge_direction_filter(trades)
    for lbl, s in dir_s.items():
        print(f"  {lbl:12s}: {s['n']:>4} trades | WR {s['wr']:.1%} | PF {s['pf']:.2f} "
              f"| Net ${s['net']:,.0f}")

    # ── 8. Result breakdown (target / stop / eod) ─────────────────────────────
    print_section("Exit type breakdown")
    res_s = slice_stats("result", trades)
    for res, s in res_s.items():
        print(f"  {res:8s}: {s['n']:>4} trades ({s['n']/len(trades):.1%}) | "
              f"WR {s['wr']:.1%} | Net ${s['net']:,.0f}")

    # ── 9. ATR filter edge search ─────────────────────────────────────────────
    print_section("ATR filter edge search  (only trade when ATR ≥ threshold)")
    atr_res = edge_atr_filter(trades, atr_by_day, atr_thresholds)
    base_pf = 0
    if trades:
        wins = [t for t in trades if t["pnl_usd"] > 0]
        gw = sum(t["pnl_usd"] for t in wins)
        gl = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] <= 0))
        base_pf = gw/gl if gl > 0 else 0
    print(f"  {'Threshold':>10} {'Trades':>7} {'WR':>7} {'PF':>6} {'vs base':>8} {'Net $':>10}")
    for r in atr_res[:6]:
        delta = r["pf"] - base_pf
        flag = " ↑ BETTER" if delta > 0.05 else (" ↓ worse" if delta < -0.05 else "")
        print(f"  {r['threshold']:>10.1f} {r['trades']:>7} {r['wr']:>7.1%} {r['pf']:>6.2f} "
              f"{delta:>+8.2f} {r['net']:>10,.0f}{flag}")

    # ── 10. Prev-day range filter (no lookahead bias) ────────────────────────
    print_section("Prev-day range filter  (PREVIOUS session range — no lookahead bias)")
    range_res = edge_day_range_filter(trades, day_range, range_thresholds)
    print(f"  {'Min range':>10} {'Trades':>7} {'WR':>7} {'PF':>6} {'vs base':>8} {'Net $':>10}")
    for r in range_res[:6]:
        delta = r["pf"] - base_pf
        flag = " ↑ BETTER" if delta > 0.05 else (" ↓ worse" if delta < -0.05 else "")
        print(f"  {r['min_range']:>10.0f} {r['trades']:>7} {r['wr']:>7.1%} {r['pf']:>6.2f} "
              f"{delta:>+8.2f} {r['net']:>10,.0f}{flag}")

    # ── 11. DOW skip search ────────────────────────────────────────────────────
    print_section("Additional DOW skip search")
    skip_res = edge_skip_week(trades, [1, 2, 3, 4])  # 0=Mon already skipped
    print(f"  {'Skip':6s} {'Trades':>7} {'PF':>6} {'Net $':>10}")
    for r in sorted(skip_res, key=lambda x: x["pf"], reverse=True):
        print(f"  {r['skip']:6s} {r['trades']:>7} {r['pf']:>6.2f} {r['net']:>10,.0f}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Loading data...")
    nq_bbd, nq_range, nq_prev = load_bars(os.path.join(BASE,"data","nq_1min.csv"))
    es_bbd, es_range, es_prev = load_bars(os.path.join(BASE,"data","es_1min.csv"))
    print(f"  NQ: {len(nq_bbd)} days | ES: {len(es_bbd)} days")

    print("Computing ATR...")
    nq_atr = compute_atr(nq_bbd, n=14)
    es_atr  = compute_atr(es_bbd,  n=14)

    print("Collecting trades...")
    es_orb_t   = collect_es_orb(es_bbd, dict(es_prev))
    nq_vwap_t  = collect_vwap(nq_bbd, NQ_PT, NQ_COST, WEAK_NQ, "NQ_VWAP",
                               stop_pt=20, rr=2.5, min_ext=25,
                               win_end=time(13,0), max_trades=1, trend_aligned=True)
    es_vwap_t  = collect_vwap(es_bbd, ES_PT, ES_COST, WEAK_ES, "ES_VWAP",
                               stop_pt=20, rr=1.5, min_ext=20,
                               win_end=time(12,0), max_trades=1, trend_aligned=True)
    nq_ph_t    = collect_ph(nq_bbd, NQ_PT, NQ_COST, WEAK_NQ, "NQ_PH",
                             stop_pt=12, rr=2.5, last_entry=time(15,0))
    es_ph_t    = collect_ph(es_bbd, ES_PT, ES_COST, WEAK_ES, "ES_PH",
                             stop_pt=25, rr=3.0, last_entry=time(15,30))

    print(f"  ES ORB:{len(es_orb_t):4d} | NQ VWAP:{len(nq_vwap_t):4d} | "
          f"ES VWAP:{len(es_vwap_t):4d} | NQ PH:{len(nq_ph_t):4d} | ES PH:{len(es_ph_t):4d}")

    NQ_ATR_T = [80, 100, 120, 150, 175, 200]
    ES_ATR_T = [25,  30,  35,  40,  50,  60]
    NQ_RNG_T = [150, 200, 250, 300, 350, 400]
    ES_RNG_T = [50,  60,  70,  80,  90, 100]

    print_full_analysis("ES ORB  (best strategy — OOS PF 2.24)",
                        es_orb_t, es_atr, es_range, ES_ATR_T, ES_RNG_T)

    print_full_analysis("NQ VWAP Reclaim  (marginal — OOS PF 1.15)",
                        nq_vwap_t, nq_atr, nq_range, NQ_ATR_T, NQ_RNG_T)

    print_full_analysis("ES VWAP Reclaim  (marginal — OOS PF 1.13)",
                        es_vwap_t, es_atr, es_range, ES_ATR_T, ES_RNG_T)

    print_full_analysis("NQ Power Hour  (no edge found)",
                        nq_ph_t, nq_atr, nq_range, NQ_ATR_T, NQ_RNG_T)

    print_full_analysis("ES Power Hour  (OOS flat at PF 1.01)",
                        es_ph_t, es_atr, es_range, ES_ATR_T, ES_RNG_T)

    # ── Cross-strategy summary ────────────────────────────────────────────────
    print(f"\n{'='*68}")
    print(f"  SUMMARY — ACTIONABLE IMPROVEMENTS")
    print(f"{'='*68}")
    print("  Check the ATR and range filter sections above for each strategy.")
    print("  Any row marked '↑ BETTER' is a candidate filter to add to the")
    print("  NinjaScript — particularly if PF improvement is > 0.1 and")
    print("  trade count stays above 40.")
    print()
    print("  Key items to look for:")
    print("  1. Direction bias: if long-only PF >> short-only, add direction filter")
    print("  2. ATR floor: if PF jumps on high-volatility days, add ATR gate")
    print("  3. Monthly: any month with PF < 0.9 = add to SKIP list")
    print("  4. DOW: if skipping a day raises PF by >0.1 with trades staying >40, skip it")
    print("  5. Rolling windows: if strategy has 3+ consecutive losing windows,")
    print("     it signals market-regime sensitivity — needs regime filter")
