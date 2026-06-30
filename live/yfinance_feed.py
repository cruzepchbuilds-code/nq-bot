"""
live/yfinance_feed.py

Free NQ/ES 1-minute bar feed via yfinance (NQ=F, ES=F).
Drop-in replacement for the Tradovate polling feed in paper_trading.py.

Data is ~15-min delayed on the free yfinance tier for futures — acceptable
for ORB since all entries happen after 9:45 ET (OR closes at 9:45).

Usage (internal — called by PaperTradingSession):
    from live.yfinance_feed import YFinanceFeed
    feed = YFinanceFeed("NQ")
    bars = feed.poll()   # returns list of new completed bars since last call
"""

import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("yfinance_feed")

SYMBOL_MAP = {
    "NQ":  "NQ=F",
    "ES":  "ES=F",
    "RTY": "RTY=F",
    "CL":  "CL=F",
    "GC":  "GC=F",
}

ET = ZoneInfo("America/New_York")


class YFinanceFeed:
    """Polls yfinance for completed 1-minute bars. Deduplicates internally."""

    def __init__(self, symbol: str = "NQ"):
        try:
            import yfinance as yf
            self._yf = yf
        except ImportError:
            raise RuntimeError("yfinance not installed — run: pip install yfinance")

        self.yf_symbol   = SYMBOL_MAP.get(symbol.upper(), symbol)
        self.ticker      = self._yf.Ticker(self.yf_symbol)
        self._seen: set  = set()   # isoformat keys of bars already returned

    def poll(self, lookback_bars: int = 5) -> list[dict]:
        """
        Fetch the most recent 1-minute bars. Returns only NEW completed bars
        not previously returned. Filters out the current (incomplete) bar.
        """
        try:
            df = self.ticker.history(period="1d", interval="1m")
        except Exception as exc:
            log.warning("yfinance fetch error: %s", exc)
            return []

        if df.empty:
            return []

        now_et = datetime.now(ET)
        results = []

        # Work through recent bars only
        for ts, row in df.tail(lookback_bars).iterrows():
            # Normalise to ET naive datetime (mirrors backtest format)
            if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                ts_et = ts.astimezone(ET).replace(tzinfo=None)
            else:
                ts_et = ts.to_pydatetime().replace(tzinfo=None)

            # Skip bars from previous sessions (older than today ET)
            if ts_et.date() < now_et.date():
                continue

            # Skip the current incomplete bar (bar timestamp + 60s > now)
            bar_end = ts_et.replace(tzinfo=ET) + timedelta(seconds=60)
            if bar_end > now_et:
                continue

            key = ts_et.isoformat()
            if key in self._seen:
                continue

            self._seen.add(key)
            results.append({
                "timestamp": ts_et,
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": int(row.get("Volume", 0)),
            })

        return results

    def reset_day(self):
        """Clear seen-bar cache at session start."""
        self._seen.clear()
        log.info("YFinanceFeed: day reset, cache cleared")
