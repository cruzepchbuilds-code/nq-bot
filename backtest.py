"""
Backtest Engine
Runs 1-minute bars through two independent strategies that share one bankroll:
  1. London/NY Overlap Momentum  (8:00-9:25 ET)
  2. ORB Multi-Strategy          (9:30-16:00 ET)

Data format expected (CSV): timestamp,open,high,low,close,volume
Timestamps in US/Eastern. 1-minute bars covering at least 8:00-16:00.

Usage:
    python backtest.py data/nq_1min.csv          # real data
    python backtest.py --synthetic               # synthetic smoke test
"""

import csv
import sys
from datetime import datetime, time

import config
from bankroll import BankrollManager
from strategies.strategy_us import ORBStrategy
from strategies.strategy_london import LondonStrategy
from strategies.strategy_asia import AsiaStrategy
from regime import RegimeDetector
import signal_strength as ss
import live.telegram_alerts as tg

# ── ORB session constants ────────────────────────────────────────────────────
OR_END = (time(9, 30 + config.OPENING_RANGE_MINUTES)
          if config.OPENING_RANGE_MINUTES < 30
          else time(9 + (30 + config.OPENING_RANGE_MINUTES) // 60,
                    (30 + config.OPENING_RANGE_MINUTES) % 60))
SESSION_START         = time(9, 30)
LAST_ENTRY            = time(*map(int, config.LAST_ENTRY_TIME.split(":")))
FLATTEN               = time(*map(int, config.FLATTEN_TIME.split(":")))
GAP_FILL_LAST         = time(*map(int, config.GAP_FILL_LAST_ENTRY.split(":")))
PM_VWAP_START         = time(*map(int, config.PM_VWAP_START.split(":")))
PM_VWAP_LAST          = time(*map(int, config.PM_VWAP_LAST_ENTRY.split(":")))
SECOND_BREAKOUT_AFTER = time(*map(int, config.SECOND_BREAKOUT_MIN_TIME.split(":")))

# ── London session constants ─────────────────────────────────────────────────
LONDON_START    = time(8,  0)   # start collecting range bars
LONDON_RANGE_TO = time(8, 59)   # last bar included in range (8:00-8:59)
LONDON_CLASSIFY = time(9,  0)   # classify trend on this bar's close
LONDON_ENTRY    = time(9,  5)   # enter on this bar's close
LONDON_EXIT     = time(9, 25)   # hard exit on this bar's close

# ── Asia session constants ───────────────────────────────────────────────────
ASIA_OPEN_BAR   = time(18,  0)  # 6:00 PM — CME reopens, compute halt gap
ASIA_ENTRY_BAR  = time(18, 15)  # 6:15 PM — entry bar
ASIA_HARD_EXIT  = time(21,  0)  # 9:00 PM — force flatten

SLIP = config.SLIPPAGE_TICKS * config.TICK_SIZE


