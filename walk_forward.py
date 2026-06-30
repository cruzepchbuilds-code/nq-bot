"""
Walk-Forward Validation
Splits data by calendar year to test if the edge holds on unseen data.

  In-sample  : 2024        (used to inform strategy design)
  Out-of-sample: 2025-2026 (never touched during tuning)

Usage:
    python3 walk_forward.py data/nq_1min.csv
"""

import sys
from datetime import datetime, date
from collections import defaultdict

import config
from backtest import Backtester, load_csv, LAST_ENTRY, FLATTEN, SLIP
from backtest import (LONDON_START, LONDON_RANGE_TO, LONDON_CLASSIFY,
                      LONDON_ENTRY, LONDON_EXIT,
                      ASIA_OPEN_BAR, ASIA_ENTRY_BAR, ASIA_HARD_EXIT)


# -- Silent runner + summary (monkey-patched onto Backtester) ----------------

def _run_silent(self, bars):
    """Run the full backtester loop without printing."""
    from datetime import time as dt_time
    OR_END = (dt_time(9, 30 + config.OPENING_RANGE_MINUTES)
              if config.OPENING_RANGE_MINUTES < 30
              else dt_time(9 + (30 + config.OPENING_RANGE_MINUTES) // 60,
                           (30 + config.OPENING_RANGE_MINUTES) % 60))
    SESSION_START = dt_time(9, 30)
    current_day = None
    for bar in bars:
        ts = bar["timestamp"]
        t = ts.time()

        in_london = config.LONDON_ENABLED and LONDON_START <= t <= LONDON_EXIT
        in_orb    = SESSION_START <= t <= dt_time(16, 0)
        in_asia   = config.ASIA_ENABLED and ASIA_OPEN_BAR <= t <= ASIA_HARD_EXIT
        if not in_london and not in_orb and not in_asia:
            continue

        # Day rollover
        if ts.date() != current_day:
            if current_day is not None and self.day_high is not None:
                self.regime.record_day(self.day_high, self.day_low)
            current_day = ts.date()
            self.strategy.reset_day(prev_close=self._last_close, day_of_week=ts.weekday())
            self.london.reset_day()
            self.asia.reset_session()
            self.day_mode = "pending"
            self.day_high, self.day_low = None, None
            self.bank.on_new_bar_date(current_day)

        # Asia window (18:00-21:00)
        if in_asia:
            self.check_exit_asia(bar, ts, force=(t >= ASIA_HARD_EXIT))
            if not self.asia.traded_today:
                if t == ASIA_OPEN_BAR:
                    self.asia.classify_at_open(bar, ts.month, ts.weekday())
                elif t == ASIA_ENTRY_BAR and self.asia.entry_pending:
                    sig = self.asia.check_entry(bar)
                    if sig:
                        self.try_enter_asia(sig, ts)
            if not in_orb:
                continue

        # London window (8:00-9:25)
        if in_london:
            self.check_london_exit(bar, ts, force=(t >= LONDON_EXIT))
            if not self.london.traded_today:
                if t <= LONDON_RANGE_TO:
                    self.london.update_range(bar)
                elif t == LONDON_CLASSIFY:
                    self.london.classify_at_nine(bar)
                elif t == LONDON_ENTRY and self.london.signal_pending:
                    sig = self.london.check_entry(bar)
                    if sig:
                        self.try_enter_london(sig, ts)
            if not in_orb:
                continue

        # ORB session (9:30-16:00)
        self.day_high = (bar["high"] if self.day_high is None
                         else max(self.day_high, bar["high"]))
        self.day_low  = (bar["low"]  if self.day_low  is None
                         else min(self.day_low,  bar["low"]))
        self._last_close = bar["close"]
        if config.ASIA_ENABLED and t == dt_time(16, 0):
            self.asia.record_us_close(bar)
        self.check_exit(bar, ts, force=(t >= FLATTEN))
        self.strategy._update_vwap(bar)
        if t < OR_END:
            self.strategy.update_opening_range(bar)
            continue
        if self.day_mode == "pending":
            tradeable = self.strategy.finalize_range()
            self.day_mode = ("skip" if not tradeable
                             else self.regime.classify(self.strategy.range_size))
        if self.open_position is None and t <= LAST_ENTRY:
            sig = None
            if self.day_mode == "breakout":
                if not self.strategy.traded_today:
                    sig = self.strategy.check_breakout(bar)
                elif config.VWAP_PULLBACK_ENABLED and not self.strategy.second_trade_today:
                    sig = self.strategy.check_vwap_pullback(bar)
            elif self.day_mode == "fade":
                sig = self.strategy.check_fade(bar)
            if sig:
                self.try_enter(sig, ts)

    if self.day_high is not None:
        self.regime.record_day(self.day_high, self.day_low)


