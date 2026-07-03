"""
CruzCapital — ORB Multi-Strategy
Three strategies:
  1. BREAKOUT  - trade the break of the opening range (trend days)
  2. FADE      - trade failed breakouts back inside (chop days)  
  3. VWAP_PULL - after ORB, trade pullbacks to VWAP on trend days

Day filters applied:
  - Skip Mondays (historically weak for ORB)
  - Skip days with OR > 300pts (news/event chaos)
  - Volume confirmation on breakout entries
"""

from dataclasses import dataclass
from datetime import time
import config


@dataclass
class Signal:
    direction: str
    entry: float
    stop: float
    target: float
    mode: str
    confidence: float = 1.0  # 0-1, used for position sizing later


class ORBStrategy:
    def __init__(self):
        self.reset_day()
        self._last_vwap = None
        self._vwap_num = 0.0
        self._vwap_den = 0.0

    def reset_day(self, prev_close=None, day_of_week=None):
        self.or_high = None
        self.or_low = None
        self.or_complete = False
        self.traded_today = False
        self.second_trade_today = False
        self.pm_trade_today = False
        self.gap_fill_today = False
        self.pending_fade_dir = None
        self.prev_close = prev_close
        self.day_of_week = day_of_week  # 0=Mon, 4=Fri
        self.breakout_dir = None        # track which way we broke out
        self._vwap_num = 0.0
        self._vwap_den = 0.0
        self._last_vwap = None
        self.or_volume = 0.0
        self.avg_volume = 0.0
        self.reentry_count = 0          # how many re-entries taken today (max 1)

    # ── Opening Range ──────────────────────────────────────────────
    def update_opening_range(self, bar):
        if self.or_high is None:
            self.or_high = bar["high"]
            self.or_low = bar["low"]
        else:
            self.or_high = max(self.or_high, bar["high"])
            self.or_low = min(self.or_low, bar["low"])
        self.or_volume += bar.get("volume", 0)
        self._update_vwap(bar)

    def finalize_range(self) -> bool:
        self.or_complete = True
        size = self.or_high - self.or_low
        if size < config.ORB_MIN_RANGE_POINTS:
            return False
        if size > config.ORB_MAX_RANGE_POINTS:
            return False
        # Skip Mondays — ORB historically weakest
        if config.SKIP_MONDAYS and self.day_of_week == 0:
            return False
        # Skip Fridays — OOS PF 1.21 vs 4.0+ other days (v10.2 research)
        if getattr(config, "SKIP_FRIDAYS", False) and self.day_of_week == 4:
            return False
        return True

    @property
    def range_size(self):
        return (self.or_high - self.or_low) if self.or_high else 0.0

    @property
    def range_mid(self):
        return (self.or_high + self.or_low) / 2

    # ── VWAP tracking ──────────────────────────────────────────────
    def _update_vwap(self, bar):
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        vol = bar.get("volume", 1) or 1
        self._vwap_num += typical * vol
        self._vwap_den += vol
        self._last_vwap = self._vwap_num / self._vwap_den

    @property
    def vwap(self):
        return self._last_vwap

    # ── Gap direction filter ────────────────────────────────────────
    def _gap_direction(self):
        if self.prev_close is None or self.or_high is None:
            return 0
        or_open = (self.or_high + self.or_low) / 2
        gap = or_open - self.prev_close
        threshold = config.GAP_FILTER_POINTS
        if gap > threshold:
            # Check if gap falls in the excluded dead zone
            if (hasattr(config, 'GAP_EXCLUDE_MAX') and config.GAP_EXCLUDE_MAX > 0
                    and config.GAP_FILTER_POINTS < gap <= config.GAP_EXCLUDE_MAX):
                return 0  # dead zone -- treat as no directional gap
            return 1
        elif gap < -threshold:
            if (hasattr(config, 'GAP_EXCLUDE_MAX') and config.GAP_EXCLUDE_MAX > 0
                    and config.GAP_FILTER_POINTS < (-gap) <= config.GAP_EXCLUDE_MAX):
                return 0  # dead zone
            return -1
        return 0

    def _stop_distance(self):
        return config.ORB_FIXED_STOP_POINTS + config.ORB_STOP_BUFFER_POINTS

    # ── Strategy 1: Breakout ────────────────────────────────────────
    def check_breakout(self, bar) -> Signal | None:
        if self.traded_today or not self.or_complete:
            return None

        stop_dist = self._stop_distance()
        gap = self._gap_direction()

        # Volume confirmation — breakout needs above-average volume
        bar_vol = bar.get("volume", 0)
        vol_ok = bar_vol >= config.BREAKOUT_MIN_VOLUME

        buf = config.ORB_BREAKOUT_BUFFER_POINTS

        buf = config.ORB_BREAKOUT_BUFFER_POINTS
        rr = (config.ORB_BREAKOUT_RR_TARGET if config.EVAL_MODE
              else config.ORB_FUNDED_RR_TARGET)

        if bar["close"] > self.or_high + buf and gap > 0 and vol_ok:
            entry = bar["close"]
            stop = entry - stop_dist
            risk = entry - stop
            target = entry + risk * rr
            confidence = 1.0 if gap > 0 else 0.8
            self.breakout_dir = "long"
            return Signal("long", entry, stop, target, "breakout", confidence)

        if bar["close"] < self.or_low - buf and gap < 0 and vol_ok:
            entry = bar["close"]
            stop = entry + stop_dist
            risk = stop - entry
            target = entry - risk * rr
            confidence = 1.0 if gap < 0 else 0.8
            self.breakout_dir = "short"
            return Signal("short", entry, stop, target, "breakout", confidence)

        return None

    # ── Strategy 2: Fade ───────────────────────────────────────────
    def check_fade(self, bar) -> Signal | None:
        if self.traded_today or not self.or_complete:
            return None

        stop_dist = self._stop_distance()

        if self.pending_fade_dir is None:
            if bar["high"] > self.or_high and bar["close"] < self.or_high:
                self.pending_fade_dir = "short"
            elif bar["low"] < self.or_low and bar["close"] > self.or_low:
                self.pending_fade_dir = "long"
            else:
                return None

        if self.pending_fade_dir == "short" and bar["close"] < self.or_high:
            entry = bar["close"]
            stop = entry + stop_dist
            risk = stop - entry
            target = entry - risk * config.ORB_RR_TARGET
            if risk <= 0 or (entry - target) / risk < config.MIN_RR - 0.05:
                return None
            return Signal("short", entry, stop, target, "fade")

        if self.pending_fade_dir == "long" and bar["close"] > self.or_low:
            entry = bar["close"]
            stop = entry - stop_dist
            risk = entry - stop
            target = entry + risk * config.ORB_RR_TARGET
            if risk <= 0 or (target - entry) / risk < config.MIN_RR - 0.05:
                return None
            return Signal("long", entry, stop, target, "fade")

        return None

    # ── Strategy 4: Gap Fill ────────────────────────────────────────
    def check_gap_fill(self, bar) -> Signal | None:
        """
        On large-gap days (gap > GAP_FILL_MIN_POINTS), if no breakout
        signal was taken and price reverses against the gap, fade toward
        the prior close. Entry window: 9:45-GAP_FILL_LAST_ENTRY.
        """
        if self.traded_today or self.gap_fill_today or not self.or_complete:
            return None
        if self.prev_close is None:
            return None

        or_mid = (self.or_high + self.or_low) / 2
        gap = or_mid - self.prev_close
        if abs(gap) < config.GAP_FILL_MIN_POINTS:
            return None

        stop_dist = config.GAP_FILL_STOP_POINTS
        rr = config.GAP_FILL_RR

        if gap > 0:
            # Gapped up: fade short back toward prior close
            if bar["close"] < self.or_low - 2.0:
                entry = bar["close"]
                stop = entry + stop_dist
                # Target: prior close but not further than RR allows
                fill_target = self.prev_close
                rr_target = entry - stop_dist * rr
                target = max(fill_target, rr_target)
                self.gap_fill_today = True
                return Signal("short", entry, stop, target, "gap_fill")
        elif gap < 0:
            # Gapped down: fade long back toward prior close
            if bar["close"] > self.or_high + 2.0:
                entry = bar["close"]
                stop = entry - stop_dist
                fill_target = self.prev_close
                rr_target = entry + stop_dist * rr
                target = min(fill_target, rr_target)
                self.gap_fill_today = True
                return Signal("long", entry, stop, target, "gap_fill")

        return None

    # ── Strategy 5: PM VWAP Continuation ───────────────────────────
    def check_pm_vwap(self, bar) -> Signal | None:
        """
        Afternoon VWAP continuation (12:00-14:30). Trades in the direction
        of the morning bias (breakout dir > gap dir) when price touches VWAP.
        Only 1 PM trade per day, uses tighter stop and 1.5R target.
        """
        if self.pm_trade_today or self.vwap is None:
            return None

        # Determine bias: morning breakout > gap direction
        gap = self._gap_direction()
        if self.breakout_dir == "long" or (self.breakout_dir is None and gap > 0):
            bias = "long"
        elif self.breakout_dir == "short" or (self.breakout_dir is None and gap < 0):
            bias = "short"
        else:
            return None  # no clear directional bias

        vwap = self.vwap
        tol = config.PM_VWAP_TOLERANCE
        stop_dist = config.PM_VWAP_STOP_POINTS
        target_dist = stop_dist * config.PM_VWAP_RR

        if bias == "long":
            if bar["low"] <= vwap + tol and bar["close"] > vwap:
                entry = bar["close"]
                return Signal("long", entry, entry - stop_dist,
                              entry + target_dist, "pm_vwap")
        else:
            if bar["high"] >= vwap - tol and bar["close"] < vwap:
                entry = bar["close"]
                return Signal("short", entry, entry + stop_dist,
                              entry - target_dist, "pm_vwap")

        return None

    # ── Strategy 3: VWAP Pullback ──────────────────────────────────
    def check_vwap_pullback(self, bar) -> Signal | None:
        """
        After a confirmed breakout, price often pulls back to VWAP.
        Enter in breakout direction when price touches VWAP and bounces.
        Only fires as a second trade on strong breakout days.
        """
        if not self.or_complete or self.second_trade_today:
            return None
        if not self.traded_today or self.breakout_dir is None:
            return None
        if self.vwap is None:
            return None

        stop_dist = self._stop_distance() * 0.8  # tighter stop on pullback

        vwap = self.vwap
        tolerance = 3.0  # within 3 points of VWAP

        if self.breakout_dir == "long":
            # Price pulled back to VWAP and closed above it
            if bar["low"] <= vwap + tolerance and bar["close"] > vwap:
                entry = bar["close"]
                stop = entry - stop_dist
                risk = entry - stop
                target = entry + risk * config.ORB_RR_TARGET
                return Signal("long", entry, stop, target, "vwap_pull", 0.9)

        if self.breakout_dir == "short":
            # Price pulled back up to VWAP and closed below it
            if bar["high"] >= vwap - tolerance and bar["close"] < vwap:
                entry = bar["close"]
                stop = entry + stop_dist
                risk = stop - entry
                target = entry - risk * config.ORB_RR_TARGET
                return Signal("short", entry, stop, target, "vwap_pull", 0.9)

        return None
