"""
Afternoon ORB Strategy — PM session (post-lunch breakout)
Window: 1:00 PM - 2:15 PM ET

Logic:
  1. Build opening range from 13:00-13:14 bars (15 bars).
  2. At 13:15, if OR size is within PM_ORB_MIN/MAX_RANGE_POINTS, arm entry.
  3. First bar closing outside OR + PM_ORB_BREAKOUT_BUFFER_POINTS triggers entry.
  4. Fixed stop: PM_ORB_FIXED_STOP_POINTS from entry.
     Fixed target: stop_dist × PM_ORB_RR_TARGET from entry.
  5. Hard flatten at FLATTEN_TIME (15:55 ET).

Filters:
  - Skip Mondays  (Mon OOS PF 0.92, net -$1,045)
  - Skip Fridays  (Fri OOS PF 0.97, net -$280 — low volume PM session)
  - OR must be 15-50pt (tight consolidation = clean breakout signal)
  - One trade per day (no re-entry after stop)

Research basis (OOS 2025-2026, Tue-Thu only, OR 15-50pt):
  N=116  WR=47%  PF=1.64  Net=+$17,520  Avg=+$151/trade
  IS 2022-2024: N=337  WR=44%  PF=1.54  (IS/OOS consistent — not overfit)
  Year-by-year OOS: 2025 PF=1.59, 2026 PF=1.68 (improving)
  Shorts outperform: Short PF=1.97 vs Long PF=1.36

Why it works: post-lunch NQ consolidation (12:30-13:00) creates a compressed
range. The 13:00-13:14 range breaks when institutional flow resumes. The
stop-size filter (15-50pt) ensures only tight consolidations are traded.

IMPORTANT: During EVAL_MODE, uses 1 contract. Funded: uses same as morning.
"""

from datetime import time
import config
from strategies.strategy_us import Signal


PM_OR_START   = time(13, 0)
PM_OR_END     = time(13, 14)   # last minute included in OR (13:00-13:14 = 15 bars)
PM_OR_DONE    = time(13, 15)   # first possible entry bar
PM_ENTRY_END  = time(14, 15)   # last entry bar (no entries after 14:15)
PM_FLATTEN    = time(15, 55)   # hard exit same as morning session


class PMORBStrategy:
    """Afternoon ORB: 1pm range, 1:15-2:15 entry, 2:15-3:55 hold."""

    def __init__(self):
        self.reset_day()

    def reset_day(self, day_of_week: int = None):
        self.or_high: float | None = None
        self.or_low:  float | None = None
        self.or_complete: bool = False
        self.or_ok: bool = False          # False if OR size filter rejects
        self.traded_today: bool = False
        self.day_of_week: int | None = day_of_week  # 0=Mon … 4=Fri

    # ── OR building ─────────────────────────────────────────────────────────────

    def update_or(self, bar) -> None:
        """Call on each 1-min bar from 13:00 to 13:14 inclusive."""
        h, l = bar["high"], bar["low"]
        if self.or_high is None:
            self.or_high, self.or_low = h, l
        else:
            self.or_high = max(self.or_high, h)
            self.or_low  = min(self.or_low, l)

    def finalize_or(self) -> bool:
        """
        Call at 13:15 (first bar after OR window closes).
        Returns True if this day should be traded.
        """
        self.or_complete = True

        # Day-of-week filters
        if self.day_of_week is not None:
            if config.SKIP_MONDAYS and self.day_of_week == 0:
                self.or_ok = False
                return False
            if getattr(config, "SKIP_FRIDAYS", False) and self.day_of_week == 4:
                self.or_ok = False
                return False

        if self.or_high is None or self.or_low is None:
            self.or_ok = False
            return False

        or_size = self.or_high - self.or_low
        lo = getattr(config, "PM_ORB_MIN_RANGE_POINTS", 15.0)
        hi = getattr(config, "PM_ORB_MAX_RANGE_POINTS", 50.0)
        if not (lo <= or_size <= hi):
            self.or_ok = False
            return False

        self.or_ok = True
        return True

    @property
    def range_size(self) -> float:
        return (self.or_high - self.or_low) if self.or_high is not None else 0.0

    # ── Entry signal ─────────────────────────────────────────────────────────────

    def check_entry(self, bar) -> "Signal | None":
        """
        Call on each bar from 13:15 to 14:15.
        Returns a Signal on the first bar that closes outside OR + buffer.
        Returns None once traded, OR not OK, or past entry window.
        """
        if self.traded_today or not self.or_ok:
            return None

        buf  = getattr(config, "PM_ORB_BREAKOUT_BUFFER_POINTS", 2.0)
        stop = getattr(config, "PM_ORB_FIXED_STOP_POINTS", 22.0)
        rr   = getattr(config, "PM_ORB_RR_TARGET", 2.0)

        close = bar["close"]

        if close > self.or_high + buf:
            entry  = close
            stop_p = entry - stop
            tgt    = entry + stop * rr
            return Signal("long", entry, stop_p, tgt, "pm_orb")

        if close < self.or_low - buf:
            entry  = close
            stop_p = entry + stop
            tgt    = entry - stop * rr
            return Signal("short", entry, stop_p, tgt, "pm_orb")

        return None

    def mark_traded(self):
        """Call after an entry signal is accepted."""
        self.traded_today = True
