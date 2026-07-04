"""
brain/research/new_entries.py

Looking at ES ORB from a completely different angle — new ENTRY MECHANICS.

Not parameter tweaks. New ways to get in:

1. OR Retest entry    — price breaks OR, pulls back to level, then resumes
                         classic break-retest-enter, higher WR, better fill price
2. VWAP at entry      — ORB signal only valid when VWAP confirms direction
3. Prev-day H/L break — forget the 15-min OR, trade breaks of yesterday's high/low
4. Opening 5-min dir  — first 5-min candle sets the day's bias, only trade aligned
5. 3-bar momentum     — require 3 consecutive closes outside OR before entry
6. OR False break     — price breaks OR, FAILS (comes back inside), then breaks AGAIN
                         the second attempt after a false break has high conviction
"""

import csv, os
from datetime import datetime, date, time
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ES_PT = 50.0; ES_COST = 10.75; STOP = 9.0; RR = 2.5

def load_bars(path):
    bbd = defaultdict(list)
    prev_closes, prev_highs, prev_lows = {}, {}, {}
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
            if pb:
                prev_closes[d] = pb[-1]["c"]
                prev_highs[d]  = max(b["h"] for b in pb)
                prev_lows[d]   = min(b["l"] for b in pb)
    return bbd, prev_closes, prev_highs, prev_lows

def pf_stats(trades):
    if not trades: return (0,0,0,0)
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    pf = gw/gl if gl > 0 else 0
    return (len(trades), round(len(wins)/len(trades),3), round(pf,2), round(sum(t["pnl"] for t in trades),0))

SKIP_MONTHS = {1,4,5,6,7,8,9,10,12}  # v4 — Feb/Mar/Nov
OOS = date(2024,1,1)

def show(label, trades):
    oos = [t for t in trades if t["date"] >= OOS]
    n,wr,pf,net = pf_stats(oos)
    tot = pf_stats(trades)
    flag = " ↑" if pf > 2.00 else (" ↓" if pf < 1.40 else "")
    print(f"  {label:<42} tot:{tot[0]:>3} OOS:{n:>3} PF:{pf:.2f}{flag:2} WR:{wr:.0%} net:${net:,.0f}")

def sim_exit(bars, idx, entry_px, pos_long, stop=STOP, rr=RR):
    """Simulate exit from bar idx+1 onwards. Returns pnl_pts or None if still open."""
    for b in bars[idx+1:]:
        t = b["t"]
        if t >= time(15,54):
            pnl_p = (b["c"]-entry_px) if pos_long else (entry_px-b["c"])
            return pnl_p, "eod"
        if pos_long:
            if b["l"] <= entry_px - stop: return -stop, "stop"
            if b["h"] >= entry_px + stop*rr: return stop*rr, "target"
        else:
            if b["h"] >= entry_px + stop: return -stop, "stop"
            if b["l"] <= entry_px - stop*rr: return stop*rr, "target"
    return None, None

