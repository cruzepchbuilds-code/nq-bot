"""
brain/research/mbt_feasibility.py

Can the BTC London-open breakout (crypto/research/sweep.py — "London_WF",
spot OOS PF 2.02) run on CME Micro Bitcoin futures (MBT) inside prop accounts?

Method
------
1. Re-run the EXACT London Wed+Fri breakout logic on crypto/data/btc_1h.csv,
   but record entry/exit PRICES (sweep.py only keeps pct P&L). A parity
   assert guarantees trade-for-trade identity with the original research.
2. Dollarize each trade at 1 and 2 MBT contracts with a futures cost model
   built from verified numbers (below), and recompute PF / $-expectancy.
3. Split per house law: IS 2022-2024 | OOS 2025-2026 (the crypto research
   used IS 22-23 | OOS 24+; both splits are shown, house law is primary).
4. Compare the 1-MBT stop against the Lucid trailing-floor room ($2,000)
   next to the NQ book's risk constants (ORB $565, REJ $415, PM $455,
   ASIA $515).

VERIFIED CONTRACT FACTS (researched 2026-07-03)
-----------------------------------------------
MBT = CME Micro Bitcoin futures, GLBX (Globex), cash-settled to BRR.
  multiplier      0.1 BTC                 (CME/Ironbeam contract specs)
  tick            5 index points = $0.50  => $0.10 per index point
  hours           Sun-Fri 5:00 PM - 4:00 PM CT (6 PM - 5 PM ET),
                  daily halt 4-5 PM CT (5-6 PM ET) — strategy window
                  (00:00-18:00 UTC) never touches the halt.
  volume          2025 total ~16.6M contracts (~65k/day, Schwab);
                  July-2026 front-month snapshot ~7.9k/day (TradingView)
                  — thin vs MNQ, but workable for 1-2 lots.
  fees (per side, per contract, NinjaTrader schedule 09/10/25):
                  exchange $1.02 + fixed $0.19 + commission $0.39
                  = $1.60 all-in  (Tradovate-style desks run ~$2.50/side;
                  both modeled). 2021 launch-era $2.50 exchange fee is gone.
  spread 3-4 AM ET: no public time-of-day series exists. Anchors: tick is
                  5 pts; CME lists BTC-MBT inter-commodity spreads to keep
                  MBT quotes tight; launch-era forum reports "wide" books.
                  Base case 3 ticks (15 pts = $1.50), stress 6 and 10 ticks.
                  Model charges the FULL spread each way (conservative).

PROP-FIRM PERMISSION (researched 2026-07-03)
--------------------------------------------
  Lucid Trading   BTC/MBT EXPLICITLY EXCLUDED from the 36-product approved
                  list ("weekend volatility breaks the EOD trailing model").
  Tradeify        crypto futures NOT supported on futures accounts.
  Apex Trader     MBT/MET ALLOWED; day = 6 PM ET -> 4:59 PM ET flatten.
                  3-4 AM ET entries are mid-session (fine); strategy
                  flattens 18:00 UTC = 1-2 PM ET (fine).
  => The edge CANNOT run in the trader's existing Lucid/Tradeify accounts.

DATABENTO PILOT (priced 2026-07-03 via FREE metadata endpoint)
--------------------------------------------------------------
  GLBX.MDP3  MBT.v.0  ohlcv-1m  2022-01-01 -> 2026-07-03
  cost $4.11  ·  1,124,602 one-minute bars
  (re-price anytime:  python3 brain/research/mbt_feasibility.py --price-data
   — metadata.get_cost is free; this script NEVER calls timeseries.get_range)

Usage (from repo root):
    python3 brain/research/mbt_feasibility.py
    python3 brain/research/mbt_feasibility.py --price-data
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

# ── Strategy params — MUST match crypto/research/sweep.py london_wedFri ──────
BUFFER   = 0.005
RNG_MIN  = 0.005
RNG_MAX  = 0.04
STOP_PCT = 0.015
TGT_PCT  = 0.04

# ── Spot reference cost model (as used by sweep.py) ──────────────────────────
POS_USD  = 10_000.0
COST_PCT = 0.001

# ── MBT contract + cost model (sources in module docstring) ──────────────────
MULT              = 0.1          # BTC per contract
USD_PER_PT        = 0.10         # $ per index point per contract (0.1 mult)
TICK_PTS          = 5            # 5 index points = $0.50
SPREAD_PTS_BASE   = 15           # 3 ticks — base-case London-window spread
SPREAD_SCENARIOS  = [5, 15, 30, 50]          # 1, 3, 6, 10 ticks
COMM_SIDE_BASE    = 1.60         # NinjaTrader all-in $/side (verified)
COMM_SCENARIOS    = [1.60, 2.50] # NinjaTrader vs Tradovate-style desks

# ── Prop-account risk frame ───────────────────────────────────────────────────
TRAIL_ROOM  = 2_000.0            # Lucid-style trailing floor room
NQ_RISKS    = {"ORB": 565, "REJ": 415, "PM": 455, "ASIA": 515}

# ── House IS/OOS law ──────────────────────────────────────────────────────────
HOUSE_OOS_START    = date(2025, 1, 1)   # IS 2022-2024 | OOS 2025-2026
RESEARCH_OOS_START = date(2024, 1, 1)   # split used by crypto/research/sweep.py

# ── Databento pilot (verified 2026-07-03, free metadata endpoint) ────────────
PILOT_DATASET = "GLBX.MDP3"
PILOT_SYMBOL  = "MBT.v.0"
PILOT_SCHEMA  = "ohlcv-1m"
PILOT_START   = "2022-01-01"
PILOT_COST    = 4.11
PILOT_RECORDS = 1_124_602

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "crypto", "data", "btc_1h.csv")


# ── Data ──────────────────────────────────────────────────────────────────────

def load_bars(path):
    bars = []
    with open(path) as f:
        for r in csv.DictReader(f):
            dt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")\
                         .replace(tzinfo=timezone.utc)
            bars.append({"dt": dt, "o": float(r["open"]), "h": float(r["high"]),
                         "l": float(r["low"]), "c": float(r["close"]),
                         "v": float(r["volume"])})
    bars.sort(key=lambda x: x["dt"])
    return bars


def by_day(bars):
    d = defaultdict(list)
    for b in bars:
        d[b["dt"].date()].append(b)
    return dict(d)


# ── Strategy — identical walk to sweep.py, but exit PRICE is recorded ────────

def sim_px(bars_after, entry, stop_px, tgt_px, is_long, flatten_dt):
    """sweep.py sim() with the exit price returned instead of pct only."""
    for b in bars_after:
        if b["dt"] >= flatten_dt:
            return b["o"], "flatten"
        if is_long:
            if b["l"] <= stop_px: return stop_px, "stop"
            if b["h"] >= tgt_px:  return tgt_px, "target"
        else:
            if b["h"] >= stop_px: return stop_px, "stop"
            if b["l"] <= tgt_px:  return tgt_px, "target"
    return entry, "eod"          # sweep.py returns pnl 0.0 in this case


def london_wedfri_px(bars):
    daily = by_day(bars)
    trades = []
    for d, db in sorted(daily.items()):
        if d.weekday() not in (2, 4):            # Wed=2, Fri=4
            continue
        asia = [b for b in db if b["dt"].hour < 8]
        if len(asia) < 6:
            continue
        hi = max(b["h"] for b in asia)
        lo = min(b["l"] for b in asia)
        if not (RNG_MIN <= (hi - lo) / lo <= RNG_MAX):
            continue
        flat = datetime(d.year, d.month, d.day, 18, 0, tzinfo=timezone.utc)
        for b in [x for x in db if 8 <= x["dt"].hour < 12]:
            if   b["c"] > hi * (1 + BUFFER): is_long = True
            elif b["c"] < lo * (1 - BUFFER): is_long = False
            else: continue
            e    = b["c"]
            stop = e * (1 - STOP_PCT) if is_long else e * (1 + STOP_PCT)
            tgt  = e * (1 + TGT_PCT)  if is_long else e * (1 - TGT_PCT)
            after = [x for x in db if x["dt"] > b["dt"]]
            xp, how = sim_px(after, e, stop, tgt, is_long, flat)
            pnl_pct = (xp - e) / e if is_long else (e - xp) / e
            trades.append({"date": d, "long": is_long, "entry": e,
                           "exit": xp, "how": how, "pnl_pct": pnl_pct})
            break                                 # one trade per day
    return trades


def parity_check(trades):
    """Assert trade-for-trade identity with the original research code."""
    sys.path.insert(0, BASE)
    from crypto.research.sweep import london_wedFri, load_bars as lb  # noqa
    ref = london_wedFri(lb(os.path.join(BASE, "crypto", "data", "btc_1h.csv")))
    assert len(ref) == len(trades), f"N mismatch: {len(ref)} vs {len(trades)}"
    for r, t in zip(ref, trades):
        assert r["date"] == t["date"] and abs(r["pnl"] - t["pnl_pct"]) < 1e-12, \
            f"trade mismatch on {t['date']}"
    return len(ref)


# ── Economics ─────────────────────────────────────────────────────────────────

def spot_net(t):
    """Spot reference: sweep.py's $10k-notional net (0.1% cost)."""
    return t["pnl_pct"] * POS_USD - COST_PCT * POS_USD


