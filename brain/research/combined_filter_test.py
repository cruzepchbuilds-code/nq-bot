"""
brain/research/combined_filter_test.py

Comprehensive combined-filter test for NQ ORB.
Post-hoc analysis: runs backtest normally, then shows what happens when we
apply pivot/VWAP alignment filters to the resulting trade log.

Tests:
  1. All years 2022-2026 (fresh bankroll per year)
  2. IS vs OOS split
  3. Six filter configs: baseline, pivot, VWAP, strict, loose, opposed-only
  4. Year-by-year impact
  5. Month-by-month breakdown
  6. Strong vs neutral month interaction
  7. Direction (long vs short) breakdown
  8. Win/loss decomposition (does filter improve avg win, avg loss, or WR?)
  9. Bootstrap significance test (is pivot ΔPF real or luck?)
  10. Drawdown / streak analysis
  11. Filter correlation (when do pivot and VWAP agree vs disagree?)

Usage: python3 brain/research/combined_filter_test.py
"""

import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import Backtester, load_csv
from collections import defaultdict
from datetime import date

DATA = "data/nq_full.csv"

ALL_YEARS = [2022, 2023, 2024, 2025, 2026]
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

MONTH_NAMES = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
               7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}

import config

STRONG_MONTHS  = config.STRONG_MONTHS   # months with contract bonus
WEAK_MONTHS    = config.WEAK_MONTHS     # months skipped entirely


# ── pivot computation ─────────────────────────────────────────────────────────

def compute_pivots(bars):
    rth = defaultdict(list)
    for b in bars:
        ts = b["timestamp"]
        h, m = ts.hour, ts.minute
        if (h > 9 or (h == 9 and m >= 30)) and h < 16:
            rth[ts.date()].append(b)
    hlc = {}
    for d, day in rth.items():
        hlc[d] = (max(b["high"] for b in day),
                  min(b["low"]  for b in day),
                  day[-1]["close"])
    sorted_d = sorted(hlc)
    out = {}
    for i in range(1, len(sorted_d)):
        H, L, C = hlc[sorted_d[i - 1]]
        P = (H + L + C) / 3.0
        out[sorted_d[i]] = {"P": P, "R1": 2*P-L, "R2": P+(H-L),
                             "S1": 2*P-H, "S2": P-(H-L)}
    return out


def compute_vwap_context(bars):
    rth = defaultdict(list)
    for b in bars:
        ts = b["timestamp"]
        h, m = ts.hour, ts.minute
        if (h > 9 or (h == 9 and m >= 30)) and h < 16:
            rth[ts.date()].append(b)
    sorted_d = sorted(rth)
    session_vwap = {}
    for d in sorted_d:
        pv = sum(b["close"] * b["volume"] for b in rth[d])
        v  = sum(b["volume"] for b in rth[d])
        session_vwap[d] = pv / v if v > 0 else None
    ctx = {}
    for i, d in enumerate(sorted_d):
        day = rth[d]
        bars_944 = [b for b in day if b["timestamp"].hour == 9 and b["timestamp"].minute <= 44]
        bars_935 = [b for b in day if b["timestamp"].hour == 9 and b["timestamp"].minute <= 35]
        pv944 = sum(b["close"] * b["volume"] for b in bars_944)
        v944  = sum(b["volume"] for b in bars_944)
        pv935 = sum(b["close"] * b["volume"] for b in bars_935)
        v935  = sum(b["volume"] for b in bars_935)
        lv    = pv944 / v944 if v944 > 0 else None
        lv935 = pv935 / v935 if v935 > 0 else None
        slope = (lv - lv935) if (lv and lv935) else None
        prior = session_vwap.get(sorted_d[i - 1]) if i > 0 else None
        ctx[d] = {"prior_vwap": prior, "live_vwap": lv, "slope": slope}
    return ctx


def build_entry_index(bars):
    idx = {}
    for i, b in enumerate(bars):
        ts = b["timestamp"]
        key = (ts.date(), ts.hour, ts.minute)
        if key not in idx:
            idx[key] = i
    return idx


def get_or_price(bars, eidx, d):
    for m in [44, 45, 43, 46]:
        i = eidx.get((d, 9, m))
        if i is not None:
            return bars[i]["close"]
    return None


def pivot_zone(price, pv):
    if   price >= pv["R2"]: return "above_R2"
    elif price >= pv["R1"]: return "R1_R2"
    elif price >= pv["P"]:  return "P_R1"
    elif price >= pv["S1"]: return "S1_P"
    elif price >= pv["S2"]: return "S2_S1"
    else:                   return "below_S2"


