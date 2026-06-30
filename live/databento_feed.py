"""
live/databento_feed.py

Real-time NQ/ES 1-minute bar stream via Databento WebSocket (GLBX.MDP3).
Bar is delivered the instant the minute closes — no polling, no delay.

Uses the same API key already in download_data.py.
Cost: ohlcv-1m for a single symbol is ~50KB/trading day — fractions of a cent.

Usage (internal — called by PaperTradingSession):
    from live.databento_feed import DatabentоLiveFeed
    feed = DatabentоLiveFeed("NQ")
    feed.connect()
    for bar in feed.stream():   # blocking; yields one bar per minute close
        ...
    feed.close()
"""

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("databento_feed")

ET = ZoneInfo("America/New_York")

SYMBOL_MAP = {
    "NQ":  "NQ.c.0",
    "ES":  "ES.c.0",
    "RTY": "RTY.c.0",
    "CL":  "CL.c.0",
}

# Databento fixed-point price scale (prices stored as int64 × 10^9)
_PRICE_SCALE = 1e9
# Sanity range: if the raw value looks like it's already a float price, skip scaling
_ALREADY_SCALED_MAX = 500_000.0


def _resolve_key() -> str:
    """API key from env first, fallback to download_data.py constant."""
    key = os.environ.get("DATABENTO_API_KEY", "")
    if key:
        return key
    try:
        import download_data as _dd
        return getattr(_dd, "API_KEY", "")
    except Exception:
        pass
    raise RuntimeError(
        "Databento API key not found. Set env var DATABENTO_API_KEY "
        "or ensure download_data.py with API_KEY is in the project root."
    )


def _scale_price(raw) -> float:
    """Convert Databento fixed-point price to float."""
    if isinstance(raw, float) and raw < _ALREADY_SCALED_MAX:
        return raw  # library already scaled it
    return float(raw) / _PRICE_SCALE


def _ts_to_et(ts_ns: int) -> datetime:
    """Convert nanosecond Unix timestamp to ET naive datetime."""
    ts_utc = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    return ts_utc.astimezone(ET).replace(tzinfo=None)


class DatabentоLiveFeed:
    """
    Event-driven Databento WebSocket feed for 1-minute OHLCV bars.
    Blocking iterator — yields one bar dict each time a minute closes.
    """

    feed_name = "Databento (real-time push)"

    def __init__(self, symbol: str = "NQ", api_key: str = ""):
        try:
            import databento as db
            self._db = db
        except ImportError:
            raise RuntimeError("databento not installed — run: pip install databento")

        self.symbol    = symbol.upper()
        self.db_symbol = SYMBOL_MAP.get(self.symbol, symbol)
        self.api_key   = api_key or _resolve_key()
        self._client   = None

    def connect(self):
        """Open WebSocket connection and subscribe to NQ 1-minute bars."""
        self._client = self._db.Live(key=self.api_key)
        self._client.subscribe(
            dataset="GLBX.MDP3",
            schema="ohlcv-1m",
            stype_in="continuous",
            symbols=[self.db_symbol],
        )
        log.info("Databento live stream connected — %s (%s)", self.symbol, self.db_symbol)

    def stream(self):
        """
        Generator — yields completed 1-min bar dicts.
        Blocks between bars (WebSocket push, no polling).
        Yields only records that match OhlcvMsg; skips heartbeats, metadata, etc.
        """
        if self._client is None:
            raise RuntimeError("Call connect() before stream()")

        for record in self._client:
            bar = self._to_bar(record)
            if bar is None:
                continue
            yield bar

    def _to_bar(self, record) -> dict | None:
        db = self._db
        # Only process 1-min OHLCV records
        if not hasattr(record, "open") or not hasattr(record, "ts_event"):
            return None

        try:
            ts_et = _ts_to_et(record.ts_event)
        except Exception:
            return None

        try:
            o = _scale_price(record.open)
            h = _scale_price(record.high)
            l = _scale_price(record.low)
            c = _scale_price(record.close)
            v = int(record.volume)
        except Exception as exc:
            log.warning("price parse error: %s — %s", exc, record)
            return None

        # Basic sanity check: NQ is roughly 10k–50k range
        if not (1_000 < c < 200_000):
            log.warning("suspicious close=%.2f — raw=%s", c, record.close)
            return None

        return {
            "timestamp": ts_et,
            "open":   o,
            "high":   h,
            "low":    l,
            "close":  c,
            "volume": v,
        }

    def close(self):
        if self._client:
            try:
                self._client.stop()
            except Exception:
                pass
            self._client = None
            log.info("Databento live stream closed")
