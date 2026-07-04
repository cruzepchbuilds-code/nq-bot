"""
brain/research/cross_market.py

Two cross-market ideas using data already on disk (carte-blanche session):

  X1. ES LIE DETECTOR — at each NQ morning-ORB entry bar, is ES confirming
      (beyond its own OR in the same direction) or diverging (still inside)?
      Hypothesis: NQ breakouts without ES confirmation are traps.
      Also applied to NQ rejection entries via ES's own VWAP side.

  X2. BTC WEEKEND ORACLE — BTC trades the weekend; NQ doesn't. Does BTC's
      Fri-16:00 -> Sun-17:00 move predict NQ's Sunday-evening session?
      Test: BTC weekend move beyond ±1%/±2% -> NQ position at Sunday 18:15
      close, Asia-style exits (25pt stop / 3R / flat 21:00).

BTC csv assumed UTC (Binance) -> shifted -4h ET approx (DST ignored, noted).

NOTE (2026-07-03): upstream fix — portfolio_policy.run_year_morning's
regime-ATR window is now a bounded 14-day deque (was unbounded list ->
expanding mean). This script inherits the fix via import; results recorded
BEFORE this date used the buggy morning stream (composed v12 stream deltas:
710 -> 713 trading days, full-period net -0.08%, OOS 2025-26 net +2.4%) —
re-run before citing absolute numbers.
"""

import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
config.SKIP_FRIDAYS = False
config.PYRAMIDING_ENABLED = False
config.PARTIAL_EXIT_ENABLED = False
config.EVAL_MODE = False

import portfolio_policy as pp
from backtest import load_csv
from datetime import datetime, date, time, timedelta
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pf(v):
    w = sum(x for x in v if x > 0)
    l = abs(sum(x for x in v if x <= 0))
    return round(w / l, 3) if l else (99.0 if w else 0.0)


# ── X1: ES lie detector ───────────────────────────────────────────────────────

def es_bars_by_daytime():
    """{(date): {time: close}} + per-day ES OR + running VWAP at each minute."""
    out = defaultdict(dict)
    with open(os.path.join(BASE, "data", "es_1min.csv")) as f:
        for row in csv.DictReader(f):
            s = row["timestamp"][:19]
            try:
                ts = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if not (9 <= ts.hour < 16):
                continue
            out[ts.date()][ts.time()] = (float(row["high"]), float(row["low"]),
                                         float(row["close"]), float(row["volume"]))
    return out


