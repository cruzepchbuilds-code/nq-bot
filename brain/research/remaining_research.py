"""
brain/research/remaining_research.py

Remaining research angles for NQ ORB confidence scoring system.
Builds on top of the enrichment logic from combined_filter_test.py
(pivot computation, VWAP context, score assignment).

Sections:
  1. Entry time breakdown (9:45 / 10:00 / 10:15 / 10:30 / 11:00+)
  2. Day-of-week edge (Mon–Fri)
  3. OR size interaction (tight vs wide, HOT zone signal strength)
  4. RR target per score tier (outcome distribution)
  5. Consecutive trade / streak analysis
  6. 2022 deep dive (month, direction, score breakdown)
  7. Month x Score interaction (strong months)

IS years: [2022, 2023, 2024]
OOS years: [2025, 2026]

Usage: python3 brain/research/remaining_research.py

NOTE (2026-07-03): regime-ATR warmup transplant fixed to a bounded 14-day
deque (was list(...) -> unbounded -> expanding mean; ref param_stability.py,
found by the parameter-stability audit). Results produced by this script
BEFORE this fix used the buggy expanding-mean regime gate - re-run before
citing absolute numbers (live-params morning-ORB effect: OOS N 52->64,
PF 2.84->2.35).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import Backtester, load_csv
from collections import defaultdict, deque
from datetime import date

import config

DATA = "data/nq_full.csv"

ALL_YEARS  = [2022, 2023, 2024, 2025, 2026]
IS_YEARS   = [2022, 2023, 2024]
OOS_YEARS  = [2025, 2026]

STRONG_MONTHS = config.STRONG_MONTHS
WEAK_MONTHS   = config.WEAK_MONTHS

MONTH_NAMES = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
               7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
DOW_NAMES   = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri"}


# ─────────────────────────────────────────────────────────────────────────────
# Enrichment helpers  (copied verbatim from combined_filter_test.py)
# ─────────────────────────────────────────────────────────────────────────────

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


def compute_or_ranges(bars):
    """Compute true opening range high-low (9:30–9:44) for each trading day."""
    or_bars = defaultdict(list)
    for b in bars:
        ts = b["timestamp"]
        h, m = ts.hour, ts.minute
        # OR window: 9:30 through 9:44 (inclusive, 15-minute OR)
        if h == 9 and 30 <= m <= 44:
            or_bars[ts.date()].append(b)
    result = {}
    for d, day_bars in or_bars.items():
        if day_bars:
            result[d] = max(b["high"] for b in day_bars) - min(b["low"] for b in day_bars)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Confidence score  (0–4)
# ─────────────────────────────────────────────────────────────────────────────

def confidence_score(t):
    score = 0
    if t["pivot_aligned"]:                                              score += 1
    if t["vwap_aligned"] is True:                                       score += 1
    if t["zone"] in ("R1_R2", "S2_S1"):                                 score += 1
    if t["slope_aligned"] is True:                                      score += 1
    return score


# ─────────────────────────────────────────────────────────────────────────────
# Backtest runner
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Stats helper
# ─────────────────────────────────────────────────────────────────────────────

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
        "avg_win":        round(gw / len(wins), 0)    if wins   else 0,
        "avg_loss":       round(-gl / len(losses), 0) if losses else 0,
        "max_loss_streak": max_streak,
    }


def ins(n):
    """Flag insufficient sample."""
    return "  [N<15]" if n < 15 else ""


W = 76

def divider(char="="):
    print(char * W)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\nCruzCapital — Remaining Research  (IS={IS_YEARS}  OOS={OOS_YEARS})")
    print(f"Confidence score: +1 pivot_aligned  +1 vwap_aligned  +1 HOT_zone  +1 slope_aligned\n")

    # ── Load + enrich ─────────────────────────────────────────────────────────
    print("  Loading data...", end=" ", flush=True)
    bars = load_csv(DATA)
    print(f"{len(bars):,} bars  ({bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()})")

    print("  Computing pivots...", end=" ", flush=True)
    pivots = compute_pivots(bars)
    print(f"{len(pivots)} sessions")

    print("  Computing VWAP context...", end=" ", flush=True)
    vwap_ctx = compute_vwap_context(bars)
    print(f"{len(vwap_ctx)} sessions")

    print("  Computing OR ranges...", end=" ", flush=True)
    or_ranges = compute_or_ranges(bars)
    print(f"{len(or_ranges)} sessions")

    eidx = build_entry_index(bars)

    print("  Running backtests...", end=" ", flush=True)
    all_trades_raw = []
    for yr in ALL_YEARS:
        all_trades_raw.extend(run_year(bars, yr))
    print(f"{len(all_trades_raw)} raw trades")

    # ── Enrich ───────────────────────────────────────────────────────────────
    enriched = []
    for t in all_trades_raw:
        # Only ORB trades (not london/asia)
        if t.get("mode") not in (None, "orb", "second_breakout", "gap_fill",
                                  "pm_vwap", "orb_long", "orb_short"):
            if t.get("mode") in ("london", "asia_gap"):
                continue

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

        zone  = pivot_zone(orp, pv)
        mon   = d.month
        dow   = d.weekday()   # 0=Mon .. 4=Fri

        or_rng = or_ranges.get(d)  # actual OR high-low range in NQ points

        rec = {
            **t,
            "pivot_aligned": piv_al,
            "vwap_aligned":  vwap_al,
            "slope_aligned": slope_al,
            "zone":          zone,
            "or_price":      orp,
            "pivot_P":       pv["P"],
            "month":         mon,
            "year":          d.year,
            "dow":           dow,
            "or_range":      or_rng,
        }
        rec["score"] = confidence_score(rec)
        enriched.append(rec)

    print(f"  {len(enriched)}/{len(all_trades_raw)} trades with full context\n")

    is_e  = [t for t in enriched if t["year"] in IS_YEARS]
    oos_e = [t for t in enriched if t["year"] in OOS_YEARS]


    # ══════════════════════════════════════════════════════════════════════════
    # 1. ENTRY TIME BREAKDOWN
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  1. ENTRY TIME BREAKDOWN")
    divider()
    print("  Entry time is the HH:MM when the ORB breakout entry was filled.\n")

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

    TIME_BUCKETS = ["09:30-09:45","09:46-09:59","10:00",
                    "10:01-10:15","10:16-10:30","10:31+"]

    for period_label, period_trades in [("IS 2022-2024", is_e), ("OOS 2025-2026", oos_e)]:
        print(f"\n  {period_label}  (N={len(period_trades)}):")
        print(f"  {'Bucket':<14}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg$':>7}  "
              f"{'Score≥3 N':>9}  {'Score≥3 PF':>10}  {'Note'}")
        print(f"  {'─' * 72}")
        base_pf = stats(period_trades)["pf"]
        for bkt in TIME_BUCKETS:
            bt = [t for t in period_trades if entry_bucket(t) == bkt]
            s  = stats(bt)
            s3 = stats([t for t in bt if t["score"] >= 3])
            note = ins(s["n"])
            if s["n"] == 0:
                continue
            dpf = s["pf"] - base_pf
            flag = "  BETTER" if dpf > 0.20 else ("  WORSE" if dpf < -0.20 else "")
            print(f"  {bkt:<14}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
                  f"${s['avg']:>+6,.0f}  "
                  f"{s3['n']:>9}  {s3['pf']:>10.3f}{note}{flag}")

    # Score×Time: does score matter more for late entries?
    print(f"\n  Score x Entry Time (OOS — does score save late entries?):")
    print(f"  {'Bucket':<14}  {'Score 0-1 PF':>12}  {'Score 2 PF':>10}  {'Score 3-4 PF':>12}  {'N total':>7}")
    print(f"  {'─' * 60}")
    for bkt in TIME_BUCKETS:
        bt = [t for t in oos_e if entry_bucket(t) == bkt]
        if not bt:
            continue
        s01 = stats([t for t in bt if t["score"] <= 1])
        s2  = stats([t for t in bt if t["score"] == 2])
        s34 = stats([t for t in bt if t["score"] >= 3])
        print(f"  {bkt:<14}  {s01['pf']:>12.3f}  {s2['pf']:>10.3f}  {s34['pf']:>12.3f}  {len(bt):>7}")


    # ══════════════════════════════════════════════════════════════════════════
    # 2. DAY OF WEEK
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  2. DAY-OF-WEEK EDGE")
    divider()

    for period_label, period_trades in [("IS 2022-2024", is_e), ("OOS 2025-2026", oos_e)]:
        print(f"\n  {period_label}:")
        print(f"  {'Day':<5}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg$':>7}  "
              f"{'Longs PF':>8}  {'Shorts PF':>9}  {'Score≥3 PF':>10}  {'Note'}")
        print(f"  {'─' * 70}")
        base_pf = stats(period_trades)["pf"]
        for dow in range(5):
            dt = [t for t in period_trades if t["dow"] == dow]
            s  = stats(dt)
            sl = stats([t for t in dt if t["dir"] == "long"])
            ss2 = stats([t for t in dt if t["dir"] == "short"])
            s3  = stats([t for t in dt if t["score"] >= 3])
            if s["n"] == 0:
                continue
            dpf  = s["pf"] - base_pf
            flag = "  BETTER" if dpf > 0.25 else ("  WORSE" if dpf < -0.25 else "")
            note = ins(s["n"])
            print(f"  {DOW_NAMES[dow]:<5}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
                  f"${s['avg']:>+6,.0f}  "
                  f"{sl['pf']:>8.3f}  {ss2['pf']:>9.3f}  {s3['pf']:>10.3f}{note}{flag}")


    # ══════════════════════════════════════════════════════════════════════════
    # 3. OR SIZE INTERACTION
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  3. OR SIZE INTERACTION  (tight vs wide — actual OR high-low range)")
    divider()
    print("  OR range = actual high-low of 9:30-9:44 bars for each day.")
    print("  Split: tight = bottom third, mid = middle, wide = top third.\n")

    import statistics

    sized = [t for t in enriched if t.get("or_range") is not None]
    all_sizes = [t["or_range"] for t in sized]
    if all_sizes:
        med_size  = statistics.median(all_sizes)
        sorted_sizes = sorted(all_sizes)
        p33 = sorted_sizes[int(len(sorted_sizes)*0.33)]
        p67 = sorted_sizes[int(len(sorted_sizes)*0.67)]
        print(f"  OR range: median={med_size:.1f}pts  p33={p33:.1f}  p67={p67:.1f}  "
              f"min={min(all_sizes):.1f}  max={max(all_sizes):.1f}\n")

        def size_bucket(t):
            r = t.get("or_range")
            if r is None:    return "unknown"
            if r < p33:      return "tight"
            elif r < p67:    return "mid"
            else:            return "wide"
    else:
        p33, p67 = 20, 40
        def size_bucket(t): return "unknown"

    for period_label, period_trades in [("IS 2022-2024", is_e), ("OOS 2025-2026", oos_e)]:
        print(f"\n  {period_label}:")
        print(f"  {'Bucket':<7}  {'N':>4}  {'PF':>5}  {'HOT-zone N':>10}  "
              f"{'HOT-zone PF':>11}  {'HOT ΔPF':>8}  {'Score≥3 PF':>10}  {'Note'}")
        print(f"  {'─' * 68}")
        for bkt in ["tight", "mid", "wide"]:
            bt = [t for t in period_trades if size_bucket(t) == bkt and t.get("or_range") is not None]
            s  = stats(bt)
            hot = [t for t in bt if t["zone"] in ("R1_R2", "S2_S1")]
            sh  = stats(hot)
            s3  = stats([t for t in bt if t["score"] >= 3])
            if s["n"] == 0:
                continue
            hdpf = sh["pf"] - s["pf"] if s["n"] else 0
            note = ins(sh["n"])
            print(f"  {bkt:<7}  {s['n']:>4}  {s['pf']:.3f}  {sh['n']:>10}  "
                  f"{sh['pf']:>11.3f}  {hdpf:>+8.3f}  {s3['pf']:>10.3f}{note}")


    # ══════════════════════════════════════════════════════════════════════════
    # 4. RR TARGET PER SCORE TIER — OUTCOME DISTRIBUTION
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  4. RR TARGET PER SCORE TIER — OUTCOME DISTRIBUTION (OOS)")
    divider()
    print("  Current config: ORB_FUNDED_RR_TARGET = 3.0 for all trades.")
    print("  Hypothesis: score≥3 → let run (RR=3.5/4.0); score≤2 → exit faster (RR=2.5).")
    print("  We look at the win/loss split by score to see if this is sensible.\n")

    # 'result' field: "target", "stop", "eod"
    print(f"  OOS 2025-2026  (N={len(oos_e)}):")
    print(f"  {'Score':<7}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg$':>7}  "
          f"{'Target%':>7}  {'Stop%':>6}  {'EOD%':>5}  {'AvgWin$':>8}  {'AvgLoss$':>9}  {'Note'}")
    print(f"  {'─' * 82}")
    for sc in range(5):
        st = [t for t in oos_e if t["score"] == sc]
        if not st:
            continue
        s = stats(st)
        n_tgt = sum(1 for t in st if t.get("result") == "target")
        n_stp = sum(1 for t in st if t.get("result") == "stop")
        n_eod = sum(1 for t in st if t.get("result") not in ("target","stop"))
        note = ins(s["n"])
        print(f"  {sc:<7}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
              f"${s['avg']:>+6,.0f}  "
              f"{n_tgt/s['n']:.0%}     {n_stp/s['n']:.0%}    {n_eod/s['n']:.0%}  "
              f"${s['avg_win']:>+7,.0f}  ${s['avg_loss']:>+8,.0f}{note}")

    print()
    # Same for IS
    print(f"  IS 2022-2024  (N={len(is_e)}):")
    print(f"  {'Score':<7}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg$':>7}  "
          f"{'Target%':>7}  {'Stop%':>6}  {'EOD%':>5}  {'AvgWin$':>8}  {'AvgLoss$':>9}  {'Note'}")
    print(f"  {'─' * 82}")
    for sc in range(5):
        st = [t for t in is_e if t["score"] == sc]
        if not st:
            continue
        s = stats(st)
        n_tgt = sum(1 for t in st if t.get("result") == "target")
        n_stp = sum(1 for t in st if t.get("result") == "stop")
        n_eod = sum(1 for t in st if t.get("result") not in ("target","stop"))
        note = ins(s["n"])
        print(f"  {sc:<7}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
              f"${s['avg']:>+6,.0f}  "
              f"{n_tgt/s['n']:.0%}     {n_stp/s['n']:.0%}    {n_eod/s['n']:.0%}  "
              f"${s['avg_win']:>+7,.0f}  ${s['avg_loss']:>+8,.0f}{note}")

    # Score 3+ longs vs shorts for RR insight
    print(f"\n  Score≥3 direction split (OOS):")
    s3l = stats([t for t in oos_e if t["score"] >= 3 and t["dir"] == "long"])
    s3s = stats([t for t in oos_e if t["score"] >= 3 and t["dir"] == "short"])
    print(f"  {'Seg':<20}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Target%':>7}  {'Stop%':>6}  {'AvgWin$':>8}")
    print(f"  {'─' * 60}")
    for lbl, sx in [("Score≥3 longs", s3l), ("Score≥3 shorts", s3s)]:
        if sx["n"] == 0: continue
        st = [t for t in oos_e if t["score"] >= 3 and t["dir"] == ("long" if "long" in lbl else "short")]
        n_tgt = sum(1 for t in st if t.get("result") == "target")
        n_stp = sum(1 for t in st if t.get("result") == "stop")
        print(f"  {lbl:<20}  {sx['n']:>4}  {sx['wr']:.1%}  {sx['pf']:.3f}  "
              f"{n_tgt/sx['n']:.0%}     {n_stp/sx['n']:.0%}    ${sx['avg_win']:>+7,.0f}")


    # ══════════════════════════════════════════════════════════════════════════
    # 5. CONSECUTIVE TRADE PATTERNS / STREAK ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  5. CONSECUTIVE TRADE PATTERNS (streak analysis)")
    divider()
    print("  Question: After 1 loss, does the next trade do worse?")
    print("  After 2+ consecutive losses, does the system underperform?\n")

    def streak_analysis(trades, label):
        if not trades:
            return
        print(f"  {label}  (N={len(trades)}):")
        # Build streak history
        # For each trade i, compute how many consecutive losses preceded it (streak before)
        streaks_before = []
        streak = 0
        for t in trades:
            streaks_before.append(streak)
            if t["pnl"] <= 0:
                streak += 1
            else:
                streak = 0

        # Group next-trade performance by prior streak
        groups = defaultdict(list)
        for i, t in enumerate(trades):
            sb = streaks_before[i]
            if sb == 0:   key = "Fresh (0 prior losses)"
            elif sb == 1: key = "After 1 loss"
            elif sb == 2: key = "After 2 losses"
            else:         key = "After 3+ losses"
            groups[key].append(t)

        order = ["Fresh (0 prior losses)","After 1 loss","After 2 losses","After 3+ losses"]
        print(f"  {'Prior streak':<24}  {'N':>4}  {'WR%':>5}  {'PF':>5}  "
              f"{'Avg$':>7}  {'AvgWin$':>8}  {'Note'}")
        print(f"  {'─' * 62}")
        for key in order:
            grp = groups.get(key, [])
            if not grp:
                continue
            s = stats(grp)
            note = ins(s["n"])
            print(f"  {key:<24}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
                  f"${s['avg']:>+6,.0f}  ${s['avg_win']:>+7,.0f}{note}")

        # Max drawdown streak breakdown
        all_streaks = []
        cur = 0
        for t in trades:
            if t["pnl"] <= 0:
                cur += 1
                all_streaks.append(cur)
            else:
                cur = 0
        if all_streaks:
            mx = max(all_streaks)
            print(f"\n  Max loss streak: {mx}")
            # After the max streak ends, how does next trade do?
            # Find all streak-end points (where streak resets from ≥2 to 0)
            # trades immediately following end of 2+ streak (trade itself can win or lose)
            recovery = []
            for i in range(1, len(trades)):
                sb = streaks_before[i]
                if sb >= 2:
                    recovery.append(trades[i])
            if recovery:
                sr = stats(recovery)
                print(f"  Trades immediately following 2+ streak: N={sr['n']}  "
                      f"WR={sr['wr']:.1%}  PF={sr['pf']:.3f}  Avg=${sr['avg']:+,.0f}{ins(sr['n'])}")
        print()

    streak_analysis(is_e,  "IS  2022-2024")
    streak_analysis(oos_e, "OOS 2025-2026")


    # ══════════════════════════════════════════════════════════════════════════
    # 6. 2022 DEEP DIVE
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  6. 2022 DEEP DIVE  (IS year, PF near breakeven)")
    divider()

    trades_2022 = [t for t in enriched if t["year"] == 2022]
    s2022 = stats(trades_2022)
    print(f"\n  Overall 2022: N={s2022['n']}  WR={s2022['wr']:.1%}  "
          f"PF={s2022['pf']:.3f}  Net=${s2022['net']:+,.0f}  Avg=${s2022['avg']:+,.0f}\n")

    # 6a. Monthly breakdown
    print(f"  6a. Monthly breakdown 2022:")
    print(f"  {'Mon':<5}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg$':>7}  "
          f"{'Longs PF':>8}  {'Shorts PF':>9}  {'Score≥3 PF':>10}  {'Note'}")
    print(f"  {'─' * 70}")
    for m in range(1, 13):
        mt = [t for t in trades_2022 if t["month"] == m]
        if not mt:
            continue
        s  = stats(mt)
        sl = stats([t for t in mt if t["dir"] == "long"])
        ss2 = stats([t for t in mt if t["dir"] == "short"])
        s3  = stats([t for t in mt if t["score"] >= 3])
        note = ins(s["n"])
        print(f"  {MONTH_NAMES[m]:<5}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
              f"${s['avg']:>+6,.0f}  "
              f"{sl['pf']:>8.3f}  {ss2['pf']:>9.3f}  {s3['pf']:>10.3f}{note}")

    # 6b. Direction breakdown 2022
    print(f"\n  6b. Direction breakdown 2022:")
    long22  = [t for t in trades_2022 if t["dir"] == "long"]
    short22 = [t for t in trades_2022 if t["dir"] == "short"]
    for lbl, lt in [("Longs 2022", long22), ("Shorts 2022", short22)]:
        s = stats(lt)
        print(f"  {lbl:<14}: N={s['n']}  WR={s['wr']:.1%}  PF={s['pf']:.3f}  "
              f"Avg=${s['avg']:+,.0f}  Net=${s['net']:+,.0f}")

    # 6c. Score would have helped — score≥3 vs rest in 2022
    print(f"\n  6c. Did confidence score help in 2022?")
    print(f"  {'Score':<7}  {'N':>4}  {'WR%':>5}  {'PF':>5}  {'Avg$':>7}  "
          f"{'Longs PF':>8}  {'Shorts PF':>9}  {'Note'}")
    print(f"  {'─' * 62}")
    for sc in range(5):
        st = [t for t in trades_2022 if t["score"] == sc]
        if not st:
            continue
        s  = stats(st)
        sl = stats([t for t in st if t["dir"] == "long"])
        ss2 = stats([t for t in st if t["dir"] == "short"])
        note = ins(s["n"])
        print(f"  {sc:<7}  {s['n']:>4}  {s['wr']:.1%}  {s['pf']:.3f}  "
              f"${s['avg']:>+6,.0f}  {sl['pf']:>8.3f}  {ss2['pf']:>9.3f}{note}")

    # 6d. 2022 vs other IS years at same score ≥3
    print(f"\n  6d. Score≥3 by year (IS):")
    print(f"  {'Year':<6}  {'All N':>5}  {'All PF':>6}  {'Score≥3 N':>9}  "
          f"{'Score≥3 PF':>10}  {'Score≤1 N':>9}  {'Score≤1 PF':>10}")
    print(f"  {'─' * 62}")
    for yr in IS_YEARS + OOS_YEARS:
        yt  = [t for t in enriched if t["year"] == yr]
        ys3 = [t for t in yt if t["score"] >= 3]
        ys1 = [t for t in yt if t["score"] <= 1]
        if not yt:
            continue
        sa  = stats(yt)
        s3  = stats(ys3)
        s1  = stats(ys1)
        print(f"  {yr:<6}  {sa['n']:>5}  {sa['pf']:>6.3f}  "
              f"{s3['n']:>9}  {s3['pf']:>10.3f}  {s1['n']:>9}  {s1['pf']:>10.3f}")


    # ══════════════════════════════════════════════════════════════════════════
    # 7. MONTH x SCORE INTERACTION
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  7. MONTH x SCORE INTERACTION  (strong months only)")
    divider()
    print("  For each strong month, does score≥3 add meaningfully on top?")
    print(f"  Strong months: {sorted(STRONG_MONTHS)}\n")

    for period_label, period_trades in [("IS 2022-2024", is_e), ("OOS 2025-2026", oos_e)]:
        print(f"\n  {period_label}:")
        print(f"  {'Mon':<5}  {'Status':<8}  {'All N':>5}  {'All PF':>6}  "
              f"{'Score≥3 N':>9}  {'Score≥3 PF':>10}  {'ΔPF':>6}  {'Score≤1 PF':>10}  {'Note'}")
        print(f"  {'─' * 78}")

        for m in range(1, 13):
            mt  = [t for t in period_trades if t["month"] == m]
            if not mt:
                continue
            ms3 = [t for t in mt if t["score"] >= 3]
            ms1 = [t for t in mt if t["score"] <= 1]
            sa  = stats(mt)
            s3  = stats(ms3)
            s1  = stats(ms1)
            dpf = s3["pf"] - sa["pf"]
            status = ("STRONG" if m in STRONG_MONTHS else
                      "WEAK"   if m in WEAK_MONTHS   else "neutral")
            flag = "  <<" if dpf > 0.30 else ""
            note = ins(s3["n"])
            print(f"  {MONTH_NAMES[m]:<5}  {status:<8}  {sa['n']:>5}  {sa['pf']:>6.3f}  "
                  f"{s3['n']:>9}  {s3['pf']:>10.3f}  {dpf:>+6.3f}  "
                  f"{s1['pf']:>10.3f}{note}{flag}")

    # Quick strong-month summary
    print(f"\n  Strong-month summary (all years 2022-2026):")
    strong_t = [t for t in enriched if t["month"] in STRONG_MONTHS]
    s_base   = stats(strong_t)
    s_s3     = stats([t for t in strong_t if t["score"] >= 3])
    s_s0     = stats([t for t in strong_t if t["score"] <= 1])
    print(f"  Strong-month baseline   : N={s_base['n']}  PF={s_base['pf']:.3f}  Avg=${s_base['avg']:+,.0f}")
    print(f"  Strong-month score≥3    : N={s_s3['n']}  PF={s_s3['pf']:.3f}  Avg=${s_s3['avg']:+,.0f}  ΔPF={s_s3['pf']-s_base['pf']:+.3f}")
    print(f"  Strong-month score≤1    : N={s_s0['n']}  PF={s_s0['pf']:.3f}  Avg=${s_s0['avg']:+,.0f}")

    # Strong-month OOS
    strong_oos = [t for t in oos_e if t["month"] in STRONG_MONTHS]
    sb_oos     = stats(strong_oos)
    s3_oos     = stats([t for t in strong_oos if t["score"] >= 3])
    s0_oos     = stats([t for t in strong_oos if t["score"] <= 1])
    print(f"\n  Strong-month OOS only:")
    print(f"  Baseline   : N={sb_oos['n']}  PF={sb_oos['pf']:.3f}  Avg=${sb_oos['avg']:+,.0f}")
    print(f"  Score≥3    : N={s3_oos['n']}  PF={s3_oos['pf']:.3f}  Avg=${s3_oos['avg']:+,.0f}  ΔPF={s3_oos['pf']-sb_oos['pf']:+.3f}")
    print(f"  Score≤1    : N={s0_oos['n']}  PF={s0_oos['pf']:.3f}  Avg=${s0_oos['avg']:+,.0f}")


    # ══════════════════════════════════════════════════════════════════════════
    # BONUS: Score distribution and combined cutoffs
    # ══════════════════════════════════════════════════════════════════════════
    divider()
    print("  BONUS: SCORE DISTRIBUTION AND COMBINED FILTER SUMMARY")
    divider()
    print(f"\n  Full score distribution (all years, IS + OOS):")
    print(f"  {'Score':<7}  {'N':>4}  {'%total':>7}  {'WR%':>5}  {'PF':>5}  "
          f"{'Avg$':>7}  {'OOS PF':>7}  {'IS PF':>6}")
    print(f"  {'─' * 58}")
    for sc in range(5):
        at = [t for t in enriched if t["score"] == sc]
        ot = [t for t in oos_e   if t["score"] == sc]
        it = [t for t in is_e    if t["score"] == sc]
        if not at:
            continue
        sa = stats(at)
        so = stats(ot)
        si = stats(it)
        pct = sa["n"] / len(enriched) * 100
        print(f"  {sc:<7}  {sa['n']:>4}  {pct:>6.1f}%  {sa['wr']:.1%}  {sa['pf']:.3f}  "
              f"${sa['avg']:>+6,.0f}  {so['pf']:>7.3f}  {si['pf']:>6.3f}")

    print(f"\n  Score≥2 combined (keep):")
    for period_label, period_trades in [("IS", is_e), ("OOS", oos_e)]:
        s2p  = stats([t for t in period_trades if t["score"] >= 2])
        s3p  = stats([t for t in period_trades if t["score"] >= 3])
        sb   = stats(period_trades)
        print(f"  {period_label}: baseline PF={sb['pf']:.3f} N={sb['n']}  |  "
              f"score≥2: PF={s2p['pf']:.3f} N={s2p['n']}  |  "
              f"score≥3: PF={s3p['pf']:.3f} N={s3p['n']}")

    divider()
    print("  Done.")
    divider()
    print()


if __name__ == "__main__":
    main()
