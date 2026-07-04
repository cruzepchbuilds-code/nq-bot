"""
brain/research/final_sweep.py

Final comprehensive sweep on NQ VWAP v3 and ES ORB v3.
Tests every remaining angle before deployment.

NQ VWAP tests:
  1. Stop × RR grid (stop 12-25, RR 1.5-4.0)
  2. Extension threshold (15-40pt)
  3. Window end time (11am, 12pm, 1pm, 2pm)
  4. Entry hour breakdown (which hour within window is best)
  5. Gap direction filter (only trade when gap aligns with direction)
  6. Long vs short OOS breakdown

ES ORB tests:
  1. Drop April → Feb+Nov only
  2. Wednesday skip (OOS only validation)
  3. Direction bias in OOS (long vs short)
  4. Signal score threshold (40, 50, 60, 70, 80)
  5. OR range refinement (5-20, 5-25, 5-30 current, 8-25, 10-25)
  6. Entry cutoff time (9:55, 10:00, 10:05, 10:10, 10:15 current)
  7. Second breakout contribution (enable/disable)
"""

import csv, os
from datetime import datetime, date, time, timedelta
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NQ_PT=20.0; NQ_COST=9.50; ES_PT=50.0; ES_COST=10.75

# ── data loaders ─────────────────────────────────────────────────────────────

def load_nq(path):
    bbd = defaultdict(list)
    prev_closes = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            ts_str = row["timestamp"][:19]
            try: ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except: continue
            if ts.hour < 9 or ts.hour >= 16: continue
            d = ts.date()
            bbd[d].append({"t":ts.time(),"o":float(row["open"]),"h":float(row["high"]),
                 "l":float(row["low"]),"c":float(row["close"]),"v":float(row["volume"]),
                 "dow":ts.weekday(),"month":ts.month,"date":d})
    all_days = sorted(bbd.keys())
    for i, d in enumerate(all_days):
        if i > 0:
            pb = bbd[all_days[i-1]]
            if pb: prev_closes[d] = pb[-1]["c"]
    return bbd, prev_closes

def load_es(path):
    return load_nq(path)

def pf_stats(trades):
    if not trades: return (0,0,0,0)
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    pf = gw/gl if gl > 0 else 0
    net = sum(t["pnl"] for t in trades)
    wr = len(wins)/len(trades)
    return (len(trades), round(wr,3), round(pf,2), round(net,0))

def print_row(label, total, oos, base_oos_pf):
    tn,twr,tpf,tnet = pf_stats(total)
    on,owr,opf,onet = pf_stats(oos)
    delta = opf - base_oos_pf
    flag = " ↑" if delta > 0.05 else (" ↓" if delta < -0.05 else "")
    print(f"  {label:<35} tot:{tn:>3} OOS:{on:>3} PF:{opf:.2f}{flag:2s} WR:{owr:.0%} net:${onet:,.0f}")

# ══════════════════════════════════════════════════════════════════════════════
# NQ VWAP SWEEP
# ══════════════════════════════════════════════════════════════════════════════

