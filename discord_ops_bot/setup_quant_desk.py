#!/usr/bin/env python3
"""
setup_quant_desk.py — Expand the ops server into a full quant-trading-desk
layout, modeled on a real prop-trading firm's internal comms:

  Role "Trading Desk"   (gold)  -> granted to Ops Agent
  Role "Risk Desk"      (red)   -> granted to Ops Agent
  Role "Quant Research" (teal)  -> granted to Research Agent

  Category "📈 TRADING DESK"
    #pre-market    — overnight gap / session-open notes (NQ + ES)
    #signals       — live ORB entry signals
    #trade-log     — executed trade entries/exits
    #daily-pnl     — end-of-day P&L + equity recap

  Category "🛡️ RISK DESK"
    #risk-alerts     — drawdown / loss-limit breach alerts
    #account-status  — Apex eval/funded equity + drawdown curve

  Category "🔬 QUANT RESEARCH"
    #nq-strategy    — NQ v7 config + changelog
    #es-strategy    — ES config + changelog
    #walk-forward   — OOS walk-forward validation results
    #brain-insights — pattern-engine findings (brain/insights.md)

Each new channel: @everyone can view + read history but not post; the
matching desk role can post (and whichever bot holds that role). Posts a
short "desk online" message in each new channel from the owning bot.

Idempotent — safe to re-run. Uses the Ops Agent's token (Administrator)
for provisioning, plus the Research Agent's token for its own intro posts.

Usage:
  ./venv/bin/python setup_quant_desk.py
"""
import base64
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import config

API = "https://discord.com/api/v10"

VIEW_CHANNEL = 1 << 10   # 1024
SEND_MESSAGES = 1 << 11  # 2048
READ_HISTORY = 1 << 16   # 65536

READ_ONLY_ALLOW = VIEW_CHANNEL | READ_HISTORY
FULL_ALLOW = VIEW_CHANNEL | SEND_MESSAGES | READ_HISTORY

ENV_PATH = config.ROOT / ".env"

# role name -> (color int, owning agent key)
DESK_ROLES = {
    "Trading Desk":   (0xF1C40F, "ops"),       # gold
    "Risk Desk":      (0xE74C3C, "ops"),       # red
    "Quant Research": (0x1ABC9C, "research"),  # teal
}

NEW_CATEGORIES = ["📈 TRADING DESK", "🛡️ RISK DESK", "🔬 QUANT RESEARCH"]

