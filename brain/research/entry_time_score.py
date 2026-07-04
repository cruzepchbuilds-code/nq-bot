"""
brain/research/entry_time_score.py

Tests whether entry time (9:46-9:59 window) should become the 5th confidence
score component, a hard gate, or left out of the existing 4-point system.

Key findings from remaining_research.py that motivated this test:
  - 9:46-9:59 bucket OOS PF 3.174  (N=37, 55% of all OOS trades)
  - Score≥3 AND 9:46-9:59 OOS PF 4.522  (strongest multiplicative combo)
  - 9:30-9:45 (first breakout bar) OOS PF 1.348  (mediocre)

Sections:
  A. Entry time breakdown — raw time bucket performance (IS vs OOS)
  B. 5-point score distribution (0-5) vs 4-point (0-4) on OOS
  C. Threshold comparison: 4-point score≥3 vs 5-point score≥4
  D. Hard gate: drop 9:30-9:45 entries, measure what remains
  E. Hard gate + tiered sizing — net $ improvement vs no gate
  F. Bootstrap significance (20,000 trials) on best combos
  G. Final verdict

Usage: python3 brain/research/entry_time_score.py
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

W = 76


# ── pivot / vwap (identical to confidence_score_test.py) ─────────────────────

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
        def vwap_at(minute, day=day):
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


# ── entry time helpers ────────────────────────────────────────────────────────

TIME_BUCKETS = [
    "09:30-09:45", "09:46-09:59", "10:00",
    "10:01-10:15", "10:16-10:30", "10:31+",
]

def entry_bucket(t):
    et = t.get("entry_time", "")
    if not et:
        return "unknown"
    h, m = map(int, et.split(":"))
    if h == 9 and m <= 45:                return "09:30-09:45"
    if h == 9 and m <= 59:                return "09:46-09:59"
    if h == 10 and m == 0:                return "10:00"
    if h == 10 and 1 <= m <= 15:          return "10:01-10:15"
    if h == 10 and 16 <= m <= 30:         return "10:16-10:30"
    if h >= 11 or (h == 10 and m >= 31):  return "10:31+"
    return "unknown"

def is_hot_window(t):
    """True if entry is 9:46-9:59 (the confirmed sweet-spot)."""
    et = t.get("entry_time", "")
    if not et:
        return False
    h, m = map(int, et.split(":"))
    return h == 9 and 46 <= m <= 59

def is_early_entry(t):
    """True if entry is 9:45 or earlier (first breakout bar, mediocre)."""
    et = t.get("entry_time", "")
    if not et:
        return False
    h, m = map(int, et.split(":"))
    return h == 9 and m <= 45


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


def row(label, s, base_pf, w=34):
    dpf  = s["pf"] - base_pf
    flag = ("  ← BETTER" if dpf > 0.15 else
            "  ← WORSE"  if dpf < -0.15 else "")
    n_str = str(s["n"]) if s["n"] > 0 else "—"
    pf_str = f"{s['pf']:.3f}" if s["n"] > 0 else "  —  "
    print(f"  {label:<{w}}  {n_str:>4}  {s['wr']:.1%}  {pf_str:>5}  "
          f"${s['net']:>+9,.0f}  ${s['avg']:>+5,.0f}  {dpf:>+6.3f}{flag}")


def hdr(w=34):
    print(f"  {'Label':<{w}}  {'N':>4}  {'WR':>5}  {'PF':>5}  "
          f"{'Net $':>10}  {'Avg':>6}  {'ΔPF':>7}")
    print(f"  {'─' * 77}")


def divider(char="="):
    print(char * W)


# ── bootstrap ─────────────────────────────────────────────────────────────────

def bootstrap(trades, keep_fn, n_boot=20000, seed=42):
    rng    = random.Random(seed)
    base   = stats(trades)["pf"]
    actual = stats([t for t in trades if keep_fn(t)])
    if actual["n"] == 0:
        return 0.0, 1.0
    n_keep     = actual["n"]
    actual_dpf = actual["pf"] - base
    beat = 0
    idxs = list(range(len(trades)))
    for _ in range(n_boot):
        kept = [trades[i] for i in rng.sample(idxs, n_keep)]
        if stats(kept)["pf"] - base >= actual_dpf:
            beat += 1
    p = beat / n_boot
    return actual_dpf, p


def sig_label(p):
    if p <= 0.01: return f"SIGNIFICANT ★★★  (p={p:.3f})"
    if p <= 0.05: return f"SIGNIFICANT ★★   (p={p:.3f})"
    if p <= 0.10: return f"MARGINAL ★        (p={p:.3f})"
    return           f"not significant   (p={p:.3f})"


# ── tiered sizing simulation ──────────────────────────────────────────────────

def sim_tiered(trades, skip_fn=None, double_fn=None):
    """Simulate 1c/2c sizing. skip_fn(t)=True → skip. double_fn(t)=True → 2c."""
    total = 0.0
    n_skip = n_single = n_double = 0
    for t in trades:
        if skip_fn and skip_fn(t):
            n_skip += 1
            continue
        if double_fn and double_fn(t):
            total += t["pnl"] * 2
            n_double += 1
        else:
            total += t["pnl"]
            n_single += 1
    return {"net": round(total, 0), "n_skip": n_skip,
            "n_single": n_single, "n_double": n_double,
            "n_active": n_single + n_double}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\nCruzCapital — Entry Time Score Test")
    print(f"Question: Add entry-time as 5th score component (0-5), hard gate, or leave 4-point system?")
    print(f"NQ  |  All years {ALL_YEARS}  |  IS {IS_YEARS}  |  OOS {OOS_YEARS}\n")

    print("  Loading data...", end=" ", flush=True)
    bars = load_csv(DATA)
    print(f"{len(bars):,} bars")

    print("  Computing pivots...", end=" ", flush=True)
    pivots = compute_pivots(bars)
    print(f"{len(pivots)} sessions")

    print("  Computing VWAP...", end=" ", flush=True)
    vwap = compute_vwap(bars)
    print(f"{len(vwap)} sessions")

    eidx = build_entry_index(bars)

    print("  Running backtests...", end=" ", flush=True)
    raw = []
    for yr in ALL_YEARS:
        raw.extend(run_year(bars, yr))
    print(f"{len(raw)} trades 2022-2026")

    # ── enrich: compute 4-point score + entry time flags ─────────────────────
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

        prior_v  = vc["prior"]
        vwap_al  = None
        if prior_v is not None:
            above_pv = orp >= prior_v
            vwap_al  = (direction == "long" and above_pv) or (direction == "short" and not above_pv)

        slope    = vc["slope"]
        slope_al = None
        if slope is not None:
            slope_up = slope > 0
            slope_al = (direction == "long" and slope_up) or (direction == "short" and not slope_up)

        z   = zone(orp, pv)
        hot = z in HOT_ZONES

        score4 = sum([
            1 if piv_al else 0,
            1 if vwap_al is True else 0,
            1 if hot else 0,
            1 if slope_al is True else 0,
        ])

        hot_window = is_hot_window(t)
        early_ent  = is_early_entry(t)
        score5     = score4 + (1 if hot_window else 0)

        trades.append({
            **t,
            "pivot_al":   piv_al,
            "vwap_al":    vwap_al,
            "slope_al":   slope_al,
            "zone":       z,
            "hot":        hot,
            "score4":     score4,
            "score5":     score5,
            "score":      score4,   # keep 'score' alias for compat
            "hot_window": hot_window,
            "early_ent":  early_ent,
            "time_bkt":   entry_bucket(t),
            "month":      d.month,
            "year":       d.year,
            "or_price":   orp,
        })

    print(f"  {len(trades)}/{len(raw)} trades scored\n")

    is_t  = [t for t in trades if t["year"] in IS_YEARS]
    oos_t = [t for t in trades if t["year"] in OOS_YEARS]


    # ══════════════════════════════════════════════════════════════════════════
    # A. ENTRY TIME BREAKDOWN
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  A. Entry Time Bucket Performance")
    print("  09:30-09:45 = first breakout bar at 9:45  |  09:46-09:59 = sweet spot")
    divider()

    for period_label, period_t in [("IS 2022-2024", is_t), ("OOS 2025-2026", oos_t)]:
        base = stats(period_t)
        print(f"\n  {period_label}  (N={base['n']},  baseline PF={base['pf']:.3f}):")
        print(f"  {'Bucket':<14}  {'N':>4}  {'WR%':>5}  {'PF':>5}  "
              f"{'Avg$':>7}  {'Score≥3 N':>9}  {'Score≥3 PF':>10}")
        print(f"  {'─' * 66}")
        for bkt in TIME_BUCKETS:
            bt  = [t for t in period_t if t["time_bkt"] == bkt]
            s   = stats(bt)
            s3  = stats([t for t in bt if t["score4"] >= 3])
            if s["n"] == 0:
                continue
            dpf  = s["pf"] - base["pf"]
            flag = ("  BETTER" if dpf > 0.20 else
                    "  WORSE"  if dpf < -0.20 else "")
            note = "  [N<15]" if s["n"] < 15 else ""
            print(f"  {bkt:<14}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
                  f"${s['avg']:>+6,.0f}  "
                  f"{s3['n']:>9}  {s3['pf']:>10.3f}{note}{flag}")

    # Score x Time matrix (OOS only)
    print(f"\n  Score x Entry-Time (OOS) — does score rescue early entries?")
    print(f"  {'Bucket':<14}  {'Score 0-1 PF':>12}  {'Score 2 PF':>10}  "
          f"{'Score 3-4 PF':>12}  {'Score 4 PF':>10}  {'N total':>7}")
    print(f"  {'─' * 66}")
    for bkt in TIME_BUCKETS:
        bt = [t for t in oos_t if t["time_bkt"] == bkt]
        if not bt:
            continue
        s01 = stats([t for t in bt if t["score4"] <= 1])
        s2  = stats([t for t in bt if t["score4"] == 2])
        s34 = stats([t for t in bt if t["score4"] >= 3])
        s4  = stats([t for t in bt if t["score4"] == 4])
        note = "  [N<15]" if len(bt) < 15 else ""
        print(f"  {bkt:<14}  {s01['pf']:>12.3f}  {s2['pf']:>10.3f}  "
              f"{s34['pf']:>12.3f}  {s4['pf']:>10.3f}  {len(bt):>7}{note}")


    # ══════════════════════════════════════════════════════════════════════════
    # B. 5-POINT SCORE DISTRIBUTION vs 4-POINT
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  B. 5-Point Score (0-5) Distribution vs 4-Point")
    print("  Score5 = score4 + 1 if entry is in 9:46-9:59 window")
    divider()

    for period_label, period_t in [("IS 2022-2024", is_t), ("OOS 2025-2026", oos_t)]:
        base = stats(period_t)
        print(f"\n  {period_label}  (N={base['n']},  baseline PF={base['pf']:.3f}):")
        print(f"  {'Score5':<8}  {'N':>4}  {'%':>5}  {'WR%':>5}  {'PF':>5}  "
              f"{'Avg$':>7}  {'4-pt same tier PF':>18}")
        print(f"  {'─' * 62}")
        for sc in range(6):
            tier5 = [t for t in period_t if t["score5"] == sc]
            if not tier5:
                continue
            # For the same score-4 level comparison: score4 == sc (capped at 4)
            tier4 = [t for t in period_t if t["score4"] == min(sc, 4)]
            s5  = stats(tier5)
            s4c = stats(tier4)
            pct = s5["n"] / base["n"] * 100
            dpf = s5["pf"] - base["pf"]
            bar = "█" * int(max(0, dpf) * 5)
            flag = ("  ← BEST"  if dpf > 0.5  else
                    "  ← GOOD"  if dpf > 0.15 else
                    "  ← BAD"   if dpf < -0.2 else "")
            note = "  [N<15]" if s5["n"] < 15 else ""
            print(f"  Score5={sc}  {bar:<10}  {s5['n']:>4}  {pct:>4.0f}%  "
                  f"{s5['wr']:.1%}  {s5['pf']:.3f}  ${s5['avg']:>+6,.0f}  "
                  f"  (4-pt={s4c['pf']:.3f} N={s4c['n']}){note}{flag}")


    # ══════════════════════════════════════════════════════════════════════════
    # C. THRESHOLD COMPARISON: 4-POINT score≥3  vs  5-POINT score≥4
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  C. Threshold Comparison — OOS 2025-2026")
    print("  Existing: 4-point score≥3  |  Proposed: 5-point score≥4")
    divider()

    base_oos = stats(oos_t)
    print(f"\n  Baseline OOS (all trades):  PF={base_oos['pf']:.3f}  N={base_oos['n']}  "
          f"Net=${base_oos['net']:+,.0f}\n")

    comparisons = [
        ("4-pt: ALL trades (baseline)",    lambda t: True,                  "score4"),
        ("4-pt: score≥1 (current skip)",   lambda t: t["score4"] >= 1,      "score4"),
        ("4-pt: score≥2",                  lambda t: t["score4"] >= 2,      "score4"),
        ("4-pt: score≥3 (current 2c thr)", lambda t: t["score4"] >= 3,      "score4"),
        ("4-pt: score=4 (perfect)",        lambda t: t["score4"] == 4,      "score4"),
        ("5-pt: score≥2",                  lambda t: t["score5"] >= 2,      "score5"),
        ("5-pt: score≥3",                  lambda t: t["score5"] >= 3,      "score5"),
        ("5-pt: score≥4",                  lambda t: t["score5"] >= 4,      "score5"),
        ("5-pt: score=5 (perfect)",        lambda t: t["score5"] == 5,      "score5"),
    ]

    hdr(w=36)
    for label, fn, _ in comparisons:
        subset = [t for t in oos_t if fn(t)]
        row(label, stats(subset), base_oos["pf"], w=36)
    print()

    # Also show IS to check for overfit
    base_is = stats(is_t)
    print(f"  Same comparison on IS 2022-2024 (baseline PF={base_is['pf']:.3f}):")
    hdr(w=36)
    for label, fn, _ in comparisons:
        subset = [t for t in is_t if fn(t)]
        row(label, stats(subset), base_is["pf"], w=36)


    # ══════════════════════════════════════════════════════════════════════════
    # D. HARD GATE: DROP 9:30-9:45 ENTRIES
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  D. Hard Gate — Skip all entries at 9:45 or earlier")
    print("  Gate = drop the 09:30-09:45 bucket (first breakout bar)")
    divider()

    gated_oos = [t for t in oos_t if not t["early_ent"]]
    early_oos = [t for t in oos_t if t["early_ent"]]
    gated_is  = [t for t in is_t  if not t["early_ent"]]
    early_is  = [t for t in is_t  if t["early_ent"]]

    print(f"\n  OOS: total {len(oos_t)} → gated (drop early) {len(gated_oos)}  "
          f"({len(early_oos)} early entries removed, "
          f"{len(early_oos)/len(oos_t)*100:.0f}% of trades)")
    print(f"  IS:  total {len(is_t)} → gated {len(gated_is)}  "
          f"({len(early_is)} early entries removed, "
          f"{len(early_is)/len(is_t)*100:.0f}% of trades)\n")

    print(f"  OOS — Early entries being dropped:")
    s_early_oos = stats(early_oos)
    print(f"    N={s_early_oos['n']}  WR={s_early_oos['wr']:.1%}  "
          f"PF={s_early_oos['pf']:.3f}  Avg=${s_early_oos['avg']:+,.0f}  "
          f"Net=${s_early_oos['net']:+,.0f}")
    print(f"  IS — Early entries being dropped:")
    s_early_is = stats(early_is)
    print(f"    N={s_early_is['n']}  WR={s_early_is['wr']:.1%}  "
          f"PF={s_early_is['pf']:.3f}  Avg=${s_early_is['avg']:+,.0f}  "
          f"Net=${s_early_is['net']:+,.0f}\n")

    print(f"  Full comparison (OOS):")
    hdr(w=42)
    row("No gate (baseline)",          stats(oos_t),    base_oos["pf"], w=42)
    row("Hard gate (drop 9:30-9:45)",  stats(gated_oos), base_oos["pf"], w=42)
    print()
    row("Hard gate + 4-pt score≥3",    stats([t for t in gated_oos if t["score4"] >= 3]),  base_oos["pf"], w=42)
    row("Hard gate + 4-pt score≥2",    stats([t for t in gated_oos if t["score4"] >= 2]),  base_oos["pf"], w=42)
    row("Hard gate + 5-pt score≥4",    stats([t for t in gated_oos if t["score5"] >= 4]),  base_oos["pf"], w=42)
    row("Hard gate + 5-pt score≥3",    stats([t for t in gated_oos if t["score5"] >= 3]),  base_oos["pf"], w=42)
    print()

    # Compare early-entry score distribution to confirm early entries are worse even at high score
    print(f"  Early-entry (9:30-9:45) score breakdown (OOS):")
    print(f"  {'Score4':<8}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg$':>7}")
    print(f"  {'─' * 36}")
    for sc in range(5):
        tier = [t for t in early_oos if t["score4"] == sc]
        s    = stats(tier)
        if s["n"] == 0:
            continue
        note = "  [N<15]" if s["n"] < 15 else ""
        print(f"  {sc:<8}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  ${s['avg']:>+6,.0f}{note}")


    # ══════════════════════════════════════════════════════════════════════════
    # E. TIERED SIZING — HARD GATE IMPACT ON NET $
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  E. Tiered Sizing Simulation (OOS 2025-2026)")
    print("  Skip score<1, double score≥3 — does hard gate improve net $?")
    divider()

    print(f"\n  {'Config':<50}  {'Net $':>10}  {'vs Baseline':>12}  "
          f"{'Skip':>5}  {'1c':>5}  {'2c':>5}")
    print(f"  {'─' * 88}")

    base_skip1_double3 = sim_tiered(
        oos_t,
        skip_fn=lambda t: t["score4"] < 1,
        double_fn=lambda t: t["score4"] >= 3
    )
    baseline_net = base_skip1_double3["net"]

    # Show pure baseline first
    r0 = sim_tiered(oos_t)
    print(f"  {'Baseline (no skip/double, 1c flat)':<50}  ${r0['net']:>+9,.0f}  "
          f"{'—':>12}  {r0['n_skip']:>5}  {r0['n_single']:>5}  {r0['n_double']:>5}")

    sizing_configs = [
        ("Current: skip<1, double≥3 (4-pt)",
         lambda t: t["score4"] < 1,
         lambda t: t["score4"] >= 3),
        ("Current: skip<1, double≥3 (4-pt) — IS reference",
         None, None),  # placeholder, handled separately
        ("Hard gate only (skip early, 1c flat)",
         lambda t: t["early_ent"],
         None),
        ("Hard gate + skip<1, double≥3 (4-pt)",
         lambda t: t["early_ent"] or t["score4"] < 1,
         lambda t: t["score4"] >= 3),
        ("Hard gate + skip<1, double≥4 (4-pt)",
         lambda t: t["early_ent"] or t["score4"] < 1,
         lambda t: t["score4"] >= 4),
        ("Hard gate + skip<2, double≥3 (4-pt)",
         lambda t: t["early_ent"] or t["score4"] < 2,
         lambda t: t["score4"] >= 3),
        ("5-pt: skip<2, double≥4",
         lambda t: t["score5"] < 2,
         lambda t: t["score5"] >= 4),
        ("5-pt: hard gate + skip<2, double≥4",
         lambda t: t["early_ent"] or t["score5"] < 2,
         lambda t: t["score5"] >= 4),
    ]

    for label, skip_fn, dbl_fn in sizing_configs:
        if label.endswith("IS reference"):
            continue
        r = sim_tiered(oos_t, skip_fn=skip_fn, double_fn=dbl_fn)
        delta = r["net"] - r0["net"]
        flag  = "  ← BETTER" if delta > 0 else ("  ← WORSE" if delta < 0 else "")
        print(f"  {label:<50}  ${r['net']:>+9,.0f}  {delta:>+12,.0f}  "
              f"{r['n_skip']:>5}  {r['n_single']:>5}  {r['n_double']:>5}{flag}")


    # ══════════════════════════════════════════════════════════════════════════
    # F. BOOTSTRAP SIGNIFICANCE (20,000 trials)
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  F. Bootstrap Significance — 20,000 trials (OOS 2025-2026)")
    print("  p = fraction of random same-size subsets that beat the filter's ΔPF")
    divider()
    print()

    boot_tests = [
        ("4-pt score≥3 (existing threshold)",   lambda t: t["score4"] >= 3),
        ("4-pt score=4 (perfect 4-pt)",         lambda t: t["score4"] == 4),
        ("5-pt score≥4",                        lambda t: t["score5"] >= 4),
        ("5-pt score=5 (perfect 5-pt)",         lambda t: t["score5"] == 5),
        ("Hard gate only (not early entry)",     lambda t: not t["early_ent"]),
        ("Hot window (9:46-9:59)",               lambda t: t["hot_window"]),
        ("Hot window + 4-pt score≥3",            lambda t: t["hot_window"] and t["score4"] >= 3),
        ("Hard gate + 4-pt score≥3",             lambda t: not t["early_ent"] and t["score4"] >= 3),
    ]

    print(f"  {'Filter':<42}  {'N':>4}  {'PF':>5}  {'ΔPF':>6}  {'Significance'}")
    print(f"  {'─' * 78}")
    for label, fn in boot_tests:
        subset = [t for t in oos_t if fn(t)]
        dpf, p = bootstrap(oos_t, fn, n_boot=20000)
        s = stats(subset)
        print(f"  {label:<42}  {s['n']:>4}  {s['pf']:.3f}  {dpf:>+6.3f}  {sig_label(p)}")
    print()

    # Also bootstrap on IS to check consistency
    print(f"  Same tests on IS 2022-2024 (overfitting check):")
    print(f"  {'Filter':<42}  {'N':>4}  {'PF':>5}  {'ΔPF':>6}  {'Significance'}")
    print(f"  {'─' * 78}")
    for label, fn in boot_tests:
        subset = [t for t in is_t if fn(t)]
        dpf, p = bootstrap(is_t, fn, n_boot=20000)
        s = stats(subset)
        print(f"  {label:<42}  {s['n']:>4}  {s['pf']:.3f}  {dpf:>+6.3f}  {sig_label(p)}")


    # ══════════════════════════════════════════════════════════════════════════
    # G. FINAL VERDICT
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  G. FINAL VERDICT")
    divider()

    oos_base   = stats(oos_t)
    oos_s4_3   = stats([t for t in oos_t if t["score4"] >= 3])
    oos_s5_4   = stats([t for t in oos_t if t["score5"] >= 4])
    oos_gated  = stats([t for t in oos_t if not t["early_ent"]])
    oos_g_s3   = stats([t for t in oos_t if not t["early_ent"] and t["score4"] >= 3])
    oos_hw_s3  = stats([t for t in oos_t if t["hot_window"] and t["score4"] >= 3])

    # Net delta when gating early entries
    r_no_gate = sim_tiered(oos_t, skip_fn=lambda t: t["score4"] < 1, double_fn=lambda t: t["score4"] >= 3)
    r_gated   = sim_tiered(oos_t, skip_fn=lambda t: t["early_ent"] or t["score4"] < 1, double_fn=lambda t: t["score4"] >= 3)
    gate_net_delta = r_gated["net"] - r_no_gate["net"]

    # N and PF for early entries to verify whether dropping them is net-positive
    early_pf    = stats(early_oos)["pf"]
    early_avg   = stats(early_oos)["avg"]
    gated_count = len(early_oos)

    print(f"""
  Summary of key numbers (OOS 2025-2026):
  ─────────────────────────────────────────────────────────────────────────
  Baseline (all trades):              N={oos_base['n']:>3}  PF={oos_base['pf']:.3f}  Avg=${oos_base['avg']:>+5,.0f}
  4-pt score≥3 (current):            N={oos_s4_3['n']:>3}  PF={oos_s4_3['pf']:.3f}
  5-pt score≥4 (proposed):           N={oos_s5_4['n']:>3}  PF={oos_s5_4['pf']:.3f}
  Hard gate (drop 9:30-9:45):        N={oos_gated['n']:>3}  PF={oos_gated['pf']:.3f}  ({gated_count} trades removed)
  Hard gate + 4-pt score≥3:          N={oos_g_s3['n']:>3}  PF={oos_g_s3['pf']:.3f}
  Hot window + 4-pt score≥3:         N={oos_hw_s3['n']:>3}  PF={oos_hw_s3['pf']:.3f}
  Early entries being removed:        N={gated_count:>3}  PF={early_pf:.3f}  Avg=${early_avg:>+5,.0f}
  Net $ delta from hard gate (sizing) = ${gate_net_delta:>+,.0f}
  ─────────────────────────────────────────────────────────────────────────