def mbt_net(t, contracts, spread_pts, comm_side):
    """
    MBT dollars. Fill worsened by the FULL spread each way (conservative):
      long : entry+S paid, exit-S received   |   short: mirrored
      pnl/$ = dir*(exit-entry)*0.1*n  -  (2*S*0.1 + 2*comm)*n
    """
    d = 1 if t["long"] else -1
    gross = d * (t["exit"] - t["entry"]) * MULT * contracts
    drag  = (2 * spread_pts * USD_PER_PT + 2 * comm_side) * contracts
    return gross - drag


def stats(vals):
    if not vals:
        return None
    wins = [v for v in vals if v > 0]
    gw = sum(wins)
    gl = sum(v for v in vals if v <= 0)
    peak = cum = mdd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {"n": len(vals), "wr": len(wins) / len(vals),
            "pf": gw / abs(gl) if gl else float("inf"),
            "net": sum(vals), "avg": sum(vals) / len(vals),
            "avg_w": gw / len(wins) if wins else 0.0,
            "avg_l": gl / (len(vals) - len(wins)) if len(vals) > len(wins) else 0.0,
            "mdd": mdd}


def split(trades, oos_start):
    return ([t for t in trades if t["date"] < oos_start],
            [t for t in trades if t["date"] >= oos_start])


