#!/usr/bin/env python3
"""
configure_channels.py — Lock each agent's "home" channel so only that
agent's bot role can post there, and create a shared #agent-general
channel (under the 🤖 OPS AGENTS category) where all 5 agent roles —
and humans — can post.

For each agent in shared.config.AGENT_HOME_CHANNEL:
  - @everyone: can view + read history, but SEND_MESSAGES denied
  - <agent>'s role (e.g. "CI Agent"): can view + send + read history

Then creates (or updates the permissions of) #agent-general:
  - @everyone: can view + send + read history
  - every agent role: can view + send + read history

Idempotent — safe to re-run. Uses the Ops Agent's token (Administrator).

Usage:
  ./venv/bin/python configure_channels.py
"""
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

OPS_CATEGORY_NAME = "🤖 OPS AGENTS"
SHARED_CHANNEL_NAME = "agent-general"

ENV_PATH = config.ROOT / ".env"


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


def set_perm(headers, channel_id, target_id, allow, deny):
    r = requests.put(
        f"{API}/channels/{channel_id}/permissions/{target_id}",
        headers=headers,
        json={"type": 0, "allow": str(allow), "deny": str(deny)},
    )
    return r


def main():
    admin_token = config.agent_token("ops")
    if not admin_token:
        print("ERROR: DISCORD_BOT_TOKEN_OPS (or DISCORD_BOT_TOKEN) not set in .env")
        sys.exit(1)
    if not config.DISCORD_GUILD_ID:
        print("ERROR: DISCORD_GUILD_ID not set in .env")
        sys.exit(1)

    headers = {"Authorization": f"Bot {admin_token}"}
    guild_id = config.DISCORD_GUILD_ID
    everyone_id = str(guild_id)  # @everyone role id == guild id

    r = requests.get(f"{API}/guilds/{guild_id}/roles", headers=headers)
    r.raise_for_status()
    roles_by_name = {role["name"]: role["id"] for role in r.json()}

    r = requests.get(f"{API}/guilds/{guild_id}/channels", headers=headers)
    r.raise_for_status()
    channels = r.json()
    channels_by_name = {c["name"]: c for c in channels}

    # ── 1. Lock each agent's home channel ───────────────────────────────────
    for agent, ch_name in config.AGENT_HOME_CHANNEL.items():
        ch = channels_by_name.get(ch_name)
        if not ch:
            print(f"  skip #{ch_name}: channel not found (run setup_server.py)")
            continue
        role_name = config.AGENTS[agent]["role"]
        role_id = roles_by_name.get(role_name)
        if not role_id:
            print(f"  skip #{ch_name}: role '{role_name}' not found (run setup_server.py)")
            continue

        r1 = set_perm(headers, ch["id"], everyone_id, READ_ONLY_ALLOW, SEND_MESSAGES)
        r2 = set_perm(headers, ch["id"], role_id, FULL_ALLOW, 0)
        ok = r1.status_code == 204 and r2.status_code == 204
        print(f"  #{ch_name}: locked to '{role_name}' only "
              f"{'OK' if ok else f'(everyone={r1.status_code}/{r1.text}, role={r2.status_code}/{r2.text})'}")

    # ── 2. Create / update the shared #agent-general channel ────────────────
    ops_cat = next((c for c in channels if c["type"] == 4 and c["name"] == OPS_CATEGORY_NAME), None)
    if not ops_cat:
        print(f"ERROR: category '{OPS_CATEGORY_NAME}' not found (run setup_server.py)")
        sys.exit(1)

    overwrites = [{"id": everyone_id, "type": 0, "allow": str(FULL_ALLOW), "deny": "0"}]
    for agent in config.AGENT_HOME_CHANNEL:
        role_id = roles_by_name.get(config.AGENTS[agent]["role"])
        if role_id:
            overwrites.append({"id": role_id, "type": 0, "allow": str(FULL_ALLOW), "deny": "0"})

    existing = channels_by_name.get(SHARED_CHANNEL_NAME)
    if existing:
        for ow in overwrites:
            set_perm(headers, existing["id"], ow["id"], int(ow["allow"]), 0)
        ch_id = existing["id"]
        print(f"  #{SHARED_CHANNEL_NAME}: exists (id={ch_id}), permissions refreshed for all 5 agent roles")
    else:
        r = requests.post(
            f"{API}/guilds/{guild_id}/channels",
            headers=headers,
            json={
                "name": SHARED_CHANNEL_NAME,
                "type": 0,
                "topic": "Shared channel — all 5 agents post general updates here; humans welcome too",
                "parent_id": ops_cat["id"],
                "permission_overwrites": overwrites,
            },
        )
        if r.status_code == 201:
            ch_id = r.json()["id"]
            print(f"  #{SHARED_CHANNEL_NAME}: created (id={ch_id}) under '{OPS_CATEGORY_NAME}'")
        else:
            print(f"  ERROR creating #{SHARED_CHANNEL_NAME}: {r.status_code} {r.text}")
            sys.exit(1)

    update_env({"CHANNEL_AGENT_GENERAL": ch_id})
    print(f"\nWrote CHANNEL_AGENT_GENERAL={ch_id} to {ENV_PATH}")


if __name__ == "__main__":
    main()
