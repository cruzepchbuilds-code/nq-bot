"""
live/paper_trading.py

Paper trading session runner.
Fetches real-time 1-minute bars from Tradovate, runs the ORB strategy,
logs signals and simulated P&L, and optionally fires real orders.

Modes:
    paper  — strategy runs, no real orders (default)
    live   — strategy runs AND sends orders via ExecutionEngine

Usage:
    python live/paper_trading.py                 # paper mode, NQ
    python live/paper_trading.py --symbol ES     # paper mode, ES
    python live/paper_trading.py --live          # LIVE mode (real orders)
    python live/paper_trading.py --replay <csv>  # replay historical CSV

Environment:
    TRADOVATE_DEMO=1   (set in .env or shell)
    See live/execution.py for full credential variables.
"""

import sys
import os
import csv
import time
import logging
import argparse
from datetime import datetime, time as dtime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional — credentials can be set via shell env vars

import config
from strategies.strategy_us import ORBStrategy, Signal
from strategies.strategy_london import LondonStrategy
from strategies.strategy_asia import AsiaStrategy
from regime import RegimeDetector
from bankroll import BankrollManager
import signal_strength as ss
import live.telegram_alerts as tg
import live.discord_alerts as da

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

log = logging.getLogger("paper_trading")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("live/session.log"),
    ],
)

# ---------------------------------------------------------------------------
# Tradovate market data helpers
# ---------------------------------------------------------------------------

DEMO_MD_URL = "https://demo.tradovateapi.com/v1"
LIVE_MD_URL = "https://live.tradovateapi.com/v1"

CHART_DESCRIPTION = {
    "underlyingType": "MinuteBar",
    "elementSize": 1,
    "elementSizeUnit": "UnderlyingUnits",
    "withHistogram": False,
}


def _tradovate_bars(base_url: str, headers: dict, contract_id: int,
                    count: int = 5) -> list[dict]:
    """Fetch the last `count` completed 1-minute bars for contract_id."""
    payload = {
        "symbol": str(contract_id),
        "chartDescription": CHART_DESCRIPTION,
        "timeRange": {"closingBarsOfSessionByCount": count + 2},
    }
    try:
        r = requests.post(f"{base_url}/md/getchart",
                          headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("getchart error: %s", exc)
        return []

    bars = data.get("bars", [])
    result = []
    for b in bars[-count:]:
        ts_ms = b.get("timestamp", 0)
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        ts_et = ts.astimezone(tz=None)
        result.append({
            "timestamp": ts_et.replace(tzinfo=None),
            "open":   b.get("open",  0.0),
            "high":   b.get("high",  0.0),
            "low":    b.get("low",   0.0),
            "close":  b.get("close", 0.0),
            "volume": int(b.get("upVolume", 0) + b.get("downVolume", 0)),
        })
    return result


def _load_replay_csv(path: str) -> list[dict]:
    bars = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            bars.append({
                "timestamp": ts,
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": int(float(row.get("volume", 0))),
            })
    return bars


# ---------------------------------------------------------------------------
# Paper trade logger
# ---------------------------------------------------------------------------

class TradeLog:
    """Append trades to a CSV log file each session."""

    def __init__(self, path: str = "live/paper_trades.csv"):
        self.path = path
        self._ensure_header()

    def _ensure_header(self):
        if not os.path.exists(self.path):
            with open(self.path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "time", "symbol", "direction", "mode",
                             "contracts", "entry", "stop", "target",
                             "result", "exit_price", "points", "net_pnl"])

    def record(self, row: dict):
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerow([
                row.get("date", ""), row.get("time", ""),
                row.get("symbol", ""), row.get("direction", ""),
                row.get("mode", ""), row.get("contracts", 0),
                row.get("entry", ""), row.get("stop", ""),
                row.get("target", ""), row.get("result", ""),
                row.get("exit_price", ""), row.get("points", ""),
                row.get("net_pnl", ""),
            ])


# ---------------------------------------------------------------------------
# Paper position tracker
# ---------------------------------------------------------------------------

