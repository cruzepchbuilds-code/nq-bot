"""
London/NY Overlap Momentum Strategy
Window: 8:00 AM - 9:25 AM ET ONLY. Never overlaps with ORB (starts 9:30).

Logic:
  1. 8:00-8:59 ET — build the "London range" (running H/L of all bars)
  2. 9:00 bar close — classify trend:
       upper 30% of range → bullish  → long setup
       lower 30% of range → bearish  → short setup
       middle 40%         → skip
  3. 9:05 bar close — enter in trend direction
       stop:   entry ± LONDON_STOP_POINTS   (fixed 20 pts)
       target: entry ± LONDON_TARGET_POINTS (fixed 30 pts = 1.5R)
  4. Hard exit at 9:25 bar close regardless of P&L
  Only 1 trade per day from this strategy.
  Skip days where London range < 20 pts (dead) or > 200 pts (news chaos).
"""

import config
from strategies.strategy_us import Signal


class LondonStrategy:
    def __init__(self):
        self.reset_day()

    def reset_day(self):
        self.london_high: float | None = None
        self.london_low: float | None = None
        self.range_valid: bool = False    # True if range passes size filter
        self.trend: str | None = None    # 'long', 'short', or None (skip)
        self.signal_pending: bool = False # set at 9:00, consumed at 9:05
        self.traded_today: bool = False

    # ── Range building (8:00-8:59) ─────────────────────────────────────
    def update_range(self, bar):
        """Call on every bar from 8:00 to 8:59 ET (inclusive)."""
        if self.london_high is None:
            self.london_high = bar["high"]
            self.london_low  = bar["low"]
        else:
            self.london_high = max(self.london_high, bar["high"])
            self.london_low  = min(self.london_low,  bar["low"])

    # ── Trend classification (9:00 bar) ────────────────────────────────
    def classify_at_nine(self, bar) -> bool:
        """
        Call on the 9:00 bar.
        Validates range size and sets self.trend / self.signal_pending.
        Returns True if a setup was identified.
        """
        if self.london_high is None:
            return False

        size = self.london_high - self.london_low
        if size < config.LONDON_MIN_RANGE_POINTS or size > config.LONDON_MAX_RANGE_POINTS:
            self.trend = None
            self.range_valid = False
            return False

        self.range_valid = True
        close = bar["close"]
        upper_thresh = self.london_low + size * (1.0 - config.LONDON_TREND_THRESHOLD)
        lower_thresh = self.london_low + size * config.LONDON_TREND_THRESHOLD

        if close >= upper_thresh:
            self.trend = "long"
            self.signal_pending = True
        elif close <= lower_thresh:
            self.trend = "short"
            self.signal_pending = True
        else:
            self.trend = None
            self.signal_pending = False

        return self.signal_pending

    # ── Entry (9:05 bar) ───────────────────────────────────────────────
    def check_entry(self, bar) -> Signal | None:
        """
        Call on the 9:05 bar.
        Returns a Signal to enter, or None if no setup.
        """
        if self.traded_today or not self.signal_pending or self.trend is None:
            return None

        self.signal_pending = False   # consume the signal
        entry = bar["close"]

        if self.trend == "long":
            stop   = entry - config.LONDON_STOP_POINTS
            target = entry + config.LONDON_TARGET_POINTS
            return Signal("long", entry, stop, target, "london")

        if self.trend == "short":
            stop   = entry + config.LONDON_STOP_POINTS
            target = entry - config.LONDON_TARGET_POINTS
            return Signal("short", entry, stop, target, "london")

        return None

    @property
    def range_size(self) -> float:
        if self.london_high is None:
            return 0.0
        return self.london_high - self.london_low