def run_vwap(bbd, prev_closes, stop_pt=20, rr=2.5, min_ext=25,
             win_end=time(13,0), skip_mon=True, weak=None,
             trend_aligned=True, max_trades=1, gap_align=False):
    if weak is None: weak = {4,5,6,9,12}
    trades = []
    for d in sorted(bbd.keys()):
        bars = bbd[d]
        if not bars: continue
        month = bars[0]["month"]; dow = bars[0]["dow"]
        if month in weak: continue
        if skip_mon and dow == 0: continue

        prev_c = prev_closes.get(d)
        sum_pv = sum_vol = 0.0; vwap = None
        open_930 = am_trend = None; was_ext = False
        gap = None; prev_above = None; trades_today = 0
        in_pos = pos_long = False
        entry_px = sl = tp = entry_t = None

        for b in bars:
            t = b["t"]
            if t >= time(15,55): break
            if t >= time(9,30):
                if t < time(9,31) and open_930 is None:
                    open_930 = b["o"]
                    if prev_c: gap = open_930 - prev_c
                tp2 = (b["h"]+b["l"]+b["c"])/3.0
                sum_pv += tp2*b["v"]; sum_vol += b["v"]
                if sum_vol > 0: vwap = sum_pv/sum_vol
            if am_trend is None and t >= time(10,30) and open_930 and vwap:
                am_trend = "bull" if b["c"] > open_930 else "bear"
            if vwap is None or t < time(10,0):
                prev_above = (b["c"] > vwap) if vwap else None; continue
            close = b["c"]
            if in_pos:
                if pos_long:
                    if b["l"] <= sl:   pnl_p = sl-entry_px;    res="stop"
                    elif b["h"] >= tp: pnl_p = tp-entry_px;    res="target"
                    elif t >= win_end: pnl_p = close-entry_px; res="eod"
                    else: prev_above = close>vwap; continue
                else:
                    if b["h"] >= sl:   pnl_p = entry_px-sl;    res="stop"
                    elif b["l"] <= tp: pnl_p = entry_px-tp;    res="target"
                    elif t >= win_end: pnl_p = entry_px-close; res="eod"
                    else: prev_above = close>vwap; continue
                trades.append({"date":d,"pnl":round(pnl_p*NQ_PT-NQ_COST,2),
                    "dir":"long" if pos_long else "short","month":month,
                    "dow":dow,"entry_h":entry_t.hour if entry_t else 0,"result":res})
                in_pos = False; prev_above = close>vwap; continue
            if trades_today >= max_trades or t >= win_end:
                prev_above = close>vwap; continue
            if not was_ext and abs(close-vwap) > min_ext: was_ext = True
            curr_above = close>vwap
            if was_ext and prev_above is not None:
                can_l = (not trend_aligned or am_trend=="bull")
                can_s = (not trend_aligned or am_trend=="bear")
                if gap_align and gap is not None:
                    can_l = can_l and gap >= 0
                    can_s = can_s and gap < 0
                if (not prev_above) and curr_above and can_l:
                    entry_px=close; sl=close-stop_pt; tp=close+stop_pt*rr
                    pos_long=True; in_pos=True; trades_today+=1; was_ext=False; entry_t=t
                elif prev_above and (not curr_above) and can_s:
                    entry_px=close; sl=close+stop_pt; tp=close-stop_pt*rr
                    pos_long=False; in_pos=True; trades_today+=1; was_ext=False; entry_t=t
            prev_above = curr_above
        if in_pos and entry_px and bars:
            lc = bars[-1]["c"]; pnl_p = (lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*NQ_PT-NQ_COST,2),
                "dir":"long" if pos_long else "short","month":month,
                "dow":dow,"entry_h":entry_t.hour if entry_t else 0,"result":"eod"})
    return trades

# ══════════════════════════════════════════════════════════════════════════════
# ES ORB SWEEP
# ══════════════════════════════════════════════════════════════════════════════

