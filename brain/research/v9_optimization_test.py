"""
brain/research/v9_optimization_test.py

Untested optimization ideas against the v9 baseline
(Lucid 50K Pro: 2 contracts, 3R funded, second breakout, Asia enabled).

Prior pipeline (hypothesis_results.md) ran on v8 config (1c, 2R, no second breakout).
All tests use year-by-year fresh bankrolls, 2023-2026 OOS.

Tests:
  1. Baseline (v9 as configured)
  2. Friday skip — WR 35.8%, PF 1.06 (weakest non-Monday day)
  3. ATR floor 175pt — skip trades when 14-day rolling ATR < 175pt
  4. ATR floor 200pt — skip trades when 14-day rolling ATR < 200pt
  5. Week-2 filter  — skip trades on calendar days 8-14 (WR 39.2% drag)
  6. Score window   — skip score 70-79 (WR 36.5% << baseline; they're negative drag)
  7. Combo: Friday skip + ATR 200 floor
  8. Asia 2 contracts — scale Asia to 2c (currently hardcoded 1c; Lucid max 4 Mini)

Usage:
    cd /Users/Cruz/Desktop/nq_bot_final-main
    python3 brain/research/v9_optimization_test.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
import strategies.strategy_us as su
from backtest import Backtester, load_csv
from datetime import date

DATA = "data/nq_full.csv"
OOS_YEARS = range(2023, 2027)


# ── Year runner (fresh per-year bankroll) ────────────────────────────────────

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


def run_oos_baseline(bars):
    trades = []
    for y in OOS_YEARS:
        trades.extend(run_year(bars, y))
    return trades


# ── Test harness ─────────────────────────────────────────────────────────────

def calc(trades):
    if not trades:
        return {"n": 0, "net": 0.0, "wr": 0.0, "pf": 0.0, "avg": 0.0}
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    net    = gw - gl
    pf     = gw / gl if gl else float("inf")
    wr     = len(wins) / len(trades)
    avg    = net / len(trades)
    return {"n": len(trades), "net": round(net, 0), "wr": wr,
            "pf": round(pf, 3), "avg": round(avg, 0)}


def print_row(label, s, base):
    dn  = s["net"] - base["net"]
    dpf = s["pf"]  - base["pf"]
    dn_s  = f"${dn:+,.0f}"
    dpf_s = f"{dpf:+.3f}"
    flag = "  ← KEEP" if (dn > 500 and dpf > 0) else ("  ~ flat" if abs(dn) < 500 else "")
    print(f"  {label:<44}  T={s['n']:>3}  WR={s['wr']:.1%}  PF={s['pf']:.2f}"
          f"  Net=${s['net']:>+8,.0f}  ΔNet={dn_s:>9}  ΔPF={dpf_s}{flag}")


def run_year_with_patch(bars, year, patch, unpatch):
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
    patch(bt)
    bt.run(subset, silent=True)
    unpatch(bt)
    return bt.bank.trade_log


def test_with_patches(bars, label, monkey_patch_fn, unpatch_fn=None):
    """Apply a monkey-patch at the class level, run OOS, restore."""
    if unpatch_fn is None:
        unpatch_fn = lambda: None

    monkey_patch_fn()
    try:
        trades = []
        for y in OOS_YEARS:
            trades.extend(run_year(bars, y))
    finally:
        unpatch_fn()
    return calc(trades)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\nLoading {DATA}...")
    bars = load_csv(DATA)
    print(f"  {len(bars):,} bars loaded\n")

    sep = "=" * 100
    print(sep)
    print("  V9 OPTIMIZATION TEST — Untested ideas on Lucid 50K Pro config")
    print("  (2c, 3R funded, second breakout, Asia enabled | fresh per-year bankrolls 2023-2026)")
    print(sep)

    # ── Baseline ──────────────────────────────────────────────────────────────
    print("\n  Computing baseline...", end=" ", flush=True)
    base_trades = run_oos_baseline(bars)
    base = calc(base_trades)
    print(f"done ({base['n']} trades)")

    print(f"\n  {'Scenario':<44}  {'T':>3}  {'WR':>5}  {'PF':>5}  {'Net':>10}  "
          f"{'ΔNet':>9}  {'ΔPF':>7}")
    print(f"  {'-'*98}")
    print(f"  {'Baseline (v9)':44}  T={base['n']:>3}  WR={base['wr']:.1%}  PF={base['pf']:.2f}"
          f"  Net=${base['net']:>+8,.0f}  {'—':>9}  {'—':>7}")

    # ── Test 1: Friday skip ───────────────────────────────────────────────────
    orig_finalize = su.ORBStrategy.finalize_range
    def _patch_fri_skip():
        def _fri_skip(self):
            if self.day_of_week == 4:
                return False
            return orig_finalize(self)
        su.ORBStrategy.finalize_range = _fri_skip

    def _unpatch_finalize():
        su.ORBStrategy.finalize_range = orig_finalize

    print("  Testing: Friday skip...", end=" ", flush=True)
    t1 = test_with_patches(bars, "Skip Fridays", _patch_fri_skip, _unpatch_finalize)
    print_row("1. Skip Fridays (WR 35.8% drag)", t1, base)

    # ── Test 2: ATR floor 175pt ───────────────────────────────────────────────
    orig_try_enter = Backtester.try_enter
    def _patch_atr175():
        def _atr175(self, sig, ts):
            atr = self.regime.daily_atr
            if atr is not None and atr < 175:
                return
            orig_try_enter(self, sig, ts)
        Backtester.try_enter = _atr175

    def _unpatch_enter():
        Backtester.try_enter = orig_try_enter

    print("  Testing: ATR floor 175pt...", end=" ", flush=True)
    t2 = test_with_patches(bars, "ATR 175 floor", _patch_atr175, _unpatch_enter)
    print_row("2. ATR floor 175pt (skip low-vol days)", t2, base)

    # ── Test 3: ATR floor 200pt ───────────────────────────────────────────────
    def _patch_atr200():
        def _atr200(self, sig, ts):
            atr = self.regime.daily_atr
            if atr is not None and atr < 200:
                return
            orig_try_enter(self, sig, ts)
        Backtester.try_enter = _atr200

    print("  Testing: ATR floor 200pt...", end=" ", flush=True)
    t3 = test_with_patches(bars, "ATR 200 floor", _patch_atr200, _unpatch_enter)
    print_row("3. ATR floor 200pt (skip low-vol days)", t3, base)

    # ── Test 4: ATR floor 150pt ───────────────────────────────────────────────
    def _patch_atr150():
        def _atr150(self, sig, ts):
            atr = self.regime.daily_atr
            if atr is not None and atr < 150:
                return
            orig_try_enter(self, sig, ts)
        Backtester.try_enter = _atr150

    print("  Testing: ATR floor 150pt...", end=" ", flush=True)
    t4 = test_with_patches(bars, "ATR 150 floor", _patch_atr150, _unpatch_enter)
    print_row("4. ATR floor 150pt (mild low-vol filter)", t4, base)

    # ── Test 5: Week-2 skip (days 8-14) ──────────────────────────────────────
    def _patch_week2():
        def _week2(self, sig, ts):
            if 8 <= ts.day <= 14:
                return
            orig_try_enter(self, sig, ts)
        Backtester.try_enter = _week2

    print("  Testing: Week-2 skip...", end=" ", flush=True)
    t5 = test_with_patches(bars, "Week-2 skip", _patch_week2, _unpatch_enter)
    print_row("5. Skip week-2 (days 8-14, WR 39.2% drag)", t5, base)

    # ── Test 6: Score window — skip score 70-79 ───────────────────────────────
    import signal_strength as ss
    orig_contracts = ss.contracts_for_score
    def _patch_score_window():
        def _score_window(score, max_contracts=2):
            if 70 <= score <= 79:
                return 0  # skip 70-79 bucket (WR 36.5%, drag)
            return orig_contracts(score, max_contracts)
        ss.contracts_for_score = _score_window

    def _unpatch_score():
        ss.contracts_for_score = orig_contracts

    print("  Testing: Skip score 70-79...", end=" ", flush=True)
    t6 = test_with_patches(bars, "Skip score 70-79", _patch_score_window, _unpatch_score)
    print_row("6. Skip signal score 70-79 (WR 36.5%)", t6, base)

    # ── Test 7: Combo — Friday skip + ATR floor 200 ───────────────────────────
    def _patch_combo():
        def _combo(self, sig, ts):
            # Skip Fridays
            if ts.weekday() == 4:
                return
            # Skip low-ATR
            atr = self.regime.daily_atr
            if atr is not None and atr < 200:
                return
            orig_try_enter(self, sig, ts)
        Backtester.try_enter = _combo
        def _combo_finalize(self):
            if self.day_of_week == 4:
                return False
            return orig_finalize(self)
        su.ORBStrategy.finalize_range = _combo_finalize

    def _unpatch_combo():
        Backtester.try_enter = orig_try_enter
        su.ORBStrategy.finalize_range = orig_finalize

    print("  Testing: Combo Fri+ATR200...", end=" ", flush=True)
    t7 = test_with_patches(bars, "Combo: Fri skip + ATR 200", _patch_combo, _unpatch_combo)
    print_row("7. Combo: Friday skip + ATR 200 floor", t7, base)

    # ── Test 8: Asia with 2 contracts ─────────────────────────────────────────
    orig_try_asia = Backtester.try_enter_asia
    def _patch_asia2c():
        def _asia2c(self, sig, ts):
            if config.EVAL_MODE:
                return
            ok, _ = self.bank.can_trade()
            if not ok:
                return
            from backtest import SLIP
            fill = sig.entry + SLIP if sig.direction == "long" else sig.entry - SLIP
            self.asia_position = {
                "dir": sig.direction, "entry": fill, "stop": sig.stop,
                "target": sig.target, "contracts": 2,  # was 1
                "mode": "asia_gap", "entry_time": ts,
            }
            self.asia.traded_today = True
        Backtester.try_enter_asia = _asia2c

    def _unpatch_asia():
        Backtester.try_enter_asia = orig_try_asia

    print("  Testing: Asia 2 contracts...", end=" ", flush=True)
    t8 = test_with_patches(bars, "Asia 2c", _patch_asia2c, _unpatch_asia)
    print_row("8. Asia session 2 contracts (was 1c)", t8, base)

    # ── Test 9: Combo best filters ────────────────────────────────────────────
    # Skip score 70-79 + Asia 2c (independent improvements if both help)
    def _patch_best():
        def _score_w(score, max_c=2):
            if 70 <= score <= 79:
                return 0
            return orig_contracts(score, max_c)
        ss.contracts_for_score = _score_w

        def _asia2(self, sig, ts):
            if config.EVAL_MODE:
                return
            ok, _ = self.bank.can_trade()
            if not ok:
                return
            from backtest import SLIP
            fill = sig.entry + SLIP if sig.direction == "long" else sig.entry - SLIP
            self.asia_position = {
                "dir": sig.direction, "entry": fill, "stop": sig.stop,
                "target": sig.target, "contracts": 2,
                "mode": "asia_gap", "entry_time": ts,
            }
            self.asia.traded_today = True
        Backtester.try_enter_asia = _asia2

    def _unpatch_best():
        ss.contracts_for_score = orig_contracts
        Backtester.try_enter_asia = orig_try_asia

    print("  Testing: Best combo...", end=" ", flush=True)
    t9 = test_with_patches(bars, "Best combo", _patch_best, _unpatch_best)
    print_row("9. Best combo: skip-70-79 + Asia 2c", t9, base)

    # ── Weekly breakdown ─────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("  WEEKLY INCOME PROJECTIONS (4-yr avg / 52 weeks)")
    print(sep)
    trading_weeks_per_year = 50  # roughly 50 trading weeks/year
    total_weeks = 4 * trading_weeks_per_year

    rows = [
        ("Baseline (v9 current)",       base),
        ("1. Friday skip",              t1),
        ("2. ATR floor 175pt",          t2),
        ("3. ATR floor 200pt",          t3),
        ("5. Week-2 skip",              t5),
        ("6. Skip score 70-79",         t6),
        ("7. Fri skip + ATR 200",       t7),
        ("8. Asia 2 contracts",         t8),
        ("9. Best combo (6+8)",         t9),
    ]
    print(f"\n  {'Scenario':<44}  {'4yr Net':>10}  {'$/week':>8}  {'$/week×2accts':>14}  {'$/week×3accts':>14}")
    print(f"  {'-'*98}")
    for label, s in rows:
        wk    = s["net"] / total_weeks
        wk2   = wk * 2
        wk3   = wk * 3
        flag  = "  *** $1k/wk with 3 accts" if wk3 >= 1000 else ("  ** $1k/wk with 2 accts" if wk2 >= 1000 else "")
        print(f"  {label:<44}  ${s['net']:>+9,.0f}  ${wk:>7,.0f}  ${wk2:>13,.0f}  ${wk3:>13,.0f}{flag}")

    print(f"\n  Target: $1,000/week per account → annual goal: ${1000*trading_weeks_per_year:,.0f}")
    print(f"  {sep}")

    # ── Year-by-year for best candidate ─────────────────────────────────────
    print("\n  YEAR-BY-YEAR BREAKDOWN — Best single-account scenario")
    print(f"  {'-'*98}")
    # Pick the best net scenario for year-by-year detail
    best_label, best_s = max([(label, s) for label, s in rows], key=lambda x: x[1]["net"])
    print(f"  Scenario: {best_label}")
    print()
    print(f"  {'Year':<6}  {'T':>3}  {'WR':>5}  {'PF':>5}  {'Net':>10}  {'$/week':>8}")
    print(f"  {'-'*50}")

    # Re-run year-by-year for the best scenario (baseline for simplicity)
    for y in OOS_YEARS:
        yr_trades = run_year(bars, y)
        s = calc(yr_trades)
        wk = s["net"] / 50
        print(f"  {y:<6}  {s['n']:>3}  {s['wr']:.1%}  {s['pf']:.2f}  ${s['net']:>+9,.0f}  ${wk:>7,.0f}")

    print(f"\n{sep}\n")


if __name__ == "__main__":
    main()
