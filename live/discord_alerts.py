"""
live/discord_alerts.py

Posts live-trading events to the CruzCapital quant-desk Discord server
(see ../discord_ops_bot/). Uses the Ops Agent's bot token to post directly
into the channels created by discord_ops_bot/setup_quant_desk.py:

    #signals          — ORB entry signals
    #trade-log        — executed trade entries/exits
    #daily-pnl        — end-of-day P&L + equity recap
    #risk-alerts      — drawdown / loss-limit breaches
    #account-status   — Apex eval/funded equity + DD curve
    #pre-market       — morning go/no-go checklist

No-op (returns False, never raises) if config.DISCORD_ALERTS_ENABLED is
False, or if discord_ops_bot/.env / the bot token / channel IDs aren't
found — so this is always safe to call from backtests or sessions where
the ops bot hasn't been set up.
"""

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

API = "https://discord.com/api/v10"
ENV_PATH = Path(__file__).resolve().parent.parent / "discord_ops_bot" / ".env"

# Embed colors
GREEN  = 0x2ECC71
RED    = 0xE74C3C
BLUE   = 0x3498DB
GOLD   = 0xF1C40F
GREY   = 0x95A5A6
ORANGE = 0xE67E22

_CREDS_CACHE: dict | None = None


def _load_creds() -> dict:
    """Parse discord_ops_bot/.env once (token + channel IDs). Cached."""
    global _CREDS_CACHE
    if _CREDS_CACHE is not None:
        return _CREDS_CACHE

    creds: dict = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            creds[key.strip()] = val.strip()
    _CREDS_CACHE = creds
    return creds


def _enabled() -> bool:
    return bool(getattr(config, "DISCORD_ALERTS_ENABLED", False))


def _post(channel_env_key: str, embed: dict) -> bool:
    if not _enabled():
        return False
    creds = _load_creds()
    token = creds.get("DISCORD_BOT_TOKEN_OPS") or creds.get("DISCORD_BOT_TOKEN")
    channel_id = creds.get(channel_env_key)
    if not token or not channel_id:
        return False
    try:
        r = requests.post(
            f"{API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}"},
            json={"embeds": [embed]},
            timeout=5,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Trading desk
# ---------------------------------------------------------------------------

def post_signal(symbol: str, direction: str, entry: float, stop: float,
                target: float, contracts: int, score: int, mode: str, timestamp) -> bool:
    """#signals — a new ORB entry signal just fired."""
    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr = reward / risk if risk > 0 else 0.0
    arrow = "🟢 LONG" if direction == "long" else "🔴 SHORT"
    color = GREEN if direction == "long" else RED

    embed = {
        "title": f"📡 {symbol} Signal — {arrow}",
        "color": color,
        "fields": [
            {"name": "Entry", "value": f"{entry:.2f}", "inline": True},
            {"name": "Stop", "value": f"{stop:.2f} ({risk:.1f}pt)", "inline": True},
            {"name": "Target", "value": f"{target:.2f} ({reward:.1f}pt)", "inline": True},
            {"name": "R:R", "value": f"{rr:.1f}x", "inline": True},
            {"name": "Score", "value": f"{score}/100", "inline": True},
            {"name": "Size", "value": f"{contracts}c", "inline": True},
            {"name": "Mode", "value": mode, "inline": True},
        ],
        "footer": {"text": timestamp.strftime("%Y-%m-%d %H:%M ET")},
    }
    return _post("CHANNEL_SIGNALS", embed)


def post_trade_close(symbol: str, direction: str, contracts: int, entry: float,
                      exit_price: float, points: float, net_pnl: float, result: str,
                      mode: str, session_pnl: float, timestamp) -> bool:
    """#trade-log — a position was closed (stop, target, or flatten)."""
    win = net_pnl > 0
    emoji = "✅" if win else ("⚪" if net_pnl == 0 else "❌")
    color = GREEN if win else (GREY if net_pnl == 0 else RED)
    dir_label = "LONG" if direction == "long" else "SHORT"

    embed = {
        "title": f"{emoji} {symbol} {dir_label} closed — {result.upper()}",
        "color": color,
        "fields": [
            {"name": "Entry -> Exit", "value": f"{entry:.2f} -> {exit_price:.2f}", "inline": True},
            {"name": "Points", "value": f"{points:+.1f}", "inline": True},
            {"name": "Size", "value": f"{contracts}c", "inline": True},
            {"name": "Net P&L", "value": f"${net_pnl:+,.0f}", "inline": True},
            {"name": "Session P&L", "value": f"${session_pnl:+,.0f}", "inline": True},
            {"name": "Mode", "value": mode, "inline": True},
        ],
        "footer": {"text": timestamp.strftime("%Y-%m-%d %H:%M ET")},
    }
    return _post("CHANNEL_TRADE_LOG", embed)


