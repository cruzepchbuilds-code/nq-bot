"""CI Agent — GitHub Actions build/test status, posted to #ci-cd."""
import discord
from discord import app_commands
from discord.ext import commands, tasks

from shared import config, github_api

STATUS_EMOJI = {
    "success": "✅",
    "failure": "❌",
    "cancelled": "⚪",
    "skipped": "⏭️",
    "timed_out": "⏱️",
    "action_required": "⚠️",
    None: "🟡",  # in_progress / queued have conclusion=None
}


class CIAgent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_seen_run_id: int | None = None
        self.poll_runs.start()

    def cog_unload(self):
        self.poll_runs.cancel()

    @app_commands.command(name="ci", description="Show the latest CI run status")
    @app_commands.describe(scope="status = latest run, runs = last 5 runs")
    @app_commands.choices(scope=[
        app_commands.Choice(name="status", value="status"),
        app_commands.Choice(name="runs", value="runs"),
    ])
    async def ci(self, interaction: discord.Interaction, scope: app_commands.Choice[str]):
        if not config.GITHUB_REPO:
            await interaction.response.send_message(
                "⚠️ `GITHUB_REPO` not set in `.env` — CI Agent has nothing to check.",
                ephemeral=True)
            return

        await interaction.response.defer()
        n = 1 if scope.value == "status" else 5
        runs = await github_api.get_workflow_runs(per_page=n)
        if runs is None:
            await interaction.followup.send("❌ Could not reach GitHub API (check GITHUB_TOKEN/repo).")
            return
        if not runs:
            await interaction.followup.send(f"No workflow runs found for `{config.GITHUB_REPO}`.")
            return

        embed = discord.Embed(title=f"⚙️ CI — {config.GITHUB_REPO}", color=discord.Color.green())
        for run in runs:
            emoji = STATUS_EMOJI.get(run.get("conclusion"), "🟡")
            embed.add_field(
                name=f"{emoji} {run['name']} (#{run['run_number']})",
                value=f"[{run['head_branch']} @ {run['head_sha'][:7]}]({run['html_url']}) — {run['status']}",
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @tasks.loop(minutes=5)
    async def poll_runs(self):
        """Post to #ci-cd whenever a new workflow run completes."""
        ch_id = config.channel_id("ci-cd")
        if not ch_id or not config.GITHUB_REPO:
            return
        runs = await github_api.get_workflow_runs(per_page=5)
        if not runs:
            return

        latest = runs[0]
        if self._last_seen_run_id is None:
            # First poll after startup: just remember, don't spam history.
            self._last_seen_run_id = latest["id"]
            return

        if latest["id"] != self._last_seen_run_id and latest.get("conclusion") is not None:
            channel = self.bot.get_channel(ch_id)
            if channel:
                emoji = STATUS_EMOJI.get(latest.get("conclusion"), "🟡")
                await channel.send(
                    f"{emoji} **{latest['name']}** (#{latest['run_number']}) on "
                    f"`{latest['head_branch']}` — **{latest['conclusion']}**\n{latest['html_url']}"
                )
            self._last_seen_run_id = latest["id"]

    @poll_runs.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(CIAgent(bot))
