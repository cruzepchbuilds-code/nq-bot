"""
live/tradovate_feed.py

Real-time NQ/ES 1-minute bars via Tradovate REST polling.
Included free with any Tradovate account (Apex Trader Funding eval provides one).

Interface is identical to DatabentоLiveFeed: connect() → stream() → close()
paper_trading.py assigns this to self._db_feed as a drop-in replacement.

Latency: bars delivered ~5s after each minute closes (60s polling, aligned to
the minute boundary). Equivalent to Databento for ORB — decisions happen at bar
close anyway.

Environment:
    TRADOVATE_USERNAME   — Tradovate email
    TRADOVATE_PASSWORD   — Tradovate password
    TRADOVATE_CID        — integer CID from API Access page
    TRADOVATE_SECRET     — secret from API Access page
    TRADOVATE_APP_ID     — registered app name (default: NQBot)
    TRADOVATE_APP_VERSION — app version string (default: 1.0)
    TRADOVATE_DEMO       — "1" = demo account (default), "0" = live
"""

import os
import time
import uuid
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("tradovate_feed")
ET  = ZoneInfo("America/New_York")

DEMO_URL = "https://demo.tradovateapi.com/v1"
LIVE_URL = "https://live.tradovateapi.com/v1"

CHART_DESC = {
    "underlyingType":    "MinuteBar",
    "elementSize":       1,
    "elementSizeUnit":   "UnderlyingUnits",
    "withHistogram":     False,
}


class TradovateLiveFeed:
    """
    Tradovate-backed 1-min bar feed.  Drop-in for DatabentоLiveFeed.
    Polls /md/getchart every ~60 s; yields each new completed bar exactly once.
    """

    feed_name = "Tradovate (60 s polling)"  # shown in Telegram startup message

    def __init__(self, symbol: str = "NQ"):
        try:
            import requests
            self._req = requests
        except ImportError:
            raise RuntimeError("requests not installed — run: pip install requests")

        self.symbol       = symbol.upper()
        demo              = os.getenv("TRADOVATE_DEMO", "1") == "1"
        self.base_url     = DEMO_URL if demo else LIVE_URL
        self._contract_id = 0
        self._last_ts: datetime | None = None
        self._running     = False

        from live.execution import TradovateAuth
        self._auth = TradovateAuth(self.base_url, {
            "username":    os.getenv("TRADOVATE_USERNAME", ""),
            "password":    os.getenv("TRADOVATE_PASSWORD", ""),
            "cid":         os.getenv("TRADOVATE_CID", "0"),
            "secret":      os.getenv("TRADOVATE_SECRET", ""),
            "device_id":   os.getenv("TRADOVATE_DEVICE_ID", str(uuid.uuid4())),
            "app_id":      os.getenv("TRADOVATE_APP_ID", "NQBot"),
            "app_version": os.getenv("TRADOVATE_APP_VERSION", "1.0"),
        })

    # ── Public interface ──────────────────────────────────────────────────────

    def connect(self):
        """Authenticate and resolve the front-month contract id."""
        if not self._auth.login():
            raise RuntimeError("Tradovate login failed — check credentials in .env")

        r = self._req.get(
            f"{self.base_url}/contract/suggest",
            headers=self._auth.headers,
            params={"t": self.symbol, "l": 5},
            timeout=10,
        )
        r.raise_for_status()
        for c in (r.json() if isinstance(r.json(), list) else []):
            if c.get("name", "").startswith(self.symbol):
                self._contract_id = c["id"]
                log.info("Tradovate feed — %s  contract_id=%d  url=%s",
                         c["name"], self._contract_id, self.base_url)
                break

        if not self._contract_id:
            raise RuntimeError(f"No {self.symbol} contract found on Tradovate")

        self._running = True
        log.info("Tradovate data feed ready — polling every ~60 s")

    def stream(self):
        """
        Blocking generator — yields completed 1-min bar dicts.
        Polls Tradovate every 60 s (aligned to minute boundary + 5 s buffer).
        Deduplicates by timestamp so each bar is yielded exactly once.
        """
        while self._running:
            try:
                self._auth.refresh_if_needed()
                for bar in self._fetch_bars(count=3):
                    ts = bar["timestamp"]
                    if self._last_ts is None or ts > self._last_ts:
                        self._last_ts = ts
                        yield bar
            except Exception as exc:
                log.warning("Feed poll error: %s — retrying in 30 s", exc)
                time.sleep(30)
                continue

            # Sleep until ~5 s into the next minute so the bar is fully settled
            now            = time.time()
            secs_into_min  = now % 60
            sleep_secs     = max(5, (60 - secs_into_min) + 5)
            time.sleep(sleep_secs)

    def close(self):
        self._running = False
        log.info("Tradovate feed closed")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch_bars(self, count: int = 3) -> list[dict]:
        payload = {
            "symbol":           str(self._contract_id),
            "chartDescription": CHART_DESC,
            "timeRange":        {"closingBarsOfSessionByCount": count + 2},
        }
        r = self._req.post(
            f"{self.base_url}/md/getchart",
            headers=self._auth.headers,
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        raw = r.json().get("bars", [])

        result = []
        for b in raw[-count:]:
            ts_utc = datetime.fromtimestamp(b.get("timestamp", 0) / 1000,
                                            tz=timezone.utc)
            ts_et  = ts_utc.astimezone(ET).replace(tzinfo=None)
            result.append({
                "timestamp": ts_et,
                "open":   b.get("open",  0.0),
                "high":   b.get("high",  0.0),
                "low":    b.get("low",   0.0),
                "close":  b.get("close", 0.0),
                "volume": int(b.get("upVolume", 0) + b.get("downVolume", 0)),
            })
        return result