# channel name -> (topic, category, desk role, env key, intro author agent, intro text)
NEW_CHANNELS = {
    "pre-market": (
        "Overnight gap + session-open notes (NQ/ES)", "📈 TRADING DESK", "Trading Desk",
        "CHANNEL_PRE_MARKET", "ops",
        "**Trading Desk online** 📈\nDaily pre-market notes (overnight gap, ON range, "
        "session bias) post here before the open.",
    ),
    "signals": (
        "Live NQ/ES ORB entry signals", "📈 TRADING DESK", "Trading Desk",
        "CHANNEL_SIGNALS", "ops",
        "**Signals feed online** 📡\nLive ORB breakout entries (NQ + ES, v7/ES-calibrated) "
        "post here in real time once the live bot is connected.",
    ),
    "trade-log": (
        "Executed trade entries/exits", "📈 TRADING DESK", "Trading Desk",
        "CHANNEL_TRADE_LOG", "ops",
        "**Trade log online** 🧾\nEvery fill (entry, stop, target, exit reason, P&L) "
        "gets logged here for the record.",
    ),
    "daily-pnl": (
        "End-of-day P&L + equity recap", "📈 TRADING DESK", "Trading Desk",
        "CHANNEL_DAILY_PNL", "ops",
        "**Daily P&L recap online** 💰\nEnd-of-day summary: trades taken, win rate, "
        "net P&L, running equity curve.",
    ),
    "risk-alerts": (
        "Drawdown / loss-limit breach alerts", "🛡️ RISK DESK", "Risk Desk",
        "CHANNEL_RISK_ALERTS", "ops",
        "**Risk Desk online** ⚠️\nDrawdown thresholds, max-daily-loss hits, and "
        "consecutive-loss-day pauses (MAX_CONSEC_LOSING_DAYS) get flagged here.",
    ),
    "account-status": (
        "Apex eval/funded account equity + drawdown curve", "🛡️ RISK DESK", "Risk Desk",
        "CHANNEL_ACCOUNT_STATUS", "ops",
        "**Account status online** 🏦\nApex eval/funded balance, trailing drawdown, "
        "and EVAL_MODE status post here.",
    ),
    "nq-strategy": (
        "NQ v7 config + changelog", "🔬 QUANT RESEARCH", "Quant Research",
        "CHANNEL_NQ_STRATEGY", "research",
        "**NQ strategy desk online** 🟦\nv7 config (`config.py`) changes, parameter "
        "tweaks, and rationale get logged here.\nCurrent: breakout-only, "
        "LAST_ENTRY=10:30, OR 55-110pt, OOS PF 2.14.",
    ),
    "es-strategy": (
        "ES config + changelog", "🔬 QUANT RESEARCH", "Quant Research",
        "CHANNEL_ES_STRATEGY", "research",
        "**ES strategy desk online** 🟧\nES (`es_config.py`) calibration changes get "
        "logged here.\nCurrent: ORB_FIXED_STOP=6pt, BUFFER=2.25pt, OOS PF 1.61.",
    ),
    "walk-forward": (
        "OOS walk-forward validation results", "🔬 QUANT RESEARCH", "Quant Research",
        "CHANNEL_WALK_FORWARD", "research",
        "**Walk-forward results online** 📐\nYear-by-year IS/OOS walk-forward runs "
        "(`walk_forward.py`, `es_backtest.py`) post their summary tables here.",
    ),
    "brain-insights": (
        "Pattern-engine findings (brain/insights.md)", "🔬 QUANT RESEARCH", "Quant Research",
        "CHANNEL_BRAIN_INSIGHTS", "research",
        "**Brain insights online** 🧠\nPattern-engine findings from `brain/pattern_engine.py` "
        "(`brain/insights.md`) — edge grades, win-rate breakdowns, filter candidates.",
    ),
}


def client_id_from_token(token: str) -> str:
    seg = token.split(".")[0]
    seg += "=" * (-len(seg) % 4)
    return base64.b64decode(seg).decode()