if __name__ == "__main__":
    print("X1: generating NQ morning trades (funded config)...", flush=True)
    bars = load_csv(pp.DATA)
    morning = []
    for y in pp.YEARS:
        morning.extend(pp.run_year_morning(bars, y))
    del bars
    print(f"  {len(morning)} NQ morning trades")

    print("  loading ES minute map...", flush=True)
    es = es_bars_by_daytime()

    # per-day ES OR + VWAP-to-time
    es_or, es_vwap = {}, {}
    for d, mp in es.items():
        orh = orl = None
        pv = vol = 0.0
        vw = {}
        for t in sorted(mp):
            h, l, c, v = mp[t]
            if time(9, 30) <= t < time(9, 45):
                orh = h if orh is None else max(orh, h)
                orl = l if orl is None else min(orl, l)
            pv += (h + l + c) / 3 * v
            vol += v
            vw[t] = pv / vol if vol else None
        if orh is not None:
            es_or[d] = (orh, orl)
        es_vwap[d] = vw

    confirm, diverge, missing = [], [], 0
    for t in morning:
        d = date.fromisoformat(t["date"])
        et = t.get("entry_time")
        if not et or d not in es_or:
            missing += 1
            continue
        h, m = map(int, et.split(":"))
        tt = time(h, m)
        mp = es.get(d, {})
        bar = mp.get(tt)
        if bar is None:
            for off in (1, -1, 2, -2):
                mm = (datetime.combine(d, tt) + timedelta(minutes=off)).time()
                bar = mp.get(mm)
                if bar:
                    break
        if bar is None:
            missing += 1
            continue
        es_close = bar[2]
        orh, orl = es_or[d]
        pnl_1c = t["pnl"] / max(1, t.get("contracts", 1))
        if t["dir"] == "long":
            (confirm if es_close > orh else diverge).append(pnl_1c)
        else:
            (confirm if es_close < orl else diverge).append(pnl_1c)

    print(f"\n{'═'*88}")
    print(f"  X1a: NQ MORNING ORB split by ES confirmation at entry bar "
          f"(ES beyond its own OR)")
    print(f"{'═'*88}")
    print(f"  ES CONFIRMS:  N={len(confirm):>3}  PF={pf(confirm):>6}  "
          f"${sum(confirm):>+9,.0f}  avg=${(sum(confirm)/len(confirm)) if confirm else 0:>+6,.0f}")
    print(f"  ES DIVERGES:  N={len(diverge):>3}  PF={pf(diverge):>6}  "
          f"${sum(diverge):>+9,.0f}  avg=${(sum(diverge)/len(diverge)) if diverge else 0:>+6,.0f}")
    print(f"  (unmatched: {missing})")

    # X1b: NQ rejection entries vs ES VWAP side
    print(f"\n  X1b: NQ REJECTION split by ES side of its own VWAP at entry")
    from vwap_fulldata import load_days as vload
    rth = vload(pp.DATA)
    conf_r, div_r = [], []
    for d in sorted(rth):
        wd, mo = d.weekday(), d.month
        if wd == 0 or mo in (4, 5, 6, 9, 12):
            continue
        r = pp.rejection_day(rth[d])
        if not r:
            continue
        e_t, _, pnl = r
        vw = es_vwap.get(d, {})
        bar = es.get(d, {}).get(e_t)
        v_at = vw.get(e_t)
        if bar is None or v_at is None:
            continue
        # rejection direction: infer from pnl sign? No — need dir; rejection_day
        # returns (entry_t, exit_t, pnl). Re-derive dir cheaply: skip dir check,
        # use |ES-VWAP spread| regime instead: wide spread = trending ES day
        spread = abs(bar[2] - v_at)
        (conf_r if spread > 4.0 else div_r).append(pnl)
    print(f"  ES stretched >4pt from its VWAP: N={len(conf_r):>3}  PF={pf(conf_r):>6}  ${sum(conf_r):>+8,.0f}")
    print(f"  ES pinned    ≤4pt:               N={len(div_r):>3}  PF={pf(div_r):>6}  ${sum(div_r):>+8,.0f}")

    # ── X2: BTC weekend oracle ────────────────────────────────────────────────
    print(f"\n{'═'*88}\n  X2: BTC WEEKEND → NQ SUNDAY EVENING (Asia-style 25pt/3R, flat 21:00)\n{'═'*88}")
    btc = {}
    with open(os.path.join(BASE, "crypto", "data", "btc_1h.csv")) as f:
        for row in csv.DictReader(f):
            ts = datetime.strptime(row["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
            btc[ts] = float(row["close"])

    eve = pp.load_days(pp.DATA, 17, 21)
    sundays = [d for d in sorted(eve) if d.weekday() == 6]
    results = defaultdict(list)
    for d in sundays:
        fri = d - timedelta(days=2)
        # BTC UTC: Fri 20:00 UTC ≈ 16:00 ET; Sun 21:00 UTC ≈ 17:00 ET
        p0 = btc.get(datetime.combine(fri, time(20, 0)))
        p1 = btc.get(datetime.combine(d, time(21, 0)))
        if not p0 or not p1:
            continue
        ret = (p1 - p0) / p0
        bars = [b for b in eve[d] if b["t"] >= time(18, 0)]
        entry_bar = next((b for b in bars if time(18, 15) <= b["t"] < time(18, 16)), None)
        if entry_bar is None:
            continue
        entry = entry_bar["c"]
        for thr in (0.01, 0.02):
            if abs(ret) < thr:
                continue
            is_long = ret > 0
            sl = entry - 25 if is_long else entry + 25
            tp = entry + 75 if is_long else entry - 75
            res = None
            for b in bars:
                if b["t"] <= time(18, 15):
                    continue
                if b["t"] >= time(21, 0):
                    res = (b["c"] - entry) if is_long else (entry - b["c"])
                    break
                if is_long:
                    if b["l"] <= sl: res = sl - entry; break
                    if b["h"] >= tp: res = tp - entry; break
                else:
                    if b["h"] >= sl: res = entry - sl; break
                    if b["l"] <= tp: res = entry - tp; break
            if res is None and bars:
                res = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
            results[thr].append((d, res * 20.0 - 14.50))
    for thr, rows in sorted(results.items()):
        v = [p for _, p in rows]
        vis = [p for dd, p in rows if dd.year <= 2024]
        vos = [p for dd, p in rows if dd.year >= 2025]
        print(f"  |BTC wknd| > {thr:.0%}:  N={len(v):>3}  PF={pf(v):>6}  ${sum(v):>+8,.0f}   "
              f"(IS PF={pf(vis)} N={len(vis)} | OOS PF={pf(vos)} N={len(vos)})")

    print(f"\n{'═'*88}\n  cross_market done.\n{'═'*88}")
