"""
Regime Detector
Decides each day: BREAKOUT mode, FADE mode, or SKIP.
Logic: compare today's opening range size to recent daily ATR.
  - Big OR relative to ATR  -> energy in the market -> BREAKOUT
  - Mid OR                  -> failed-break setups  -> FADE
  - Tiny OR                 -> dead market          -> SKIP
"""

from collections import deque
import config


class RegimeDetector:
    def __init__(self):
        self.daily_ranges = deque(maxlen=config.REGIME_ATR_PERIOD)

    def record_day(self, day_high: float, day_low: float):
        self.daily_ranges.append(day_high - day_low)

    @property
    def daily_atr(self) -> float | None:
        if len(self.daily_ranges) < 3:
            return None
        return sum(self.daily_ranges) / len(self.daily_ranges)

    def classify(self, opening_range_size: float) -> str:
        """Returns 'breakout', 'fade', or 'skip'."""
        atr = self.daily_atr
        if atr is None or atr <= 0:
            return "breakout"  # default until we have history

        ratio = opening_range_size / atr

        if ratio >= config.REGIME_BREAKOUT_THRESHOLD:
            return "breakout"
        if ratio <= config.REGIME_FADE_THRESHOLD:
            return "skip"
        return "fade"
