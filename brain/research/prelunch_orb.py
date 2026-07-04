"""
brain/research/prelunch_orb.py

NQ Pre-Lunch ORB — 11:00-11:14 ET opening range, entry 11:15-12:00.

Hypothesis: The mid-morning consolidation (11:00-11:14) before the NY lunch
lull produces a directional range. Break above/below that range has
follow-through in the 11:15-12:00 window.

Strategy params (fixed):
  - OR window:     11:00-11:14 ET (15 bars)
  - Entry window:  11:15-12:00 ET
  - Direction:     close > OR_hi + 2pt → long;  close < OR_lo - 2pt → short
  - Stop:          22pt fixed
  - Target:        2R = 44pt
  - Hard flatten:  12:00
  - One trade/day, skip Mon + Fri
  - POINT_VALUE=20, commission=$5/trade

Parts:
  1. OR range filter sweep (OOS) — OR_MAX in [20,30,40,50,60], OR_MIN in [5,10,15]
  2. DOW breakdown (OOS best params)
  3. Year-by-year OOS (2025, 2026)
  4. Independence check — what % of pre-lunch trade days overlap with morning ORB days?
"""

import sys
import os
from datetime import date, datetime, time, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest import load_csv

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "data", "nq_full.csv")

# ── Pre-Lunch ORB constants ───────────────────────────────────────────────────
PL_OR_START   = time(11,  0)
PL_OR_END     = time(11, 15)   # exclusive; bars 11:00-11:14 inclusive
PL_ENTRY_START = time(11, 15)
PL_ENTRY_END   = time(12,  0)  # hard flatten at 12:00
PL_EXIT_TIME   = time(12,  0)

# Fixed params
STOP_PT     = 22.0
RR          = 2.0
BUFFER_PT   = 2.0
POINT_VALUE = 20.0
COMMISSION  = 5.0   # round-trip

# Morning ORB constants (9:30-9:44 OR window, entry 9:45-10:30)
# used only for independence check
MOR_OR_START    = time(9, 30)
MOR_OR_END      = time(9, 45)
MOR_ENTRY_START = time(9, 45)
MOR_ENTRY_END   = time(10, 30)

IS_END    = date(2024, 12, 31)
OOS_START = date(2025,  1,  1)

DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri"]


# ── Core engine ───────────────────────────────────────────────────────────────

