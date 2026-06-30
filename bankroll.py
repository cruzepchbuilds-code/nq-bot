"""
Bankroll Manager
The gatekeeper. Every trade must pass through can_trade() and size_position().
The strategy NEVER decides size or whether trading is allowed - this module does.
"""

from dataclasses import dataclass, field
from datetime import date
import config


@dataclass
class BankrollState:
    balance: float = config.STARTING_BALANCE
    peak_balance: float = config.STARTING_BALANCE
    day_start_balance: float = config.STARTING_BALANCE
    week_start_balance: float = config.STARTING_BALANCE
    current_day: date | None = None
    current_week: int | None = None
    trades_today: int = 0
    losses_today: int = 0
    halted_for_day: bool = False
    halted_for_week: bool = False
    halted_permanently: bool = False
    halt_reason: str = ""
    consecutive_losing_days: int = 0
    paused_today: bool = False          # sitting out after losing streak
    # Apex-style trailing drawdown floor
    apex_floor: float = config.STARTING_BALANCE - config.APEX_TRAILING_DD


class BankrollManager:
    def __init__(self):
        self.s = BankrollState()
        self.trade_log = []

    # ---------- day/week rollover ----------
    def on_new_bar_date(self, d: date):
        iso_week = d.isocalendar()[1]
        if self.s.current_day != d:
            # Update consecutive losing days before resetting day_start_balance
            prev_day_pnl = self.s.balance - self.s.day_start_balance
            if self.s.current_day is not None:
                if prev_day_pnl < 0:
                    self.s.consecutive_losing_days += 1
                else:
                    self.s.consecutive_losing_days = 0
            self.s.paused_today = (
                self.s.consecutive_losing_days >= config.MAX_CONSECUTIVE_LOSING_DAYS
            )
            self.s.current_day = d
            self.s.day_start_balance = self.s.balance
            self.s.trades_today = 0
            self.s.losses_today = 0
            self.s.halted_for_day = False
        if self.s.current_week != iso_week:
            self.s.current_week = iso_week
            self.s.week_start_balance = self.s.balance
            self.s.halted_for_week = False

    # ---------- the gate ----------
    def can_trade(self) -> tuple[bool, str]:
        s = self.s
        if s.halted_permanently:
            return False, f"PERMANENT HALT: {s.halt_reason}"
        if s.paused_today:
            return False, f"Paused: {s.consecutive_losing_days} consecutive losing days"
        if s.halted_for_week:
            return False, "Weekly loss limit hit - paused until Monday"
        if s.halted_for_day:
            return False, "Daily halt active"

        # max total drawdown from peak
        dd = (s.peak_balance - s.balance) / s.peak_balance
        if dd >= config.MAX_TOTAL_DRAWDOWN_PCT:
            s.halted_permanently = True
            s.halt_reason = f"Max drawdown {dd:.1%} from peak"
            return False, s.halt_reason

        # Apex trailing drawdown
        if config.ENFORCE_APEX_RULES and s.balance <= s.apex_floor:
            s.halted_permanently = True
            s.halt_reason = f"Apex trailing DD floor breached (${s.apex_floor:,.0f})"
            return False, s.halt_reason

        # daily limits
        day_pnl = s.balance - s.day_start_balance
        if day_pnl <= -config.DAILY_LOSS_LIMIT_PCT * s.day_start_balance:
            s.halted_for_day = True
            return False, "Daily loss limit hit"
        if day_pnl >= config.DAILY_PROFIT_LOCK_PCT * s.day_start_balance:
            s.halted_for_day = True
            return False, "Daily profit target locked in"
        if s.trades_today >= config.MAX_TRADES_PER_DAY:
            s.halted_for_day = True
            return False, "Max trades for day reached"
        if s.losses_today >= config.MAX_LOSSES_PER_DAY:
            s.halted_for_day = True
            return False, "2 losses - done for the day"

        # weekly limit
        week_pnl = s.balance - s.week_start_balance
        if week_pnl <= -config.WEEKLY_LOSS_LIMIT_PCT * s.week_start_balance:
            s.halted_for_week = True
            return False, "Weekly loss limit hit"

        return True, "OK"

    # ---------- position sizing ----------
    def size_position(self, entry: float, stop: float, target: float) -> tuple[int, str]:
        """Returns (contracts, reason). 0 contracts = rejected."""
        risk_points = abs(entry - stop)
        reward_points = abs(target - entry)
        if risk_points <= 0:
            return 0, "Invalid stop"

        rr = reward_points / risk_points
        if rr < config.MIN_RR - 0.01:  # small tolerance for floating point
            return 0, f"RR {rr:.2f} below minimum {config.MIN_RR}"

        risk_dollars = self.s.balance * config.RISK_PER_TRADE_PCT

        # recovery mode: half size when down 3% from peak
        dd = (self.s.peak_balance - self.s.balance) / self.s.peak_balance
        if dd >= config.RECOVERY_MODE_TRIGGER_PCT:
            risk_dollars *= config.RECOVERY_SIZE_MULTIPLIER

        per_contract_risk = risk_points * config.POINT_VALUE
        contracts = int(risk_dollars // per_contract_risk)
        contracts = min(contracts, config.MAX_CONTRACTS)

        if contracts < 1:
            # Allow 1 contract if risk is within 3x budget (accounts for NQ's high point value)
            if per_contract_risk <= risk_dollars * 3:
                return 1, "OK (min size)"
            return 0, f"Risk per contract (${per_contract_risk:,.0f}) exceeds budget (${risk_dollars:,.0f})"
        return contracts, "OK"

    # ---------- record results ----------
    def record_trade(self, pnl: float, meta: dict, count_as_new: bool = True):
        """Record a trade or partial exit.
        count_as_new=False for partial exits so trades_today isn't double-counted."""
        s = self.s
        s.balance += pnl
        if count_as_new:
            s.trades_today += 1
            if pnl < 0:
                s.losses_today += 1
        if s.balance > s.peak_balance:
            s.peak_balance = s.balance
            # Apex floor trails the peak until it locks at start+100 (simplified)
            new_floor = s.peak_balance - config.APEX_TRAILING_DD
            locked_floor = config.STARTING_BALANCE + 100
            s.apex_floor = min(max(s.apex_floor, new_floor), locked_floor) \
                if s.apex_floor >= locked_floor else max(s.apex_floor, new_floor)
        self.trade_log.append({"pnl": pnl, "balance": s.balance, **meta})