# ─────────────────────────────────────────────────────────────────────────────
# BASELINE (v4 current params — same as final_sweep.py)
# ─────────────────────────────────────────────────────────────────────────────
def run_baseline(bbd, prev_closes):
    STRONG={2,11}; GAP_MIN=5.0; BRK=1.0; SIG_MIN=60.0
    trades=[]; or_vol_hist=[]
    for d in sorted(bbd.keys()):
        bars=bbd[d]
        if not bars: continue
        month=bars[0]["month"]; dow=bars[0]["dow"]
        if month in SKIP_MONTHS or dow==0: continue
        prev_c=prev_closes.get(d)
        or_hi=-1e9; or_lo=1e9; or_vol=0; or_built=False
        avg_ov=sum(or_vol_hist[-20:])/min(len(or_vol_hist),20) if or_vol_hist else 0
        open_930=None; gap=None; traded=False; in_pos=False
        pos_long=None; entry_px=sl=tp=None
        for i,b in enumerate(bars):
            t=b["t"]
            if t>=time(15,55): break
            if t<time(9,30): continue
            if t<time(9,31) and open_930 is None:
                open_930=b["o"]
                if prev_c: gap=open_930-prev_c
            if t<time(9,45):
                or_hi=max(or_hi,b["h"]); or_lo=min(or_lo,b["l"]); or_vol+=b["v"]
                if t>=time(9,44) and not or_built:
                    or_built=True; or_vol_hist.append(or_vol)
                    if len(or_vol_hist)>20: or_vol_hist.pop(0)
                    avg_ov=sum(or_vol_hist)/len(or_vol_hist)
                continue
            if not or_built: continue
            or_range=or_hi-or_lo
            if or_range<5 or or_range>30: continue
            if t>time(10,15) and not in_pos: break
            close=b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:       pnl_p=-STOP; res="stop"
                    elif b["h"]>=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:       pnl_p=-STOP; res="stop"
                    elif b["l"]<=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow})
                in_pos=False; traded=True; continue
            if traded: continue
            go_l=close>or_hi+BRK; go_s=close<or_lo-BRK
            if not go_l and not go_s: continue
            sc=0
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
            if sc<SIG_MIN: continue
            if go_l: entry_px=close; sl=close-STOP; tp=close+STOP*RR; pos_long=True
            else:    entry_px=close; sl=close+STOP; tp=close-STOP*RR; pos_long=False
            in_pos=True
        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow})
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY 1: OR RETEST
# price breaks OR → pulls back to within RETEST_TOL of the OR boundary
# → then closes back outside (resumption) → ENTER
# Classic break-retest-enter pattern
# ─────────────────────────────────────────────────────────────────────────────
def run_or_retest(bbd, prev_closes, retest_tol=2.0, last_entry=time(11,0)):
    trades=[]
    for d in sorted(bbd.keys()):
        bars=bbd[d]
        if not bars: continue
        month=bars[0]["month"]; dow=bars[0]["dow"]
        if month in SKIP_MONTHS or dow==0: continue
        or_hi=-1e9; or_lo=1e9; or_built=False
        broke_hi=broke_lo=False; retested_hi=retested_lo=False
        traded=False; in_pos=False; pos_long=None; entry_px=sl=tp=None
        for i,b in enumerate(bars):
            t=b["t"]
            if t>=time(15,55): break
            if t<time(9,30): continue
            if t<time(9,45):
                or_hi=max(or_hi,b["h"]); or_lo=min(or_lo,b["l"])
                if t>=time(9,44): or_built=True
                continue
            if not or_built or traded: continue
            if t>last_entry and not in_pos: break
            close=b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:       pnl_p=-STOP; res="stop"
                    elif b["h"]>=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:       pnl_p=-STOP; res="stop"
                    elif b["l"]<=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow})
                in_pos=False; traded=True; continue
            # Track initial break
            if close>or_hi+1: broke_hi=True
            if close<or_lo-1: broke_lo=True
            # Track retest (price comes back near OR boundary)
            if broke_hi and not retested_hi and abs(close-or_hi)<=retest_tol: retested_hi=True
            if broke_lo and not retested_lo and abs(close-or_lo)<=retest_tol: retested_lo=True
            # Entry: after retest, price resumes in breakout direction
            if retested_hi and close>or_hi+1 and not in_pos:
                entry_px=close; sl=close-STOP; tp=close+STOP*RR; pos_long=True; in_pos=True
            elif retested_lo and close<or_lo-1 and not in_pos:
                entry_px=close; sl=close+STOP; tp=close-STOP*RR; pos_long=False; in_pos=True
        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow})
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY 2: VWAP CONFLUENCE
# Standard ORB breakout BUT only enter when VWAP confirms:
#   Long ORB only if close > VWAP at entry time
#   Short ORB only if close < VWAP at entry time
# ─────────────────────────────────────────────────────────────────────────────
def run_vwap_confluence(bbd, prev_closes):
    STRONG={2,11}; GAP_MIN=5.0; BRK=1.0; SIG_MIN=60.0
    trades=[]; or_vol_hist=[]
    for d in sorted(bbd.keys()):
        bars=bbd[d]
        if not bars: continue
        month=bars[0]["month"]; dow=bars[0]["dow"]
        if month in SKIP_MONTHS or dow==0: continue
        prev_c=prev_closes.get(d)
        or_hi=-1e9; or_lo=1e9; or_vol=0; or_built=False
        avg_ov=sum(or_vol_hist[-20:])/min(len(or_vol_hist),20) if or_vol_hist else 0
        open_930=None; gap=None; traded=False; in_pos=False
        pos_long=None; entry_px=sl=tp=None
        sum_pv=sum_vol=0.0; vwap=None
        for i,b in enumerate(bars):
            t=b["t"]
            if t>=time(15,55): break
            if t<time(9,30): continue
            if t<time(9,31) and open_930 is None:
                open_930=b["o"]
                if prev_c: gap=open_930-prev_c
            # Build VWAP from 9:30
            if t>=time(9,30):
                tp2=(b["h"]+b["l"]+b["c"])/3.0
                sum_pv+=tp2*b["v"]; sum_vol+=b["v"]
                if sum_vol>0: vwap=sum_pv/sum_vol
            if t<time(9,45):
                or_hi=max(or_hi,b["h"]); or_lo=min(or_lo,b["l"]); or_vol+=b["v"]
                if t>=time(9,44) and not or_built:
                    or_built=True; or_vol_hist.append(or_vol)
                    if len(or_vol_hist)>20: or_vol_hist.pop(0)
                    avg_ov=sum(or_vol_hist)/len(or_vol_hist)
                continue
            if not or_built or vwap is None: continue
            or_range=or_hi-or_lo
            if or_range<5 or or_range>30: continue
            if t>time(10,15) and not in_pos: break
            close=b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:       pnl_p=-STOP; res="stop"
                    elif b["h"]>=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:       pnl_p=-STOP; res="stop"
                    elif b["l"]<=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow})
                in_pos=False; traded=True; continue
            if traded: continue
            go_l=close>or_hi+BRK; go_s=close<or_lo-BRK
            if not go_l and not go_s: continue
            # VWAP confluence filter
            if go_l and close < vwap: continue  # breaking up but below VWAP — skip
            if go_s and close > vwap: continue  # breaking down but above VWAP — skip
            sc=0
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
            if sc<SIG_MIN: continue
            if go_l: entry_px=close; sl=close-STOP; tp=close+STOP*RR; pos_long=True
            else:    entry_px=close; sl=close+STOP; tp=close-STOP*RR; pos_long=False
            in_pos=True
        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow})
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY 3: PREV-DAY HIGH/LOW BREAKOUT
# Completely different reference levels — forget the 15-min OR
# Enter when price breaks YESTERDAY's high or low
# These are major institutional reference points
# ─────────────────────────────────────────────────────────────────────────────
def run_prev_day_breakout(bbd, prev_highs, prev_lows, prev_closes,
                          buf=0.5, last_entry=time(12,0)):
    trades=[]
    for d in sorted(bbd.keys()):
        bars=bbd[d]
        if not bars: continue
        month=bars[0]["month"]; dow=bars[0]["dow"]
        if month in SKIP_MONTHS or dow==0: continue
        pdh=prev_highs.get(d); pdl=prev_lows.get(d)
        if pdh is None or pdl is None: continue
        traded=False; in_pos=False; pos_long=None; entry_px=sl=tp=None
        for b in bars:
            t=b["t"]
            if t>=time(15,55): break
            if t<time(9,30): continue
            if t>last_entry and not in_pos: break
            close=b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:       pnl_p=-STOP; res="stop"
                    elif b["h"]>=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:       pnl_p=-STOP; res="stop"
                    elif b["l"]<=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow})
                in_pos=False; traded=True; continue
            if traded: continue
            go_l=close>pdh+buf; go_s=close<pdl-buf
            if go_l and not in_pos:
                entry_px=close; sl=close-STOP; tp=close+STOP*RR; pos_long=True; in_pos=True
            elif go_s and not in_pos:
                entry_px=close; sl=close+STOP; tp=close-STOP*RR; pos_long=False; in_pos=True
        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow})
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY 4: OPENING 5-MIN MOMENTUM DIRECTION
# First 5-min candle (9:30-9:34) sets the day's directional bias
# Baseline ORB signal ONLY accepted if it aligns with the opening 5-min direction
# Logic: markets often telegraph the day direction in the first 5 minutes
# ─────────────────────────────────────────────────────────────────────────────
def run_5min_momentum(bbd, prev_closes):
    STRONG={2,11}; GAP_MIN=5.0; BRK=1.0; SIG_MIN=60.0
    trades=[]; or_vol_hist=[]
    for d in sorted(bbd.keys()):
        bars=bbd[d]
        if not bars: continue
        month=bars[0]["month"]; dow=bars[0]["dow"]
        if month in SKIP_MONTHS or dow==0: continue
        prev_c=prev_closes.get(d)
        or_hi=-1e9; or_lo=1e9; or_vol=0; or_built=False
        avg_ov=sum(or_vol_hist[-20:])/min(len(or_vol_hist),20) if or_vol_hist else 0
        open_930=None; gap=None; traded=False; in_pos=False
        pos_long=None; entry_px=sl=tp=None
        open5=close5=None  # 5-min open/close
        for i,b in enumerate(bars):
            t=b["t"]
            if t>=time(15,55): break
            if t<time(9,30): continue
            if t<time(9,31) and open_930 is None:
                open_930=b["o"]
                open5=b["o"]
                if prev_c: gap=open_930-prev_c
            if t<time(9,35) and open5 is not None:
                close5=b["c"]  # keep updating close of 5-min window
            if t<time(9,45):
                or_hi=max(or_hi,b["h"]); or_lo=min(or_lo,b["l"]); or_vol+=b["v"]
                if t>=time(9,44) and not or_built:
                    or_built=True; or_vol_hist.append(or_vol)
                    if len(or_vol_hist)>20: or_vol_hist.pop(0)
                    avg_ov=sum(or_vol_hist)/len(or_vol_hist)
                continue
            if not or_built or open5 is None or close5 is None: continue
            or_range=or_hi-or_lo
            if or_range<5 or or_range>30: continue
            if t>time(10,15) and not in_pos: break
            close=b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:       pnl_p=-STOP; res="stop"
                    elif b["h"]>=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:       pnl_p=-STOP; res="stop"
                    elif b["l"]<=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow})
                in_pos=False; traded=True; continue
            if traded: continue
            go_l=close>or_hi+BRK; go_s=close<or_lo-BRK
            if not go_l and not go_s: continue
            # 5-min momentum filter
            mom_bull = close5 > open5       # first 5 min was up
            mom_bear = close5 < open5       # first 5 min was down
            if go_l and not mom_bull: continue   # long ORB needs bullish 5-min
            if go_s and not mom_bear: continue   # short ORB needs bearish 5-min
            sc=0
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
            if sc<SIG_MIN: continue
            if go_l: entry_px=close; sl=close-STOP; tp=close+STOP*RR; pos_long=True
            else:    entry_px=close; sl=close+STOP; tp=close-STOP*RR; pos_long=False
            in_pos=True
        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow})
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY 5: FALSE BREAK REVERSAL
# Price breaks OR → FAILS (closes back inside OR) → second break = HIGH CONVICTION
# The "fakeout" exhausts the wrong-way traders and the second move is cleaner
# ─────────────────────────────────────────────────────────────────────────────
def run_false_break(bbd, prev_closes, last_entry=time(11,30)):
    trades=[]
    for d in sorted(bbd.keys()):
        bars=bbd[d]
        if not bars: continue
        month=bars[0]["month"]; dow=bars[0]["dow"]
        if month in SKIP_MONTHS or dow==0: continue
        or_hi=-1e9; or_lo=1e9; or_built=False
        fake_hi=fake_lo=False   # had a failed break
        first_break_hi=first_break_lo=False  # first break happened
        traded=False; in_pos=False; pos_long=None; entry_px=sl=tp=None
        for b in bars:
            t=b["t"]
            if t>=time(15,55): break
            if t<time(9,30): continue
            if t<time(9,45):
                or_hi=max(or_hi,b["h"]); or_lo=min(or_lo,b["l"])
                if t>=time(9,44): or_built=True
                continue
            if not or_built or traded: continue
            if t>last_entry and not in_pos: break
            close=b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:       pnl_p=-STOP; res="stop"
                    elif b["h"]>=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:       pnl_p=-STOP; res="stop"
                    elif b["l"]<=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow})
                in_pos=False; traded=True; continue
            # Track first break
            if close>or_hi+1: first_break_hi=True
            if close<or_lo-1: first_break_lo=True
            # Track when first break fails (closes back inside OR)
            if first_break_hi and not fake_hi and or_lo<=close<=or_hi: fake_hi=True
            if first_break_lo and not fake_lo and or_lo<=close<=or_hi: fake_lo=True
            # Entry: after fakeout, second break is the real move
            if fake_hi and close<or_lo-1 and not in_pos:  # hi faked, breaks lo
                entry_px=close; sl=close+STOP; tp=close-STOP*RR; pos_long=False; in_pos=True
            elif fake_lo and close>or_hi+1 and not in_pos:  # lo faked, breaks hi
                entry_px=close; sl=close-STOP; tp=close+STOP*RR; pos_long=True; in_pos=True
        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow})
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY 6: 3-BAR MOMENTUM CONFIRMATION
# Don't enter on the first close outside OR
# Require 3 consecutive closes above OR_HI (or below OR_LO) to confirm the move
# Filters weak/shallow breakouts
# ─────────────────────────────────────────────────────────────────────────────
def run_3bar_confirm(bbd, prev_closes, n_bars=3, last_entry=time(10,30)):
    STRONG={2,11}; GAP_MIN=5.0; SIG_MIN=60.0
    trades=[]; or_vol_hist=[]
    for d in sorted(bbd.keys()):
        bars=bbd[d]
        if not bars: continue
        month=bars[0]["month"]; dow=bars[0]["dow"]
        if month in SKIP_MONTHS or dow==0: continue
        prev_c=bbd.get(d)  # use raw
        or_hi=-1e9; or_lo=1e9; or_vol=0; or_built=False
        avg_ov=sum(or_vol_hist[-20:])/min(len(or_vol_hist),20) if or_vol_hist else 0
        open_930=None; gap_val=None; traded=False; in_pos=False
        pos_long=None; entry_px=sl=tp=None
        consec_hi=consec_lo=0
        for b in bars:
            t=b["t"]
            if t>=time(15,55): break
            if t<time(9,30): continue
            if t<time(9,31) and open_930 is None:
                open_930=b["o"]
            if t<time(9,45):
                or_hi=max(or_hi,b["h"]); or_lo=min(or_lo,b["l"]); or_vol+=b["v"]
                if t>=time(9,44) and not or_built:
                    or_built=True; or_vol_hist.append(or_vol)
                    if len(or_vol_hist)>20: or_vol_hist.pop(0)
                    avg_ov=sum(or_vol_hist)/len(or_vol_hist)
                continue
            if not or_built: continue
            or_range=or_hi-or_lo
            if or_range<5 or or_range>30: continue
            if t>last_entry and not in_pos: break
            close=b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:       pnl_p=-STOP; res="stop"
                    elif b["h"]>=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:       pnl_p=-STOP; res="stop"
                    elif b["l"]<=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow})
                in_pos=False; traded=True; continue
            if traded: continue
            # Count consecutive closes
            if close>or_hi+1: consec_hi+=1; consec_lo=0
            elif close<or_lo-1: consec_lo+=1; consec_hi=0
            else: consec_hi=0; consec_lo=0
            if consec_hi>=n_bars and not in_pos:
                entry_px=close; sl=close-STOP; tp=close+STOP*RR; pos_long=True; in_pos=True
            elif consec_lo>=n_bars and not in_pos:
                entry_px=close; sl=close+STOP; tp=close-STOP*RR; pos_long=False; in_pos=True
        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow})
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY 7: COMBINED BEST — OR RETEST + VWAP CONFLUENCE
# Take a retest entry only when VWAP also confirms direction
# Two independent frameworks pointing the same way = highest conviction
# ─────────────────────────────────────────────────────────────────────────────
def run_retest_vwap(bbd, prev_closes, retest_tol=3.0, last_entry=time(11,30)):
    trades=[]
    for d in sorted(bbd.keys()):
        bars=bbd[d]
        if not bars: continue
        month=bars[0]["month"]; dow=bars[0]["dow"]
        if month in SKIP_MONTHS or dow==0: continue
        or_hi=-1e9; or_lo=1e9; or_built=False
        broke_hi=broke_lo=False; retested_hi=retested_lo=False
        traded=False; in_pos=False; pos_long=None; entry_px=sl=tp=None
        sum_pv=sum_vol=0.0; vwap=None
        for b in bars:
            t=b["t"]
            if t>=time(15,55): break
            if t<time(9,30): continue
            # VWAP from 9:30
            if t>=time(9,30):
                tp2=(b["h"]+b["l"]+b["c"])/3.0
                sum_pv+=tp2*b["v"]; sum_vol+=b["v"]
                if sum_vol>0: vwap=sum_pv/sum_vol
            if t<time(9,45):
                or_hi=max(or_hi,b["h"]); or_lo=min(or_lo,b["l"])
                if t>=time(9,44): or_built=True
                continue
            if not or_built or traded or vwap is None: continue
            if t>last_entry and not in_pos: break
            close=b["c"]
            if in_pos:
                if pos_long:
                    if b["l"]<=sl:       pnl_p=-STOP; res="stop"
                    elif b["h"]>=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=close-entry_px; res="eod"
                    else: continue
                else:
                    if b["h"]>=sl:       pnl_p=-STOP; res="stop"
                    elif b["l"]<=tp:     pnl_p=STOP*RR; res="target"
                    elif t>=time(15,54): pnl_p=entry_px-close; res="eod"
                    else: continue
                trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                    "dir":"long" if pos_long else "short","month":month,"dow":dow})
                in_pos=False; traded=True; continue
            if close>or_hi+1: broke_hi=True
            if close<or_lo-1: broke_lo=True
            if broke_hi and not retested_hi and abs(close-or_hi)<=retest_tol: retested_hi=True
            if broke_lo and not retested_lo and abs(close-or_lo)<=retest_tol: retested_lo=True
            # Entry requires VWAP confirmation
            if retested_hi and close>or_hi+1 and close>vwap and not in_pos:
                entry_px=close; sl=close-STOP; tp=close+STOP*RR; pos_long=True; in_pos=True
            elif retested_lo and close<or_lo-1 and close<vwap and not in_pos:
                entry_px=close; sl=close+STOP; tp=close-STOP*RR; pos_long=False; in_pos=True
        if in_pos and entry_px and bars:
            lc=bars[-1]["c"]; pnl_p=(lc-entry_px) if pos_long else (entry_px-lc)
            trades.append({"date":d,"pnl":round(pnl_p*ES_PT-ES_COST,2),
                "dir":"long" if pos_long else "short","month":month,"dow":dow})
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# RUN EVERYTHING
# ─────────────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    print("Loading data...")
    bbd,prev_c,prev_h,prev_l = load_bars(os.path.join(BASE,"data","es_1min.csv"))
    print(f"  {len(bbd)} days loaded\n")

    print("═"*60)
    print("NEW ENTRY MECHANICS — ES (Feb+Mar+Nov, RR=2.5)")
    print("═"*60)
    print(f"  {'Entry type':<42} {'tot':>4} {'OOS':>4} PF    WR    net")
    print("  "+"-"*56)

    show("BASELINE v4 (immediate break + signal score)", run_baseline(bbd, dict(prev_c)))
    print()

    # Retest with different tolerances
    print("  [1] OR Retest (break → pullback → resume)")
    for tol in [1.0, 2.0, 3.0, 5.0]:
        show(f"  retest_tol={tol}pt  last_entry=11am", run_or_retest(bbd,dict(prev_c),retest_tol=tol))
    for last in [time(11,0), time(11,30), time(12,0)]:
        show(f"  retest_tol=2pt   last_entry={last.strftime('%I:%M')}",
             run_or_retest(bbd,dict(prev_c),retest_tol=2.0,last_entry=last))

    print()
    print("  [2] VWAP confluence at ORB entry (VWAP must confirm direction)")
    show("  VWAP confluence filter on baseline", run_vwap_confluence(bbd,dict(prev_c)))

    print()
    print("  [3] Previous-day High/Low breakout (different reference entirely)")
    for buf in [0.25, 0.5, 1.0, 2.0]:
        show(f"  prev-day H/L buf={buf}pt  last=12pm", run_prev_day_breakout(bbd,prev_h,prev_l,prev_c,buf=buf))
    for last in [time(10,0), time(10,30), time(11,0), time(12,0)]:
        show(f"  prev-day H/L buf=0.5  last={last.strftime('%H:%M')}",
             run_prev_day_breakout(bbd,prev_h,prev_l,prev_c,buf=0.5,last_entry=last))

    print()
    print("  [4] Opening 5-min momentum direction filter")
    show("  5-min momentum must align with ORB direction", run_5min_momentum(bbd,dict(prev_c)))

    print()
    print("  [5] False break reversal (fade the fakeout, trade the second move)")
    for last in [time(11,0), time(11,30), time(12,0)]:
        show(f"  false break  last_entry={last.strftime('%H:%M')}",
             run_false_break(bbd,dict(prev_c),last_entry=last))

    print()
    print("  [6] N-bar momentum confirmation (N consecutive closes outside OR)")
    for n in [2, 3, 4]:
        show(f"  {n}-bar consecutive confirm  last=10:30",
             run_3bar_confirm(bbd,dict(prev_c),n_bars=n))

    print()
    print("  [7] Combined: OR Retest + VWAP confluence (highest conviction)")
    for tol in [2.0, 3.0, 4.0]:
        show(f"  retest_tol={tol}  +VWAP confirm  last=11:30",
             run_retest_vwap(bbd,dict(prev_c),retest_tol=tol))

    # Deep-dive the best new entry
    print()
    print("═"*60)
    print("DEEP DIVE — best new entries (OOS yearly)")
    print("═"*60)
    candidates = [
        ("OR Retest tol=2pt last=11am",   run_or_retest(bbd,dict(prev_c),retest_tol=2.0,last_entry=time(11,0))),
        ("OR Retest tol=3pt last=11:30",  run_or_retest(bbd,dict(prev_c),retest_tol=3.0,last_entry=time(11,30))),
        ("VWAP confluence",               run_vwap_confluence(bbd,dict(prev_c))),
        ("5-min momentum",                run_5min_momentum(bbd,dict(prev_c))),
        ("Retest+VWAP tol=3 last=11:30",  run_retest_vwap(bbd,dict(prev_c),retest_tol=3.0,last_entry=time(11,30))),
        ("False break last=11:30",        run_false_break(bbd,dict(prev_c),last_entry=time(11,30))),
    ]
    for label, trades in candidates:
        oos=[t for t in trades if t["date"]>=OOS]
        n,wr,pf,net=pf_stats(oos)
        print(f"\n  {label}  (OOS: {n}t PF:{pf:.2f} WR:{wr:.0%} net:${net:,.0f})")
        for yr in [2022,2023,2024,2025,2026]:
            sub=[t for t in trades if t["date"].year==yr]
            sn,swr,spf,snet=pf_stats(sub)
            tag="IS " if yr<2024 else "OOS"
            if sn>0: print(f"    {yr} ({tag}): {sn:>2}t PF:{spf:.2f} WR:{swr:.0%} net:${snet:,.0f}")
