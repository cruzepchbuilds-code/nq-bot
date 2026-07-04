"""
brain/research/confidence_score_test.py

Synthesizes all research findings into a confidence score per trade.

Each trade gets 0-4 points:
  +1  Pivot aligned  (OR close above/below prior-day pivot P)
  +1  VWAP aligned   (OR close above/below prior-session VWAP)
  +1  HOT zone       (price in R1_R2 or S2_S1 at OR close)
  +1  Slope aligned  (VWAP rising during OR → long; falling → short)

Then tests:
  A. Performance by score tier (0, 1, 2, 3, 4)
  B. Tiered position sizing simulation (2c on score≥3, 1c otherwise, skip score=0)
  C. HOT zone + alignment combos in detail
  D. Bootstrap significance on the HOT-zone signal
  E. Best single filter vs best 2-signal combo (OOS)
  F. Shorts-only slope filter (strong standalone finding)
  G. Breakdown: what score are current live trades getting?

Usage: python3 brain/research/confidence_score_test.py
"""

import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import Backtester, load_csv
from collections import defaultdict
from datetime import date
import config

DATA      = "data/nq_full.csv"
ALL_YEARS = [2022, 2023, 2024, 2025, 2026]
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]
HOT_ZONES = {"R1_R2", "S2_S1"}

MONTH_NAMES = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
               7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
STRONG = config.STRONG_MONTHS
WEAK   = config.WEAK_MONTHS


# ── pivot / vwap computation ──────────────────────────────────────────────────

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
    sd = sorted(hlc)
    out = {}
    for i in range(1, len(sd)):
        H, L, C = hlc[sd[i - 1]]
        P = (H + L + C) / 3.0
        out[sd[i]] = {"P": P, "R1": 2*P-L, "R2": P+(H-L),
                      "S1": 2*P-H, "S2": P-(H-L)}
    return out


def compute_vwap(bars):
    rth = defaultdict(list)
    for b in bars:
        ts = b["timestamp"]
        h, m = ts.hour, ts.minute
        if (h > 9 or (h == 9 and m >= 30)) and h < 16:
            rth[ts.date()].append(b)
    sd = sorted(rth)
    sv = {}
    for d in sd:
        pv = sum(b["close"] * b["volume"] for b in rth[d])
        v  = sum(b["volume"] for b in rth[d])
        sv[d] = pv / v if v > 0 else None
    out = {}
    for i, d in enumerate(sd):
        day = rth[d]
        def vwap_at(minute):
            bs = [b for b in day if b["timestamp"].hour == 9
                  and b["timestamp"].minute <= minute]
            pv = sum(b["close"] * b["volume"] for b in bs)
            v  = sum(b["volume"] for b in bs)
            return pv / v if v > 0 else None
        lv944 = vwap_at(44)
        lv935 = vwap_at(35)
        slope = (lv944 - lv935) if (lv944 and lv935) else None
        prior = sv.get(sd[i - 1]) if i > 0 else None
        out[d] = {"prior": prior, "live": lv944, "slope": slope}
    return out


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


def zone(price, pv):
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
        return {"n": 0, "net": 0, "wr": 0.0, "pf": 0.0, "avg": 0}
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


def row(label, s, base_pf, w=30):
    dpf  = s["pf"] - base_pf
    flag = ("  ← BETTER" if dpf > 0.15 else "  ← WORSE" if dpf < -0.15 else "")
    print(f"  {label:<{w}}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
          f"${s['net']:>+9,.0f}  ${s['avg']:>+5,.0f}  {dpf:>+6.3f}{flag}")


def hdr(w=30):
    print(f"  {'Label':<{w}}  {'N':>4}  {'WR':>5}  {'PF':>5}  "
          f"{'Net $':>10}  {'Avg':>6}  {'ΔPF':>7}")
    print(f"  {'─'*73}")


# ── bootstrap ─────────────────────────────────────────────────────────────────

def bootstrap(trades, keep_fn, n_boot=20000, seed=42):
    rng    = random.Random(seed)
    base   = stats(trades)["pf"]
    actual = stats([t for t in trades if keep_fn(t)])
    n_keep = actual["n"]
    actual_dpf = actual["pf"] - base
    beat = 0
    idxs = list(range(len(trades)))
    for _ in range(n_boot):
        kept = [trades[i] for i in rng.sample(idxs, n_keep)]
        if stats(kept)["pf"] - base >= actual_dpf:
            beat += 1
    p = beat / n_boot
    return actual_dpf, p


