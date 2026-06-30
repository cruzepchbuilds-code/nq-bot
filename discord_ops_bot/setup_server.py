#!/usr/bin/env python3
"""
setup_server.py — One-shot provisioner for the multi-agent ops Discord server.

Creates (idempotently — safe to re-run):
  Category "🤖 OPS AGENTS"
    #ci-cd            — CI Agent   (build/test status from GitHub Actions)
    #code-review      — Review Agent (open PRs needing review)
    #deployments      — Deploy Agent (deploy triggers/status)
    #research-alerts  — Research Agent (NQ/ES backtest + walk-forward results)
    #bot-status       — Ops Agent  (live trading bot heartbeat)
    #ops-log          — Ops Agent  (general bot/system log)
  Category "💬 GENERAL"
    #general
    #announcements

  Roles: "CI Agent", "Review Agent", "Deploy Agent", "Research Agent", "Ops Agent"
  Incoming webhooks for ci-cd / code-review / deployments / research-alerts
    (so webhook_server.py can post without the bot process running)

Writes the resulting channel IDs + webhook URLs back into .env.

Usage:
  1. cp .env.example .env
  2. Fill in DISCORD_BOT_TOKEN and DISCORD_GUILD_ID
  3. Invite the bot to your server with the "bot" + "applications.commands"
     scopes and the "Manage Channels", "Manage Roles", "Manage Webhooks"
     permissions (Administrator is simplest for first run).
  4. ./venv/bin/python setup_server.py
"""
import asyncio
import sys
from pathlib import Path

import discord

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import config

ENV_PATH = config.ROOT / ".env"

AGENT_ROLES = {
    "CI Agent":       discord.Color.green(),
    "Review Agent":   discord.Color.blue(),
    "Deploy Agent":   discord.Color.orange(),
    "Research Agent": discord.Color.purple(),
    "Ops Agent":      discord.Color.dark_grey(),
}

OPS_CATEGORY = "🤖 OPS AGENTS"
GENERAL_CATEGORY = "💬 GENERAL"

# channel name -> (topic, needs_incoming_webhook)
OPS_CHANNELS = {
    "ci-cd":            ("CI Agent: build / test status from GitHub Actions", True),
    "code-review":      ("Review Agent: open PRs awaiting review", True),
    "deployments":      ("Deploy Agent: deploy triggers + status", True),
    "research-alerts":  ("Research Agent: NQ/ES backtest + walk-forward results", True),
    "bot-status":       ("Ops Agent: live trading bot heartbeat", False),
    "ops-log":          ("Ops Agent: general system/bot log", False),
}

GENERAL_CHANNELS = {
    "general":        ("General discussion", False),
    "announcements":  ("Project announcements", False),
}


def update_env(updates: dict[str, str]):
    """Read .env, update/append keys, write back."""
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()

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


async def main():
    if not config.DISCORD_BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set. Copy .env.example to .env and fill it in.")
        return
    if not config.DISCORD_GUILD_ID:
        print("ERROR: DISCORD_GUILD_ID not set. Copy .env.example to .env and fill it in.")
        return

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    env_updates: dict[str, str] = {}

    @client.event
    async def on_ready():
        try:
            guild = client.get_guild(config.DISCORD_GUILD_ID)
            if guild is None:
                guild = await client.fetch_guild(config.DISCORD_GUILD_ID)
            print(f"Connected to guild: {guild.name} ({guild.id})")

            # ── Roles ──────────────────────────────────────────────────────
            existing_roles = {r.name: r for r in guild.roles}
            for role_name, color in AGENT_ROLES.items():
                if role_name in existing_roles:
                    print(f"  Role exists:   {role_name}")
                    continue
                await guild.create_role(name=role_name, color=color, mentionable=True,
                                         reason="ops bot setup")
                print(f"  Role created:  {role_name}")

            # ── Categories ─────────────────────────────────────────────────
            existing_categories = {c.name: c for c in guild.categories}

            async def get_or_create_category(name):
                if name in existing_categories:
                    print(f"  Category exists: {name}")
                    return existing_categories[name]
                cat = await guild.create_category(name, reason="ops bot setup")
                print(f"  Category created: {name}")
                existing_categories[name] = cat
                return cat

            ops_cat = await get_or_create_category(OPS_CATEGORY)
            gen_cat = await get_or_create_category(GENERAL_CATEGORY)

            # ── Channels ───────────────────────────────────────────────────
            existing_channels = {c.name: c for c in guild.text_channels}

            async def get_or_create_channel(name, topic, category):
                if name in existing_channels:
                    print(f"  Channel exists: #{name}")
                    return existing_channels[name]
                ch = await guild.create_text_channel(name, topic=topic, category=category,
                                                       reason="ops bot setup")
                print(f"  Channel created: #{name}")
                existing_channels[name] = ch
                return ch

            all_channels = {}
            for name, (topic, _) in OPS_CHANNELS.items():
                ch = await get_or_create_channel(name, topic, ops_cat)
                all_channels[name] = ch
                env_key = config.CHANNEL_ENV_KEYS.get(name)
                if env_key:
                    env_updates[env_key] = str(ch.id)

            for name, (topic, _) in GENERAL_CHANNELS.items():
                ch = await get_or_create_channel(name, topic, gen_cat)
                all_channels[name] = ch
                env_key = config.CHANNEL_ENV_KEYS.get(name)
                if env_key:
                    env_updates[env_key] = str(ch.id)

            # ── Incoming webhooks ──────────────────────────────────────────
            for name, (_, needs_hook) in OPS_CHANNELS.items():
                if not needs_hook:
                    continue
                ch = all_channels[name]
                hooks = await ch.webhooks()
                hook = next((h for h in hooks if h.name == "ops-bot-relay"), None)
                if hook is None:
                    hook = await ch.create_webhook(name="ops-bot-relay", reason="ops bot setup")
                    print(f"  Webhook created: #{name}")
                else:
                    print(f"  Webhook exists:  #{name}")
                env_key = config.WEBHOOK_ENV_KEYS.get(name)
                if env_key:
                    env_updates[env_key] = hook.url

            # ── Welcome posts ──────────────────────────────────────────────
            ops_log = all_channels["ops-log"]
            await ops_log.send(
                "**Ops server provisioned** ✅\n"
                "Agents online: `CI Agent`, `Review Agent`, `Deploy Agent`, "
                "`Research Agent`, `Ops Agent`.\n"
                "Run `bot.py` to enable slash commands, and `webhook_server.py` "
                "to relay GitHub events into `#ci-cd`, `#code-review`, "
                "`#deployments`, and backtest results into `#research-alerts`."
            )

            update_env(env_updates)
            print(f"\nWrote {len(env_updates)} keys to {ENV_PATH}")
            print("Setup complete. Now run: ./venv/bin/python bot.py")
        except Exception as e:
            print(f"ERROR during setup: {e}")
            raise
        finally:
            await client.close()

    await client.start(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