# ── backtest runner ───────────────────────────────────────────────────────────

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


# ── stats ─────────────────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0,
                "avg": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "max_loss_streak": 0}
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    net    = sum(t["pnl"] for t in trades)
    # max consecutive losses
    streak = max_streak = 0
    for t in trades:
        if t["pnl"] <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {
        "n":              len(trades),
        "net":            round(net, 0),
        "wr":             len(wins) / len(trades),
        "pf":             round(gw / gl, 3) if gl else 99.0,
        "avg":            round(net / len(trades), 0),
        "avg_win":        round(gw / len(wins), 0)   if wins   else 0,
        "avg_loss":       round(-gl / len(losses), 0) if losses else 0,
        "max_loss_streak": max_streak,
    }


def row(label, s, base_pf, w=32):
    dpf  = s["pf"] - base_pf
    flag = ("  ← BETTER" if dpf >  0.10 else
            "  ← WORSE"  if dpf < -0.10 else "")
    print(f"  {label:<{w}}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
          f"${s['net']:>+9,.0f}  ${s['avg']:>+5,.0f}  {dpf:+.3f}{flag}")


def hdr(w=32):
    print(f"  {'Filter':<{w}}  {'N':>4}  {'WR%':>5}  {'PF':>5}  "
          f"{'Net $':>10}  {'Avg':>6}  {'ΔPF':>6}")
    print(f"  {'─' * 74}")


# ── bootstrap significance ────────────────────────────────────────────────────

