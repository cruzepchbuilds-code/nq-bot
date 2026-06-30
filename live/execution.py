"""
live/execution.py

Tradovate execution module for NQ/ES live and paper trading.
Handles authentication, order placement, position tracking, and daily risk limits.

Environment variables required:
    TRADOVATE_USERNAME   — Tradovate account email
    TRADOVATE_PASSWORD   — Tradovate account password
    TRADOVATE_CID        — API application client ID (integer)
    TRADOVATE_SECRET     — API application client secret
    TRADOVATE_DEVICE_ID  — unique device UUID (generate once, save)
    TRADOVATE_APP_ID     — application name registered on Tradovate
    TRADOVATE_APP_VERSION — application version string (e.g. "1.0")
    TRADOVATE_DEMO       — "1" for demo, "0" or unset for live

Usage:
    from live.execution import ExecutionEngine
    engine = ExecutionEngine()
    engine.connect()
    result = engine.enter("NQ", "long", contracts=1, stop_pts=22, target_pts=44)
    engine.print_status()
"""

import os
import sys
import time
import uuid
import logging
import requests
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import discord_alerts as da

log = logging.getLogger("execution")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s")

DEMO_URL  = "https://demo.tradovateapi.com/v1"
LIVE_URL  = "https://live.tradovateapi.com/v1"
TOKEN_TTL = timedelta(minutes=75)  # tokens expire after 80 min; refresh at 75


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class OrderResult:
    order_id:    int
    status:      str          # "accepted" | "filled" | "rejected" | "error"
    fill_price:  float = 0.0
    error_msg:   str   = ""


