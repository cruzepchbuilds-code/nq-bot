"""
brain/research/pivot_filter.py

Floor Pivot research: does prior-day pivot context improve NQ/ES ORB edge?

Floor pivots (P, R1, R2, S1, S2) computed from prior RTH session H/L/C.
Context price = OR close bar (9:44 1-min bar close).

Hypotheses:
  H1: Directional alignment — long above P (uptrend), short below P (downtrend)
  H2: Pivot proximity filter — skip entries within X pts of any pivot level
  H3: Room-to-run filter — next opposing pivot >= X pts away
  H4: Zone analysis — breakdown by which pivot zone entry is in

IS: [2024]  |  OOS: [2025, 2026]
Usage: python3 brain/research/pivot_filter.py [--nq] [--es]
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import importlib
import config
from backtest import Backtester, load_csv
from collections import defaultdict
from datetime import date

NQ_DATA = "data/nq_full.csv"
ES_DATA = "data/es_1min.csv"

IS_YEARS  = [2024]
OOS_YEARS = [2025, 2026]

ES_OVERRIDES = {
    "FUTURES_SYMBOL":                     "ES",
    "CONTRACT_POINT_VALUE":               50.0,
    "TICK_SIZE":                          0.25,
    "MIN_OR_POINTS":                      5.0,
    "MAX_OR_POINTS":                      30.0,
    "STOP_POINTS":                        7.0,
    "STOP_BUFFER_POINTS":                 2.0,
    "BREAKOUT_BUFFER_POINTS":             1.0,
    "ORB_FUNDED_RR_TARGET":               2.5,
    "STRONG_MONTHS":                      [2, 3, 11],
    "WEAK_MONTHS":                        [1, 4, 5, 6, 7, 8, 9, 10, 12],
    "SIGNAL_STRENGTH_MIN_SCORE":          101,
    "SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP": 101,
}

def apply_es():
    for k, v in ES_OVERRIDES.items():
        setattr(config, k, v)

def revert_nq():
    importlib.reload(config)


# ── pivot computation ─────────────────────────────────────────────────────────

def compute_pivots(bars):
    """Returns {trade_date: {P, R1, R2, S1, S2}} using prior RTH H/L/C."""
    rth_by_date = defaultdict(list)
    for bar in bars:
        ts = bar["timestamp"]
        h, m = ts.hour, ts.minute
        if (h > 9 or (h == 9 and m >= 30)) and h < 16:
            rth_by_date[ts.date()].append(bar)

    session_hlc = {}
    for d, day_bars in rth_by_date.items():
        H = max(b["high"] for b in day_bars)
        L = min(b["low"]  for b in day_bars)
        C = day_bars[-1]["close"]
        session_hlc[d] = (H, L, C)

    sorted_dates = sorted(session_hlc.keys())
    pivots = {}
    for i in range(1, len(sorted_dates)):
        curr_d = sorted_dates[i]
        H, L, C = session_hlc[sorted_dates[i - 1]]
        P  = (H + L + C) / 3.0
        R1 = 2 * P - L
        R2 = P + (H - L)
        S1 = 2 * P - H
        S2 = P - (H - L)
        pivots[curr_d] = {"P": P, "R1": R1, "R2": R2, "S1": S1, "S2": S2}
    return pivots


def build_entry_index(bars):
    idx = {}
    for i, bar in enumerate(bars):
        ts = bar["timestamp"]
        key = (ts.date(), ts.hour, ts.minute)
        if key not in idx:
            idx[key] = i
    return idx


def get_or_price(bars, entry_idx, trade_date):
    """OR close price: 9:44 bar close (last minute of the 15-min OR)."""
    for minute in [44, 45, 43, 46]:
        key = (trade_date, 9, minute)
        i = entry_idx.get(key)
        if i is not None:
            return bars[i]["close"]
    return None


def pivot_zone(price, pv):
    P, R1, R2 = pv["P"], pv["R1"], pv["R2"]
    S1, S2    = pv["S1"], pv["S2"]
    if   price >= R2: return "above_R2"
    elif price >= R1: return "R1_R2"
    elif price >= P:  return "P_R1"
    elif price >= S1: return "S1_P"
    elif price >= S2: return "S2_S1"
    else:             return "below_S2"


def nearest_pivot_dist(price, pv):
    return min(abs(price - pv[k]) for k in ("P", "R1", "R2", "S1", "S2"))


def room_to_next(price, direction, pv):
    """Distance to the next opposing pivot level in trade direction."""
    P, R1, R2 = pv["P"], pv["R1"], pv["R2"]
    S1, S2    = pv["S1"], pv["S2"]
    if direction == "long":
        above = [lvl for lvl in [P, R1, R2] if lvl > price]
        return (min(above) - price) if above else 999.0
    else:
        below = [lvl for lvl in [P, S1, S2] if lvl < price]
        return (price - max(below)) if below else 999.0


def classify(trade, pv, or_price):
    direction  = trade["dir"]
    above_P    = or_price >= pv["P"]
    aligned    = (direction == "long" and above_P) or (direction == "short" and not above_P)
    return {
        "zone":    pivot_zone(or_price, pv),
        "above_P": above_P,
        "aligned": aligned,
        "nearest": nearest_pivot_dist(or_price, pv),
        "room":    room_to_next(or_price, direction, pv),
    }


# ── backtest helpers ──────────────────────────────────────────────────────────

def run_year(bars, year):
    ystart = date(year, 1, 1)
    yend   = date(year + 1, 1, 1)
    prior  = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return []
    warmup = Backtester()
    warmup.run(prior, silent=True)
    bt = Backtester()
    bt._last_close         = warmup._last_close
    bt.regime.daily_ranges = list(warmup.regime.daily_ranges)
    bt.or_volume_history   = list(warmup.or_volume_history)
    bt.prev_day_mode       = warmup.prev_day_mode
    bt.run(subset, silent=True)
    return bt.bank.trade_log


def run_years(bars, years):
    trades = []
    for y in years:
        trades.extend(run_year(bars, y))
    return trades


# ── stats ─────────────────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "avg": 0.0}
    wins = [t for t in trades if t["pnl"] > 0]
    gl   = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    net  = sum(t["pnl"] for t in trades)
    return {
        "n":   len(trades),
        "net": round(net, 0),
        "wr":  len(wins) / len(trades),
        "pf":  round(sum(t["pnl"] for t in wins) / gl, 3) if gl else 99.0,
        "avg": round(net / len(trades), 0),
    }


COL_W = 26

def row(label, s, base_pf, w=None):
    w = w or COL_W
    dpf  = s["pf"] - base_pf
    flag = ("  ← BETTER" if dpf >  0.10 else
            "  ← WORSE"  if dpf < -0.10 else "")
    print(f"  {label:<{w}}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
          f"  ${s['net']:>+9,.0f}  ${s['avg']:>+6,.0f}  {dpf:+.3f}{flag}")


def section(title):
    print(f"\n  ── {title} ──")
    print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  "
          f"  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
    print(f"  {'─' * 76}")


# ── analysis ──────────────────────────────────────────────────────────────────

def analyze(symbol, bars, is_years, oos_years):
    W = 72
    print(f"\n{'=' * W}")
    print(f"  {symbol} — Floor Pivot Filter")
    print(f"  IS: {is_years}  |  OOS: {oos_years}")
    print(f"{'=' * W}")
    print(f"  {len(bars):,} bars  |  {bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()}")

    print("  Computing prior-day pivots...", end=" ", flush=True)
    pivots    = compute_pivots(bars)
    entry_idx = build_entry_index(bars)
    print(f"{len(pivots)} sessions")

    print("  Running IS backtest...",  end=" ", flush=True)
    is_trades  = run_years(bars, is_years)
    print(f"{len(is_trades)} trades")

    print("  Running OOS backtest...", end=" ", flush=True)
    oos_trades = run_years(bars, oos_years)
    print(f"{len(oos_trades)} trades")

    # ── enrich trades with pivot context ──────────────────────────────────────
    def enrich(trades):
        out = []
        for t in trades:
            d  = date.fromisoformat(t["date"])
            pv = pivots.get(d)
            if pv is None:
                continue
            orp = get_or_price(bars, entry_idx, d)
            if orp is None:
                continue
            out.append({**t, **classify(t, pv, orp)})
        return out

    is_e   = enrich(is_trades)
    oos_e  = enrich(oos_trades)
    all_e  = is_e + oos_e
    n_with = len(all_e)
    print(f"  {n_with}/{len(is_trades) + len(oos_trades)} trades with pivot data\n")

    # pivot proximity / room-to-run parameter sets
    if symbol == "NQ":
        prox_pts   = [10, 20, 30, 40]
        room_floors = [20, 40, 60, 80]
        best_room  = 40
    else:
        prox_pts   = [3, 5, 8, 10]
        room_floors = [5, 10, 15, 20]
        best_room  = 10

    base = stats(all_e)

    # ── H1: Directional Alignment ─────────────────────────────────────────────
    section(f"H1: Directional Alignment  (IS+OOS combined, {n_with}/{len(is_trades)+len(oos_trades)} with pivot)")
    aligned = [t for t in all_e if t["aligned"]]
    opposed = [t for t in all_e if not t["aligned"]]
    row("Baseline (with pivot)",       base,           base["pf"])
    row("aligned (long≥P / short<P)",  stats(aligned), base["pf"])
    row("opposed (long<P / short≥P)",  stats(opposed), base["pf"])

    print(f"\n  LONG trades")
    print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
    print(f"  {'─' * 76}")
    longs     = [t for t in all_e if t["dir"] == "long"]
    bl        = stats(longs)
    row("Baseline long",               bl,                                          bl["pf"])
    row("long + aligned (≥P)",         stats([t for t in longs if t["aligned"]]),  bl["pf"])
    row("long + opposed (<P)",         stats([t for t in longs if not t["aligned"]]), bl["pf"])

    print(f"\n  SHORT trades")
    print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
    print(f"  {'─' * 76}")
    shorts    = [t for t in all_e if t["dir"] == "short"]
    bs        = stats(shorts)
    row("Baseline short",              bs,                                           bs["pf"])
    row("short + aligned (<P)",        stats([t for t in shorts if t["aligned"]]),  bs["pf"])
    row("short + opposed (≥P)",        stats([t for t in shorts if not t["aligned"]]), bs["pf"])

    # ── H2: Pivot Proximity Filter ────────────────────────────────────────────
    section("H2: Pivot Proximity Filter  (IS+OOS combined)")
    row("Baseline", base, base["pf"])
    print()
    for z in prox_pts:
        near = [t for t in all_e if t["nearest"] <= z]
        far  = [t for t in all_e if t["nearest"] >  z]
        print(f"  zone = ±{z}pt  (near={len(near)}  far={len(far)})")
        row(f"  skip near (±{z}pt)", stats(far),  base["pf"], w=22)
        row(f"  only near (±{z}pt)", stats(near), base["pf"], w=22)
        print()

    # ── H3: Room-to-Run Filter ────────────────────────────────────────────────
    section("H3: Room-to-Run Filter  (IS+OOS combined)")
    row("Baseline", base, base["pf"])
    print()
    for f in room_floors:
        keep = [t for t in all_e if t["room"] >= f]
        skip = [t for t in all_e if t["room"] <  f]
        print(f"  room >= {f}pt  (keep={len(keep)}  skip={len(skip)})")
        row(f"  keep room>={f}pt", stats(keep), base["pf"], w=22)
        row(f"  skip room>={f}pt", stats(skip), base["pf"], w=22)
        print()

    # ── H4: Zone Analysis ─────────────────────────────────────────────────────
    section("H4: Zone Analysis  (IS+OOS combined)")
    row("Baseline", base, base["pf"])
    print()
    for z in ["above_R2", "R1_R2", "P_R1", "S1_P", "S2_S1", "below_S2"]:
        zt = [t for t in all_e if t["zone"] == z]
        if not zt:
            continue
        zs   = stats(zt)
        dpf  = zs["pf"] - base["pf"]
        flag = ("  ← HOT" if dpf > 0.20 else "  ← COLD" if dpf < -0.20 else "")
        print(f"  {z:<12}  N={zs['n']:>3}  WR={zs['wr']:.0%}  PF={zs['pf']:.3f}  "
              f"Net=${zs['net']:>+9,.0f}  Avg=${zs['avg']:>+6,.0f}  ΔPF={dpf:+.3f}{flag}")
        lz = [t for t in zt if t["dir"] == "long"]
        sz = [t for t in zt if t["dir"] == "short"]
        if lz:
            ls = stats(lz)
            print(f"    ↳ longs : N={ls['n']:>3}  WR={ls['wr']:.0%}  PF={ls['pf']:.3f}  Net=${ls['net']:>+9,.0f}")
        if sz:
            ss = stats(sz)
            print(f"    ↳ shorts: N={ss['n']:>3}  WR={ss['wr']:.0%}  PF={ss['pf']:.3f}  Net=${ss['net']:>+9,.0f}")

    # ── IS vs OOS: H1 ────────────────────────────────────────────────────────
    print(f"\n  ── IS vs OOS: H1 Alignment ──")
    for label, trades_e, yr_label in [("IS",  is_e,  str(is_years)),
                                       ("OOS", oos_e, str(oos_years))]:
        b  = stats(trades_e)
        al = stats([t for t in trades_e if t["aligned"]])
        op = stats([t for t in trades_e if not t["aligned"]])
        print(f"\n  {label} {yr_label}  (N={len(trades_e)}):")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 76}")
        row("Baseline", b,  b["pf"])
        row("aligned",  al, b["pf"])
        row("opposed",  op, b["pf"])

    # ── OOS Year-by-Year ─────────────────────────────────────────────────────
    print(f"\n  ── OOS Year-by-Year ──")
    for yr in oos_years:
        yr_e = [t for t in oos_e if t["date"][:4] == str(yr)]
        if not yr_e:
            continue
        b  = stats(yr_e)
        al = stats([t for t in yr_e if t["aligned"]])
        op = stats([t for t in yr_e if not t["aligned"]])
        print(f"\n  {yr}  (N={len(yr_e)}):")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 76}")
        row("Baseline", b,  b["pf"])
        row("aligned",  al, b["pf"])
        row("opposed",  op, b["pf"])

    # ── IS vs OOS: H3 Room ───────────────────────────────────────────────────
    print(f"\n  ── IS vs OOS: H3 Room-to-Run (≥{best_room}pt) ──")
    for label, trades_e, yr_label in [("IS",  is_e,  str(is_years)),
                                       ("OOS", oos_e, str(oos_years))]:
        b    = stats(trades_e)
        keep = stats([t for t in trades_e if t["room"] >= best_room])
        skip = stats([t for t in trades_e if t["room"] <  best_room])
        print(f"\n  {label} {yr_label}:")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 76}")
        row("Baseline",              b,    b["pf"])
        row(f"keep room≥{best_room}pt", keep, b["pf"])
        row(f"skip room<{best_room}pt", skip, b["pf"])

    # ── Verdict ───────────────────────────────────────────────────────────────
    ob   = stats(oos_e)
    oal  = stats([t for t in oos_e if t["aligned"]])
    oop  = stats([t for t in oos_e if not t["aligned"]])
    ork  = stats([t for t in oos_e if t["room"] >= best_room])

    dpf_al   = oal["pf"]  - ob["pf"]
    dpf_room = ork["pf"]  - ob["pf"]

    print(f"\n  ── Verdict ──")
    print(f"  OOS Baseline PF : {ob['pf']:.3f}  (N={ob['n']})")
    print(f"  H1 Aligned PF   : {oal['pf']:.3f}  (N={oal['n']})")
    print(f"  H1 Opposed PF   : {oop['pf']:.3f}  (N={oop['n']})")
    print(f"  H3 Room≥{best_room}pt PF  : {ork['pf']:.3f}  (N={ork['n']})")

    def verdict_line(label, dpf, n):
        if dpf > 0.10 and n >= 30:
            return f"  → {label}: SIGNAL — ΔPF {dpf:+.3f}, N={n} sufficient"
        elif dpf > 0.10:
            return f"  → {label}: WEAK — ΔPF {dpf:+.3f} but N={n} insufficient"
        else:
            return f"  → {label}: NO SIGNAL — ΔPF {dpf:+.3f}"

    print(verdict_line("H1 Alignment",     dpf_al,   oal["n"]))
    print(verdict_line(f"H3 Room≥{best_room}pt", dpf_room, ork["n"]))
    print()


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    args   = sys.argv[1:]
    run_nq = "--es" not in args or "--nq" in args
    run_es = "--es" in args or "--nq" not in args

    print(f"\nCruzCapital — Floor Pivot Research")
    print(f"IS: {IS_YEARS}  |  OOS: {OOS_YEARS}")

    if run_nq:
        bars = load_csv(NQ_DATA)
        analyze("NQ", bars, IS_YEARS, OOS_YEARS)

    if run_es:
        apply_es()
        bars = load_csv(ES_DATA)
        analyze("ES", bars, IS_YEARS, OOS_YEARS)
        revert_nq()

    print(f"{'=' * 72}")
    print(f"  Done.")
    print(f"{'=' * 72}\n")


if __name__ == "__main__":
    main()
