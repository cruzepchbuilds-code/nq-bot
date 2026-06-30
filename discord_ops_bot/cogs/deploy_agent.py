"""Deploy Agent — deployment status, posted to #deployments.

Other scripts report deploys by calling `report_deploy.py` (or POSTing to
webhook_server.py's /deploy endpoint), which appends to data/deploys.json.
This cog just reads that file for /deploy commands.
"""
import json

import discord
from discord import app_commands
from discord.ext import commands

from shared import config

DEPLOYS_FILE = config.DATA_DIR / "deploys.json"


def _load_deploys() -> list[dict]:
    if DEPLOYS_FILE.exists():
        return json.loads(DEPLOYS_FILE.read_text())
    return []


class DeployAgent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="deploy", description="Show recent deployment status")
    @app_commands.describe(scope="status = most recent deploy, log = last 5 deploys")
    @app_commands.choices(scope=[
        app_commands.Choice(name="status", value="status"),
        app_commands.Choice(name="log", value="log"),
    ])
    async def deploy(self, interaction: discord.Interaction, scope: app_commands.Choice[str]):
        deploys = _load_deploys()
        if not deploys:
            await interaction.response.send_message(
                "No deploys recorded yet. Use `report_deploy.py` from your deploy "
                "scripts to log one.", ephemeral=True)
            return

        n = 1 if scope.value == "status" else 5
        recent = deploys[-n:][::-1]

        embed = discord.Embed(title="🚀 Deployments", color=discord.Color.orange())
        for d in recent:
            status_emoji = "✅" if d.get("status") == "success" else "❌" if d.get("status") == "failure" else "🟡"
            embed.add_field(
                name=f"{status_emoji} {d.get('target', 'unknown')} — {d.get('version', '')}",
                value=f"{d.get('message', '')}\n_{d.get('timestamp', '')}_",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(DeployAgent(bot))