@dataclass
class Position:
    symbol:        str
    direction:     str        # "long" | "short"
    contracts:     int
    entry_price:   float
    stop_order_id: Optional[int] = None
    target_order_id: Optional[int] = None
    entry_time:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DailyStats:
    date:           str   = ""
    trades:         int   = 0
    wins:           int   = 0
    losses:         int   = 0
    gross_pnl:      float = 0.0
    commissions:    float = 0.0
    halted:         bool  = False
    halt_reason:    str   = ""

    @property
    def net_pnl(self):
        return self.gross_pnl - self.commissions

    @property
    def win_rate(self):
        return (self.wins / self.trades * 100) if self.trades else 0.0


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TradovateAuth:
    """Manages Tradovate OAuth token lifecycle."""

    def __init__(self, base_url: str, creds: dict):
        self.base_url  = base_url
        self.creds     = creds
        self.token:    Optional[str] = None
        self.md_token: Optional[str] = None
        self._expires: Optional[datetime] = None

    def login(self) -> bool:
        payload = {
            "name":       self.creds["username"],
            "password":   self.creds["password"],
            "appId":      self.creds["app_id"],
            "appVersion": self.creds["app_version"],
            "cid":        int(self.creds["cid"]),
            "sec":        self.creds["secret"],
            "deviceId":   self.creds["device_id"],
        }
        try:
            r = requests.post(f"{self.base_url}/auth/accesstokenrequest",
                              json=payload, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.error("Login failed: %s", exc)
            return False

        if "errorText" in data:
            log.error("Login error: %s", data["errorText"])
            return False

        self.token    = data.get("accessToken") or data.get("p-ticket")
        self.md_token = data.get("mdAccessToken")
        self._expires = datetime.now(timezone.utc) + TOKEN_TTL
        log.info("Authenticated ✓  expires %s UTC", self._expires.strftime("%H:%M:%S"))
        return True

    def refresh_if_needed(self) -> bool:
        if self._expires and datetime.now(timezone.utc) < self._expires:
            return True
        log.info("Token expiring — refreshing...")
        return self.login()

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type":  "application/json"}


# ---------------------------------------------------------------------------
# REST client
# ---------------------------------------------------------------------------

class TradovateClient:
    """Thin REST wrapper around the Tradovate API."""

    def __init__(self, auth: TradovateAuth):
        self.auth = auth

    def _get(self, path: str, params: dict = None) -> dict:
        self.auth.refresh_if_needed()
        r = requests.get(f"{self.auth.base_url}{path}",
                         headers=self.auth.headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        self.auth.refresh_if_needed()
        r = requests.post(f"{self.auth.base_url}{path}",
                          headers=self.auth.headers, json=body, timeout=10)
        r.raise_for_status()
        return r.json()

    # -- Account --

    def get_accounts(self) -> list:
        return self._get("/account/list")

    def get_cash_balance(self, account_id: int) -> float:
        data = self._get("/cashBalance/getcashbalancesnapshot",
                         {"accountId": account_id})
        return data.get("totalCashValue", 0.0)

    def get_positions(self, account_id: int) -> list:
        return self._get("/position/find", {"accountId": account_id}) or []

    def get_orders(self, account_id: int) -> list:
        return self._get("/order/list", {"accountId": account_id}) or []

    # -- Contracts --

    def find_contract(self, symbol: str) -> Optional[dict]:
        """Return front-month contract for symbol (e.g. 'NQ', 'ES')."""
        results = self._get("/contract/suggest", {"t": symbol, "l": 5})
        if isinstance(results, list):
            for c in results:
                if c.get("name", "").startswith(symbol):
                    return c
        return None

    # -- Orders --

    def place_market_order(self, account_id: int, contract_id: int,
                           action: str, qty: int) -> dict:
        return self._post("/order/placeorder", {
            "accountSpec":  "",
            "accountId":    account_id,
            "action":       action,          # "Buy" | "Sell"
            "symbol":       "",
            "orderQty":     qty,
            "orderType":    "Market",
            "contractId":   contract_id,
            "isAutomated":  True,
        })

    def place_stop_order(self, account_id: int, contract_id: int,
                         action: str, qty: int, stop_price: float) -> dict:
        return self._post("/order/placeorder", {
            "accountSpec":  "",
            "accountId":    account_id,
            "action":       action,
            "symbol":       "",
            "orderQty":     qty,
            "orderType":    "Stop",
            "stopPrice":    round(stop_price, 2),
            "contractId":   contract_id,
            "isAutomated":  True,
        })

    def place_limit_order(self, account_id: int, contract_id: int,
                          action: str, qty: int, limit_price: float) -> dict:
        return self._post("/order/placeorder", {
            "accountSpec":  "",
            "accountId":    account_id,
            "action":       action,
            "symbol":       "",
            "orderQty":     qty,
            "orderType":    "Limit",
            "price":        round(limit_price, 2),
            "contractId":   contract_id,
            "isAutomated":  True,
        })

    def cancel_order(self, order_id: int) -> dict:
        return self._post("/order/cancelorder", {"orderId": order_id})

    def get_order_status(self, order_id: int) -> str:
        data = self._get(f"/order/item", {"id": order_id})
        return data.get("ordStatus", "Unknown")

    def liquidate_position(self, account_id: int, contract_id: int) -> dict:
        return self._post("/order/liquidateposition", {
            "accountId":  account_id,
            "contractId": contract_id,
            "admin":      False,
        })


# ---------------------------------------------------------------------------
# Execution Engine
# ---------------------------------------------------------------------------

class ExecutionEngine:
    """
    Top-level execution manager.

    Responsibilities:
      - Session auth and reconnection
      - One-position guard (never more than one open trade)
      - Daily risk limits (max trades, max losses, daily loss limit)
      - Bracket entry: market + stop + profit target
      - Forced flat at FLATTEN_TIME or on daily halt
    """

    def __init__(self):
        demo = os.getenv("TRADOVATE_DEMO", "1") == "1"
        self.base_url = DEMO_URL if demo else LIVE_URL
        self.mode     = "DEMO" if demo else "LIVE"

        self.creds = {
            "username":    os.getenv("TRADOVATE_USERNAME", ""),
            "password":    os.getenv("TRADOVATE_PASSWORD", ""),
            "cid":         os.getenv("TRADOVATE_CID", "0"),
            "secret":      os.getenv("TRADOVATE_SECRET", ""),
            "device_id":   os.getenv("TRADOVATE_DEVICE_ID", str(uuid.uuid4())),
            "app_id":      os.getenv("TRADOVATE_APP_ID", "CruzCapital"),
            "app_version": os.getenv("TRADOVATE_APP_VERSION", "1.0"),
        }

        self.auth:        Optional[TradovateAuth]   = None
        self.client:      Optional[TradovateClient] = None
        self.account_id:  Optional[int]             = None
        self.position:    Optional[Position]        = None
        self.daily:       DailyStats                = DailyStats()

        # Risk limits (sync with config.py)
        self.max_trades_per_day  = 2
        self.max_losses_per_day  = 2
        self.daily_loss_limit    = 750.0   # $750 = 1.5% of $50k
        self.point_value         = 20.0    # NQ default
        self.commission_per_rt   = 5.00    # $2.50 × 2 sides

    # -- Lifecycle --

    def connect(self) -> bool:
        log.info("Connecting to Tradovate [%s] ...", self.mode)
        self.auth   = TradovateAuth(self.base_url, self.creds)
        self.client = TradovateClient(self.auth)

        if not self.auth.login():
            return False

        accounts = self.client.get_accounts()
        if not accounts:
            log.error("No accounts found")
            return False

        self.account_id = accounts[0]["id"]
        balance = self.client.get_cash_balance(self.account_id)
        log.info("Account %d  |  Balance $%,.0f", self.account_id, balance)
        self.reset_day()
        return True

    def reconnect(self, retries: int = 4) -> bool:
        for attempt in range(1, retries + 1):
            wait = 2 ** attempt
            log.info("Reconnect attempt %d/%d ...", attempt, retries)
            if self.auth and self.auth.login():
                return True
            log.warning("Reconnect %d failed — waiting %ds", attempt, wait)
            time.sleep(wait)
        log.error("All reconnect attempts failed")
        return False

    def reset_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily = DailyStats(date=today)
        self.position = None
        log.info("Daily stats reset for %s", today)

    # -- Risk gate --

    def can_trade(self, symbol: str = "NQ") -> tuple[bool, str]:
        if self.daily.halted:
            return False, f"Day halted: {self.daily.halt_reason}"
        if self.position:
            return False, "Position already open"
        if self.daily.trades >= self.max_trades_per_day:
            return False, f"Max trades reached ({self.max_trades_per_day})"
        if self.daily.losses >= self.max_losses_per_day:
            return False, f"Max losses reached ({self.max_losses_per_day})"
        if self.daily.net_pnl <= -self.daily_loss_limit:
            self._halt(f"Daily loss limit ${self.daily_loss_limit:.0f} hit")
            return False, self.daily.halt_reason
        return True, "ok"

    def _halt(self, reason: str):
        self.daily.halted     = True
        self.daily.halt_reason = reason
        log.warning("SESSION HALTED — %s", reason)
        da.post_risk_alert(
            reason,
            detail=(f"Execution engine [{self.mode}] — "
                    f"{self.daily.trades} trades, net ${self.daily.net_pnl:+,.0f}"),
        )

    # -- Trading --

    def enter(self, symbol: str, direction: str, contracts: int,
              stop_pts: float, target_pts: float) -> Optional[OrderResult]:
        """
        Enter a bracket trade.

        Args:
            symbol:     "NQ" | "ES"
            direction:  "long" | "short"
            contracts:  number of contracts
            stop_pts:   stop distance in points (e.g. 27.0)
            target_pts: target distance in points (e.g. 44.0)

        Returns:
            OrderResult with fill price, or None if blocked.
        """
        ok, reason = self.can_trade(symbol)
        if not ok:
            log.info("Trade blocked: %s", reason)
            return None

        contract = self.client.find_contract(symbol)
        if not contract:
            log.error("Contract not found for %s", symbol)
            return None

        contract_id = contract["id"]
        tick_size   = contract.get("tickSize", 0.25)
        action_in   = "Buy"  if direction == "long" else "Sell"
        action_stop = "Sell" if direction == "long" else "Buy"

        log.info("ENTER %s %s ×%d | stop %.1f pt | target %.1f pt",
                 direction.upper(), symbol, contracts, stop_pts, target_pts)

        result = self.client.place_market_order(
            self.account_id, contract_id, action_in, contracts)

        if "orderId" not in result:
            err = result.get("errorText", str(result))
            log.error("Market order rejected: %s", err)
            return OrderResult(0, "rejected", error_msg=err)

        order_id   = result["orderId"]
        fill_price = self._wait_for_fill(order_id)
        if fill_price is None:
            log.error("Market order did not fill (id=%d)", order_id)
            return OrderResult(order_id, "error", error_msg="fill timeout")

        log.info("Filled @ %.2f", fill_price)

        # Place stop and target
        if direction == "long":
            stop_price   = self._round_tick(fill_price - stop_pts,   tick_size)
            target_price = self._round_tick(fill_price + target_pts,  tick_size)
        else:
            stop_price   = self._round_tick(fill_price + stop_pts,   tick_size)
            target_price = self._round_tick(fill_price - target_pts,  tick_size)

        stop_resp   = self.client.place_stop_order(
            self.account_id, contract_id, action_stop, contracts, stop_price)
        target_resp = self.client.place_limit_order(
            self.account_id, contract_id, action_stop, contracts, target_price)

        stop_id   = stop_resp.get("orderId")
        target_id = target_resp.get("orderId")
        log.info("Stop @ %.2f (id=%s)  Target @ %.2f (id=%s)",
                 stop_price, stop_id, target_price, target_id)

        self.position = Position(
            symbol=symbol, direction=direction, contracts=contracts,
            entry_price=fill_price,
            stop_order_id=stop_id, target_order_id=target_id,
        )

        return OrderResult(order_id, "filled", fill_price=fill_price)

    def exit(self, reason: str = "manual") -> Optional[float]:
        """Market exit the current position. Returns fill price or None."""
        if not self.position:
            log.info("No open position to exit")
            return None

        pos        = self.position
        action_out = "Sell" if pos.direction == "long" else "Buy"
        contract   = self.client.find_contract(pos.symbol)
        if not contract:
            return None

        contract_id = contract["id"]

        # Cancel working stop and target first
        for oid in [pos.stop_order_id, pos.target_order_id]:
            if oid:
                try:
                    self.client.cancel_order(oid)
                except Exception as e:
                    log.warning("Cancel order %d failed: %s", oid, e)

        result = self.client.place_market_order(
            self.account_id, contract_id, action_out, pos.contracts)
        order_id   = result.get("orderId")
        fill_price = self._wait_for_fill(order_id) if order_id else None

        if fill_price:
            self.on_fill(fill_price, reason)

        return fill_price

    def flatten_all(self, reason: str = "flatten_time"):
        """Flatten all open positions (end-of-day or halt)."""
        if self.position:
            log.info("Flattening — %s", reason)
            self.exit(reason)
        log.info("All positions flat")

    def on_fill(self, exit_price: float, reason: str = "fill"):
        """Call when a position is confirmed closed (stop or target hit)."""
        if not self.position:
            return
        pos = self.position

        if pos.direction == "long":
            gross_pts = exit_price - pos.entry_price
        else:
            gross_pts = pos.entry_price - exit_price

        gross_pnl = gross_pts * self.point_value * pos.contracts
        comm      = self.commission_per_rt * pos.contracts
        net_pnl   = gross_pnl - comm

        self.daily.trades      += 1
        self.daily.gross_pnl   += gross_pnl
        self.daily.commissions += comm
        if net_pnl > 0:
            self.daily.wins   += 1
        else:
            self.daily.losses += 1

        log.info("CLOSED %s %s @ %.2f → %.2f | pts %.1f | net $%+.0f [%s]",
                 pos.direction.upper(), pos.symbol,
                 pos.entry_price, exit_price,
                 gross_pts, net_pnl, reason)

        self.position = None

        if self.daily.net_pnl <= -self.daily_loss_limit:
            self._halt(f"Daily loss limit ${self.daily_loss_limit:.0f} breached")

    # -- Helpers --

    def _wait_for_fill(self, order_id: int,
                       timeout: int = 15, poll: float = 0.5) -> Optional[float]:
        """Poll until order fills. Returns fill price or None on timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = self.client._get("/order/item", {"id": order_id})
                status = data.get("ordStatus", "")
                if status == "Filled":
                    fills = data.get("fills", [])
                    if fills:
                        return fills[-1].get("price")
                    # fallback: avgFillPrice
                    return data.get("avgFillPrice")
                if status in ("Rejected", "Cancelled"):
                    log.warning("Order %d status: %s", order_id, status)
                    return None
            except Exception as e:
                log.warning("Poll error: %s", e)
            time.sleep(poll)
        log.error("Fill timeout for order %d", order_id)
        return None

    @staticmethod
    def _round_tick(price: float, tick: float) -> float:
        return round(round(price / tick) * tick, 10)

    def status(self) -> dict:
        return {
            "mode":       self.mode,
            "account_id": self.account_id,
            "position":   vars(self.position) if self.position else None,
            "daily": {
                "date":        self.daily.date,
                "trades":      self.daily.trades,
                "wins":        self.daily.wins,
                "losses":      self.daily.losses,
                "net_pnl":     round(self.daily.net_pnl, 2),
                "win_rate":    round(self.daily.win_rate, 1),
                "halted":      self.daily.halted,
                "halt_reason": self.daily.halt_reason,
            },
        }

    def print_status(self):
        s = self.status()
        print(f"\n{'═'*52}")
        print(f"  ExecutionEngine [{s['mode']}]  account={s['account_id']}")
        print(f"{'─'*52}")
        d = s["daily"]
        print(f"  Date     : {d['date']}")
        print(f"  Trades   : {d['trades']}  (W{d['wins']} / L{d['losses']})")
        print(f"  Net P&L  : ${d['net_pnl']:+,.0f}")
        print(f"  Win Rate : {d['win_rate']:.0f}%")
        if d["halted"]:
            print(f"  *** HALTED: {d['halt_reason']} ***")
        p = s["position"]
        if p:
            print(f"  Position : {p['direction'].upper()} {p['contracts']}c "
                  f"{p['symbol']} @ {p['entry_price']:.2f}")
        else:
            print(f"  Position : FLAT")
        print(f"{'═'*52}\n")


# ---------------------------------------------------------------------------
# Quick connectivity test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    engine = ExecutionEngine()
    if engine.connect():
        engine.print_status()
        print("Tradovate connection OK — ready for paper trading.")
    else:
        print("Connection FAILED — check environment variables.")
        print("Required: TRADOVATE_USERNAME, TRADOVATE_PASSWORD, "
              "TRADOVATE_CID, TRADOVATE_SECRET")