""")

    print("  DECISION FRAMEWORK:")
    print()
    print("  Option 1 — Add 5th score component (+1 for 9:46-9:59)")
    print("    PRO: Quantifies known edge in score; easier to reason about tiers")
    print("    CON: 5-pt score≥4 keeps fewer trades than 4-pt score≥3;")
    print("         early-entry trades are NOT rescued by high score4 anyway")
    print("         (early + score4=4 still underperforms late + score4=3)")
    print()
    print("  Option 2 — Hard gate: skip any entry at 9:45 or earlier")
    print("    PRO: Directly removes the underperforming first-bar entries;")
    print("         clean binary rule, easy to implement in live config;")
    print("         does NOT reduce the 2x doubling pool for score≥3 late trades")
    print("    CON: Loses some volume; if early entry has score=4 it's still skipped")
    print()
    print("  Option 3 — Leave 4-point system unchanged")
    print("    PRO: No change needed; current skip (score<1) already filters ~half")
    print("    CON: Score=0 early entries still taken; 9:45 score≥1 trades included")
    print()

    # Auto-verdict based on data
    # If hard gate net delta is positive AND early_pf < 1.0, recommend gate
    if early_pf < 1.0 and gate_net_delta >= 0:
        verdict = "HARD GATE (Option 2)"
        reason  = (f"Early entries have PF={early_pf:.3f} (negative expectancy). "
                   f"Hard gate adds ${gate_net_delta:+,.0f} net after sizing. "
                   f"Leave 4-point score system unchanged.")
    elif oos_s5_4["pf"] > oos_s4_3["pf"] and oos_s5_4["n"] >= 15:
        verdict = "5TH SCORE COMPONENT (Option 1)"
        reason  = (f"5-pt score≥4 PF={oos_s5_4['pf']:.3f} beats 4-pt score≥3 "
                   f"PF={oos_s4_3['pf']:.3f} with N={oos_s5_4['n']} trades.")
    else:
        verdict = "LEAVE 4-POINT SYSTEM UNCHANGED (Option 3)"
        reason  = "Neither the 5th component nor the hard gate show meaningful net improvement."

    print(f"  ╔══════════════════════════════════════════════════════════════╗")
    print(f"  ║  VERDICT: {verdict:<51}║")
    print(f"  ╚══════════════════════════════════════════════════════════════╝")
    print(f"\n  Reason: {reason}")
    print()


if __name__ == "__main__":
    main()
