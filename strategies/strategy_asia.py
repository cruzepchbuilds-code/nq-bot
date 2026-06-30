"""
Asia Gap Continuation Strategy
Window: 6:00 PM - 9:00 PM ET (CME Globex, Mon-Thu evenings)

Logic:
  1. Record the 4:00 PM ET bar close (same calendar day as the Asia session)
  2. At 6:00 PM ET (CME reopens after 1-hour halt), compute:
         halt_gap = 6pm_open - 4pm_close
     If abs(halt_gap) is outside [GAP_MIN, GAP_MAX], skip the session.
  3. At 6:15 PM close, enter IN THE DIRECTION of the halt gap:
         gap > 0 (gapped up)   → long
         gap < 0 (gapped down) → short
  4. Fixed stop:   ASIA_STOP_POINTS from entry
     Fixed target: ASIA_STOP_POINTS × ASIA_RR_TARGET from entry
  5. Hard exit at 9:00 PM close regardless of P&L

Filters (applied at classify_at_open):
  - Skip Thursdays  (OOS PF 0.82 — worst day-of-week)
  - Skip if abs(halt_gap) < ASIA_GAP_MIN_POINTS or > ASIA_GAP_MAX_POINTS
  - Skip ASIA_WEAK_MONTHS (Aug, Nov by default)
  - Only 1 trade per session

Research basis (2024-2026 OOS, same-day 4pm→6pm halt gap):
  gap 30-80pt, skip Thu → OOS PF 1.80, WR 56%, n=77 OOS trades
  Year-over-year improving: 2024 PF 1.42 → 2025 PF 1.82 → 2026 PF 2.31

Edge: CME's 1-hour halt (5pm-6pm ET) separates US RTH from Asia.
Gap at reopening signals institutional overnight positioning.
Continuation beats fade: OOS PF 1.67 vs 0.86 (tested across 464 configs).

Strong months: Feb (PF 1.89), Sep (PF 1.88), Jun (PF 1.42), Oct (PF 1.46)
Weak months:  Nov (PF 0.33), Aug (PF 0.71)
Skip Fridays: Friday 6pm = Saturday — no Asia session data.

IMPORTANT: Funded phase only. In EVAL_MODE this strategy is disabled
(low volume 3.6% of US = real slippage risk during evaluation).
"""

import config
from strategies.strategy_us import Signal


class AsiaStrategy:
    def __init__(self):
        self.reset_session()

    def reset_session(self):
        """Call once at the start of each calendar trading day."""
        self.us_close_4pm: float | None = None   # 16:00 ET close (same day)
        self.asia_open: float | None = None      # 18:00 ET bar close
        self.halt_gap: float | None = None       # asia_open - us_close_4pm
        self.direction: str | None = None        # 'long' or 'short'
        self.entry_pending: bool = False         # armed at 18:00, consumed at 18:15
        self.traded_today: bool = False

    # ── US session close recording ──────────────────────────────────────────
    def record_us_close(self, bar):
        """
        Call on the 4:00 PM ET (16:00) bar close.
        This is the reference price for the halt gap computation.
        """
        self.us_close_4pm = bar["close"]

    # ── Asia open + halt-gap classification ─────────────────────────────────
    def classify_at_open(self, bar, month: int, day_of_week: int) -> bool:
        """
        Call on the 6:00 PM ET (18:00) bar close.

        Computes the CME halt gap (6pm_open − 4pm_close) and decides
        whether to arm an entry for the 6:15 PM bar.

        Args:
            bar:          1-minute bar dict with 'close' key
            month:        calendar month (1=Jan … 12=Dec)
            day_of_week:  0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri

        Returns:
            True if a gap setup was identified.
        """
        if self.traded_today:
            return False

        # Skip Thursdays — OOS PF 0.82 is the worst DOW
        if config.ASIA_SKIP_THURSDAYS and day_of_week == 3:
            return False

        # Skip weak months unconditionally
        if month in config.ASIA_WEAK_MONTHS:
            return False

        # Optionally restrict to strong months only
        if config.ASIA_STRONG_MONTHS_ONLY and month not in config.ASIA_STRONG_MONTHS:
            return False

        # Need a same-day US close for halt gap calculation
        if self.us_close_4pm is None:
            return False

        self.asia_open = bar["close"]
        self.halt_gap = self.asia_open - self.us_close_4pm
        gap_abs = abs(self.halt_gap)

        if gap_abs < config.ASIA_GAP_MIN_POINTS or gap_abs > config.ASIA_GAP_MAX_POINTS:
            self.direction = None
            self.entry_pending = False
            return False

        # Trade in the direction of the halt gap (continuation, not fade)
        self.direction = "long" if self.halt_gap > 0 else "short"
        self.entry_pending = True
        return True

    # ── Entry signal at 6:15 PM ─────────────────────────────────────────────
    def check_entry(self, bar) -> "Signal | None":
        """
        Call on the 6:15 PM ET (18:15) bar close.

        Returns a Signal if a gap setup is pending, otherwise None.
        The caller is responsible for checking bankroll / eval mode guards.
        """
        if self.traded_today or not self.entry_pending or self.direction is None:
            return None

        self.entry_pending = False   # consume — only one entry per session
        entry = bar["close"]

        stop_dist   = config.ASIA_STOP_POINTS
        target_dist = config.ASIA_STOP_POINTS * config.ASIA_RR_TARGET

        if self.direction == "long":
            stop   = entry - stop_dist
            target = entry + target_dist
            return Signal("long", entry, stop, target, "asia_gap")

        if self.direction == "short":
            stop   = entry + stop_dist
            target = entry - target_dist
            return Signal("short", entry, stop, target, "asia_gap")

        return None

    # ── Convenience properties ───────────────────────────────────────────────
    @property
    def gap_size(self) -> float:
        """Absolute halt gap in NQ points."""
        return abs(self.halt_gap) if self.halt_gap is not None else 0.0

    @property
    def gap_description(self) -> str:
        """Human-readable halt gap summary."""
        if self.halt_gap is None:
            return "no gap data"
        sign = "+" if self.halt_gap >= 0 else ""
        return f"{sign}{self.halt_gap:.1f}pt ({self.direction or 'skip'})"