def _summary(self, label: str):
    log = self.bank.trade_log
    s = self.bank.s
    no_trades = {"label": label, "pf": None, "net": 0, "max_dd": 0, "trades": 0,
                 "win_rate": 0, "max_dd_pct": 0,
                 "row": f"  {label:<29} {'—':>6} {'—':>7} {'—':>5}  {'no trades':>9}  {'—':>7}  {'—':>6}  —"}
    if not log:
        return no_trades

    wins   = [t for t in log if t["pnl"] > 0]
    losses = [t for t in log if t["pnl"] <= 0]
    gross_win  = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_win / gross_loss if gross_loss else float("inf")
    net = s.balance - config.STARTING_BALANCE

    peak = config.STARTING_BALANCE
    max_dd = 0.0
    for t in log:
        peak = max(peak, t["balance"])
        max_dd = max(max_dd, peak - t["balance"])
    max_dd_pct = max_dd / peak if peak else 0
    win_rate = len(wins) / len(log) if log else 0

    halt_str = ""
    if s.halted_permanently:
        reason = s.halt_reason
        if "drawdown" in reason.lower():
            halt_str = "HALTED(DD)"
        elif "Apex" in reason:
            halt_str = "HALTED(Apex)"
        else:
            halt_str = "HALTED"
    else:
        halt_str = "active"

    # mode breakdown
    by_mode = defaultdict(list)
    for t in log:
        by_mode[t["mode"]].append(t["pnl"])

    row = (f"  {label:<29} {len(log):>6} {win_rate:>6.1%} {pf:>5.2f}  "
           f"  {net:>+8,.0f}  {max_dd:>7,.0f}  {max_dd_pct:>5.1%}  {halt_str}")
    return {
        "label": label, "pf": pf, "net": net, "max_dd": max_dd,
        "trades": len(log), "win_rate": win_rate, "max_dd_pct": max_dd_pct,
        "gross_win": gross_win, "gross_loss": gross_loss,
        "by_mode": dict(by_mode), "halt_str": halt_str,
        "avg_win":  gross_win  / len(wins)   if wins   else 0,
        "avg_loss": gross_loss / len(losses) if losses else 0,
        "row": row,
    }


Backtester.run_silent = _run_silent
Backtester.summary    = _summary


# -- Main --------------------------------------------------------------------

def run_window(bars, label, start: date, end: date):
    subset = [b for b in bars if start <= b["timestamp"].date() < end]
    if not subset:
        return None
    bt = Backtester()
    bt.run(subset, silent=True)
    return bt.summary(label)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/nq_1min.csv"
    bars = load_csv(path)
    print(f"Loaded {len(bars):,} bars  |  "
          f"{bars[0]['timestamp'].date()} -> {bars[-1]['timestamp'].date()}")

    windows = [
        ("2024  [IN-SAMPLE]    ", date(2024, 1, 1), date(2025, 1, 1)),
        ("2025  [OUT-OF-SAMPLE]", date(2025, 1, 1), date(2026, 1, 1)),
        ("2026  [OUT-OF-SAMPLE]", date(2026, 1, 1), date(2027, 1, 1)),
        ("2025-26 [OOS COMBINED]", date(2025, 1, 1), date(2027, 1, 1)),
    ]

    results = []
    bar_subsets = {}
    for label, start, end in windows:
        subset = [b for b in bars if start <= b["timestamp"].date() < end]
        bar_subsets[label] = subset
        bt = Backtester()
        bt.run(subset, silent=True)
        r = bt.summary(label)
        results.append(r)

    _print_main_table(results)
    _print_mode_breakdown(results)
    _print_monthly(bar_subsets)
    _print_verdict(results)


