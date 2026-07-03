"""
crypto/paper_trade.py

Live paper trading engine for BTC strategies.
Connects to Binance WebSocket for real-time 1-min bars.
Simulates fills locally — no real orders ever sent.

Usage:
    pip install ccxt websocket-client
    python crypto/paper_trade.py

Set LIVE_MODE = True + add API keys to go real (after strategy validates).
"""

import json, time, csv, os
from datetime import datetime, timezone
from threading import Lock

try:
    import websocket
except ImportError:
    raise SystemExit("Run: pip install websocket-client")

# ── config ───────────────────────────────────────────────────────────────────
LIVE_MODE       = False          # False = paper only; True = real orders (future)
POSITION_USD    = 10_000.0
COST_PCT        = 0.0010
LOG_PATH        = "crypto/data/paper_trades.csv"
STRATEGY        = "london_bo"    # which strategy to run live

BINANCE_WS      = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"

# ── state ────────────────────────────────────────────────────────────────────
lock        = Lock()
position    = None   # {"entry": px, "stop": px, "target": px, "is_long": bool, "time": dt}
closed_trades = []
lifetime_pnl  = 0.0

# ── logging ──────────────────────────────────────────────────────────────────
def init_log():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    if not os.path.exists(LOG_PATH):
        with open(LOG_PATH, "w", newline="") as f:
            csv.writer(f).writerow(
                ["timestamp", "strategy", "direction", "entry", "exit",
                 "pnl_pct", "net_usd", "outcome", "lifetime_pnl"])


def log_trade(strategy, direction, entry, exit_px, outcome):
    global lifetime_pnl
    pnl_pct = (exit_px - entry) / entry if direction == "L" else (entry - exit_px) / entry
    net     = pnl_pct * POSITION_USD - COST_PCT * POSITION_USD
    lifetime_pnl += net
    row = [datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
           strategy, direction, f"{entry:.2f}", f"{exit_px:.2f}",
           f"{pnl_pct:.4f}", f"{net:.2f}", outcome, f"{lifetime_pnl:.2f}"]
    with open(LOG_PATH, "a", newline="") as f:
        csv.writer(f).writerow(row)
    sign = "+" if net >= 0 else ""
    print(f"  [{outcome.upper():7}] {direction} {entry:.0f}→{exit_px:.0f}  "
          f"net={sign}{net:.0f}  lifetime={lifetime_pnl:+.0f}")

# ── strategy: London Open Breakout (example) ─────────────────────────────────
# Replace this class with whichever strategy wins in sweep.py

class LondonBOStrategy:
    def __init__(self):
        self.range_bars = []
        self.hi = self.lo = None
        self.traded_today = False

    def on_bar(self, bar, position_ref):
        dt = bar["dt"]
        hour, minute = dt.hour, dt.minute

        # Reset at midnight UTC
        if hour == 0 and minute == 0:
            self.range_bars = []
            self.hi = self.lo = None
            self.traded_today = False

        # Build range 05:00-08:00 UTC
        if 5 <= hour < 8:
            self.range_bars.append(bar)
            if self.range_bars:
                self.hi = max(b["high"] for b in self.range_bars)
                self.lo = min(b["low"]  for b in self.range_bars)

        # Entry window 08:00-10:00 UTC
        if 8 <= hour < 10 and self.hi and not self.traded_today:
            rng_pct = (self.hi - self.lo) / self.lo
            if not (0.005 <= rng_pct <= 0.03):
                return None

            close = bar["close"]
            if   close > self.hi * 1.002: is_long = True
            elif close < self.lo * 0.998: is_long = False
            else: return None

            self.traded_today = True
            entry  = close
            stop_p = entry * 0.99  if is_long else entry * 1.01
            tgt_p  = entry * 1.02  if is_long else entry * 0.98
            flatten_dt = dt.replace(hour=14, minute=0, second=0, microsecond=0)
            return {"entry": entry, "stop": stop_p, "target": tgt_p,
                    "is_long": is_long, "flatten_dt": flatten_dt}

        return None


# ── WebSocket handler ─────────────────────────────────────────────────────────

strategy = LondonBOStrategy()


def on_message(ws, msg):
    global position

    data = json.loads(msg)
    k    = data.get("k", {})
    if not k.get("x"):  # only process closed candles
        return

    bar = {
        "dt":     datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc),
        "open":   float(k["o"]),
        "high":   float(k["h"]),
        "low":    float(k["l"]),
        "close":  float(k["c"]),
        "volume": float(k["v"]),
    }

    with lock:
        # Check if open position should close
        if position:
            px     = bar["close"]
            is_lg  = position["is_long"]
            hit_stop   = (is_lg and bar["low"]  <= position["stop"]) or \
                         (not is_lg and bar["high"] >= position["stop"])
            hit_target = (is_lg and bar["high"] >= position["target"]) or \
                         (not is_lg and bar["low"] <= position["target"])
            flatten    = bar["dt"] >= position["flatten_dt"]

            if hit_stop:
                log_trade(STRATEGY, "L" if is_lg else "S",
                          position["entry"], position["stop"], "stop")
                position = None
            elif hit_target:
                log_trade(STRATEGY, "L" if is_lg else "S",
                          position["entry"], position["target"], "target")
                position = None
            elif flatten:
                log_trade(STRATEGY, "L" if is_lg else "S",
                          position["entry"], bar["open"], "flatten")
                position = None

        # Try to open new position
        if not position:
            sig = strategy.on_bar(bar, position)
            if sig:
                position = sig
                direction = "LONG" if sig["is_long"] else "SHORT"
                print(f"  [ENTRY] {direction} @ {sig['entry']:.2f}  "
                      f"stop={sig['stop']:.2f}  target={sig['target']:.2f}")


def on_error(ws, err):
    print(f"WS error: {err}")


def on_close(ws, *args):
    print("WebSocket closed — reconnecting in 5s...")
    time.sleep(5)
    run()


def on_open(ws):
    print(f"Connected to Binance · strategy={STRATEGY} · "
          f"{'LIVE' if LIVE_MODE else 'PAPER'} mode")


def run():
    ws = websocket.WebSocketApp(
        BINANCE_WS,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    ws.run_forever(ping_interval=30, ping_timeout=10)


if __name__ == "__main__":
    init_log()
    print(f"BTC Paper Trader — strategy: {STRATEGY}")
    print(f"Position size: ${POSITION_USD:,.0f}  |  Log: {LOG_PATH}")
    print("Press Ctrl+C to stop.\n")
    run()