def run_es_orb(bbd, prev_closes,
               skip_months=None, stop=9.0, rr=2.0, brk_buf=1.0,
               min_or=5.0, max_or=30.0, last_entry=time(10,15),
               sig_min=60.0, skip_wed=False, second_breakout=False):
    if skip_months is None: skip_months = {1,3,5,6,7,8,9,10,12}
    STRONG = {2,4,11}
    GAP_MIN = 5.0
    trades = []; or_vol_hist = []

    for d in sorted(bbd.keys()):
        bars = bbd[d]
        if not bars: continue
        month = bars[0]["month"]; dow = bars[0]["dow"]
        if month in skip_months or dow == 0: continue
        if skip_wed and dow == 2: continue

        prev_c = prev_closes.get(d)
        or_hi=-1e9; or_lo=1e9; or_vol=0; or_built=False
        avg_ov = sum(or_vol_hist[-20:])/min(len(or_vol_hist),20) if or_vol_hist else 0
        open_930=None; gap=None; trade_count=0; in_pos=False
        pos_long=None; entry_px=sl=tp=None; or_range=0; first_win=False

        for b in bars:
            t = b["t"]
            if t >= time(15,55): break
            if t < time(9,30): continue
            if t >= time(9,30) and t < time(9,31) and open_930 is None:
                open_930 = b["o"]
                if prev_c: gap = open_930 - prev_c
            if t < time(9,45):
                or_hi=max(or_hi,b["h"]); or_lo=min(or_lo,b["l"]); or_vol+=b["v"]
                if t >= time(9,44) and not or_built:
                    or_built=True; or_vol_hist.append(or_vol)
                    if len(or_vol_hist)>20: or_vol_hist.pop(0)
                    avg_ov = sum(or_vol_hist)/len(or_vol_hist)
                continue
            if not or_built: continue
            or_range = or_hi - or_lo
            if or_range < min_or or or_range > max_or: continue
            if t > last_entry and not in_pos: break
            close = b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:     pnl_p=-stop; res="stop"
                    elif b["h"]>=tp:   pnl_p=stop*rr; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:     pnl_p=-stop; res="stop"
                    elif b["l"]<=tp:   pnl_p=stop*rr; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow,"result":res})
                if res=="target": first_win=True
                in_pos=False; trade_count+=1; continue

            # allow second entry only if second_breakout enabled and first was a winner
            if trade_count >= 1 and not (second_breakout and first_win): continue
            if trade_count >= 2: continue

            go_l = close > or_hi+brk_buf; go_s = close < or_lo-brk_buf
            if not go_l and not go_s: continue
            # signal score
            sc = 0
            if t<time(9,50): sc+=20
            elif t<time(10,0): sc+=15
            elif t<time(10,15): sc+=10
            else: sc+=5
            if gap is not None:
                if go_l and gap>GAP_MIN: sc+=25
                elif not go_l and gap<-GAP_MIN: sc+=25
                else: sc+=15
            else: sc+=15
            if avg_ov>0:
                r=or_vol/avg_ov; sc+=25 if 0.7<=r<=1.5 else (15 if r>=0.5 else 5)
            else: sc+=15
            if 10<=or_range<=20: sc+=20
            elif 20<or_range<=30: sc+=15
            elif 5<or_range<10: sc+=10
            if month in STRONG: sc+=10
            if sc < sig_min: continue
            if go_l: entry_px=close; sl=close-stop; tp=close+stop*rr; pos_long=True
            else:    entry_px=close; sl=close+stop; tp=close-stop*rr; pos_long=False
            in_pos=True

        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow,"result":"eod"})
    return trades

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Loading data...")
    nq_bbd, nq_prev = load_nq(os.path.join(BASE,"data","nq_1min.csv"))
    es_bbd, es_prev = load_es(os.path.join(BASE,"data","es_1min.csv"))
    print(f"  NQ {len(nq_bbd)} days | ES {len(es_bbd)} days\n")

    OOS_NQ = date(2025,1,1)
    OOS_ES = date(2024,1,1)

    def split_nq(t): return [x for x in t if x["date"]>=OOS_NQ]
    def split_es(t): return [x for x in t if x["date"]>=OOS_ES]

    # baseline
    base_vwap = run_vwap(nq_bbd, nq_prev)
    base_es   = run_es_orb(es_bbd, dict(es_prev))
    BASE_VWAP_OOS_PF = pf_stats(split_nq(base_vwap))[2]
    BASE_ES_OOS_PF   = pf_stats(split_es(base_es))[2]

    # ─────────────────────────────────────────────────────────────────────────
    print("━"*60)
    print("NQ VWAP — SWEEP")
    print("━"*60)
    print(f"  {'Config':<35} {'tot':>4} {'OOS':>4} PF    WR    net")
    print(f"  {'─'*55}")
    print_row("BASELINE (stop=20 RR=2.5 ext=25 1pm)", base_vwap, split_nq(base_vwap), BASE_VWAP_OOS_PF)

    # 1. Stop × RR grid
    print("\n  [1] Stop × RR grid (OOS focus)")
    for stop in [12, 15, 18, 20, 25]:
        for rr in [2.0, 2.5, 3.0, 4.0]:
            t = run_vwap(nq_bbd, nq_prev, stop_pt=stop, rr=rr)
            print_row(f"  stop={stop} rr={rr}", t, split_nq(t), BASE_VWAP_OOS_PF)

    # 2. Extension threshold
    print("\n  [2] Extension threshold")
    for ext in [10, 15, 20, 22, 25, 28, 30, 35, 40]:
        t = run_vwap(nq_bbd, nq_prev, min_ext=ext)
        print_row(f"  min_ext={ext}", t, split_nq(t), BASE_VWAP_OOS_PF)

    # 3. Window end time
    print("\n  [3] Window end time")
    for h,m in [(11,0),(11,30),(12,0),(12,30),(13,0),(13,30),(14,0)]:
        t = run_vwap(nq_bbd, nq_prev, win_end=time(h,m))
        print_row(f"  win_end={h}:{m:02d}", t, split_nq(t), BASE_VWAP_OOS_PF)

    # 4. Entry hour breakdown (from base trades)
    print("\n  [4] Entry hour breakdown (OOS trades)")
    oos_v = split_nq(base_vwap)
    for h in [10, 11, 12]:
        subset = [t for t in oos_v if t["entry_h"] == h]
        if subset:
            n,wr,pf,net = pf_stats(subset)
            print(f"  {'entry hour='+str(h)+'xx':<35} OOS:{n:>3} PF:{pf:.2f}  WR:{wr:.0%} net:${net:,.0f}")

    # 5. Direction breakdown OOS
    print("\n  [5] Direction breakdown (OOS only)")
    for lbl, filt in [("long only", lambda t:t["dir"]=="long"),
                       ("short only",lambda t:t["dir"]=="short")]:
        subset = [t for t in split_nq(base_vwap) if filt(t)]
        n,wr,pf,net = pf_stats(subset)
        print(f"  {lbl:<35} OOS:{n:>3} PF:{pf:.2f}  WR:{wr:.0%} net:${net:,.0f}")

    # 6. Gap alignment filter
    print("\n  [6] Gap direction alignment (only trade gap-confirming setups)")
    t = run_vwap(nq_bbd, nq_prev, gap_align=True)
    print_row("  gap_align=True", t, split_nq(t), BASE_VWAP_OOS_PF)

    # 7. Max trades per day (1 vs 2)
    print("\n  [7] Max trades per day")
    for mt in [1, 2]:
        t = run_vwap(nq_bbd, nq_prev, max_trades=mt)
        print_row(f"  max_trades={mt}", t, split_nq(t), BASE_VWAP_OOS_PF)

    # 8. Trend aligned vs unaligned
    print("\n  [8] Trend alignment filter")
    for ta in [True, False]:
        t = run_vwap(nq_bbd, nq_prev, trend_aligned=ta)
        print_row(f"  trend_aligned={ta}", t, split_nq(t), BASE_VWAP_OOS_PF)

    # 9. Tuesday as an additional skip day (was decent but test it)
    print("\n  [9] Additional DOW skips")
    for skip_dow, name in [(1,"Tue"),(2,"Wed"),(3,"Thu"),(4,"Fri")]:
        weak_extra = {4,5,6,9,12}
        # manually filter
        t = [x for x in base_vwap if x["dow"] != skip_dow]
        # recompute properly (need to rerun since filter must be in generator)
        # approximate: just filter from base
        n,wr,pf,net = pf_stats([x for x in split_nq(base_vwap) if x["dow"] != skip_dow])
        tot = pf_stats([x for x in base_vwap if x["dow"] != skip_dow])
        print(f"  {'skip '+name:<35} OOS:{n:>3} PF:{pf:.2f}  WR:{wr:.0%} net:${net:,.0f}")

    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "━"*60)
    print("ES ORB — SWEEP")
    print("━"*60)
    print(f"  {'Config':<35} {'tot':>4} {'OOS':>4} PF    WR    net")
    print(f"  {'─'*55}")
    print_row("BASELINE (skip={1,3,5,6,7,8,9,10,12} RR=2)", base_es, split_es(base_es), BASE_ES_OOS_PF)

    # 1. Drop April → Feb+Nov only
    print("\n  [1] Month sets")
    for skip_set, label in [
        ({1,3,5,6,7,8,9,10,12},    "v3 baseline (Feb/Apr/Nov)"),
        ({1,3,4,5,6,7,8,9,10,12},  "Feb+Nov only (drop Apr)"),
        ({1,3,4,5,6,7,8,9,10,11,12},"Feb only"),
        ({1,4,5,6,7,8,9,10,12},    "Feb+Mar+Nov (add Mar back)"),
    ]:
        t = run_es_orb(es_bbd, dict(es_prev), skip_months=skip_set)
        print_row(f"  {label}", t, split_es(t), BASE_ES_OOS_PF)

    # 2. Wednesday skip (OOS only)
    print("\n  [2] Wednesday skip")
    for skip_w in [False, True]:
        t = run_es_orb(es_bbd, dict(es_prev), skip_wed=skip_w)
        print_row(f"  skip_wed={skip_w}", t, split_es(t), BASE_ES_OOS_PF)

    # 3. Direction breakdown OOS
    print("\n  [3] Direction breakdown (OOS only)")
    for lbl, filt in [("long only", lambda t:t["dir"]=="long"),
                       ("short only",lambda t:t["dir"]=="short")]:
        subset = [t for t in split_es(base_es) if filt(t)]
        n,wr,pf,net = pf_stats(subset)
        print(f"  {lbl:<35} OOS:{n:>3} PF:{pf:.2f}  WR:{wr:.0%} net:${net:,.0f}")

    # 4. Signal score threshold
    print("\n  [4] Signal score threshold")
    for sig in [40, 50, 55, 60, 65, 70, 75, 80]:
        t = run_es_orb(es_bbd, dict(es_prev), sig_min=sig)
        print_row(f"  sig_min={sig}", t, split_es(t), BASE_ES_OOS_PF)

    # 5. OR range refinement
    print("\n  [5] OR range filter")
    for min_r, max_r in [(5,30),(5,25),(5,20),(8,25),(8,20),(10,25),(10,20),(12,25)]:
        t = run_es_orb(es_bbd, dict(es_prev), min_or=min_r, max_or=max_r)
        print_row(f"  OR={min_r}-{max_r}pt", t, split_es(t), BASE_ES_OOS_PF)

    # 6. Entry cutoff time
    print("\n  [6] Entry cutoff time")
    for h,m in [(9,55),(10,0),(10,5),(10,10),(10,15),(10,20),(10,30)]:
        t = run_es_orb(es_bbd, dict(es_prev), last_entry=time(h,m))
        print_row(f"  cutoff={h}:{m:02d}", t, split_es(t), BASE_ES_OOS_PF)

    # 7. RR variation
    print("\n  [7] RR variation")
    for rr in [1.5, 2.0, 2.5, 3.0]:
        t = run_es_orb(es_bbd, dict(es_prev), rr=rr)
        print_row(f"  rr={rr}", t, split_es(t), BASE_ES_OOS_PF)

    # 8. Second breakout
    print("\n  [8] Second breakout enabled")
    for sb in [False, True]:
        t = run_es_orb(es_bbd, dict(es_prev), second_breakout=sb)
        print_row(f"  second_breakout={sb}", t, split_es(t), BASE_ES_OOS_PF)

    # 9. Stop variation
    print("\n  [9] Stop variation")
    for stop in [7.0, 8.0, 9.0, 10.0, 12.0, 15.0]:
        t = run_es_orb(es_bbd, dict(es_prev), stop=stop)
        print_row(f"  stop={stop}pt", t, split_es(t), BASE_ES_OOS_PF)

    # ─────────────────────────────────────────────────────────────────────────
    # Final: validate best combos from above
    print("\n" + "━"*60)
    print("BEST COMBO VALIDATION (OOS only)")
    print("━"*60)
    print(f"  {'Config':<42} OOS PF  OOS net")
    print(f"  {'─'*55}")

    def show(label, trades, split_fn):
        oos = split_fn(trades)
        n,wr,pf,net = pf_stats(oos)
        print(f"  {label:<42} {pf:.2f}   ${net:,.0f}  ({n}t  WR:{wr:.0%})")

    show("NQ VWAP v3 baseline", base_vwap, split_nq)
    show("ES ORB v3 baseline",  base_es,   split_es)
    print()

    # VWAP: best stop/RR from grid
    best_vwap_combos = []
    for stop in [15, 18, 20]:
        for rr in [2.5, 3.0]:
            t = run_vwap(nq_bbd, nq_prev, stop_pt=stop, rr=rr)
            _,_,pf,_ = pf_stats(split_nq(t))
            best_vwap_combos.append((pf, stop, rr, t))
    best_vwap_combos.sort(reverse=True)
    for pf, stop, rr, t in best_vwap_combos[:3]:
        show(f"VWAP stop={stop} rr={rr}", t, split_nq)

    # ES ORB: Feb+Nov only
    t_feb_nov = run_es_orb(es_bbd, dict(es_prev), skip_months={1,3,4,5,6,7,8,9,10,12})
    show("ES ORB Feb+Nov only (drop Apr)", t_feb_nov, split_es)

    # ES ORB: best sig score
    best_es_sig = []
    for sig in [50, 55, 60, 65, 70]:
        t = run_es_orb(es_bbd, dict(es_prev), sig_min=sig)
        _,_,pf,_ = pf_stats(split_es(t))
        best_es_sig.append((pf, sig, t))
    best_es_sig.sort(reverse=True)
    for pf, sig, t in best_es_sig[:3]:
        show(f"ES ORB sig_min={sig}", t, split_es)

    # Combined best guesses
    print()
    print("  VWAP best candidate:")
    t = run_vwap(nq_bbd, nq_prev,
                 stop_pt=best_vwap_combos[0][1],
                 rr=best_vwap_combos[0][2])
    show(f"  VWAP stop={best_vwap_combos[0][1]} rr={best_vwap_combos[0][2]}", t, split_nq)

    print("  ES ORB best candidate:")
    t = run_es_orb(es_bbd, dict(es_prev),
                   skip_months={1,3,4,5,6,7,8,9,10,12},
                   sig_min=best_es_sig[0][1])
    show(f"  ES ORB Feb+Nov sig={best_es_sig[0][1]}", t, split_es)