def _print_main_table(results):
    print()
    hdr_line = "=" * 90
    print(hdr_line)
    print(f"  {'WALK-FORWARD RESULTS':^86}")
    print(hdr_line)
    hdr = (f"  {'Period':<29} {'Trades':>6} {'WinRate':>7} {'PF':>5}  "
           f"{'Net P&L':>9}  {'MaxDD$':>7}  {'DD%':>5}  Status")
    print(hdr)
    print("=" * 90)
    for r in results:
        print(r["row"])
    print("=" * 90)


def _print_mode_breakdown(results):
    # Collect all modes seen across all periods
    all_modes = []
    for r in results:
        for m in r.get("by_mode", {}):
            if m not in all_modes:
                all_modes.append(m)

    col_w = 10
    header = f"  {'Period':<29}" + "".join(f"{m:>{col_w}}" for m in all_modes) + f" {'#trades':>8}"
    print(f"\n  Mode breakdown (Net P&L by mode)")
    print(header)
    print("  " + "-" * (30 + col_w * len(all_modes) + 9))
    for r in results:
        if r["pf"] is None:
            continue
        bm = r.get("by_mode", {})
        cols = ""
        for m in all_modes:
            if m in bm:
                cols += f"${sum(bm[m]):>+,.0f}"[-col_w:].rjust(col_w)
            else:
                cols += "  —".rjust(col_w)
        print(f"  {r['label']:<29}{cols} {r['trades']:>8}")


def _print_monthly(bar_subsets):
    print(f"\n  Month-by-month P&L -- 2025 out-of-sample")
    print(f"  {'Month':<10} {'Trades':>6} {'Wins':>5} {'Net P&L':>10}")
    print("  " + "-" * 34)
    label = "2025  [OUT-OF-SAMPLE]"
    subset_2025 = bar_subsets.get(label, [])

    months = defaultdict(list)
    for b in subset_2025:
        months[b["timestamp"].strftime("%Y-%m")].append(b)

    total_trades = total_net = 0
    for ym in sorted(months):
        bt = Backtester()
        bt.run(months[ym], silent=True)
        log = bt.bank.trade_log
        net = sum(t["pnl"] for t in log)
        wins = sum(1 for t in log if t["pnl"] > 0)
        n = len(log)
        total_trades += n
        total_net    += net
        marker = "  < halt" if bt.bank.s.halted_permanently else ""
        print(f"  {ym:<10} {n:>6} {wins:>5} {net:>+10,.0f}{marker}")
    print("  " + "-" * 34)
    print(f"  {'TOTAL':<10} {total_trades:>6} {'':>5} {total_net:>+10,.0f}")


def _print_verdict(results):
    is_r  = next((r for r in results if "IN-SAMPLE"    in r["label"]), None)
    oos_r = next((r for r in results if "OOS COMBINED" in r["label"]), None)
    print()
    print("=" * 90)
    print(f"  EDGE STABILITY ANALYSIS")
    print("=" * 90)
    if is_r and oos_r and is_r["pf"] and oos_r["pf"]:
        ret = oos_r["pf"] / is_r["pf"]
        print(f"  In-sample  2024:       PF {is_r['pf']:.2f}  |  "
              f"{is_r['trades']} trades  |  Net {is_r['net']:+,.0f}  |  MaxDD {is_r['max_dd_pct']:.1%}")
        print(f"  Out-of-sample 2025-26: PF {oos_r['pf']:.2f}  |  "
              f"{oos_r['trades']} trades  |  Net {oos_r['net']:+,.0f}  |  MaxDD {oos_r['max_dd_pct']:.1%}")
        print(f"  PF retention:          {ret:.0%}")
        print()
        if ret >= 0.80:
            verdict = "ROBUST -- strategy generalises well"
        elif ret >= 0.60:
            verdict = "MODERATE -- real degradation; caution before live trading"
        else:
            verdict = "FRAGILE -- likely overfit to 2024 conditions"

        if oos_r["pf"] and oos_r["pf"] > 1.0:
            verdict += " [OOS still profitable]"
        elif oos_r["pf"] and oos_r["pf"] < 1.0:
            verdict += " [OOS is net-negative -- investigate before going live]"

        print(f"  Verdict: {verdict}")

    print()
    print("  KEY FINDING: if OOS trade count is far below IS, check whether the strategy")
    print("  is being halted early in OOS (max-DD or Apex floor). Interpret PF with caution")
    print("  when sample size < 50 trades.")
    print("=" * 90)


if __name__ == "__main__":
    main()
