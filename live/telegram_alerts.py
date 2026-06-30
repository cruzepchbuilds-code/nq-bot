"""
Telegram Alert System — CruzCapital NQ Bot.

All outbound communications go through here: trade signals, exits, daily P&L,
risk halts, research findings, morning briefs.

Setup:
  1. Message @BotFather on Telegram -> /newbot -> copy the token
  2. Add the bot to your chat, get your chat_id:
     Send any message, then open:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     The "chat":{"id": ...} is your chat_id.
  3. Set in config.py:
       TELEGRAM_ALERTS_ENABLED = True
       TELEGRAM_BOT_TOKEN      = "123456:ABC-..."
       TELEGRAM_CHAT_ID        = "123456789"   # your personal chat id
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime

import config


# ── Core sender ──────────────────────────────────────────────────────────────

def send(text: str) -> bool:
    """Send a plain-text message. No-op if alerts disabled or creds missing."""
    if not config.TELEGRAM_ALERTS_ENABLED:
        return False
    token   = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return bool(json.loads(resp.read()).get("ok", False))
    except Exception:
        return False


# ── Bot lifecycle ────────────────────────────────────────────────────────────

def send_startup(symbol: str, feed: str, mode: str) -> bool:
    icon = "🟢"
    text = (
        f"{icon} <b>NQ Bot ACTIVE — {symbol}</b>\n"
        f"Feed    {feed}\n"
        f"Mode    {mode}\n"
        f"Time    {datetime.now().strftime('%H:%M ET  %Y-%m-%d')}"
    )
    return send(text)


def send_shutdown(symbol: str, date, trades: int,
                  session_pnl: float, reason: str = "session complete") -> bool:
    text = (
        f"🔴 <b>NQ Bot OFFLINE — {symbol}</b>\n"
        f"{reason}\n"
        f"Trades {trades}  |  Day P&L ${session_pnl:+,.0f}\n"
        f"{date}"
    )
    return send(text)


# ── Trade lifecycle ───────────────────────────────────────────────────────────

def send_entry(symbol: str, direction: str, contracts: int,
               entry: float, stop: float, target: float,
               score: int, mode: str, ts: datetime) -> bool:
    arrow  = "▲" if direction == "long" else "▼"
    risk   = abs(entry - stop)
    reward = abs(target - entry)
    rr     = reward / risk if risk else 0
    text = (
        f"<b>{arrow} {direction.upper()} {symbol} x{contracts}</b>  [{mode.upper()}]\n"
        f"Entry   {entry:.2f}\n"
        f"Stop    {stop:.2f}  ({risk:.1f} pts)\n"
        f"Target  {target:.2f}  ({reward:.1f} pts)\n"
        f"RR {rr:.1f}x  |  Score {score}/100\n"
        f"{ts.strftime('%H:%M ET')}"
    )
    return send(text)


def send_exit(symbol: str, direction: str, contracts: int,
              entry: float, exit_price: float, pts: float,
              net_pnl: float, result: str, mode: str,
              session_pnl: float, ts: datetime) -> bool:
    icon = "✅" if net_pnl > 0 else "❌"
    text = (
        f"{icon} <b>{result.upper()} {direction.upper()} {symbol} x{contracts}</b>  [{mode.upper()}]\n"
        f"Entry {entry:.2f}  →  Exit {exit_price:.2f}\n"
        f"Pts {pts:+.1f}  |  Net <b>${net_pnl:+,.0f}</b>\n"
        f"Session P&L: ${session_pnl:+,.0f}\n"
        f"{ts.strftime('%H:%M ET')}"
    )
    return send(text)


# ── Daily summaries ───────────────────────────────────────────────────────────

def send_daily_summary(symbol: str, date, trades: int,
                       wins: int, losses: int,
                       session_pnl: float, balance: float) -> bool:
    wr   = wins / trades if trades else 0
    icon = "✅" if session_pnl >= 0 else "❌"
    text = (
        f"{icon} <b>EOD Summary — {symbol}  {date}</b>\n"
        f"Trades  {trades}  ({wins}W / {losses}L  {wr:.0%} WR)\n"
        f"Day P&L <b>${session_pnl:+,.0f}</b>\n"
        f"Balance ${balance:,.0f}"
    )
    return send(text)


def send_morning_brief(symbol: str, date,
                       gap_pts: float, gap_dir: str,
                       regime: str, or_window: str,
                       skip_reason: str = "") -> bool:
    icon = "🟢" if not skip_reason else "🔴"
    lines = [
        f"{icon} <b>Pre-Market Brief — {symbol}  {date}</b>",
        f"Gap     {gap_pts:+.1f} pts  ({gap_dir})",
        f"Regime  {regime}",
        f"OR window  {or_window}",
    ]
    if skip_reason:
        lines.append(f"<b>SKIP: {skip_reason}</b>")
    return send("\n".join(lines))


# ── Risk alerts ───────────────────────────────────────────────────────────────

def send_risk_alert(reason: str, symbol: str = "NQ",
                    balance: float = 0, dd_pct: float = 0) -> bool:
    text = (
        f"🚨 <b>RISK ALERT — {symbol}</b>\n"
        f"{reason}\n"
    )
    if balance:
        text += f"Balance ${balance:,.0f}  |  DD {dd_pct:.1%}"
    return send(text)


def send_halt(reason: str, balance: float, symbol: str = "NQ") -> bool:
    text = (
        f"🛑 <b>TRADING HALTED — {symbol}</b>\n"
        f"{reason}\n"
        f"Balance ${balance:,.0f}"
    )
    return send(text)


# ── Research team ─────────────────────────────────────────────────────────────

def send_research_start(n_hypotheses: int, date) -> bool:
    text = (
        f"🔬 <b>Research session started — {date}</b>\n"
        f"Running {n_hypotheses} hypotheses overnight"
    )
    return send(text)


def send_research_finding(hypothesis: str, result: str,
                          pf_baseline: float, pf_new: float,
                          net_change: float, verdict: str) -> bool:
    icon = "✅" if verdict == "PASS" else "❌"
    text = (
        f"{icon} <b>{hypothesis}</b>\n"
        f"PF  {pf_baseline:.2f} → {pf_new:.2f}\n"
        f"Net change  ${net_change:+,.0f}\n"
        f"Verdict  <b>{verdict}</b>"
    )
    return send(text)


def send_research_summary(date, tested: int, passed: int,
                          best_hypothesis: str, best_gain: float) -> bool:
    text = (
        f"📊 <b>Research complete — {date}</b>\n"
        f"Tested {tested} hypotheses  |  {passed} passed baseline\n"
    )
    if best_hypothesis and best_gain > 0:
        text += f"Best: {best_hypothesis}  (+${best_gain:,.0f})"
    elif passed == 0:
        text += "No improvements found — current config holds"
    return send(text)


# ── Backward compat: old call sites use send_alert() ─────────────────────────

def send_alert(direction, entry, stop, target, score, contracts, timestamp,
               mode="breakout"):
    return send_entry("NQ", direction, contracts, entry, stop, target,
                      score, mode, timestamp)
