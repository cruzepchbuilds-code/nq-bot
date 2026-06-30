#!/usr/bin/env python3
"""
bot.py — Launch ONE agent as its own Discord bot identity.

Each agent (ops, ci, review, deploy, research) is a separate Discord
Application with its own token, name, and avatar (see generate_avatars.py
and set_identity.py). This script loads just that agent's cog under its
own bot token and syncs only its slash commands.

Usage:
  ./venv/bin/python bot.py --agent ops
  ./venv/bin/python bot.py --agent ci
  ./venv/bin/python bot.py --agent review
  ./venv/bin/python bot.py --agent deploy
  ./venv/bin/python bot.py --agent research

Or run all five at once with run_all.sh.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

import discord
from discord.ext import commands

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import config

INTENTS = discord.Intents.default()
INTENTS.message_content = False  # cogs only use slash commands


class AgentBot(commands.Bot):
    def __init__(self, agent: str):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.agent = agent
        self.cog_path = config.AGENTS[agent]["cog"]

    async def setup_hook(self):
        await self.load_extension(self.cog_path)
        log.info("[%s] Loaded %s", self.agent, self.cog_path)

        if config.DISCORD_GUILD_ID:
            guild = discord.Object(id=config.DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("[%s] Synced %d commands to guild %s", self.agent, len(synced), config.DISCORD_GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("[%s] Synced %d global commands (may take up to 1h to appear)", self.agent, len(synced))

    async def on_ready(self):
        log.info("[%s] Logged in as %s (id=%s)", self.agent, self.user, self.user.id)


async def main(agent: str):
    if agent not in config.AGENTS:
        log.error("Unknown agent '%s'. Choices: %s", agent, ", ".join(config.AGENTS))
        return

    token = config.agent_token(agent)
    if not token:
        log.error("No token for agent '%s'. Set DISCORD_BOT_TOKEN_%s in .env "
                   "(see README for creating per-agent bot applications).",
                   agent, agent.upper())
        return

    bot = AgentBot(agent)
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, choices=list({"ops", "ci", "review", "deploy", "research"}))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger(f"ops-bot.{args.agent}")

    asyncio.run(main(args.agent))