class Backtester:
    def __init__(self):
        self.bank     = BankrollManager()
        self.strategy = ORBStrategy()
        self.london   = LondonStrategy()
        self.asia     = AsiaStrategy()
        self.regime   = RegimeDetector()
        # Separate position slots — strategies never share
        self.open_position   = None   # ORB
        self.london_position = None   # London
        self.asia_position   = None   # Asia
        self.day_mode  = "skip"
        self.day_high  = None
        self.day_low   = None
        self._last_close = None
        # Signal strength tracking
        self.or_volume_history = []   # rolling 20-day list of OR volumes
        self.prev_day_mode     = None # day_mode from previous day

    # ── ORB position lifecycle ───────────────────────────────────────────────
    def try_enter(self, sig, ts):
        ok, _ = self.bank.can_trade()
        if not ok:
            return

        # Minimum RR guard
        risk   = abs(sig.entry - sig.stop)
        reward = abs(sig.target - sig.entry)
        if risk <= 0 or reward / risk < config.MIN_RR - 0.01:
            return

        # ── Signal strength: size by conviction, not a hard gate ───────────
        # vol_ratio relative to 20-day OR volume average
        avg_or_vol = (sum(self.or_volume_history[-20:]) /
                      len(self.or_volume_history[-20:])
                      if self.or_volume_history else 0.0)
        vol_ratio = (self.strategy.or_volume / avg_or_vol
                     if avg_or_vol > 0 else 1.0)

        # Volume ratio floor gate (if configured)
        if (config.BREAKOUT_MIN_OR_VOLUME_RATIO > 0
                and avg_or_vol > 0
                and vol_ratio < config.BREAKOUT_MIN_OR_VOLUME_RATIO):
            return  # insufficient OR volume relative to 20-day avg

        # Volume ratio ceiling gate — exclude spike bars (WR 27.3% above 1.5x)
        if (config.BREAKOUT_MAX_OR_VOLUME_RATIO > 0
                and avg_or_vol > 0
                and vol_ratio > config.BREAKOUT_MAX_OR_VOLUME_RATIO):
            return  # volume spike — likely news/reversal bar, not a clean breakout

        gap_dir    = self.strategy._gap_direction()
        trade_sign = 1 if sig.direction == "long" else -1
        gap_aligned = gap_dir * trade_sign   # +1 aligned, -1 against, 0 neutral

        score = ss.score_signal(
            entry_time=ts.time(),
            gap_aligned_with_direction=gap_aligned,
            vol_ratio=vol_ratio,
            or_size=self.strategy.range_size,
            prev_day_breakout=(self.prev_day_mode == "breakout"),
        )

        # Regime calendar: adjust contract ceiling by month
        month = ts.month
        if month in config.STRONG_MONTHS:
            max_c = min(3, config.MAX_CONTRACTS + 1)
        elif month in config.WEAK_MONTHS:
            max_c = 1
        else:
            max_c = config.MAX_CONTRACTS

        # Score threshold: lower bar on high-gap days if configured
        if (self.strategy.prev_close is not None
                and self.strategy.or_high is not None):
            or_mid = (self.strategy.or_high + self.strategy.or_low) / 2
            gap_magnitude = abs(or_mid - self.strategy.prev_close)
        else:
            gap_magnitude = 0.0
        min_score = (config.SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP
                     if gap_magnitude >= config.HIGH_GAP_THRESHOLD
                     else config.SIGNAL_STRENGTH_MIN_SCORE)

        # Score gates sizing only if >= min_score; otherwise 1 contract baseline
        if score >= min_score:
            contracts = ss.contracts_for_score(score, max_c)
        else:
            contracts = 1  # baseline: always allow 1 contract regardless of score

        # Still cap by max_c (regime calendar constraint)
        contracts = min(contracts, max_c)

        # Eval mode: override to 1 contract — minimize variance during evaluation
        if config.EVAL_MODE:
            contracts = 1

        fill = sig.entry + SLIP if sig.direction == "long" else sig.entry - SLIP
        self.open_position = {
            "dir": sig.direction, "entry": fill, "stop": sig.stop,
            "target": sig.target, "contracts": contracts,
            "mode": sig.mode, "entry_time": ts,
            "orig_stop_dist": abs(fill - sig.stop),
            "partial_exit_done": False,
            "stall_start": None,
            "pyramid_level": 0,        # 0=none, 1=added at 1R, 2=added at 2R
            "pyramid_entries": [],     # [(contracts_added, fill_price), ...]
        }
        if self.strategy.traded_today:
            self.strategy.second_trade_today = True
        else:
            self.strategy.traded_today = True

        tg.send_alert(
            direction=sig.direction, entry=fill, stop=sig.stop,
            target=sig.target, score=score, contracts=contracts,
            timestamp=ts, mode=sig.mode,
        )

    def check_exit(self, bar, ts, force=False):
        p = self.open_position
        if p is None:
            return
        is_long = (p["dir"] == "long")
        entry   = p["entry"]
        orig_sd = p["orig_stop_dist"]   # original stop distance (~30pts)

        # Dynamic levels
        target_15r = entry + orig_sd * 1.5 if is_long else entry - orig_sd * 1.5

        exit_price = result = None

        # 1. Stop hit (stop may have moved to breakeven/1R after pyramiding)
        if is_long and bar["low"] <= p["stop"]:
            exit_price, result = p["stop"] - SLIP, "stop"
        elif not is_long and bar["high"] >= p["stop"]:
            exit_price, result = p["stop"] + SLIP, "stop"

        # 2. Pyramiding: add 1 contract at 1R, stop -> breakeven, target unchanged (2R)
        #    Guards: account up $1,500+ from start AND 20+ trades logged (warmup)
        #    Disabled in eval mode (reduce variance during prop firm evaluation)
        if config.PYRAMIDING_ENABLED and not config.EVAL_MODE and exit_price is None:
            account_profit = self.bank.s.balance - config.STARTING_BALANCE
            trades_done    = len(self.bank.trade_log)
            pyramid_armed  = (
                account_profit >= config.PYRAMID_MIN_PROFIT_BUFFER
                and trades_done >= config.PYRAMID_WARMUP_TRADES
            )

            if pyramid_armed and p["pyramid_level"] == 0:
                target_1r  = entry + orig_sd if is_long else entry - orig_sd
                total_added = sum(c for c, _ in p["pyramid_entries"])
                hit_1r = bar["high"] >= target_1r if is_long else bar["low"] <= target_1r
                if hit_1r and p["contracts"] + total_added < config.PYRAMID_MAX_CONTRACTS:
                    pyr_fill = target_1r + SLIP if is_long else target_1r - SLIP
                    p["pyramid_entries"].append((1, pyr_fill))
                    p["stop"] = entry           # stop -> breakeven; target stays at 2R
                    p["pyramid_level"] = 1

        # 3. Partial exit at 1.5R (only when pyramiding is off)
        if (exit_price is None and config.PARTIAL_EXIT_ENABLED
                and not config.PYRAMIDING_ENABLED
                and not p["partial_exit_done"] and p["contracts"] >= 2):
            hit_15r = (bar["high"] >= target_15r if is_long
                       else bar["low"] <= target_15r)
            if hit_15r:
                half     = p["contracts"] // 2
                pe_price = target_15r
                pts      = (pe_price - entry) if is_long else (entry - pe_price)
                pnl      = (pts * config.POINT_VALUE * half
                            - config.COMMISSION_PER_SIDE * 2 * half)
                self.bank.record_trade(pnl, {
                    "date": str(ts.date()), "dir": p["dir"], "mode": p["mode"],
                    "result": "partial_1.5R", "contracts": half,
                    "points": round(pts, 2), "base_pnl": round(pnl, 2),
                    "pyramid_level": 0,
                }, count_as_new=False)
                p["contracts"]         -= half
                p["partial_exit_done"]  = True
                p["stop"]               = entry
                if p["contracts"] == 0:
                    self.open_position = None
                return

        # 4. Full target exit (2R baseline; extended to 3R when pyramiding fires)
        if exit_price is None:
            if is_long and bar["high"] >= p["target"]:
                exit_price, result = p["target"], "target"
            elif not is_long and bar["low"] <= p["target"]:
                exit_price, result = p["target"], "target"

        if force and exit_price is None:
            exit_price, result = bar["close"], "flatten"
        if exit_price is None:
            return

        # Calculate PnL: base contracts + any pyramid add-ons
        base_pts = (exit_price - entry) if is_long else (entry - exit_price)
        base_pnl = base_pts * config.POINT_VALUE * p["contracts"]
        pyr_pnl  = 0.0
        total_pyr_c = 0
        for c, fill in p.get("pyramid_entries", []):
            pts = (exit_price - fill) if is_long else (fill - exit_price)
            pyr_pnl  += pts * config.POINT_VALUE * c
            total_pyr_c += c
        total_contracts = p["contracts"] + total_pyr_c
        pnl = (base_pnl + pyr_pnl
               - config.COMMISSION_PER_SIDE * 2 * total_contracts)

        entry_t = p.get("entry_time")
        entry_time_str = entry_t.strftime("%H:%M") if entry_t else ""
        self.bank.record_trade(pnl, {
            "date": str(ts.date()), "dir": p["dir"], "mode": p["mode"],
            "result": result, "contracts": p["contracts"],
            "points": round(base_pts, 2), "base_pnl": round(base_pnl, 2),
            "pyramid_level": p.get("pyramid_level", 0),
            "total_contracts": total_contracts,
            "entry_time": entry_time_str,
        })
        self.open_position = None

        # Arm one re-entry if first trade hit target and time window allows
        if (result == "target"
                and config.SECOND_BREAKOUT_ENABLED
                and self.strategy.reentry_count < 1
                and ts.time() < time(*map(int, config.LAST_ENTRY_TIME.split(":")))):
            self.strategy.reentry_count += 1
            self.strategy.traded_today = False  # let check_breakout fire again

    # ── London position lifecycle ────────────────────────────────────────────
    def try_enter_london(self, sig, ts):
        ok, _ = self.bank.can_trade()
        if not ok:
            return
        # London RR is fixed at 1.5 by design — bypass MIN_RR gate,
        # size directly from risk budget.
        risk_points = abs(sig.entry - sig.stop)
        risk_dollars = self.bank.s.balance * config.RISK_PER_TRADE_PCT
        dd = (self.bank.s.peak_balance - self.bank.s.balance) / self.bank.s.peak_balance
        if dd >= config.RECOVERY_MODE_TRIGGER_PCT:
            risk_dollars *= config.RECOVERY_SIZE_MULTIPLIER
        per_contract = risk_points * config.POINT_VALUE
        contracts = min(max(int(risk_dollars // per_contract), 1),
                        config.MAX_CONTRACTS)
        if per_contract > risk_dollars * 3:
            return
        fill = sig.entry + SLIP if sig.direction == "long" else sig.entry - SLIP
        self.london_position = {
            "dir": sig.direction, "entry": fill, "stop": sig.stop,
            "target": sig.target, "contracts": contracts,
            "mode": "london", "entry_time": ts,
        }
        self.london.traded_today = True

    def check_london_exit(self, bar, ts, force=False):
        p = self.london_position
        if p is None:
            return
        exit_price = result = None

        if p["dir"] == "long":
            if bar["low"] <= p["stop"]:
                exit_price, result = p["stop"] - SLIP, "stop"
            elif bar["high"] >= p["target"]:
                exit_price, result = p["target"], "target"
        else:
            if bar["high"] >= p["stop"]:
                exit_price, result = p["stop"] + SLIP, "stop"
            elif bar["low"] <= p["target"]:
                exit_price, result = p["target"], "target"

        if force and exit_price is None:
            exit_price, result = bar["close"], "flatten"
        if exit_price is None:
            return

        points = (exit_price - p["entry"]) if p["dir"] == "long" else (p["entry"] - exit_price)
        pnl = points * config.POINT_VALUE * p["contracts"]
        pnl -= config.COMMISSION_PER_SIDE * 2 * p["contracts"]
        self.bank.record_trade(pnl, {
            "date": str(ts.date()), "dir": p["dir"], "mode": "london",
            "result": result, "contracts": p["contracts"], "points": round(points, 2),
        })
        self.london_position = None

    # ── Asia position lifecycle ──────────────────────────────────────────────
    def try_enter_asia(self, sig, ts):
        if config.EVAL_MODE:
            return
        ok, _ = self.bank.can_trade()
        if not ok:
            return
        fill = sig.entry + SLIP if sig.direction == "long" else sig.entry - SLIP
        self.asia_position = {
            "dir": sig.direction, "entry": fill, "stop": sig.stop,
            "target": sig.target, "contracts": 1,
            "mode": "asia_gap", "entry_time": ts,
        }
        self.asia.traded_today = True

    def check_exit_asia(self, bar, ts, force=False):
        p = self.asia_position
        if p is None:
            return
        exit_price = result = None

        if p["dir"] == "long":
            if bar["low"] <= p["stop"]:
                exit_price, result = p["stop"] - SLIP, "stop"
            elif bar["high"] >= p["target"]:
                exit_price, result = p["target"], "target"
        else:
            if bar["high"] >= p["stop"]:
                exit_price, result = p["stop"] + SLIP, "stop"
            elif bar["low"] <= p["target"]:
                exit_price, result = p["target"], "target"

        if force and exit_price is None:
            exit_price, result = bar["close"], "flatten"
        if exit_price is None:
            return

        points = (exit_price - p["entry"]) if p["dir"] == "long" else (p["entry"] - exit_price)
        pnl = points * config.POINT_VALUE * p["contracts"]
        pnl -= config.COMMISSION_PER_SIDE * 2 * p["contracts"]
        self.bank.record_trade(pnl, {
            "date": str(ts.date()), "dir": p["dir"], "mode": "asia_gap",
            "result": result, "contracts": p["contracts"], "points": round(points, 2),
        })
        self.asia_position = None

    # ── Main loop ────────────────────────────────────────────────────────────
    def run(self, bars, *, silent=False):
        current_day = None
        for bar in bars:
            ts = bar["timestamp"]
            t  = ts.time()

            # Decide which windows this bar belongs to
            in_london = config.LONDON_ENABLED and LONDON_START <= t <= LONDON_EXIT
            in_orb    = SESSION_START <= t <= time(16, 0)
            in_asia   = config.ASIA_ENABLED and ASIA_OPEN_BAR <= t <= ASIA_HARD_EXIT

            if not in_london and not in_orb and not in_asia:
                continue

            # ── Day rollover (triggers on US session date change only) ────────
            if ts.date() != current_day:
                if current_day is not None:
                    if self.day_high is not None:
                        self.regime.record_day(self.day_high, self.day_low)
                    # Save OR volume for signal strength lookback (before reset)
                    if self.strategy.or_complete and self.strategy.or_volume > 0:
                        self.or_volume_history.append(self.strategy.or_volume)
                        if len(self.or_volume_history) > 20:
                            self.or_volume_history.pop(0)
                    self.prev_day_mode = self.day_mode
                current_day = ts.date()
                self.strategy.reset_day(prev_close=self._last_close,
                                        day_of_week=ts.weekday())
                self.london.reset_day()
                self.asia.reset_session()
                self.day_mode  = "pending"
                self.day_high  = self.day_low = None
                self.bank.on_new_bar_date(current_day)
                # Month skip gate (SKIP_MONTHS list in config)
                skip_months = getattr(config, "SKIP_MONTHS", [])
                if skip_months and current_day.month in skip_months:
                    self.day_mode = "skip"

            # ── Asia window (18:00-21:00) ────────────────────────────────────
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

            # ── London window (8:00-9:25) ────────────────────────────────────
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

            # ── ORB session (9:30-16:00) ─────────────────────────────────────
            self.day_high = (bar["high"] if self.day_high is None
                             else max(self.day_high, bar["high"]))
            self.day_low  = (bar["low"]  if self.day_low  is None
                             else min(self.day_low,  bar["low"]))
            self._last_close = bar["close"]

            # Record 4pm close for Asia halt-gap reference
            if config.ASIA_ENABLED and t == time(16, 0):
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

            if self.open_position is None:
                sig = None
                if t <= time(*map(int, config.LAST_ENTRY_TIME.split(":"))):
                    # AM session: breakout / VWAP pullback / gap fill
                    if self.day_mode == "breakout":
                        if not self.strategy.traded_today:
                            # First entry, or armed re-entry (after target hit)
                            is_reentry = self.strategy.reentry_count >= 1
                            if not is_reentry or t >= SECOND_BREAKOUT_AFTER:
                                sig = self.strategy.check_breakout(bar)
                        elif (config.VWAP_PULLBACK_ENABLED
                              and not self.strategy.second_trade_today):
                            sig = self.strategy.check_vwap_pullback(bar)
                    elif self.day_mode == "fade":
                        sig = self.strategy.check_fade(bar)
                    # Gap fill: eligible on any tradeable day within entry window
                    if sig is None and config.GAP_FILL_ENABLED and t <= GAP_FILL_LAST:
                        if not self.strategy.traded_today:
                            sig = self.strategy.check_gap_fill(bar)
                elif (config.PM_VWAP_ENABLED
                      and PM_VWAP_START <= t <= PM_VWAP_LAST
                      and not self.strategy.pm_trade_today):
                    # PM session: VWAP continuation
                    sig = self.strategy.check_pm_vwap(bar)
                    if sig:
                        self.strategy.pm_trade_today = True
                if sig:
                    self.try_enter(sig, ts)

        if self.day_high is not None:
            self.regime.record_day(self.day_high, self.day_low)

        if not silent:
            self.report()

    # ── Results ──────────────────────────────────────────────────────────────
    def report(self):
        log = self.bank.trade_log
        if not log:
            print("No trades taken.")
            return

        def _section_stats(trades, label):
            if not trades:
                return
            wins   = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] <= 0]
            gw = sum(t["pnl"] for t in wins)
            gl = abs(sum(t["pnl"] for t in losses))
            pf = gw / gl if gl else float("inf")
            net = sum(t["pnl"] for t in trades)
            wr  = len(wins) / len(trades)
            aw  = gw / len(wins)   if wins   else 0
            al  = gl / len(losses) if losses else 0
            print(f"  [{label}]")
            print(f"  Trades: {len(trades):>4}  |  Win rate: {wr:.1%}  |  PF: {pf:.2f}")
            print(f"  Net P&L:   ${net:>+10,.0f}  |  Avg win: ${aw:,.0f}  |  Avg loss: $-{al:,.0f}")

        london_trades = [t for t in log if t["mode"] == "london"]
        asia_trades   = [t for t in log if t["mode"] == "asia_gap"]
        orb_trades    = [t for t in log if t["mode"] not in ("london", "asia_gap")]

        wins   = [t for t in log if t["pnl"] > 0]
        losses = [t for t in log if t["pnl"] <= 0]
        gw     = sum(t["pnl"] for t in wins)
        gl     = abs(sum(t["pnl"] for t in losses))
        peak, max_dd = config.STARTING_BALANCE, 0.0
        for t in log:
            peak   = max(peak, t["balance"])
            max_dd = max(max_dd, peak - t["balance"])

        sep = "=" * 60
        print(sep)
        print(f"  BACKTEST RESULTS - {config.SYMBOL}")
        print(sep)

        if london_trades:
            _section_stats(london_trades, "LONDON/NY OVERLAP")
            print()

        if orb_trades:
            _section_stats(orb_trades, "ORB STRATEGIES")
            by_mode = {}
            for t in orb_trades:
                by_mode.setdefault(t["mode"], []).append(t["pnl"])
            for mode, pnls in by_mode.items():
                print(f"    {mode:>12}: {len(pnls):>4} trades, ${sum(pnls):>+10,.0f}")
            print()

        if asia_trades:
            _section_stats(asia_trades, "ASIA GAP (6-9 PM ET)")
            print()

        print("  [COMBINED]")
        print(f"  Trades: {len(log):>4}  |  Win rate: {len(wins)/len(log):.1%}"
              f"  |  PF: {gw/gl if gl else float('inf'):.2f}")
        print(f"  Net P&L:   ${self.bank.s.balance - config.STARTING_BALANCE:>+10,.0f}")
        print(f"  End balance: ${self.bank.s.balance:>10,.0f}")
        print(f"  Max drawdown: ${max_dd:>9,.0f}")
        if self.bank.s.halted_permanently:
            print(f"  !! HALTED: {self.bank.s.halt_reason}")

        # ── Pyramid analysis ─────────────────────────────────────────────────
        if config.PYRAMIDING_ENABLED and wins:
            pyr1 = sum(1 for t in log if t.get("pyramid_level", 0) >= 1)
            pyr2 = sum(1 for t in log if t.get("pyramid_level", 0) >= 2)
            wins_sorted = sorted(wins, key=lambda t: t["pnl"], reverse=True)
            top_n = max(1, len(wins_sorted) // 5)   # top 20%
            top20 = wins_sorted[:top_n]
            top20_actual = sum(t["pnl"] for t in top20)
            top20_base   = sum(t.get("base_pnl", t["pnl"]) for t in top20)
            all_base_pnl = sum(t.get("base_pnl", t["pnl"]) for t in log)
            all_pyr_boost = sum(t["pnl"] for t in log) - all_base_pnl
            avg_win_actual = sum(t["pnl"] for t in wins) / len(wins)
            avg_win_base   = sum(t.get("base_pnl", t["pnl"]) for t in wins) / len(wins)
            print()
            print("  [PYRAMID ANALYSIS]")
            print(f"  L1 fired (1R add): {pyr1:>4} trades  |  L2 fired (2R add): {pyr2:>4} trades")
            print(f"  Avg win actual:  ${avg_win_actual:>8,.0f}  |  Avg win base: ${avg_win_base:>8,.0f}")
            print(f"  Total pyramid boost: ${all_pyr_boost:>+10,.0f}")
            print(f"  Top 20% wins ({top_n} trades): actual ${top20_actual:>+10,.0f}"
                  f"  vs base ${top20_base:>+10,.0f}"
                  f"  (+${top20_actual - top20_base:,.0f})")

        print(sep)


# ── Data loading ─────────────────────────────────────────────────────────────
def load_csv(path):
    bars = []
    with open(path) as f:
        for row in csv.DictReader(f):
            bars.append({
                "timestamp": datetime.fromisoformat(row["timestamp"]),
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": float(row.get("volume", 0)),
            })
    return bars


def synthetic_bars(days=60, seed=7):
    """Random-walk NQ-like data. ONLY for testing that the engine runs."""
    import random
    from datetime import timedelta
    random.seed(seed)
    bars = []
    price = 21000.0
    base_day = datetime(2026, 1, 5)
    for d in range(days):
        day_dt = base_day + timedelta(days=d)
        while day_dt.weekday() >= 5:
            day_dt += timedelta(days=1)
        trend = random.choice([-1, 0, 0, 1]) * random.uniform(0.5, 3.0)
        t = day_dt.replace(hour=8, minute=0)
        for _ in range(85):
            drift = trend * 0.03
            move  = random.gauss(drift, 2.5)
            o = price; c = price + move
            h = max(o, c) + abs(random.gauss(0, 1.5))
            l = min(o, c) - abs(random.gauss(0, 1.5))
            bars.append({"timestamp": t, "open": o, "high": h, "low": l,
                         "close": c, "volume": 500})
            price = c
            t += timedelta(minutes=1)
        t = day_dt.replace(hour=9, minute=30)
        for _ in range(390):
            drift = trend * 0.05
            move  = random.gauss(drift, 4.0)
            o = price; c = price + move
            h = max(o, c) + abs(random.gauss(0, 2))
            l = min(o, c) - abs(random.gauss(0, 2))
            bars.append({"timestamp": t, "open": o, "high": h, "low": l,
                         "close": c, "volume": 1000})
            price = c
            t += timedelta(minutes=1)
        price += random.gauss(0, 30)
    return bars


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--synthetic":
        data = load_csv(sys.argv[1])
        print(f"Loaded {len(data):,} bars from {sys.argv[1]}")
    else:
        print("Running SYNTHETIC smoke test (random data — results are meaningless,")
        print("this only proves the engine works end to end)\n")
        data = synthetic_bars()
    Backtester().run(data)