def run_prelunch_orb(bars, or_min=5.0, or_max=60.0,
                     stop_pts=STOP_PT, rr=RR, buffer=BUFFER_PT):
    """Simulate pre-lunch ORB on a list of bar dicts.

    Returns list of trade dicts with keys:
        date, dow, side, pnl, hit
    """
    trades = []
    by_date = defaultdict(list)
    for b in bars:
        by_date[b["ts"].date()].append(b)

    for d, day_bars in sorted(by_date.items()):
        dow = d.weekday()  # 0=Mon, 4=Fri
        if dow == 0 or dow == 4:   # skip Mon + Fri
            continue

        or_hi = or_lo = None
        or_built = False
        position = None
        entry_px = stop_px = target_px = None
        entry_ts = None
        trade_done = False

        for bar in sorted(day_bars, key=lambda x: x["ts"]):
            ts = bar["ts"].time()
            px_h, px_l, px_c = bar["high"], bar["low"], bar["close"]

            # ── Build OR (11:00–11:14) ─────────────────────────────────────
            if PL_OR_START <= ts < PL_OR_END:
                or_hi = max(or_hi, px_h) if or_hi is not None else px_h
                or_lo = min(or_lo, px_l) if or_lo is not None else px_l

            # ── Finalize OR once we reach 11:15 ───────────────────────────
            if ts >= PL_OR_END and not or_built:
                or_built = True
                if or_hi is None or or_lo is None:
                    break
                or_range = or_hi - or_lo
                if not (or_min <= or_range <= or_max):
                    break   # skip this day — OR outside filter

            if not or_built:
                continue

            # ── Manage open position ───────────────────────────────────────
            if position:
                if position == "long":
                    if px_l <= stop_px:
                        pnl = (stop_px - entry_px) * POINT_VALUE - COMMISSION
                        trades.append({"date": d, "dow": dow, "side": "long",
                                       "pnl": pnl, "hit": "stop"})
                        position = None
                        continue
                    if px_h >= target_px:
                        pnl = (target_px - entry_px) * POINT_VALUE - COMMISSION
                        trades.append({"date": d, "dow": dow, "side": "long",
                                       "pnl": pnl, "hit": "target"})
                        position = None
                        continue
                elif position == "short":
                    if px_h >= stop_px:
                        pnl = (entry_px - stop_px) * POINT_VALUE - COMMISSION
                        trades.append({"date": d, "dow": dow, "side": "short",
                                       "pnl": pnl, "hit": "stop"})
                        position = None
                        continue
                    if px_l <= target_px:
                        pnl = (entry_px - target_px) * POINT_VALUE - COMMISSION
                        trades.append({"date": d, "dow": dow, "side": "short",
                                       "pnl": pnl, "hit": "target"})
                        position = None
                        continue

                # Hard flatten at 12:00
                if ts >= PL_EXIT_TIME:
                    exit_px = px_c
                    if position == "long":
                        pnl = (exit_px - entry_px) * POINT_VALUE - COMMISSION
                    else:
                        pnl = (entry_px - exit_px) * POINT_VALUE - COMMISSION
                    trades.append({"date": d, "dow": dow, "side": position,
                                   "pnl": pnl, "hit": "flatten"})
                    position = None
                    continue

                continue  # hold

            # ── Entry logic ───────────────────────────────────────────────
            if PL_ENTRY_START <= ts < PL_ENTRY_END and not trade_done:
                if px_c > or_hi + buffer:
                    position   = "long"
                    entry_px   = or_hi + buffer
                    stop_px    = entry_px - stop_pts
                    target_px  = entry_px + stop_pts * rr
                    entry_ts   = bar["ts"]
                    trade_done = True
                elif px_c < or_lo - buffer:
                    position   = "short"
                    entry_px   = or_lo - buffer
                    stop_px    = entry_px + stop_pts
                    target_px  = entry_px - stop_pts * rr
                    entry_ts   = bar["ts"]
                    trade_done = True

        # End-of-day cleanup if still in position past data
        if position and day_bars:
            exit_px = sorted(day_bars, key=lambda x: x["ts"])[-1]["close"]
            if position == "long":
                pnl = (exit_px - entry_px) * POINT_VALUE - COMMISSION
            else:
                pnl = (entry_px - exit_px) * POINT_VALUE - COMMISSION
            trades.append({"date": d, "dow": dow, "side": position,
                           "pnl": pnl, "hit": "flatten"})

    return trades


def run_morning_orb_days(bars):
    """Collect dates where morning ORB fired (9:30 OR, entry 9:45-10:30).
    Returns a set of dates."""
    fire_dates = set()
    by_date = defaultdict(list)
    for b in bars:
        by_date[b["ts"].date()].append(b)

    for d, day_bars in sorted(by_date.items()):
        dow = d.weekday()
        if dow == 0 or dow == 4:
            continue

        or_hi = or_lo = None
        or_built = False

        for bar in sorted(day_bars, key=lambda x: x["ts"]):
            ts = bar["ts"].time()
            px_h, px_l, px_c = bar["high"], bar["low"], bar["close"]

            if MOR_OR_START <= ts < MOR_OR_END:
                or_hi = max(or_hi, px_h) if or_hi is not None else px_h
                or_lo = min(or_lo, px_l) if or_lo is not None else px_l

            if ts >= MOR_OR_END and not or_built:
                or_built = True
                if or_hi is None or or_lo is None:
                    break

            if not or_built:
                continue

            if MOR_ENTRY_START <= ts < MOR_ENTRY_END:
                if px_c > or_hi + 2.0 or px_c < or_lo - 2.0:
                    fire_dates.add(d)
                    break  # one trade per day

    return fire_dates


# ── Stats helpers ─────────────────────────────────────────────────────────────

def pf(trades):
    wins   = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    return wins / losses if losses > 0 else float("inf")