def update_env(updates: dict[str, str]):
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0]
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    for key, val in updates.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def main():
    admin_token = config.agent_token("ops")
    if not admin_token:
        print("ERROR: DISCORD_BOT_TOKEN_OPS not set in .env")
        sys.exit(1)
    guild_id = config.DISCORD_GUILD_ID
    if not guild_id:
        print("ERROR: DISCORD_GUILD_ID not set in .env")
        sys.exit(1)

    headers = {"Authorization": f"Bot {admin_token}"}
    everyone_id = str(guild_id)

    # ── Roles ────────────────────────────────────────────────────────────
    r = requests.get(f"{API}/guilds/{guild_id}/roles", headers=headers)
    r.raise_for_status()
    roles_by_name = {role["name"]: role["id"] for role in r.json()}

    for role_name, (color, _) in DESK_ROLES.items():
        if role_name in roles_by_name:
            print(f"  Role exists:  {role_name}")
            continue
        r = requests.post(
            f"{API}/guilds/{guild_id}/roles", headers=headers,
            json={"name": role_name, "color": color, "mentionable": True, "hoist": True},
        )
        if r.status_code in (200, 201):
            roles_by_name[role_name] = r.json()["id"]
            print(f"  Role created: {role_name}")
        else:
            print(f"  ERROR creating role {role_name}: {r.status_code} {r.text}")
            sys.exit(1)

    # ── Categories ───────────────────────────────────────────────────────
    r = requests.get(f"{API}/guilds/{guild_id}/channels", headers=headers)
    r.raise_for_status()
    channels = r.json()
    channels_by_name = {c["name"]: c for c in channels}
    categories_by_name = {c["name"]: c for c in channels if c["type"] == 4}

    for cat_name in NEW_CATEGORIES:
        if cat_name in categories_by_name:
            print(f"  Category exists: {cat_name}")
            continue
        r = requests.post(
            f"{API}/guilds/{guild_id}/channels", headers=headers,
            json={"name": cat_name, "type": 4},
        )
        if r.status_code in (200, 201):
            cat = r.json()
            categories_by_name[cat_name] = cat
            channels_by_name[cat_name] = cat
            print(f"  Category created: {cat_name}")
        else:
            print(f"  ERROR creating category {cat_name}: {r.status_code} {r.text}")
            sys.exit(1)

    # ── Channels ─────────────────────────────────────────────────────────
    env_updates: dict[str, str] = {}
    intro_targets = []  # (agent, channel_id, text)

    for ch_name, (topic, cat_name, desk_role, env_key, intro_agent, intro_text) in NEW_CHANNELS.items():
        cat_id = categories_by_name[cat_name]["id"]
        desk_role_id = roles_by_name[desk_role]
        overwrites = [
            {"id": everyone_id, "type": 0, "allow": str(READ_ONLY_ALLOW), "deny": str(SEND_MESSAGES)},
            {"id": desk_role_id, "type": 0, "allow": str(FULL_ALLOW), "deny": "0"},
        ]

        existing = channels_by_name.get(ch_name)
        if existing:
            ch_id = existing["id"]
            for ow in overwrites:
                requests.put(f"{API}/channels/{ch_id}/permissions/{ow['id']}", headers=headers,
                              json={"type": ow["type"], "allow": ow["allow"], "deny": ow["deny"]})
            print(f"  Channel exists: #{ch_name} (permissions refreshed)")
        else:
            r = requests.post(
                f"{API}/guilds/{guild_id}/channels", headers=headers,
                json={"name": ch_name, "type": 0, "topic": topic, "parent_id": cat_id,
                      "permission_overwrites": overwrites},
            )
            if r.status_code in (200, 201):
                ch_id = r.json()["id"]
                print(f"  Channel created: #{ch_name} -> {cat_name}")
                intro_targets.append((intro_agent, ch_id, intro_text))
            else:
                print(f"  ERROR creating #{ch_name}: {r.status_code} {r.text}")
                continue

        env_updates[env_key] = str(ch_id)

    update_env(env_updates)
    print(f"\nWrote {len(env_updates)} channel IDs to {ENV_PATH}")

    # ── Grant desk roles to the owning bots ─────────────────────────────────
    for role_name, (_, agent) in DESK_ROLES.items():
        agent_tok = config.agent_token(agent)
        if not agent_tok:
            print(f"  skip role grant '{role_name}': DISCORD_BOT_TOKEN_{agent.upper()} not set")
            continue
        user_id = client_id_from_token(agent_tok)
        role_id = roles_by_name[role_name]
        r = requests.put(f"{API}/guilds/{guild_id}/members/{user_id}/roles/{role_id}", headers=headers)
        if r.status_code == 204:
            print(f"  Granted '{role_name}' to {config.AGENTS[agent]['display']}")
        else:
            print(f"  ERROR granting '{role_name}' to {agent}: {r.status_code} {r.text}")

    # ── Post "desk online" intro messages for newly-created channels ───────
    for agent, ch_id, text in intro_targets:
        tok = config.agent_token(agent)
        if not tok:
            continue
        r = requests.post(
            f"{API}/channels/{ch_id}/messages",
            headers={"Authorization": f"Bot {tok}"},
            json={"content": text},
        )
        if r.status_code not in (200, 201):
            print(f"  WARN: intro post to {ch_id} failed: {r.status_code} {r.text}")

    print("\nQuant desk setup complete.")


if __name__ == "__main__":
    main()
