"""
brain/research/vwap_filter.py

VWAP as directional filter for NQ/ES ORB.
Distinct from the failed VWAP Pullback AM strategy (entry ON VWAP).
This tests VWAP as a go/no-go CONTEXT filter at OR close.

Computations:
  prior_vwap   = full prior RTH session volume-weighted avg price
  live_vwap    = current session VWAP at 9:44 (OR close)
  vwap_slope   = live_vwap_at_9:44 minus live_vwap_at_9:35 (rising/falling OR)
  weekly_vwap  = prior 5 RTH sessions VWAP (Mon-Fri anchor)

Hypotheses:
  H1: Prior session VWAP — long if OR above prior VWAP, short if below
  H2: VWAP slope during OR — long if rising, short if falling
  H3: Distance from prior VWAP — does proximity predict chop or breakout?
  H4: 5-day rolling VWAP — broader trend context filter
  H5: COMBO — prior VWAP + slope agree (strongest alignment signal)

IS: [2024]  |  OOS: [2025, 2026]
Usage: python3 brain/research/vwap_filter.py [--nq] [--es]

NOTE (2026-07-03): regime-ATR warmup transplant fixed to a bounded 14-day
deque (was list(...) -> unbounded -> expanding mean; ref param_stability.py,
found by the parameter-stability audit). Results produced by this script
BEFORE this fix used the buggy expanding-mean regime gate - re-run before
citing absolute numbers (live-params morning-ORB effect: OOS N 52->64,
PF 2.84->2.35).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import importlib
import config
from backtest import Backtester, load_csv
from collections import defaultdict, deque
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


# ── VWAP computation ──────────────────────────────────────────────────────────

def compute_vwap_context(bars):
    """
    Returns dict keyed by trade_date:
      {
        "prior_vwap":   float | None,   # full prior RTH session VWAP
        "weekly_vwap":  float | None,   # prior 5 RTH sessions VWAP
        "live_vwap":    float | None,   # current session VWAP at 9:44
        "slope":        float | None,   # live_vwap_9:44 - live_vwap_9:35
      }
    """
    rth_by_date = defaultdict(list)
    for bar in bars:
        ts = bar["timestamp"]
        h, m = ts.hour, ts.minute
        if (h > 9 or (h == 9 and m >= 30)) and h < 16:
            rth_by_date[ts.date()].append(bar)

    sorted_dates = sorted(rth_by_date.keys())

    # Compute full-session VWAP for each RTH day
    session_vwap = {}
    for d in sorted_dates:
        day_bars = rth_by_date[d]
        cum_pv = sum(b["close"] * b["volume"] for b in day_bars)
        cum_v  = sum(b["volume"] for b in day_bars)
        session_vwap[d] = cum_pv / cum_v if cum_v > 0 else None

    ctx = {}
    for i, d in enumerate(sorted_dates):
        day_bars = rth_by_date[d]

        # Live VWAP at 9:44 (current session up to OR close)
        bars_944 = [b for b in day_bars
                    if b["timestamp"].hour == 9 and b["timestamp"].minute <= 44]
        if bars_944:
            pv = sum(b["close"] * b["volume"] for b in bars_944)
            v  = sum(b["volume"] for b in bars_944)
            live_944 = pv / v if v > 0 else None
        else:
            live_944 = None

        # Live VWAP at 9:35 (for slope computation)
        bars_935 = [b for b in day_bars
                    if b["timestamp"].hour == 9 and b["timestamp"].minute <= 35]
        if bars_935:
            pv = sum(b["close"] * b["volume"] for b in bars_935)
            v  = sum(b["volume"] for b in bars_935)
            live_935 = pv / v if v > 0 else None
        else:
            live_935 = None

        slope = (live_944 - live_935) if (live_944 and live_935) else None

        # Prior session VWAP
        prior = session_vwap.get(sorted_dates[i - 1]) if i > 0 else None

        # 5-day rolling VWAP (prior 5 RTH sessions)
        if i >= 5:
            prior_5 = sorted_dates[i - 5:i]
            all_bars_5 = []
            for pd in prior_5:
                all_bars_5.extend(rth_by_date[pd])
            pv5 = sum(b["close"] * b["volume"] for b in all_bars_5)
            v5  = sum(b["volume"] for b in all_bars_5)
            weekly = pv5 / v5 if v5 > 0 else None
        else:
            weekly = None

        ctx[d] = {
            "prior_vwap":  prior,
            "weekly_vwap": weekly,
            "live_vwap":   live_944,
            "slope":       slope,
        }

    return ctx


def build_entry_index(bars):
    idx = {}
    for i, bar in enumerate(bars):
        ts = bar["timestamp"]
        key = (ts.date(), ts.hour, ts.minute)
        if key not in idx:
            idx[key] = i
    return idx


def get_or_price(bars, entry_idx, trade_date):
    for minute in [44, 45, 43, 46]:
        key = (trade_date, 9, minute)
        i = entry_idx.get(key)
        if i is not None:
            return bars[i]["close"]
    return None


def classify(trade, ctx, or_price):
    direction = trade["dir"]
    pv    = ctx["prior_vwap"]
    wv    = ctx["weekly_vwap"]
    lv    = ctx["live_vwap"]
    slope = ctx["slope"]

    above_prior  = (or_price >= pv)  if pv    is not None else None
    above_weekly = (or_price >= wv)  if wv    is not None else None
    above_live   = (or_price >= lv)  if lv    is not None else None
    slope_up     = (slope > 0)       if slope is not None else None

    h1_aligned = ((direction == "long" and above_prior) or
                  (direction == "short" and not above_prior)) if above_prior is not None else None

    h4_aligned = ((direction == "long" and above_weekly) or
                  (direction == "short" and not above_weekly)) if above_weekly is not None else None

    # H2: slope alignment (rising VWAP during OR → long bias)
    h2_aligned = ((direction == "long" and slope_up) or
                  (direction == "short" and not slope_up)) if slope_up is not None else None

    # H5: combo — prior VWAP AND slope both agree
    if h1_aligned is not None and h2_aligned is not None:
        h5_both_aligned = h1_aligned and h2_aligned
        h5_both_opposed = (not h1_aligned) and (not h2_aligned)
        h5_mixed        = h1_aligned != h2_aligned
    else:
        h5_both_aligned = h5_both_opposed = h5_mixed = None

    dist_prior  = (or_price - pv)  if pv    is not None else None
    dist_weekly = (or_price - wv)  if wv    is not None else None

    return {
        "above_prior":    above_prior,
        "above_weekly":   above_weekly,
        "above_live":     above_live,
        "slope_up":       slope_up,
        "slope_pts":      slope,
        "h1_aligned":     h1_aligned,
        "h2_aligned":     h2_aligned,
        "h4_aligned":     h4_aligned,
        "h5_aligned":     h5_both_aligned,
        "h5_opposed":     h5_both_opposed,
        "h5_mixed":       h5_mixed,
        "dist_prior":     dist_prior,
        "dist_weekly":    dist_weekly,
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
    bt.regime.daily_ranges = deque(warmup.regime.daily_ranges, maxlen=config.REGIME_ATR_PERIOD)
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


COL_W = 28

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
    print(f"  {'─' * 78}")


# ── analysis ──────────────────────────────────────────────────────────────────

def analyze(symbol, bars, is_years, oos_years):
    W = 72
    print(f"\n{'=' * W}")
    print(f"  {symbol} — VWAP Directional Filter")
    print(f"  IS: {is_years}  |  OOS: {oos_years}")
    print(f"{'=' * W}")
    print(f"  {len(bars):,} bars  |  {bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()}")

    print("  Computing VWAP context...", end=" ", flush=True)
    vwap_ctx  = compute_vwap_context(bars)
    entry_idx = build_entry_index(bars)
    print(f"{len(vwap_ctx)} sessions")

    print("  Running IS backtest...",  end=" ", flush=True)
    is_trades  = run_years(bars, is_years)
    print(f"{len(is_trades)} trades")

    print("  Running OOS backtest...", end=" ", flush=True)
    oos_trades = run_years(bars, oos_years)
    print(f"{len(oos_trades)} trades")

    # ── enrich trades ──────────────────────────────────────────────────────────
    def enrich(trades):
        out = []
        for t in trades:
            d   = date.fromisoformat(t["date"])
            ctx = vwap_ctx.get(d)
            if ctx is None:
                continue
            orp = get_or_price(bars, entry_idx, d)
            if orp is None:
                continue
            out.append({**t, **classify(t, ctx, orp), "or_price": orp})
        return out

    is_e   = enrich(is_trades)
    oos_e  = enrich(oos_trades)
    all_e  = is_e + oos_e
    n_with = len(all_e)
    print(f"  {n_with}/{len(is_trades) + len(oos_trades)} trades with VWAP data\n")

    base = stats(all_e)

    # ── H1: Prior Session VWAP ─────────────────────────────────────────────────
    h1_trades  = [t for t in all_e if t["h1_aligned"] is not None]
    h1_al      = [t for t in h1_trades if t["h1_aligned"]]
    h1_op      = [t for t in h1_trades if not t["h1_aligned"]]
    base_h1    = stats(h1_trades)

    section(f"H1: Prior Session VWAP  (IS+OOS combined, {len(h1_trades)}/{n_with} with prior VWAP)")
    row("Baseline (with prior VWAP)", base_h1,         base_h1["pf"])
    row("aligned (long≥pvwap / short<pvwap)", stats(h1_al), base_h1["pf"])
    row("opposed (long<pvwap / short≥pvwap)", stats(h1_op), base_h1["pf"])

    # H1 by direction
    for dir_label, direction in [("LONG", "long"), ("SHORT", "short")]:
        print(f"\n  {dir_label} trades")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 78}")
        dt    = [t for t in all_e if t["dir"] == direction and t["h1_aligned"] is not None]
        bdt   = stats(dt)
        row(f"Baseline {direction}",         bdt,                                     bdt["pf"])
        row(f"{direction} + aligned",        stats([t for t in dt if t["h1_aligned"]]),  bdt["pf"])
        row(f"{direction} + opposed",        stats([t for t in dt if not t["h1_aligned"]]), bdt["pf"])

    # ── H1 Distance Buckets ────────────────────────────────────────────────────
    if symbol == "NQ":
        dist_buckets = [50, 100, 200, 300]
        far_floor    = 100
    else:
        dist_buckets = [10, 25, 50, 75]
        far_floor    = 25

    section(f"H1b: Distance from Prior VWAP  (IS+OOS combined)")
    row("Baseline", base_h1, base_h1["pf"])
    print()
    for d_thresh in dist_buckets:
        near = [t for t in h1_trades if t["dist_prior"] is not None and abs(t["dist_prior"]) <= d_thresh]
        far  = [t for t in h1_trades if t["dist_prior"] is not None and abs(t["dist_prior"]) >  d_thresh]
        print(f"  ±{d_thresh}pt  (near={len(near)}  far={len(far)})")
        row(f"  only near (±{d_thresh}pt)", stats(near), base_h1["pf"], w=24)
        row(f"  only far  (>{d_thresh}pt)", stats(far),  base_h1["pf"], w=24)
        print()

    # ── H2: VWAP Slope During OR ───────────────────────────────────────────────
    h2_trades = [t for t in all_e if t["h2_aligned"] is not None]
    h2_al     = [t for t in h2_trades if t["h2_aligned"]]
    h2_op     = [t for t in h2_trades if not t["h2_aligned"]]
    base_h2   = stats(h2_trades)

    section(f"H2: VWAP Slope 9:35→9:44  (IS+OOS combined, {len(h2_trades)}/{n_with} with slope)")
    row("Baseline (with slope)", base_h2, base_h2["pf"])
    row("slope aligned (rising→long / falling→short)", stats(h2_al), base_h2["pf"])
    row("slope opposed (rising→short / falling→long)", stats(h2_op), base_h2["pf"])

    for dir_label, direction in [("LONG", "long"), ("SHORT", "short")]:
        print(f"\n  {dir_label} trades")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 78}")
        dt  = [t for t in all_e if t["dir"] == direction and t["h2_aligned"] is not None]
        bdt = stats(dt)
        row(f"Baseline {direction}", bdt, bdt["pf"])
        row(f"{direction} slope aligned",  stats([t for t in dt if t["h2_aligned"]]),     bdt["pf"])
        row(f"{direction} slope opposed",  stats([t for t in dt if not t["h2_aligned"]]), bdt["pf"])

    # ── H4: 5-Day Rolling VWAP ────────────────────────────────────────────────
    h4_trades = [t for t in all_e if t["h4_aligned"] is not None]
    h4_al     = [t for t in h4_trades if t["h4_aligned"]]
    h4_op     = [t for t in h4_trades if not t["h4_aligned"]]
    base_h4   = stats(h4_trades)

    section(f"H4: 5-Day Rolling VWAP  (IS+OOS combined, {len(h4_trades)}/{n_with} with weekly VWAP)")
    row("Baseline (with weekly VWAP)", base_h4,        base_h4["pf"])
    row("aligned (long≥wvwap / short<wvwap)", stats(h4_al), base_h4["pf"])
    row("opposed (long<wvwap / short≥wvwap)", stats(h4_op), base_h4["pf"])

    # ── H5: Combo — Prior VWAP + Slope Agree ──────────────────────────────────
    h5_trades   = [t for t in all_e if t["h5_aligned"] is not None]
    h5_both_al  = [t for t in h5_trades if t["h5_aligned"]]
    h5_both_op  = [t for t in h5_trades if t["h5_opposed"]]
    h5_mixed_t  = [t for t in h5_trades if t["h5_mixed"]]
    base_h5     = stats(h5_trades)

    section(f"H5: Combo — Prior VWAP + Slope  (IS+OOS combined, {len(h5_trades)}/{n_with})")
    row("Baseline (with both signals)", base_h5,           base_h5["pf"])
    row("BOTH aligned",                 stats(h5_both_al), base_h5["pf"])
    row("BOTH opposed",                 stats(h5_both_op), base_h5["pf"])
    row("mixed (signals disagree)",     stats(h5_mixed_t), base_h5["pf"])

    # ── IS vs OOS: H1 ─────────────────────────────────────────────────────────
    print(f"\n  ── IS vs OOS: H1 Prior VWAP ──")
    for label, trades_e, yr_label in [("IS",  is_e,  str(is_years)),
                                       ("OOS", oos_e, str(oos_years))]:
        h1_t = [t for t in trades_e if t["h1_aligned"] is not None]
        b    = stats(h1_t)
        al   = stats([t for t in h1_t if t["h1_aligned"]])
        op   = stats([t for t in h1_t if not t["h1_aligned"]])
        print(f"\n  {label} {yr_label}  (N={len(h1_t)}):")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 78}")
        row("Baseline", b,  b["pf"])
        row("aligned",  al, b["pf"])
        row("opposed",  op, b["pf"])

    # ── IS vs OOS: H2 ─────────────────────────────────────────────────────────
    print(f"\n  ── IS vs OOS: H2 VWAP Slope ──")
    for label, trades_e, yr_label in [("IS",  is_e,  str(is_years)),
                                       ("OOS", oos_e, str(oos_years))]:
        h2_t = [t for t in trades_e if t["h2_aligned"] is not None]
        b    = stats(h2_t)
        al   = stats([t for t in h2_t if t["h2_aligned"]])
        op   = stats([t for t in h2_t if not t["h2_aligned"]])
        print(f"\n  {label} {yr_label}  (N={len(h2_t)}):")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 78}")
        row("Baseline", b,  b["pf"])
        row("slope aligned",  al, b["pf"])
        row("slope opposed",  op, b["pf"])

    # ── OOS Year-by-Year: H1 ──────────────────────────────────────────────────
    print(f"\n  ── OOS Year-by-Year: H1 Prior VWAP ──")
    for yr in oos_years:
        yr_e = [t for t in oos_e if t["date"][:4] == str(yr) and t["h1_aligned"] is not None]
        if not yr_e:
            continue
        b  = stats(yr_e)
        al = stats([t for t in yr_e if t["h1_aligned"]])
        op = stats([t for t in yr_e if not t["h1_aligned"]])
        print(f"\n  {yr}  (N={len(yr_e)}):")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 78}")
        row("Baseline", b,  b["pf"])
        row("aligned",  al, b["pf"])
        row("opposed",  op, b["pf"])

    # ── OOS Year-by-Year: H5 Combo ────────────────────────────────────────────
    print(f"\n  ── OOS Year-by-Year: H5 Combo ──")
    for yr in oos_years:
        yr_e = [t for t in oos_e if t["date"][:4] == str(yr) and t["h5_aligned"] is not None]
        if not yr_e:
            continue
        b   = stats(yr_e)
        al  = stats([t for t in yr_e if t["h5_aligned"]])
        op  = stats([t for t in yr_e if t["h5_opposed"]])
        mix = stats([t for t in yr_e if t["h5_mixed"]])
        print(f"\n  {yr}  (N={len(yr_e)}):")
        print(f"  {'Label':<{COL_W}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 78}")
        row("Baseline",      b,   b["pf"])
        row("BOTH aligned",  al,  b["pf"])
        row("BOTH opposed",  op,  b["pf"])
        row("mixed",         mix, b["pf"])

    # ── Verdict ───────────────────────────────────────────────────────────────
    oos_h1 = [t for t in oos_e if t["h1_aligned"] is not None]
    oos_h2 = [t for t in oos_e if t["h2_aligned"] is not None]
    oos_h5 = [t for t in oos_e if t["h5_aligned"] is not None]
    ob1    = stats(oos_h1)
    al1    = stats([t for t in oos_h1 if t["h1_aligned"]])
    ob2    = stats(oos_h2)
    al2    = stats([t for t in oos_h2 if t["h2_aligned"]])
    ob5    = stats(oos_h5)
    al5    = stats([t for t in oos_h5 if t["h5_aligned"]])

    dpf1 = al1["pf"] - ob1["pf"]
    dpf2 = al2["pf"] - ob2["pf"]
    dpf5 = al5["pf"] - ob5["pf"]

    print(f"\n  ── Verdict ──")
    print(f"  OOS H1 baseline PF  : {ob1['pf']:.3f}  (N={ob1['n']})")
    print(f"  H1 aligned PF       : {al1['pf']:.3f}  (N={al1['n']})  ΔPF {dpf1:+.3f}")
    print(f"  H2 aligned PF       : {al2['pf']:.3f}  (N={al2['n']})  ΔPF {dpf2:+.3f}")
    print(f"  H5 combo aligned PF : {al5['pf']:.3f}  (N={al5['n']})  ΔPF {dpf5:+.3f}")

    def verdict_line(label, dpf, n):
        if dpf > 0.10 and n >= 30:
            return f"  → {label}: SIGNAL — ΔPF {dpf:+.3f}, N={n} sufficient"
        elif dpf > 0.10:
            return f"  → {label}: WEAK — ΔPF {dpf:+.3f} but N={n} insufficient"
        else:
            return f"  → {label}: NO SIGNAL — ΔPF {dpf:+.3f}"

    print(verdict_line("H1 Prior VWAP", dpf1, al1["n"]))
    print(verdict_line("H2 Slope",      dpf2, al2["n"]))
    print(verdict_line("H5 Combo",      dpf5, al5["n"]))
    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    args   = sys.argv[1:]
    run_nq = "--es" not in args or "--nq" in args
    run_es = "--es" in args or "--nq" not in args

    print(f"\nCruzCapital — VWAP Directional Filter Research")
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