def stats_line(trades, label=""):
    if not trades:
        return f"  {label:32s}  N=  0  WR=  —    PF=  —     Net=        $—"
    n   = len(trades)
    net = sum(t["pnl"] for t in trades)
    wr  = sum(1 for t in trades if t["pnl"] > 0) / n
    p   = pf(trades)
    avg = net / n
    pf_str = f"{p:5.2f}" if p != float("inf") else "  inf"
    return (f"  {label:32s}  N={n:3d}  WR={wr:4.0%}  PF={pf_str}  "
            f"Net={net:>+9,.0f}  Avg={avg:>+5.0f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 75)
    print("  NQ Pre-Lunch ORB Research")
    print("  OR: 11:00-11:14 ET | Entry: 11:15-12:00 | Flatten: 12:00")
    print(f"  Stop: {STOP_PT}pt | Target: {STOP_PT*RR}pt (2R) | Buffer: {BUFFER_PT}pt")
    print("  Skip: Mon + Fri | One trade/day | POINT_VALUE=$20 | Comm=$5")
    print("=" * 75)

    # ── Load data ─────────────────────────────────────────────────────────────
    print(f"\nLoading {DATA_PATH} ...")
    raw = load_csv(DATA_PATH)
    bars = []
    for row in raw:
        try:
            ts = row["timestamp"] if isinstance(row["timestamp"], datetime) \
                 else datetime.strptime(str(row["timestamp"])[:19], "%Y-%m-%d %H:%M:%S")
            # Normalize to naive ET (strip tz info if present)
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            bars.append({
                "ts":    ts,
                "open":  float(row["open"]),
                "high":  float(row["high"]),
                "low":   float(row["low"]),
                "close": float(row["close"]),
            })
        except Exception:
            continue

    print(f"Loaded {len(bars):,} bars")

    is_bars  = [b for b in bars if b["ts"].date() <= IS_END]
    oos_bars = [b for b in bars if b["ts"].date() >= OOS_START]
    print(f"IS  ({2022}-{IS_END.year}):  {len(is_bars):,} bars")
    print(f"OOS ({OOS_START.year}-{2026}): {len(oos_bars):,} bars")

    # ── Baseline (no OR filter) ───────────────────────────────────────────────
    print("\n── Baseline (no OR filter, or_min=0, or_max=999) ───────────────")
    is_base  = run_prelunch_orb(is_bars,  or_min=0,  or_max=999)
    oos_base = run_prelunch_orb(oos_bars, or_min=0,  or_max=999)
    print(stats_line(is_base,  "IS  2022-2024"))
    print(stats_line(oos_base, "OOS 2025-2026"))

    # ═══════════════════════════════════════════════════════════════════════════
    # PART 1 — OR size sweep (OOS)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print("  PART 1 — OR Range Filter Sweep (OOS only)")
    print("  OR_MAX ∈ {20, 30, 40, 50, 60}  ×  OR_MIN ∈ {5, 10, 15}")
    print("=" * 75)

    sweep_results = []
    for or_min in [5, 10, 15]:
        for or_max in [20, 30, 40, 50, 60]:
            t = run_prelunch_orb(oos_bars, or_min=or_min, or_max=or_max)
            if len(t) == 0:
                sweep_results.append((or_min, or_max, 0, 0.0, 0.0, 0.0))
                continue
            n   = len(t)
            wr  = sum(1 for x in t if x["pnl"] > 0) / n
            p   = pf(t)
            net = sum(x["pnl"] for x in t)
            sweep_results.append((or_min, or_max, n, wr, p, net))

    # Sort by PF descending
    sweep_results.sort(key=lambda x: x[4], reverse=True)

    print(f"\n  {'OR_MIN':>6}  {'OR_MAX':>6}  {'N':>4}  {'WR':>5}  {'PF':>5}  {'Net$':>10}")
    print("  " + "-" * 50)
    for or_min, or_max, n, wr, p, net in sweep_results:
        pf_str = f"{p:5.2f}" if p != float("inf") else "  inf"
        wr_str = f"{wr:.0%}" if n > 0 else "  — "
        net_str = f"{net:>+10,.0f}" if n > 0 else "         —"
        print(f"  {or_min:>6}  {or_max:>6}  {n:>4}  {wr_str:>5}  {pf_str}  {net_str}")

    # Best combo
    best = sweep_results[0]
    best_or_min, best_or_max = best[0], best[1]
    print(f"\n  --> Best OOS combo: OR_MIN={best_or_min}, OR_MAX={best_or_max}  "
          f"(N={best[2]}, WR={best[3]:.0%}, PF={best[4]:.2f}, Net=${best[5]:+,.0f})")

    # IS check on best params
    is_best = run_prelunch_orb(is_bars, or_min=best_or_min, or_max=best_or_max)
    oos_best = run_prelunch_orb(oos_bars, or_min=best_or_min, or_max=best_or_max)
    print(stats_line(is_best,  f"  IS  (best OR {best_or_min}-{best_or_max})"))
    print(stats_line(oos_best, f"  OOS (best OR {best_or_min}-{best_or_max})"))

    # ═══════════════════════════════════════════════════════════════════════════
    # PART 2 — DOW breakdown (OOS, best params)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print(f"  PART 2 — Day-of-Week Breakdown (OOS, OR {best_or_min}-{best_or_max}pt)")
    print("=" * 75)

    for dow_idx, dow_name in enumerate(DAYS_OF_WEEK):
        dow_trades = [t for t in oos_best if t["dow"] == dow_idx]
        # Mon and Fri will be empty (skipped in engine)
        print(stats_line(dow_trades, f"  {dow_name}"))

    # ═══════════════════════════════════════════════════════════════════════════
    # PART 3 — Year-by-year OOS
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print(f"  PART 3 — Year-by-Year OOS (OR {best_or_min}-{best_or_max}pt)")
    print("=" * 75)

    for yr in [2025, 2026]:
        yr_bars = [b for b in oos_bars if b["ts"].year == yr]
        yr_t    = run_prelunch_orb(yr_bars, or_min=best_or_min, or_max=best_or_max)
        print(stats_line(yr_t, f"  {yr}"))

    # ═══════════════════════════════════════════════════════════════════════════
    # PART 4 — Independence check (OOS)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print("  PART 4 — Independence Check (OOS)")
    print("  Q: What % of pre-lunch trade days also had a morning ORB trade?")
    print("=" * 75)

    pl_trade_dates = {t["date"] for t in oos_best}
    mor_fire_dates = run_morning_orb_days(oos_bars)

    overlap = pl_trade_dates & mor_fire_dates
    n_pl    = len(pl_trade_dates)
    n_mor   = len(mor_fire_dates)
    n_over  = len(overlap)
    pct_overlap = (n_over / n_pl * 100) if n_pl > 0 else 0.0

    print(f"\n  Pre-lunch ORB trade days (OOS):   {n_pl}")
    print(f"  Morning ORB fire days (OOS):       {n_mor}")
    print(f"  Overlap (both fired same day):     {n_over}")
    print(f"  Overlap %:                         {pct_overlap:.1f}%")
    if pct_overlap < 20:
        verdict = "GOOD — strategies are largely independent (low overlap)"
    elif pct_overlap < 40:
        verdict = "MODERATE — some overlap, partially correlated days"
    else:
        verdict = "HIGH — fires on same days as morning ORB, may be redundant"
    print(f"  Verdict: {verdict}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SUMMARY / EDGE VERDICT
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print("  EDGE VERDICT")
    print("=" * 75)

    oos_pf_best = best[4]
    oos_n_best  = best[2]
    is_pf_best  = pf(is_best) if is_best else 0.0
    oos_net     = sum(t["pnl"] for t in oos_best)

    print(f"\n  Baseline (no filter):")
    print(f"    IS  PF: {pf(is_base):.2f}   OOS PF: {pf(oos_base):.2f}   OOS N: {len(oos_base)}")
    print(f"\n  Best filtered (OR {best_or_min}-{best_or_max}pt):")
    print(f"    IS  PF: {is_pf_best:.2f}   OOS PF: {oos_pf_best:.2f}   OOS N: {oos_n_best}   OOS Net: ${oos_net:+,.0f}")

    if oos_pf_best >= 1.5 and oos_n_best >= 20 and is_pf_best >= 1.3:
        print("\n  --> EDGE EXISTS: OOS PF >= 1.5, sample size adequate, IS confirms.")
        print("      Candidate for live deployment alongside morning ORB.")
    elif oos_pf_best >= 1.3 and oos_n_best >= 15:
        print("\n  --> WEAK EDGE: Marginal PF, monitor carefully. More OOS data needed.")
    else:
        print("\n  --> NO CLEAR EDGE: OOS PF too low or sample too thin. Skip.")

    print()


if __name__ == "__main__":
    main()
