"""
brain/research/gap_regime_value.py

Quantify two filters present in the Python backtest but MISSING from CruzCapitalNQ_v10_3.cs:
  1. Gap filter    — check_breakout requires OR-mid >20pt above/below prior RTH close, aligned
  2. Regime gate   — OR size must be >= 0.18 x 14-day avg daily range

The C# has neither, so NT8 takes trades the researched system (OOS PF 4.94) never would.
This measures how much each filter matters, and sweeps the gap threshold (never swept).

Variants:
  A. baseline        — config as-is (both filters on, threshold 20)
  B. no_gap          — gap alignment requirement removed (what C# does)
  C. no_regime       — regime gate forced to 'breakout' (what C# does)
  D. no_gap+no_regime— both off (closest to current C# behavior)
  E. threshold sweep — gap threshold 10 / 15 / 25 / 30

IS: 2022-2024  |  OOS: 2025-Jun 2026
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from backtest import Backtester, load_csv
from strategies.strategy_us import ORBStrategy, Signal
from regime import RegimeDetector
from datetime import date

DATA      = "data/nq_full.csv"
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]

# ── original methods (restored between variants) ─────────────────────────────
_orig_check_breakout = ORBStrategy.check_breakout
_orig_classify       = RegimeDetector.classify


def check_breakout_no_gap(self, bar):
    """check_breakout with the gap-alignment requirement removed (C# behavior)."""
    if self.traded_today or not self.or_complete:
        return None
    stop_dist = self._stop_distance()
    bar_vol = bar.get("volume", 0)
    vol_ok = bar_vol >= config.BREAKOUT_MIN_VOLUME
    buf = config.ORB_BREAKOUT_BUFFER_POINTS
    rr = (config.ORB_BREAKOUT_RR_TARGET if config.EVAL_MODE
          else config.ORB_FUNDED_RR_TARGET)

    if bar["close"] > self.or_high + buf and vol_ok:
        entry = bar["close"]
        stop = entry - stop_dist
        target = entry + (entry - stop) * rr
        self.breakout_dir = "long"
        return Signal("long", entry, stop, target, "breakout", 1.0)

    if bar["close"] < self.or_low - buf and vol_ok:
        entry = bar["close"]
        stop = entry + stop_dist
        target = entry - (stop - entry) * rr
        self.breakout_dir = "short"
        return Signal("short", entry, stop, target, "breakout", 1.0)

    return None


def classify_always_breakout(self, opening_range_size):
    return "breakout"


# ── year runner (warmup pattern from vwap_filter.py) ─────────────────────────

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


def stats(trades):
    # morning breakout trades only (modes: breakout); asia logged separately
    t = [x for x in trades if x.get("mode") == "breakout"]
    if not t:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0, "avg": 0}
    wins = [x for x in t if x["pnl"] > 0]
    gw = sum(x["pnl"] for x in wins)
    gl = abs(sum(x["pnl"] for x in t if x["pnl"] <= 0))
    net = sum(x["pnl"] for x in t)
    return {"n": len(t), "wr": len(wins) / len(t),
            "pf": round(gw / gl, 3) if gl else 99.0,
            "net": round(net), "avg": round(net / len(t))}


def show(label, s_is, s_oos):
    print(f"  {label:<28}  IS:  N={s_is['n']:>3}  WR={s_is['wr']:.0%}  "
          f"PF={s_is['pf']:>6.3f}  Net=${s_is['net']:>+9,.0f}")
    print(f"  {'':<28}  OOS: N={s_oos['n']:>3}  WR={s_oos['wr']:.0%}  "
          f"PF={s_oos['pf']:>6.3f}  Net=${s_oos['net']:>+9,.0f}")
    print()


if __name__ == "__main__":
    print(f"\n{'='*84}")
    print(f"  Gap Filter + Regime Gate — value quantification")
    print(f"  IS: 2022-2024  |  OOS: 2025-Jun 2026")
    print(f"  config: EVAL_MODE={config.EVAL_MODE}  SKIP_FRIDAYS={config.SKIP_FRIDAYS}  "
          f"PYRAMID={getattr(config, 'PYRAMID_ENABLED', '?')}  ASIA={config.ASIA_ENABLED}")
    print(f"{'='*84}\n")

    print("  Loading bars...", end=" ", flush=True)
    bars = load_csv(DATA)
    print(f"{len(bars):,} bars  {bars[0]['timestamp'].date()} → {bars[-1]['timestamp'].date()}\n")

    # A. Baseline
    print("  Running A: baseline (gap=20 + regime on)...", flush=True)
    s_is, s_oos = stats(run_years(bars, IS_YEARS)), stats(run_years(bars, OOS_YEARS))
    show("A. baseline", s_is, s_oos)
    base_oos = s_oos

    # B. No gap filter
    print("  Running B: gap filter OFF (C# behavior)...", flush=True)
    ORBStrategy.check_breakout = check_breakout_no_gap
    s_is, s_oos = stats(run_years(bars, IS_YEARS)), stats(run_years(bars, OOS_YEARS))
    ORBStrategy.check_breakout = _orig_check_breakout
    show("B. no gap filter", s_is, s_oos)

    # C. No regime gate
    print("  Running C: regime gate OFF...", flush=True)
    RegimeDetector.classify = classify_always_breakout
    s_is, s_oos = stats(run_years(bars, IS_YEARS)), stats(run_years(bars, OOS_YEARS))
    RegimeDetector.classify = _orig_classify
    show("C. no regime gate", s_is, s_oos)

    # D. Both off — closest to current C#
    print("  Running D: BOTH off (current C# v10.3)...", flush=True)
    ORBStrategy.check_breakout = check_breakout_no_gap
    RegimeDetector.classify = classify_always_breakout
    s_is, s_oos = stats(run_years(bars, IS_YEARS)), stats(run_years(bars, OOS_YEARS))
    ORBStrategy.check_breakout = _orig_check_breakout
    RegimeDetector.classify = _orig_classify
    show("D. both OFF (≈ C# v10.3)", s_is, s_oos)

    # E. Gap threshold sweep (both filters on, vary threshold)
    print("  Running E: gap threshold sweep...")
    orig_thresh = config.GAP_FILTER_POINTS
    for th in [10, 15, 20, 25, 30]:
        config.GAP_FILTER_POINTS = float(th)
        s_is, s_oos = stats(run_years(bars, IS_YEARS)), stats(run_years(bars, OOS_YEARS))
        mark = "  ← current" if th == 20 else ""
        print(f"  gap>{th:<2}   IS: N={s_is['n']:>3} PF={s_is['pf']:>6.3f} "
              f"${s_is['net']:>+9,.0f}   OOS: N={s_oos['n']:>3} PF={s_oos['pf']:>6.3f} "
              f"${s_oos['net']:>+9,.0f}{mark}")
    config.GAP_FILTER_POINTS = orig_thresh

    print(f"\n{'='*84}")
    print(f"  Baseline OOS: N={base_oos['n']} PF={base_oos['pf']} Net=${base_oos['net']:,}")
    print(f"{'='*84}\n")
