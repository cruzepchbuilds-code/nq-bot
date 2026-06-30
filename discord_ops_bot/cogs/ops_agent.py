"""Ops Agent — health checks, help, server info, and a heartbeat poster."""
import time
import platform
import discord
from discord import app_commands
from discord.ext import commands, tasks

from shared import config

START_TIME = time.time()

AGENT_DESCRIPTIONS = {
    "CI Agent":       ("#ci-cd", "/ci status, /ci runs — GitHub Actions build/test status"),
    "Review Agent":   ("#code-review", "/review pending — open PRs awaiting review"),
    "Deploy Agent":   ("#deployments", "/deploy status, /deploy log — deployment tracking"),
    "Research Agent": ("#research-alerts", "/research latest, /research backtest — NQ/ES strategy results"),
    "Ops Agent":      ("#ops-log / #bot-status", "/ping, /status, /agents — health + heartbeat"),
}


class OpsAgent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.heartbeat.start()

    def cog_unload(self):
        self.heartbeat.cancel()

    @app_commands.command(name="ping", description="Check the Ops Agent's latency")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! `{latency_ms}ms`")

    @app_commands.command(name="status", description="Show bot uptime and loaded agents")
    async def status(self, interaction: discord.Interaction):
        uptime = int(time.time() - START_TIME)
        h, rem = divmod(uptime, 3600)
        m, s = divmod(rem, 60)
        embed = discord.Embed(title="🤖 Ops Bot Status", color=discord.Color.green())
        embed.add_field(name="Uptime", value=f"{h}h {m}m {s}s", inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency*1000)}ms", inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="Loaded agents", value="\n".join(f"`{c}`" for c in self.bot.cogs), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="agents", description="List all agents and their commands")
    async def agents(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 Ops Server — Agent Directory",
            description="Each agent posts to its own channel and exposes slash commands.",
            color=discord.Color.blurple(),
        )
        for name, (channel, cmds) in AGENT_DESCRIPTIONS.items():
            embed.add_field(name=f"{name} → {channel}", value=cmds, inline=False)
        await interaction.response.send_message(embed=embed)

    @tasks.loop(minutes=30)
    async def heartbeat(self):
        ch_id = config.channel_id("bot-status")
        if not ch_id:
            return
        channel = self.bot.get_channel(ch_id)
        if channel is None:
            return
        uptime = int(time.time() - START_TIME)
        h, rem = divmod(uptime, 3600)
        m, _ = divmod(rem, 60)
        await channel.send(f"💓 Ops bot alive — uptime {h}h {m}m, latency {round(self.bot.latency*1000)}ms")

    @heartbeat.before_loop
    async def before_heartbeat(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(OpsAgent(bot))