def post_daily_pnl(symbol: str, session_date, trades: int, wins: int, losses: int,
                    net_pnl: float, bank_balance: float) -> bool:
    """#daily-pnl — end-of-day recap."""
    wr = (wins / trades * 100) if trades else 0.0
    color = GREEN if net_pnl > 0 else (GREY if net_pnl == 0 else RED)

    embed = {
        "title": f"💰 {symbol} Daily Recap — {session_date}",
        "color": color,
        "fields": [
            {"name": "Trades", "value": f"{trades} (W{wins}/L{losses})", "inline": True},
            {"name": "Win Rate", "value": f"{wr:.0f}%", "inline": True},
            {"name": "Net P&L", "value": f"${net_pnl:+,.0f}", "inline": True},
            {"name": "Equity", "value": f"${bank_balance:,.0f}", "inline": True},
        ],
    }
    return _post("CHANNEL_DAILY_PNL", embed)


# ---------------------------------------------------------------------------
# Risk desk
# ---------------------------------------------------------------------------

def post_risk_alert(reason: str, detail: str = "", symbol: str = "") -> bool:
    """#risk-alerts — a halt/drawdown/loss-limit condition tripped."""
    title = f"⚠️ Risk Alert{f' — {symbol}' if symbol else ''}"
    embed = {
        "title": title,
        "color": RED,
        "description": reason,
    }
    if detail:
        embed["fields"] = [{"name": "Detail", "value": detail, "inline": False}]
    return _post("CHANNEL_RISK_ALERTS", embed)


def post_account_status(mode: str, balance: float, peak: float, dd_floor: float,
                         headroom: float, eval_mode: bool, trade_count: int) -> bool:
    """#account-status — Apex eval/funded equity + drawdown curve snapshot."""
    color = RED if headroom < (peak - dd_floor) * 0.25 else (ORANGE if headroom < (peak - dd_floor) * 0.5 else GREEN)
    embed = {
        "title": f"🏦 Account Status — {mode}{' (EVAL)' if eval_mode else ' (FUNDED)'}",
        "color": color,
        "fields": [
            {"name": "Balance", "value": f"${balance:,.0f}", "inline": True},
            {"name": "Peak", "value": f"${peak:,.0f}", "inline": True},
            {"name": "DD Floor", "value": f"${dd_floor:,.0f}", "inline": True},
            {"name": "Headroom", "value": f"${headroom:,.0f}", "inline": True},
            {"name": "Trades YTD", "value": str(trade_count), "inline": True},
        ],
    }
    return _post("CHANNEL_ACCOUNT_STATUS", embed)


# ---------------------------------------------------------------------------
# Pre-market
# ---------------------------------------------------------------------------

def post_premarket(symbol: str, verdict: str, info: list[str],
                    warnings: list[str], issues: list[str]) -> bool:
    """#pre-market — morning go/no-go checklist summary."""
    if issues:
        color, vtitle = RED, "⛔ NO-GO"
    elif warnings:
        color, vtitle = ORANGE, "⚠️ GO WITH CAUTION"
    else:
        color, vtitle = GREEN, "✅ GO"

    lines = []
    if info:
        lines.append("\n".join(f"• {i}" for i in info))
    if warnings:
        lines.append("**Warnings:**\n" + "\n".join(f"! {w}" for w in warnings))
    if issues:
        lines.append("**Issues:**\n" + "\n".join(f"✗ {i}" for i in issues))

    embed = {
        "title": f"☀️ {symbol} Pre-Market — {vtitle}",
        "color": color,
        "description": "\n\n".join(lines)[:4000],
        "footer": {"text": verdict},
    }
    return _post("CHANNEL_PRE_MARKET", embed)