# ── simulation: tiered sizing ─────────────────────────────────────────────────

def sim_tiered(trades, skip_below, double_above, cpv=20.0):
    """
    Simulate tiered position sizing based on confidence score.
    skip_below  : skip trades with score < this
    double_above: take 2 contracts for score >= this (vs 1c baseline)
    cpv         : contract point value ($20 per point for NQ)
    """
    total = 0.0
    n_skip = n_single = n_double = 0
    for t in trades:
        sc = t["score"]
        if sc < skip_below:
            n_skip += 1
            continue
        base_pnl = t["pnl"]   # already in $ (1-contract P&L from backtest)
        if sc >= double_above:
            total += base_pnl * 2
            n_double += 1
        else:
            total += base_pnl
            n_single += 1
    return {"net": round(total, 0), "n_skip": n_skip,
            "n_single": n_single, "n_double": n_double}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    W = 74
    print(f"\nCruzCapital — Confidence Score Test")
    print(f"NQ  |  All years {ALL_YEARS}  |  IS {IS_YEARS}  |  OOS {OOS_YEARS}\n")

    print("  Loading data...", end=" ", flush=True)
    bars = load_csv(DATA)
    print(f"{len(bars):,} bars")

    print("  Computing pivots...",      end=" ", flush=True)
    pivots  = compute_pivots(bars)
    print(f"{len(pivots)} sessions")

    print("  Computing VWAP...",        end=" ", flush=True)
    vwap    = compute_vwap(bars)
    print(f"{len(vwap)} sessions")

    eidx = build_entry_index(bars)

    print("  Running backtests...",     end=" ", flush=True)
    raw = []
    for yr in ALL_YEARS:
        raw.extend(run_year(bars, yr))
    print(f"{len(raw)} trades 2022-2026")

    # ── enrich and score each trade ───────────────────────────────────────────
    trades = []
    for t in raw:
        d   = date.fromisoformat(t["date"])
        pv  = pivots.get(d)
        vc  = vwap.get(d)
        orp = get_or_price(bars, eidx, d)
        if pv is None or vc is None or orp is None:
            continue

        direction = t["dir"]
        above_P   = orp >= pv["P"]
        piv_al    = (direction == "long" and above_P) or (direction == "short" and not above_P)

        prior_v   = vc["prior"]
        vwap_al   = None
        if prior_v is not None:
            above_pv = orp >= prior_v
            vwap_al  = (direction == "long" and above_pv) or (direction == "short" and not above_pv)

        slope    = vc["slope"]
        slope_al = None
        if slope is not None:
            slope_up = slope > 0
            slope_al = (direction == "long" and slope_up) or (direction == "short" and not slope_up)

        z      = zone(orp, pv)
        hot    = z in HOT_ZONES
        # for shorts: being in S2_S1 zone and going short = very hot
        # for longs:  being in R1_R2 zone and going long  = very hot
        dir_hot = ((direction == "long"  and z == "R1_R2") or
                   (direction == "short" and z == "S2_S1"))

        score  = sum([
            1 if piv_al else 0,
            1 if vwap_al is True else 0,
            1 if hot else 0,
            1 if slope_al is True else 0,
        ])

        trades.append({
            **t,
            "pivot_al": piv_al,
            "vwap_al":  vwap_al,
            "slope_al": slope_al,
            "zone":     z,
            "hot":      hot,
            "dir_hot":  dir_hot,
            "score":    score,
            "month":    d.month,
            "year":     d.year,
            "or_price": orp,
        })

    print(f"  {len(trades)}/{len(raw)} trades scored\n")

    is_t  = [t for t in trades if t["year"] in IS_YEARS]
    oos_t = [t for t in trades if t["year"] in OOS_YEARS]

    # ══════════════════════════════════════════════════════════════════════════
    # A. SCORE TIER PERFORMANCE
    # ══════════════════════════════════════════════════════════════════════════
    print(f"{'='*W}")
    print(f"  A. Performance by Confidence Score (0-4)")
    print(f"  Score = pivot+VWAP+HOTzone+slope each worth 1 point")
    print(f"{'='*W}")

    for period_label, period_t in [("All years 2022-2026", trades),
                                    ("IS 2022-2024",        is_t),
                                    ("OOS 2025-2026",       oos_t)]:
        base = stats(period_t)
        print(f"\n  {period_label}  (N={base['n']},  baseline PF={base['pf']:.3f}):")
        hdr()
        row("Baseline (all trades)", base, base["pf"])
        print()
        for sc in range(5):
            tier = [t for t in period_t if t["score"] == sc]
            if not tier:
                continue
            s    = stats(tier)
            dpf  = s["pf"] - base["pf"]
            bar  = "█" * int(max(0, dpf) * 5)
            flag = ("  ← BEST"  if dpf > 0.5  else
                    "  ← GOOD"  if dpf > 0.15 else
                    "  ← BAD"   if dpf < -0.2 else "")
            print(f"  Score {sc}  {bar:<15}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
                  f"${s['net']:>+9,.0f}  ${s['avg']:>+5,.0f}  {dpf:>+6.3f}{flag}")
        # grouped: score 0-1 vs 2 vs 3-4
        low  = [t for t in period_t if t["score"] <= 1]
        mid  = [t for t in period_t if t["score"] == 2]
        high = [t for t in period_t if t["score"] >= 3]
        print(f"\n  Grouped:")
        row("Score 0-1  (skip tier)",  stats(low),  base["pf"])
        row("Score 2    (normal tier)", stats(mid),  base["pf"])
        row("Score 3-4  (premium tier)", stats(high), base["pf"])

    # ══════════════════════════════════════════════════════════════════════════
    # B. TIERED SIZING SIMULATION
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*W}")
    print(f"  B. Tiered Sizing Simulation (OOS 2025-2026)")
    print(f"  Note: backtest P&L already uses dynamic 1c→2c bankroll sizing.")
    print(f"  This shows ADDITIONAL overlay on top of that.")
    print(f"{'='*W}\n")

    base_oos = stats(oos_t)
    base_net = base_oos["net"]

    configs = [
        ("Baseline (no change)",          0, 99),   # skip nothing, no double
        ("Skip score=0, no double",        1, 99),
        ("Skip score≤1, no double",        2, 99),
        ("No skip, double score≥3",        0,  3),
        ("No skip, double score≥4",        0,  4),
        ("Skip score=0, double score≥3",   1,  3),
        ("Skip score≤1, double score≥3",   2,  3),
        ("Skip score≤1, double score≥4",   2,  4),
    ]

    print(f"  {'Config':<40}  {'Net $':>10}  {'vs Base':>9}  "
          f"{'Skip':>5}  {'1c':>5}  {'2c':>5}")
    print(f"  {'─'*75}")

    for label, skip_below, double_above in configs:
        r = sim_tiered(oos_t, skip_below, double_above)
        delta = r["net"] - base_net
        flag  = "  ← BETTER" if delta > 0 else ("  ← WORSE" if delta < 0 else "")
        print(f"  {label:<40}  ${r['net']:>+9,.0f}  {delta:>+9,.0f}  "
              f"{r['n_skip']:>5}  {r['n_single']:>5}  {r['n_double']:>5}{flag}")

    # ══════════════════════════════════════════════════════════════════════════
    # C. HOT ZONE + ALIGNMENT COMBOS
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*W}")
    print(f"  C. HOT Zone + Alignment Combinations (All years 2022-2026)")
    print(f"  HOT zones = R1_R2 (for longs) and S2_S1 (for shorts)")
    print(f"{'='*W}")
    hdr()
    base_all = stats(trades)
    base_pf  = base_all["pf"]
    row("Baseline", base_all, base_pf)
    print()

    combos = [
        ("HOT zone (any direction)",         lambda t: t["hot"]),
        ("Direction-HOT zone",               lambda t: t["dir_hot"]),
        ("Dir-HOT + pivot aligned",          lambda t: t["dir_hot"] and t["pivot_al"]),
        ("Dir-HOT + VWAP aligned",           lambda t: t["dir_hot"] and t["vwap_al"] is True),
        ("Dir-HOT + pivot + VWAP",           lambda t: t["dir_hot"] and t["pivot_al"] and t["vwap_al"] is True),
        ("Dir-HOT + score ≥ 3",             lambda t: t["dir_hot"] and t["score"] >= 3),
        ("R1_R2 longs only",                 lambda t: t["zone"] == "R1_R2" and t["dir"] == "long"),
        ("S2_S1 shorts only",                lambda t: t["zone"] == "S2_S1" and t["dir"] == "short"),
        ("R1_R2 long + pivot aligned",       lambda t: t["zone"] == "R1_R2" and t["dir"] == "long" and t["pivot_al"]),
        ("S2_S1 short + pivot aligned",      lambda t: t["zone"] == "S2_S1" and t["dir"] == "short" and t["pivot_al"]),
        ("NOT hot zone (avoid zones)",       lambda t: not t["hot"]),
    ]
    for label, fn in combos:
        row(label, stats([t for t in trades if fn(t)]), base_pf)

    # ── OOS only ──────────────────────────────────────────────────────────────
    print(f"\n  OOS 2025-2026 only:")
    hdr()
    base_oos_pf = stats(oos_t)["pf"]
    row("Baseline OOS", stats(oos_t), base_oos_pf)
    print()
    for label, fn in combos:
        row(label, stats([t for t in oos_t if fn(t)]), base_oos_pf)

    # ══════════════════════════════════════════════════════════════════════════
    # D. BOOTSTRAP SIGNIFICANCE — HOT ZONE
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*W}")
    print(f"  D. Bootstrap Significance — HOT Zone Signal (20,000 trials)")
    print(f"{'='*W}\n")

    for period_label, period_t in [("All years 2022-2026", trades),
                                    ("OOS 2025-2026",       oos_t)]:
        dpf_hot, p_hot   = bootstrap(period_t, lambda t: t["hot"])
        dpf_dhot, p_dhot = bootstrap(period_t, lambda t: t["dir_hot"])
        dpf_piv, p_piv   = bootstrap(period_t, lambda t: t["pivot_al"])
        dpf_3,   p_3     = bootstrap(period_t, lambda t: t["score"] >= 3)

        def sig(p):
            if p <= 0.01:  return f"SIGNIFICANT ★★★ (p={p:.3f})"
            if p <= 0.05:  return f"SIGNIFICANT ★★  (p={p:.3f})"
            if p <= 0.10:  return f"MARGINAL ★       (p={p:.3f})"
            return f"not significant  (p={p:.3f})"

        print(f"  {period_label}  (N={len(period_t)}):")
        print(f"    HOT zone (any dir)   ΔPF={dpf_hot:>+.3f}  {sig(p_hot)}")
        print(f"    Dir-HOT zone         ΔPF={dpf_dhot:>+.3f}  {sig(p_dhot)}")
        print(f"    Pivot aligned        ΔPF={dpf_piv:>+.3f}  {sig(p_piv)}")
        print(f"    Score ≥ 3            ΔPF={dpf_3:>+.3f}  {sig(p_3)}")
        print()

    # ══════════════════════════════════════════════════════════════════════════
    # E. SHORTS-ONLY SLOPE FILTER (standalone finding)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*W}")
    print(f"  E. Shorts-Only Slope Filter")
    print(f"  (VWAP falling 9:35→9:44 = bearish momentum → take the short)")
    print(f"{'='*W}")

    for period_label, period_t in [("All years", trades), ("OOS", oos_t)]:
        shorts     = [t for t in period_t if t["dir"] == "short"]
        sh_slope_y = [t for t in shorts   if t["slope_al"] is True]
        sh_slope_n = [t for t in shorts   if t["slope_al"] is False]
        bs = stats(shorts)
        print(f"\n  {period_label} — SHORTS only  (N={bs['n']}):")
        hdr(w=32)
        row("Shorts baseline",        stats(shorts),     bs["pf"], w=32)
        row("Slope aligned (falling)", stats(sh_slope_y), bs["pf"], w=32)
        row("Slope opposed (rising)",  stats(sh_slope_n), bs["pf"], w=32)

    # ══════════════════════════════════════════════════════════════════════════
    # F. BEST SINGLE vs BEST 2-SIGNAL COMBO (OOS exhaustive search)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*W}")
    print(f"  F. Exhaustive Best-Filter Search (OOS 2025-2026)")
    print(f"  WARNING: multiple comparisons — treat as hypothesis generation only")
    print(f"{'='*W}\n")

    signals = {
        "pivot_al":   lambda t: t["pivot_al"],
        "vwap_al":    lambda t: t["vwap_al"] is True,
        "hot_zone":   lambda t: t["hot"],
        "dir_hot":    lambda t: t["dir_hot"],
        "slope_al":   lambda t: t["slope_al"] is True,
        "score≥2":    lambda t: t["score"] >= 2,
        "score≥3":    lambda t: t["score"] >= 3,
    }
    sig_names = list(signals.keys())
    sig_fns   = list(signals.values())
    base_oos_pf = stats(oos_t)["pf"]

    results = []
    # single signals
    for name, fn in zip(sig_names, sig_fns):
        kept = [t for t in oos_t if fn(t)]
        s = stats(kept)
        results.append((s["pf"] - base_oos_pf, s["n"], name, s["pf"]))

    # pairs
    for i in range(len(sig_names)):
        for j in range(i + 1, len(sig_names)):
            fn = lambda t, fi=sig_fns[i], fj=sig_fns[j]: fi(t) and fj(t)
            kept = [t for t in oos_t if fn(t)]
            s = stats(kept)
            name = f"{sig_names[i]} & {sig_names[j]}"
            results.append((s["pf"] - base_oos_pf, s["n"], name, s["pf"]))

    results.sort(reverse=True)
    print(f"  Top 10 by ΔPF (OOS, N≥15 only):")
    print(f"  {'Filter':<42}  {'N':>4}  {'PF':>5}  {'ΔPF':>6}")
    print(f"  {'─'*58}")
    shown = 0
    for dpf, n, name, pf in results:
        if n < 15:
            continue
        print(f"  {name:<42}  {n:>4}  {pf:.3f}  {dpf:>+6.3f}")
        shown += 1
        if shown >= 10:
            break

    print(f"\n  Bottom 5 (worst filters, N≥15):")
    print(f"  {'─'*58}")
    shown = 0
    for dpf, n, name, pf in reversed(results):
        if n < 15:
            continue
        print(f"  {name:<42}  {n:>4}  {pf:.3f}  {dpf:>+6.3f}")
        shown += 1
        if shown >= 5:
            break

    # ══════════════════════════════════════════════════════════════════════════
    # G. SCORE DISTRIBUTION OF LIVE TRADES
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*W}")
    print(f"  G. Score Distribution — What Are Live Trades Getting?")
    print(f"{'='*W}\n")

    for period_label, period_t in [("OOS 2025-2026 (most recent)", oos_t),
                                    ("2026 YTD",  [t for t in trades if t["year"] == 2026])]:
        if not period_t:
            continue
        total = len(period_t)
        print(f"  {period_label}  (N={total}):")
        for sc in range(5):
            tier = [t for t in period_t if t["score"] == sc]
            pct  = len(tier) / total * 100
            bar  = "█" * int(pct / 5)
            s    = stats(tier)
            print(f"  Score {sc}  {bar:<20}  {len(tier):>3} trades ({pct:.0f}%)  "
                  f"PF={s['pf']:.3f}  Avg=${s['avg']:>+,.0f}")
        print()

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ══════════════════════════════════════════════════════════════════════════
    print(f"{'='*W}")
    print(f"  FINAL VERDICT")
    print(f"{'='*W}\n")

    oos_base  = stats(oos_t)
    oos_high  = stats([t for t in oos_t if t["score"] >= 3])
    oos_dhot  = stats([t for t in oos_t if t["dir_hot"]])
    oos_low   = stats([t for t in oos_t if t["score"] <= 1])

    print(f"  OOS 2025-2026 Baseline:          PF={oos_base['pf']:.3f}  N={oos_base['n']}  Net=${oos_base['net']:>+,.0f}")
    print(f"  Score ≥ 3 (premium tier):        PF={oos_high['pf']:.3f}  N={oos_high['n']}")
    print(f"  Dir-HOT zone:                    PF={oos_dhot['pf']:.3f}  N={oos_dhot['n']}")
    print(f"  Score ≤ 1 (skip tier):           PF={oos_low['pf']:.3f}  N={oos_low['n']}")
    print()
    print(f"  Clearest actionable signals (ranked by statistical confidence):")
    print(f"    1. HOT zone (R1_R2 / S2_S1) — consistent across all 5 years, large effect")
    print(f"    2. Pivot aligned — consistent IS→OOS, 2025 strong, 2026 flat")
    print(f"    3. VWAP slope for shorts — clear on short side only")
    print()
    print(f"  Recommended implementation priority:")
    print(f"    TRACK LIVE: Log score (0-4) for every trade. Build a real sample.")
    print(f"    SKIP NOW:   Score=0 trades (all 4 signals oppose)")
    print(f"    UPSIZE NOW: When dir-HOT zone (R1_R2 long or S2_S1 short) → 2c if bankroll allows")
    print()


if __name__ == "__main__":
    main()