def bootstrap_pf(trades, n_filter, n_boot=10000, seed=42):
    """
    Is the ΔPF from filtering n_filter trades out of len(trades) statistically
    significant? Randomly filter n_filter trades 10k times; return what % of
    random filters beat the actual pivot ΔPF.
    """
    rng = random.Random(seed)
    base   = stats(trades)
    # actual pivot filter result
    pivot_aligned = [t for t in trades if t.get("pivot_aligned")]
    actual_pf  = stats(pivot_aligned)["pf"]
    actual_dpf = actual_pf - base["pf"]

    beat_count = 0
    n = len(trades)
    for _ in range(n_boot):
        keep_idx = set(rng.sample(range(n), len(pivot_aligned)))
        kept = [trades[i] for i in keep_idx]
        random_pf  = stats(kept)["pf"]
        random_dpf = random_pf - base["pf"]
        if random_dpf >= actual_dpf:
            beat_count += 1

    pct_beat = beat_count / n_boot * 100
    return actual_dpf, pct_beat   # pct_beat = % of random filters that matched or beat real filter


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\nCruzCapital — Combined Pivot Filter Test")
    print(f"NQ | All years: {ALL_YEARS} | IS: {IS_YEARS} | OOS: {OOS_YEARS}\n")

    print(f"  Loading data...", end=" ", flush=True)
    bars = load_csv(DATA)
    print(f"{len(bars):,} bars  ({bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()})")

    print(f"  Computing pivots...", end=" ", flush=True)
    pivots = compute_pivots(bars)
    print(f"{len(pivots)} sessions")

    print(f"  Computing VWAP context...", end=" ", flush=True)
    vwap_ctx = compute_vwap_context(bars)
    print(f"{len(vwap_ctx)} sessions")

    eidx = build_entry_index(bars)

    # ── run backtests for all years ───────────────────────────────────────────
    print(f"  Running backtests...", end=" ", flush=True)
    all_trades_raw = []
    for yr in ALL_YEARS:
        all_trades_raw.extend(run_year(bars, yr))
    print(f"{len(all_trades_raw)} total trades 2022-2026")

    # ── enrich each trade with pivot + VWAP context ───────────────────────────
    enriched = []
    for t in all_trades_raw:
        d   = date.fromisoformat(t["date"])
        pv  = pivots.get(d)
        ctx = vwap_ctx.get(d)
        orp = get_or_price(bars, eidx, d)
        if pv is None or ctx is None or orp is None:
            continue

        direction = t["dir"]
        above_P   = orp >= pv["P"]
        piv_al    = (direction == "long" and above_P) or (direction == "short" and not above_P)

        prior_v   = ctx["prior_vwap"]
        if prior_v is not None:
            above_pv  = orp >= prior_v
            vwap_al   = (direction == "long" and above_pv) or (direction == "short" and not above_pv)
        else:
            vwap_al   = None

        slope     = ctx["slope"]
        if slope is not None:
            slope_up  = slope > 0
            slope_al  = (direction == "long" and slope_up) or (direction == "short" and not slope_up)
        else:
            slope_al  = None

        zone = pivot_zone(orp, pv)
        mon  = d.month

        enriched.append({
            **t,
            "pivot_aligned": piv_al,
            "vwap_aligned":  vwap_al,
            "slope_aligned": slope_al,
            "zone":          zone,
            "or_price":      orp,
            "pivot_P":       pv["P"],
            "month":         mon,
            "year":          d.year,
            "above_P":       above_P,
        })

    print(f"  {len(enriched)}/{len(all_trades_raw)} trades with full context\n")

    # ── define filter configs ─────────────────────────────────────────────────
    def apply_filter(trades, fn):
        return [t for t in trades if fn(t)]

    FILTERS = [
        ("Baseline (no filter)",       lambda t: True),
        ("Pivot aligned only",          lambda t: t["pivot_aligned"]),
        ("Prior VWAP aligned only",     lambda t: t["vwap_aligned"] is True),
        ("BOTH pivot+VWAP aligned",     lambda t: t["pivot_aligned"] and t["vwap_aligned"] is True),
        ("EITHER pivot OR VWAP",        lambda t: t["pivot_aligned"] or t["vwap_aligned"] is True),
        ("Pivot opposed (what we skip)",lambda t: not t["pivot_aligned"]),
    ]

    is_e  = [t for t in enriched if t["year"] in IS_YEARS]
    oos_e = [t for t in enriched if t["year"] in OOS_YEARS]

    # ══════════════════════════════════════════════════════════════════════════
    # 1. ALL YEARS OVERVIEW
    # ══════════════════════════════════════════════════════════════════════════
    W = 72
    print(f"{'=' * W}")
    print(f"  1. ALL YEARS 2022-2026 — Filter Comparison")
    print(f"{'=' * W}")
    hdr()
    base_pf = stats(enriched)["pf"]
    for label, fn in FILTERS:
        row(label, stats(apply_filter(enriched, fn)), base_pf)

    # ══════════════════════════════════════════════════════════════════════════
    # 2. IS vs OOS SPLIT
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  2. IS vs OOS Split")
    print(f"{'=' * W}")

    for period_label, period_trades in [("IS 2022-2024", is_e), ("OOS 2025-2026", oos_e)]:
        print(f"\n  {period_label}  (N={len(period_trades)}):")
        hdr()
        base_pf = stats(period_trades)["pf"]
        for label, fn in FILTERS:
            row(label, stats(apply_filter(period_trades, fn)), base_pf)

    # ══════════════════════════════════════════════════════════════════════════
    # 3. YEAR-BY-YEAR: BASELINE vs PIVOT ALIGNED
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  3. Year-by-Year: Baseline vs Pivot Aligned")
    print(f"{'=' * W}")
    print(f"  {'Year':<6}  {'N-base':>6}  {'PF-base':>7}  {'N-pivot':>7}  {'PF-pivot':>8}  {'ΔPF':>6}  {'Net-base':>10}  {'Net-pivot':>10}")
    print(f"  {'─' * 68}")
    for yr in ALL_YEARS:
        yr_trades = [t for t in enriched if t["year"] == yr]
        yr_pivot  = [t for t in yr_trades if t["pivot_aligned"]]
        if not yr_trades:
            continue
        sb = stats(yr_trades)
        sp = stats(yr_pivot)
        dpf = sp["pf"] - sb["pf"]
        flag = "  ← BETTER" if dpf > 0.10 else ("  ← WORSE" if dpf < -0.10 else "")
        print(f"  {yr:<6}  {sb['n']:>6}  {sb['pf']:>7.3f}  {sp['n']:>7}  {sp['pf']:>8.3f}  "
              f"{dpf:>+6.3f}  ${sb['net']:>+9,.0f}  ${sp['net']:>+9,.0f}{flag}")

    # ══════════════════════════════════════════════════════════════════════════
    # 4. MONTH-BY-MONTH BREAKDOWN (OOS only)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  4. Month-by-Month — OOS 2025-2026")
    print(f"  (STRONG={sorted(STRONG_MONTHS)}  WEAK={sorted(WEAK_MONTHS)})")
    print(f"{'=' * W}")
    print(f"  {'Mon':<5}  {'Status':<8}  {'N-base':>6}  {'PF-base':>7}  "
          f"{'N-pivot':>7}  {'PF-pivot':>8}  {'ΔPF':>6}  {'Filtered':>8}")
    print(f"  {'─' * 66}")
    for m in range(1, 13):
        mn_trades = [t for t in oos_e if t["month"] == m]
        if not mn_trades:
            continue
        mn_pivot  = [t for t in mn_trades if t["pivot_aligned"]]
        mn_opp    = [t for t in mn_trades if not t["pivot_aligned"]]
        sb = stats(mn_trades)
        sp = stats(mn_pivot)
        dpf = sp["pf"] - sb["pf"]
        status = ("STRONG" if m in STRONG_MONTHS else
                  "WEAK"   if m in WEAK_MONTHS   else "neutral")
        flag = " ←" if dpf > 0.10 else ""
        print(f"  {MONTH_NAMES[m]:<5}  {status:<8}  {sb['n']:>6}  {sb['pf']:>7.3f}  "
              f"{sp['n']:>7}  {sp['pf']:>8.3f}  {dpf:>+6.3f}  "
              f"{len(mn_opp):>6} cut{flag}")

    # ══════════════════════════════════════════════════════════════════════════
    # 5. STRONG vs NEUTRAL MONTH INTERACTION
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  5. Strong vs Neutral Month — Does Filter Add on Top?")
    print(f"{'=' * W}")
    for period_label, period_trades in [("All years 2022-2026", enriched),
                                         ("OOS 2025-2026", oos_e)]:
        strong_t  = [t for t in period_trades if t["month"] in STRONG_MONTHS]
        neutral_t = [t for t in period_trades if t["month"] not in STRONG_MONTHS
                                               and t["month"] not in WEAK_MONTHS]
        print(f"\n  {period_label}:")
        print(f"  {'Segment':<28}  {'N':>4}  {'PF':>5}  {'ΔPF-vs-seg':>11}")
        print(f"  {'─' * 56}")
        for seg_label, seg in [("Strong months baseline", strong_t),
                                 ("Strong + pivot aligned", [t for t in strong_t if t["pivot_aligned"]]),
                                 ("Neutral months baseline", neutral_t),
                                 ("Neutral + pivot aligned", [t for t in neutral_t if t["pivot_aligned"]])]:
            if not seg:
                continue
            s = stats(seg)
            # ΔPF vs the segment baseline (not overall)
            if "aligned" in seg_label:
                seg_name = seg_label.replace(" + pivot aligned", "")
                if "Strong" in seg_name:
                    base_seg_pf = stats(strong_t)["pf"]
                else:
                    base_seg_pf = stats(neutral_t)["pf"]
                dpf = s["pf"] - base_seg_pf
                print(f"    {seg_label:<26}  {s['n']:>4}  {s['pf']:.3f}  {dpf:>+10.3f}")
            else:
                print(f"  {seg_label:<28}  {s['n']:>4}  {s['pf']:.3f}  {'(baseline)':>11}")

    # ══════════════════════════════════════════════════════════════════════════
    # 6. DIRECTION BREAKDOWN
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  6. Long vs Short — Pivot Filter Impact")
    print(f"{'=' * W}")
    for period_label, period_trades in [("All years", enriched), ("OOS", oos_e)]:
        print(f"\n  {period_label}:")
        print(f"  {'Segment':<32}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg $':>7}  {'ΔPF':>6}")
        print(f"  {'─' * 64}")
        longs  = [t for t in period_trades if t["dir"] == "long"]
        shorts = [t for t in period_trades if t["dir"] == "short"]
        for d_label, d_trades in [("Longs baseline", longs),
                                    ("Longs pivot aligned", [t for t in longs if t["pivot_aligned"]]),
                                    ("Longs pivot opposed", [t for t in longs if not t["pivot_aligned"]]),
                                    ("Shorts baseline", shorts),
                                    ("Shorts pivot aligned", [t for t in shorts if t["pivot_aligned"]]),
                                    ("Shorts pivot opposed", [t for t in shorts if not t["pivot_aligned"]])]:
            if not d_trades:
                continue
            s = stats(d_trades)
            if "baseline" in d_label:
                base_pf = s["pf"]
                dpf_str = "(baseline)"
            else:
                dpf = s["pf"] - base_pf
                dpf_str = f"{dpf:>+6.3f}" + ("  ←" if abs(dpf) > 0.10 else "")
            print(f"  {d_label:<32}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
                  f"${s['avg']:>+6,.0f}  {dpf_str}")

    # ══════════════════════════════════════════════════════════════════════════
    # 7. WIN/LOSS DECOMPOSITION
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  7. Win/Loss Decomposition — Pivot Filter (OOS 2025-2026)")
    print(f"{'=' * W}")
    configs_wl = [
        ("Baseline",          oos_e),
        ("Pivot aligned",     [t for t in oos_e if t["pivot_aligned"]]),
        ("Pivot opposed",     [t for t in oos_e if not t["pivot_aligned"]]),
        ("BOTH pivot+VWAP",   [t for t in oos_e if t["pivot_aligned"] and t["vwap_aligned"] is True]),
    ]
    print(f"  {'Filter':<28}  {'N':>4}  {'WR%':>5}  {'PF':>5}  "
          f"{'AvgWin':>7}  {'AvgLoss':>8}  {'MaxStreak':>9}")
    print(f"  {'─' * 74}")
    for lbl, trd in configs_wl:
        if not trd:
            continue
        s = stats(trd)
        print(f"  {lbl:<28}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
              f"${s['avg_win']:>+6,.0f}  ${s['avg_loss']:>+7,.0f}  {s['max_loss_streak']:>9}")

    # ══════════════════════════════════════════════════════════════════════════
    # 8. ZONE ANALYSIS — ALL YEARS (more data than prior script)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  8. Pivot Zone Analysis — All Years 2022-2026")
    print(f"{'=' * W}")
    base_pf = stats(enriched)["pf"]
    print(f"  {'Zone':<12}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Net $':>10}  "
          f"{'ΔPF':>6}  {'Longs PF':>8}  {'Shorts PF':>9}")
    print(f"  {'─' * 70}")
    for z in ["above_R2", "R1_R2", "P_R1", "S1_P", "S2_S1", "below_S2"]:
        zt = [t for t in enriched if t["zone"] == z]
        if not zt:
            continue
        s   = stats(zt)
        sl  = stats([t for t in zt if t["dir"] == "long"])
        ss  = stats([t for t in zt if t["dir"] == "short"])
        dpf = s["pf"] - base_pf
        hot = "  ← HOT"  if dpf > 0.30 else "  ← COLD" if dpf < -0.30 else ""
        print(f"  {z:<12}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
              f"${s['net']:>+9,.0f}  {dpf:>+6.3f}  "
              f"{sl['pf']:>8.3f}  {ss['pf']:>9.3f}{hot}")

    # ══════════════════════════════════════════════════════════════════════════
    # 9. FILTER AGREEMENT MATRIX
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  9. Filter Agreement — Pivot vs VWAP  (all years)")
    print(f"{'=' * W}")
    both_trades = [t for t in enriched if t["vwap_aligned"] is not None]
    pp_va = [t for t in both_trades if t["pivot_aligned"]     and t["vwap_aligned"]]
    pp_vo = [t for t in both_trades if t["pivot_aligned"]     and not t["vwap_aligned"]]
    po_va = [t for t in both_trades if not t["pivot_aligned"] and t["vwap_aligned"]]
    po_vo = [t for t in both_trades if not t["pivot_aligned"] and not t["vwap_aligned"]]
    base_both = stats(both_trades)
    print(f"  {'Quadrant':<36}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg $':>7}  {'ΔPF':>6}")
    print(f"  {'─' * 66}")
    for lbl, subset in [
        ("Baseline (both signals present)", both_trades),
        ("Pivot aligned   + VWAP aligned   (BOTH ✓)", pp_va),
        ("Pivot aligned   + VWAP opposed   (mixed)", pp_vo),
        ("Pivot opposed   + VWAP aligned   (mixed)", po_va),
        ("Pivot opposed   + VWAP opposed   (BOTH ✗)", po_vo),
    ]:
        if not subset:
            continue
        s = stats(subset)
        dpf = s["pf"] - base_both["pf"]
        flag = "  ← BETTER" if dpf > 0.10 else "  ← WORSE" if dpf < -0.10 else ""
        print(f"  {lbl:<36}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
              f"${s['avg']:>+6,.0f}  {dpf:>+6.3f}{flag}")

    # ══════════════════════════════════════════════════════════════════════════
    # 10. BOOTSTRAP SIGNIFICANCE TEST (OOS)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  10. Statistical Significance — Bootstrap Test (OOS, 10,000 trials)")
    print(f"{'=' * W}")
    print(f"  Question: Is the pivot ΔPF real, or could it happen by randomly")
    print(f"  filtering the same number of trades?\n")
    n_pivot_oos = len([t for t in oos_e if t["pivot_aligned"]])
    actual_dpf, pct_beat = bootstrap_pf(oos_e, n_pivot_oos)
    print(f"  OOS trades total       : {len(oos_e)}")
    print(f"  Pivot aligned kept     : {n_pivot_oos}")
    print(f"  Actual pivot ΔPF       : {actual_dpf:+.3f}")
    print(f"  Random trials beating it: {pct_beat:.1f}%")
    if pct_beat <= 5.0:
        sig = f"SIGNIFICANT (p={pct_beat/100:.3f}) — less than 5% of random filters match"
    elif pct_beat <= 10.0:
        sig = f"MARGINAL (p={pct_beat/100:.3f}) — 5-10% of random filters match"
    else:
        sig = f"NOT SIGNIFICANT (p={pct_beat/100:.3f}) — too easy to match by chance"
    print(f"  Verdict: {sig}")

    # Also test all-years bootstrap
    n_pivot_all = len([t for t in enriched if t["pivot_aligned"]])
    actual_dpf_all, pct_beat_all = bootstrap_pf(enriched, n_pivot_all)
    print(f"\n  All years 2022-2026:")
    print(f"  Actual ΔPF: {actual_dpf_all:+.3f}  |  {pct_beat_all:.1f}% of random filters match")
    if pct_beat_all <= 5.0:
        print(f"  Verdict: SIGNIFICANT (p={pct_beat_all/100:.3f})")
    else:
        print(f"  Verdict: NOT SIGNIFICANT (p={pct_beat_all/100:.3f})")

    # ══════════════════════════════════════════════════════════════════════════
    # 11. FINAL SUMMARY TABLE
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * W}")
    print(f"  11. Summary — What Does Implementing This Do?")
    print(f"{'=' * W}")
    base_oos  = stats(oos_e)
    pivot_oos = stats([t for t in oos_e if t["pivot_aligned"]])
    skip_oos  = stats([t for t in oos_e if not t["pivot_aligned"]])

    print(f"\n  OOS 2025-2026  (most important — live account conditions)")
    print(f"  {'':32}  {'Value':>10}")
    print(f"  {'─' * 46}")
    print(f"  {'Baseline trades':32}  {base_oos['n']:>10}")
    print(f"  {'Pivot-aligned trades (keep)':32}  {pivot_oos['n']:>10}  ({pivot_oos['n']/base_oos['n']:.0%} of trades)")
    print(f"  {'Opposed trades (skip)':32}  {skip_oos['n']:>10}  ({skip_oos['n']/base_oos['n']:.0%} of trades)")
    print(f"  {'Baseline PF':32}  {base_oos['pf']:>10.3f}")
    print(f"  {'Pivot-aligned PF':32}  {pivot_oos['pf']:>10.3f}  (+{pivot_oos['pf']-base_oos['pf']:.3f})")
    print(f"  {'Baseline net $ OOS':32}  ${base_oos['net']:>+9,.0f}")
    print(f"  {'Pivot-aligned net $ OOS':32}  ${pivot_oos['net']:>+9,.0f}")
    print(f"  {'Baseline avg/trade':32}  ${base_oos['avg']:>+9,.0f}")
    print(f"  {'Pivot-aligned avg/trade':32}  ${pivot_oos['avg']:>+9,.0f}  (+${pivot_oos['avg']-base_oos['avg']:,.0f}/trade)")
    print(f"  {'Skipped trades avg/trade':32}  ${skip_oos['avg']:>+9,.0f}  (what we give up)")
    print(f"  {'Baseline WR':32}  {base_oos['wr']:>10.1%}")
    print(f"  {'Pivot-aligned WR':32}  {pivot_oos['wr']:>10.1%}")

    pnl_kept   = pivot_oos["net"]
    pnl_skipped = skip_oos["net"]
    print(f"\n  Net P&L kept from aligned trades  : ${pnl_kept:>+,.0f}")
    print(f"  Net P&L of skipped opposed trades : ${pnl_skipped:>+,.0f}  ← we give this up")
    print(f"  (The filter sacrifices low-quality profits for better edge)")

    print(f"\n{'=' * W}")
    print(f"  Done.")
    print(f"{'=' * W}\n")


if __name__ == "__main__":
    main()