class PaperPosition:
    def __init__(self, symbol: str, direction: str, contracts: int,
                 entry: float, stop: float, target: float,
                 mode: str, entry_time: datetime):
        self.symbol     = symbol
        self.direction  = direction
        self.contracts  = contracts
        self.entry      = entry
        self.stop       = stop
        self.target     = target
        self.mode       = mode
        self.entry_time = entry_time

    def check_exit(self, bar: dict) -> Optional[tuple[str, float]]:
        """Check if bar touches stop or target. Returns (result, price) or None."""
        if self.direction == "long":
            if bar["low"] <= self.stop:
                return ("stop", self.stop)
            if bar["high"] >= self.target:
                return ("target", self.target)
        else:
            if bar["high"] >= self.stop:
                return ("stop", self.stop)
            if bar["low"] <= self.target:
                return ("target", self.target)
        return None


# ---------------------------------------------------------------------------
# Main session
# ---------------------------------------------------------------------------

class PaperTradingSession:

    def __init__(self, symbol: str = "NQ", live_orders: bool = False,
                 replay_bars: list = None, use_yfinance: bool = False,
                 use_databento: bool = True):
        self.symbol      = symbol
        self.live_orders = live_orders
        self.replay_bars = replay_bars
        self._yf_feed    = None
        self._db_feed    = None

        if not replay_bars:
            if use_databento:
                try:
                    from live.databento_feed import DatabentоLiveFeed
                    self._db_feed = DatabentоLiveFeed(symbol)
                    log.info("Databento live feed active — symbol=%s", symbol)
                except Exception as exc:
                    log.warning("Databento unavailable (%s) — falling back to yfinance (15-min delay)", exc)
                    tg.send("⚠️ <b>Databento feed failed</b> — falling back to yfinance (15-min delay). Check API key.")
                    use_yfinance = True

            if use_yfinance and not self._db_feed:
                from live.yfinance_feed import YFinanceFeed
                self._yf_feed = YFinanceFeed(symbol)
                log.warning("yfinance fallback active — 15-min delayed data, ORB edge may be impaired")

        self.strategy  = ORBStrategy()
        self.london    = LondonStrategy()
        self.asia      = AsiaStrategy()
        self.regime    = RegimeDetector()
        self.bank      = BankrollManager()
        self.trade_log = TradeLog()

        self.open_position:    Optional[PaperPosition] = None
        self.london_position:  Optional[PaperPosition] = None
        self.asia_position:    Optional[PaperPosition] = None

        self.or_volume_history: list[float] = []
        self.prev_day_mode: Optional[str]   = None
        self._last_close:    Optional[float] = None
        self._last_bar_close: Optional[float] = None  # updated every bar for flatten
        self.day_mode = "skip"

        self.execution = None
        if live_orders:
            from live.execution import ExecutionEngine
            self.execution = ExecutionEngine()

        # Config-driven time boundaries (recompute each session in case config swapped)
        self._or_end      = dtime(9, 30 + config.OPENING_RANGE_MINUTES)
        self._last_entry  = dtime(*map(int, config.LAST_ENTRY_TIME.split(":")))
        self._flatten     = dtime(*map(int, config.FLATTEN_TIME.split(":")))

        self._session_date = None
        self._session_pnl  = 0.0
        self._trade_count  = 0
        self._session_wins   = 0
        self._session_losses = 0
        self._last_halt_reason: Optional[str] = None

    def connect(self) -> bool:
        if self.replay_bars:
            log.info("REPLAY mode — %d bars loaded", len(self.replay_bars))
            return True
        if self._db_feed:
            try:
                self._db_feed.connect()
                log.info("Databento WebSocket connected — real-time bars, zero delay")
                return True
            except Exception as exc:
                log.error("Databento connect failed: %s", exc)
                return False
        if self._yf_feed:
            log.info("yfinance paper mode — %s (15-min delay fallback)", self.symbol)
            return True
        if not REQUESTS_OK:
            log.error("requests library not installed")
            return False
        if self.live_orders and self.execution:
            return self.execution.connect()
        log.info("Paper mode — market data via Tradovate polling")
        return True

    def run(self):
        if self.replay_bars:
            self._run_replay()
        elif self._db_feed:
            self._run_stream()
        else:
            self._run_live()

    def _run_stream(self):
        """Event-driven live session via Databento WebSocket. No polling — each bar
        arrives the instant the minute closes."""
        log.info("Starting Databento stream session [%s] ...", self.symbol)
        mode_label = "LIVE orders" if self.live_orders else "Paper (no orders)"
        tg.send_startup(self.symbol, "Databento push (real-time)", mode_label)

        if self.live_orders and self.execution:
            if not self.execution.connect():
                log.error("ExecutionEngine connect failed — aborting")
                tg.send_shutdown(self.symbol, datetime.now().date(), 0, 0.0, "ExecutionEngine failed to connect")
                return

        try:
            for bar in self._db_feed.stream():
                now_et = datetime.now()
                t = now_et.time()
                # Stay alive through Asia session (6pm-9pm ET); shut down at 9:15 PM
                eod = dtime(21, 15) if config.ASIA_ENABLED else dtime(16, 5)
                if t > eod:
                    log.info("Session complete — shutting down stream")
                    self._print_summary()
                    tg.send_shutdown(self.symbol, self._session_date,
                                     self._trade_count, self._session_pnl)
                    break
                self._process_bar(bar)
        except KeyboardInterrupt:
            log.info("Stream interrupted by user")
            self._print_summary()
            tg.send_shutdown(self.symbol, self._session_date,
                             self._trade_count, self._session_pnl, "manually stopped")
        finally:
            self._db_feed.close()

    def _run_live(self):
        """Polling fallback (yfinance or Tradovate). Used only when Databento unavailable."""
        log.warning("Starting POLLING fallback session [%s] — Databento unavailable", self.symbol)
        tg.send_startup(self.symbol, "yfinance polling (15-min delay — DEGRADED)",
                        "Live orders" if self.live_orders else "Paper")

        if self.live_orders and self.execution:
            if not self.execution.connect():
                log.error("ExecutionEngine connect failed — aborting")
                tg.send_shutdown(self.symbol, datetime.now().date(), 0, 0.0, "ExecutionEngine failed to connect")
                return

        while True:
            now_et = datetime.now()
            t = now_et.time()

            if t < dtime(8, 0):
                log.info("Pre-market — sleeping 60s")
                time.sleep(60)
                continue
            eod = dtime(21, 15) if config.ASIA_ENABLED else dtime(16, 5)
            if t > eod:
                log.info("Session complete — shutting down")
                self._print_summary()
                tg.send_shutdown(self.symbol, self._session_date,
                                 self._trade_count, self._session_pnl)
                break

            if self._yf_feed:
                bars = self._yf_feed.poll()
            else:
                bars = self._fetch_bars(count=3)

            for bar in bars:
                self._process_bar(bar)

            time.sleep(15)

    def _run_replay(self):
        log.info("Starting REPLAY [%s] — %d bars", self.symbol, len(self.replay_bars))
        for bar in self.replay_bars:
            self._process_bar(bar)
        self._print_summary()

    def _fetch_bars(self, count: int = 3) -> list[dict]:
        if not self.execution or not self.execution.auth:
            return []
        demo = os.getenv("TRADOVATE_DEMO", "1") == "1"
        base = DEMO_MD_URL if demo else LIVE_MD_URL
        contract = self.execution.client.find_contract(self.symbol)
        if not contract:
            return []
        return _tradovate_bars(
            base, self.execution.auth.headers, contract["id"], count)

    # ── Core bar processor ───────────────────────────────────────────────────

    def _process_bar(self, bar: dict):
        ts = bar["timestamp"]
        t  = ts.time()

        # Asia session (18:00-21:00 ET, same calendar day as US session)
        if config.ASIA_ENABLED and dtime(18, 0) <= t <= dtime(21, 0):
            if self._session_date == ts.date():
                self._process_asia_bar(bar, ts)
            return

        # Day rollover
        bar_date = ts.date()
        if self._session_date != bar_date:
            self._end_day(ts)
            self._start_day(ts, bar)
            return

        # London window (8:00-9:25 ET) — process then return if pre-ORB
        if config.LONDON_ENABLED and dtime(8, 0) <= t <= dtime(9, 25):
            self._process_london_bar(bar, ts)
            if t < dtime(9, 30):
                return

        if t < dtime(9, 30):
            return  # ignore pre-ORB non-London bars

        # Track last known close for forced flattens
        self._last_bar_close = bar["close"]

        # Record 4pm close for Asia halt-gap reference (must happen before flatten)
        if config.ASIA_ENABLED and t == dtime(16, 0):
            self.asia.record_us_close(bar)

        # VWAP accumulates across the full ORB session (mirrors backtest.py)
        self.strategy._update_vwap(bar)

        # OR building phase: 9:30 up to (not including) OR_END
        if t < self._or_end:
            self.strategy.update_opening_range(bar)
            return

        # First bar at OR_END: finalize range and classify regime
        if self.day_mode == "pending":
            tradeable = self.strategy.finalize_range()
            if not tradeable:
                self.day_mode = "skip"
            else:
                self.day_mode = self.regime.classify(self.strategy.range_size)
            log.info("OR close: range=%.1fpt  mode=%s",
                     self.strategy.range_size, self.day_mode)

        # Check existing position exits every bar
        self._check_position_exits(bar, ts)

        # Hard flatten at session end
        if t >= self._flatten:
            self._flatten_all(ts, "flatten_time")
            return

        # ORB entry check (breakout mode, entry window, no existing position)
        if (t <= self._last_entry
                and self.day_mode == "breakout"
                and not self.open_position):
            self._try_orb_entry(bar, ts)

    # ── Risk alerts ──────────────────────────────────────────────────────────

    def _check_halt_alert(self):
        """Post to #risk-alerts the moment BankrollManager halts trading
        (new halt only — avoids spamming the channel every bar/day)."""
        ok, reason = self.bank.can_trade()
        if not ok and reason != self._last_halt_reason:
            da.post_risk_alert(reason, symbol=self.symbol)
        self._last_halt_reason = None if ok else reason

    # ── Day lifecycle ────────────────────────────────────────────────────────

    def _start_day(self, ts: datetime, bar: dict):
        self._session_date = ts.date()
        dow = ts.weekday()

        if self._yf_feed:
            self._yf_feed.reset_day()
        self.strategy.reset_day(prev_close=self._last_close, day_of_week=dow)
        self.london.reset_day()
        self.asia.reset_session()
        self.bank.on_new_bar_date(self._session_date)  # correct BankrollManager API
        self.open_position   = None
        self.london_position = None
        self.asia_position   = None
        self._check_halt_alert()

        # Skip filters applied at day start (SKIP_MONTHS and SKIP_MONDAYS)
        skip_months = getattr(config, "SKIP_MONTHS", [])
        if config.SKIP_MONDAYS and dow == 0:
            self.day_mode = "skip"
        elif skip_months and ts.month in skip_months:
            self.day_mode = "skip"
        else:
            self.day_mode = "pending"  # finalize_range() will set "breakout" or "skip"

        log.info("=== %s [%s] DOW=%d mode=%s",
                 self._session_date, self.symbol, dow, self.day_mode)

    def _end_day(self, ts: datetime):
        if self._session_date:
            self.prev_day_mode = self.day_mode
            if self.strategy.or_complete and self.strategy.or_volume > 0:
                self.or_volume_history.append(self.strategy.or_volume)
                if len(self.or_volume_history) > 20:
                    self.or_volume_history.pop(0)
            self.regime.record_day(
                self.strategy.or_high or 0,
                self.strategy.or_low  or 0,
            )
        self._session_date = None

    # ── ORB entry ────────────────────────────────────────────────────────────

    def _try_orb_entry(self, bar: dict, ts: datetime):
        # day_mode == "breakout" and or_complete are guaranteed by _process_bar
        ok, _ = self.bank.can_trade()
        if not ok:
            return

        sig = self.strategy.check_breakout(bar)
        if not sig:
            return

        if bar["volume"] < config.BREAKOUT_MIN_VOLUME:
            return

        # Signal strength scoring (mirrors backtest.py try_enter exactly)
        avg_or_vol = (sum(self.or_volume_history[-20:]) / len(self.or_volume_history[-20:])
                      if self.or_volume_history else 0.0)
        vol_ratio = (self.strategy.or_volume / avg_or_vol if avg_or_vol > 0 else 1.0)

        # Volume ceiling gate
        if (config.BREAKOUT_MAX_OR_VOLUME_RATIO > 0
                and avg_or_vol > 0
                and vol_ratio > config.BREAKOUT_MAX_OR_VOLUME_RATIO):
            return

        gap_dir     = self.strategy._gap_direction()
        trade_sign  = 1 if sig.direction == "long" else -1
        gap_aligned = gap_dir * trade_sign

        score = ss.score_signal(
            entry_time=ts.time(),
            gap_aligned_with_direction=gap_aligned,
            vol_ratio=vol_ratio,
            or_size=self.strategy.range_size,
            prev_day_breakout=(self.prev_day_mode == "breakout"),
        )

        # High-gap threshold: use actual gap magnitude (mirrors backtest.py)
        if self.strategy.prev_close is not None and self.strategy.or_high is not None:
            or_mid = (self.strategy.or_high + self.strategy.or_low) / 2
            gap_magnitude = abs(or_mid - self.strategy.prev_close)
        else:
            gap_magnitude = 0.0
        min_score = (config.SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP
                     if gap_magnitude >= config.HIGH_GAP_THRESHOLD
                     else config.SIGNAL_STRENGTH_MIN_SCORE)

        # Contract sizing: matches backtest — low-score signals get 1 contract baseline
        month = ts.month
        if month in config.STRONG_MONTHS:
            max_c = min(3, config.MAX_CONTRACTS + 1)
        elif month in config.WEAK_MONTHS:
            max_c = 1
        else:
            max_c = config.MAX_CONTRACTS
        if config.EVAL_MODE:
            max_c = 1

        if score >= min_score:
            contracts = ss.contracts_for_score(score, max_c)
        else:
            contracts = 1  # baseline: always allow 1 contract (matches backtest.py)

        contracts = min(contracts, max_c)
        if contracts <= 0:
            return  # score too low even for baseline (shouldn't happen with =1 fallback)

        self._open_paper_position(sig, contracts, ts, "ORB", score=score)

    # ── Position management ──────────────────────────────────────────────────

    def _open_paper_position(self, sig: Signal, contracts: int, ts: datetime, src: str,
                              score: int = 0):
        slip = config.SLIPPAGE_TICKS * config.TICK_SIZE
        fill = sig.entry + slip if sig.direction == "long" else sig.entry - slip

        pos = PaperPosition(
            symbol=self.symbol, direction=sig.direction, contracts=contracts,
            entry=fill, stop=sig.stop, target=sig.target,
            mode=sig.mode, entry_time=ts,
        )

        if src == "ORB":
            self.open_position = pos
        elif src == "ASIA":
            self.asia_position = pos
        else:
            self.london_position = pos

        msg = (f"[PAPER] ENTER {sig.direction.upper()} {self.symbol} ×{contracts} "
               f"@ {fill:.2f}  stop={sig.stop:.2f}  target={sig.target:.2f}  [{sig.mode}]")
        log.info(msg)
        tg.send_entry(self.symbol, sig.direction, contracts, fill,
                      sig.stop, sig.target, score, sig.mode, ts)
        da.post_signal(self.symbol, sig.direction, fill, sig.stop, sig.target,
                       contracts, score, sig.mode, ts)

        if self.live_orders and self.execution:
            self.execution.enter(self.symbol, sig.direction, contracts,
                                 stop_pts=abs(fill - sig.stop),
                                 target_pts=abs(sig.target - fill))

    def _check_position_exits(self, bar: dict, ts: datetime):
        for pos, label in [(self.open_position, "ORB"),
                           (self.london_position, "LDN"),
                           (self.asia_position, "ASIA")]:
            if not pos:
                continue
            exit_info = pos.check_exit(bar)
            if exit_info:
                result, exit_price = exit_info
                self._close_paper_position(pos, exit_price, result, ts, label)

    def _close_paper_position(self, pos: PaperPosition, exit_price: float,
                               result: str, ts: datetime, label: str):
        slip = config.SLIPPAGE_TICKS * config.TICK_SIZE
        slip_adj = slip if result == "stop" else 0.0
        if pos.direction == "long":
            pts = (exit_price - pos.entry) - slip_adj
        else:
            pts = (pos.entry - exit_price) - slip_adj

        gross_pnl = pts * config.POINT_VALUE * pos.contracts
        comm      = config.COMMISSION_PER_SIDE * 2 * pos.contracts
        net_pnl   = gross_pnl - comm

        self._session_pnl += net_pnl
        self._trade_count += 1
        self._last_close   = exit_price
        if net_pnl > 0:
            self._session_wins += 1
        elif net_pnl < 0:
            self._session_losses += 1

        emoji = "✅" if net_pnl > 0 else "❌"
        msg = (f"[PAPER] {emoji} {result.upper()} {pos.direction.upper()} {self.symbol} "
               f"×{pos.contracts} @ {exit_price:.2f}  "
               f"pts={pts:+.1f}  net=${net_pnl:+,.0f}  session=${self._session_pnl:+,.0f}")
        log.info(msg)
        tg.send_exit(self.symbol, pos.direction, pos.contracts, pos.entry,
                     exit_price, pts, net_pnl, result, pos.mode,
                     self._session_pnl, ts)
        da.post_trade_close(self.symbol, pos.direction, pos.contracts, pos.entry,
                            exit_price, pts, net_pnl, result, pos.mode,
                            self._session_pnl, ts)

        self.trade_log.record({
            "date": str(ts.date()), "time": ts.strftime("%H:%M"),
            "symbol": pos.symbol, "direction": pos.direction,
            "mode": pos.mode, "contracts": pos.contracts,
            "entry": round(pos.entry, 2), "stop": round(pos.stop, 2),
            "target": round(pos.target, 2), "result": result,
            "exit_price": round(exit_price, 2),
            "points": round(pts, 2), "net_pnl": round(net_pnl, 2),
        })

        self.bank.record_trade(net_pnl, {
            "date": str(ts.date()), "dir": pos.direction, "mode": pos.mode,
            "result": result, "contracts": pos.contracts,
            "points": round(pts, 2), "base_pnl": round(gross_pnl, 2),
            "pyramid_level": 0, "total_contracts": pos.contracts,
            "entry_time": pos.entry_time.strftime("%H:%M"),
        })
        self._check_halt_alert()

        if label == "ORB":
            self.open_position = None
        elif label == "LDN":
            self.london_position = None
        else:
            self.asia_position = None

        if self.live_orders and self.execution:
            self.execution.on_fill(exit_price, result)

    def _flatten_all(self, ts: datetime, reason: str):
        exit_price = self._last_bar_close or 0.0
        for pos, label in [(self.open_position, "ORB"),
                           (self.london_position, "LDN"),
                           (self.asia_position, "ASIA")]:
            if pos:
                self._close_paper_position(pos, exit_price, reason, ts, label)
        if self.live_orders and self.execution:
            self.execution.flatten_all(reason)

    # ── London session ───────────────────────────────────────────────────────

    def _process_london_bar(self, bar: dict, ts: datetime):
        if not config.LONDON_ENABLED:
            return
        t = ts.time()

        # Force-flatten London position at hard exit time (9:25 ET)
        if self.london_position:
            exit_info = self.london_position.check_exit(bar)
            if exit_info:
                result, price = exit_info
                self._close_paper_position(self.london_position, price, result, ts, "LDN")
            elif t >= dtime(9, 25):
                self._close_paper_position(
                    self.london_position, bar["close"], "flatten", ts, "LDN")
            return

        # Build range 8:00-8:59, classify at 9:00, enter at 9:05
        if t < dtime(9, 0):
            self.london.update_range(bar)
        elif t == dtime(9, 0):
            self.london.classify_at_nine(bar)
        elif t == dtime(9, 5) and getattr(self.london, "signal_pending", False):
            sig = self.london.check_entry(bar)
            if sig and not self.london_position:
                ok, _ = self.bank.can_trade()
                if ok:
                    self._open_paper_position(sig, 1, ts, "LDN")

    # ── Asia session ─────────────────────────────────────────────────────────

    def _process_asia_bar(self, bar: dict, ts: datetime):
        t = ts.time()

        # Manage open Asia position (stop/target/hard-exit)
        if self.asia_position:
            exit_info = self.asia_position.check_exit(bar)
            if exit_info:
                result, price = exit_info
                self._close_paper_position(self.asia_position, price, result, ts, "ASIA")
            elif t >= dtime(21, 0):
                self._close_paper_position(
                    self.asia_position, bar["close"], "flatten", ts, "ASIA")
            return

        if self.asia.traded_today or config.EVAL_MODE:
            return

        ok, _ = self.bank.can_trade()
        if not ok:
            return

        if t == dtime(18, 0):
            self.asia.classify_at_open(bar, ts.month, ts.weekday())
        elif t == dtime(18, 15) and getattr(self.asia, "entry_pending", False):
            sig = self.asia.check_entry(bar)
            if sig:
                self._open_paper_position(sig, 1, ts, "ASIA")
                self.asia.traded_today = True

    # ── Reporting ────────────────────────────────────────────────────────────

    def _print_summary(self):
        print(f"\n{'═'*52}")
        print(f"  Session Summary [{self.symbol}]  {self._session_date}")
        print(f"{'─'*52}")
        print(f"  Trades    : {self._trade_count}")
        print(f"  Net P&L   : ${self._session_pnl:+,.0f}")
        print(f"  Bank Bal  : ${self.bank.s.balance:,.0f}")
        print(f"{'═'*52}\n")

        tg.send_daily_summary(self.symbol, self._session_date or "session",
                              self._trade_count, self._session_wins, self._session_losses,
                              self._session_pnl, self.bank.s.balance)
        da.post_daily_pnl(self.symbol, self._session_date or "session",
                          self._trade_count, self._session_wins, self._session_losses,
                          self._session_pnl, self.bank.s.balance)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CruzCapital Paper Trader")
    parser.add_argument("--symbol",    default="NQ", choices=["NQ", "ES", "RTY"])
    parser.add_argument("--live",      action="store_true", help="Send real orders")
    parser.add_argument("--replay",    metavar="CSV", help="Replay historical CSV file")
    parser.add_argument("--yfinance",    action="store_true",
                        help="Force yfinance fallback feed (~15-min delay)")
    parser.add_argument("--no-databento", action="store_true",
                        help="Disable Databento live stream (use for testing without API)")
    args = parser.parse_args()

    if args.symbol == "ES":
        try:
            import es_config as ec
            for attr in dir(ec):
                if not attr.startswith("_"):
                    setattr(config, attr, getattr(ec, attr))
            log.info("ES config loaded")
        except ImportError:
            log.warning("es_config.py not found — using default config")

    replay_bars = None
    if args.replay:
        replay_bars = _load_replay_csv(args.replay)
        log.info("Replay: %d bars from %s", len(replay_bars), args.replay)

    session = PaperTradingSession(
        symbol=args.symbol,
        live_orders=args.live,
        replay_bars=replay_bars,
        use_yfinance=args.yfinance,
        use_databento=not args.no_databento,
    )

    if not session.connect():
        log.error("Failed to connect — exiting")
        sys.exit(1)

    log.info("Paper trading session started  symbol=%s  live_orders=%s",
             args.symbol, args.live)

    try:
        session.run()
    except KeyboardInterrupt:
        log.info("Session interrupted by user")
        session._print_summary()


if __name__ == "__main__":
    main()