def fmt_row(label, s):
    if s is None:
        return f"  {label:<22}{'—':>5}"
    pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "inf"
    return (f"  {label:<22}{s['n']:>5}{s['wr']:>7.0%}{pf:>7}"
            f"{s['net']:>10,.0f}{s['avg']:>9,.2f}{s['avg_w']:>9,.0f}"
            f"{s['avg_l']:>9,.0f}{s['mdd']:>10,.0f}")


HDR = (f"  {'Period':<22}{'N':>5}{'WR':>7}{'PF':>7}"
       f"{'Net $':>10}{'$/trade':>9}{'AvgWin':>9}{'AvgLoss':>9}{'MaxDD':>10}")


def price_pilot():
    """FREE Databento metadata cost query — never downloads data."""
    import databento as db
    key = None
    with open(os.path.join(BASE, ".env")) as f:
        for line in f:
            if line.startswith("DATABENTO_API_KEY"):
                key = line.strip().split("=", 1)[1]
    client = db.Historical(key)
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cost = client.metadata.get_cost(
        dataset=PILOT_DATASET, symbols=[PILOT_SYMBOL], stype_in="continuous",
        schema=PILOT_SCHEMA, start=PILOT_START, end=end)
    n = client.metadata.get_record_count(
        dataset=PILOT_DATASET, symbols=[PILOT_SYMBOL], stype_in="continuous",
        schema=PILOT_SCHEMA, start=PILOT_START, end=end)
    print(f"  LIVE quote: {PILOT_SYMBOL} {PILOT_SCHEMA} "
          f"{PILOT_START} -> {end}:  ${cost:.2f}  ({n:,} bars)")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    W = 96
    print("=" * W)
    print("  MBT FEASIBILITY — BTC London-open breakout on CME Micro Bitcoin")
    print("  spread charged FULL each way | house law: IS 2022-24, OOS 2025-26")
    print("=" * W)

    bars = load_bars(DATA)
    trades = london_wedfri_px(bars)
    n_ref = parity_check(trades)
    print(f"\n  Parity: {n_ref} trades match crypto/research/sweep.py "
          f"london_wedFri exactly (dates + pct P&L).")
    print(f"  Data: {bars[0]['dt'].date()} -> {bars[-1]['dt'].date()}  "
          f"BTC ${min(b['l'] for b in bars):,.0f}-${max(b['h'] for b in bars):,.0f}")

    # 0. Spot reference — reproduce the published numbers
    print(f"\n  SPOT REFERENCE ($10k notional, 0.1% cost — sweep.py model)")
    print(HDR)
    for lbl, oos0 in [("research split", RESEARCH_OOS_START),
                      ("HOUSE LAW", HOUSE_OOS_START)]:
        is_t, oos_t = split(trades, oos0)
        print(fmt_row(f"IS  ({lbl})", stats([spot_net(t) for t in is_t])))
        print(fmt_row(f"OOS ({lbl})", stats([spot_net(t) for t in oos_t])))

    # 1. MBT after costs — base case, 1 and 2 contracts, house split
    print(f"\n  MBT AFTER COSTS — base case: spread {SPREAD_PTS_BASE} pts "
          f"(={SPREAD_PTS_BASE//TICK_PTS} ticks, ${SPREAD_PTS_BASE*USD_PER_PT:.2f}) "
          f"each way + ${COMM_SIDE_BASE:.2f}/side commission")
    rt_drag = 2 * SPREAD_PTS_BASE * USD_PER_PT + 2 * COMM_SIDE_BASE
    print(f"  Round-trip drag: ${rt_drag:.2f} per contract "
          f"(vs $10.00/trade cost already charged in the spot model)")
    print(HDR)
    for nc in (1, 2):
        is_t, oos_t = split(trades, HOUSE_OOS_START)
        s_is = stats([mbt_net(t, nc, SPREAD_PTS_BASE, COMM_SIDE_BASE) for t in is_t])
        s_oos = stats([mbt_net(t, nc, SPREAD_PTS_BASE, COMM_SIDE_BASE) for t in oos_t])
        print(fmt_row(f"IS  2022-24  {nc}c", s_is))
        print(fmt_row(f"OOS 2025-26  {nc}c", s_oos))

    # per-year OOS detail at 1c
    print("\n  Year-by-year (1 contract, base costs):", end="")
    for yr in (2022, 2023, 2024, 2025, 2026):
        ys = stats([mbt_net(t, 1, SPREAD_PTS_BASE, COMM_SIDE_BASE)
                    for t in trades if t["date"].year == yr])
        if ys:
            pf = f"{ys['pf']:.2f}" if ys["pf"] != float("inf") else "inf"
            print(f"  {yr}: PF {pf} (n={ys['n']}, ${ys['net']:,.0f})", end="")
    print()

    # 2. Sensitivity — spread x commission, OOS PF and $/trade at 1c
    print(f"\n  SENSITIVITY — OOS 2025-26, 1 contract: PF | $/trade")
    print(f"  {'spread':>10} | " + " | ".join(f"comm ${c:.2f}/side" for c in COMM_SCENARIOS))
    _, oos_t = split(trades, HOUSE_OOS_START)
    for sp in SPREAD_SCENARIOS:
        cells = []
        for cm in COMM_SCENARIOS:
            s = stats([mbt_net(t, 1, sp, cm) for t in oos_t])
            pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "inf"
            cells.append(f"{pf:>6} {s['avg']:>7,.2f}")
        tick_lbl = f"{sp}pt/{sp//TICK_PTS}tk"
        print(f"  {tick_lbl:>10} | " + " | ".join(cells))
    print("  (half-spread-each-way convention would halve the spread drag shown)")

    # 3. Trailing-floor room usage
    print(f"\n  RISK vs ${TRAIL_ROOM:,.0f} TRAILING-FLOOR ROOM "
          f"(1 MBT stop = {STOP_PCT:.1%} of 0.1 BTC + costs)")
    risks = sorted((STOP_PCT * t["entry"] + 2 * SPREAD_PTS_BASE) * USD_PER_PT
                   + 2 * COMM_SIDE_BASE for t in trades)
    lo, med, hi = risks[0], risks[len(risks)//2], risks[-1]
    recent = [(STOP_PCT * t["entry"] + 2 * SPREAD_PTS_BASE) * USD_PER_PT
              + 2 * COMM_SIDE_BASE for t in trades if t["date"].year >= 2025]
    r_recent = sum(recent) / len(recent) if recent else med
    print(f"  1 MBT stop-out: min ${lo:,.0f} | median ${med:,.0f} | max ${hi:,.0f} "
          f"| 2025-26 avg ${r_recent:,.0f} ({r_recent/TRAIL_ROOM:.1%} of room)")
    print(f"  2 MBT 2025-26 avg: ${2*r_recent:,.0f} ({2*r_recent/TRAIL_ROOM:.1%} of room)")
    for k, v in NQ_RISKS.items():
        print(f"    NQ {k:<5} ${v}  = {v/TRAIL_ROOM:.1%} of room")

    # 4. Venue + data pilot
    print(f"\n  VENUE:  Lucid = MBT BANNED (not on 36-product list; 'weekend "
          f"volatility breaks EOD trailing model')")
    print(f"          Tradeify futures = crypto futures NOT supported")
    print(f"          Apex Trader Funding = MBT allowed; 3-4 AM ET is "
          f"mid-session; flatten 18:00 UTC beats the 4:59 PM ET rule")
    print(f"\n  DATA PILOT (verified 2026-07-03, free metadata endpoint):")
    print(f"    {PILOT_DATASET} {PILOT_SYMBOL} {PILOT_SCHEMA} "
          f"{PILOT_START} -> today = ${PILOT_COST:.2f} ({PILOT_RECORDS:,} bars)")
    if "--price-data" in sys.argv:
        price_pilot()

    print("\n" + "=" * W)
    print("  VERDICT: economics survive futures costs; the blocker is venue "
          "permission (Lucid/Tradeify ban MBT).")
    print("  Next step, if pursued: $4.11 MBT 1-min pull -> re-run breakout on "
          "REAL MBT prices (basis, gaps, halt)")
    print("  before any Apex-style account is opened for it.")
    print("=" * W)
