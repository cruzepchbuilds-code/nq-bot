"""Review Agent — open PRs awaiting review, posted to #code-review."""
import json

import discord
from discord import app_commands
from discord.ext import commands, tasks

from shared import config, github_api

SEEN_PRS_FILE = config.DATA_DIR / "seen_prs.json"


def _load_seen() -> set[int]:
    if SEEN_PRS_FILE.exists():
        return set(json.loads(SEEN_PRS_FILE.read_text()))
    return set()


def _save_seen(seen: set[int]):
    SEEN_PRS_FILE.write_text(json.dumps(sorted(seen)))


class ReviewAgent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._seen = _load_seen()
        self.poll_prs.start()

    def cog_unload(self):
        self.poll_prs.cancel()

    @app_commands.command(name="review", description="List open pull requests awaiting review")
    async def review(self, interaction: discord.Interaction):
        if not config.GITHUB_REPO:
            await interaction.response.send_message(
                "⚠️ `GITHUB_REPO` not set in `.env` — Review Agent has nothing to check.",
                ephemeral=True)
            return

        await interaction.response.defer()
        prs = await github_api.get_open_pull_requests()
        if prs is None:
            await interaction.followup.send("❌ Could not reach GitHub API (check GITHUB_TOKEN/repo).")
            return
        if not prs:
            await interaction.followup.send(f"✅ No open PRs on `{config.GITHUB_REPO}`.")
            return

        embed = discord.Embed(title=f"🔍 Open PRs — {config.GITHUB_REPO}", color=discord.Color.blue())
        for pr in prs[:10]:
            reviewers = ", ".join(r["login"] for r in pr.get("requested_reviewers", [])) or "none requested"
            embed.add_field(
                name=f"#{pr['number']} {pr['title']}",
                value=f"by **{pr['user']['login']}** → `{pr['base']['ref']}` | reviewers: {reviewers}\n{pr['html_url']}",
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @tasks.loop(minutes=10)
    async def poll_prs(self):
        """Post to #code-review when a new PR is opened."""
        ch_id = config.channel_id("code-review")
        if not ch_id or not config.GITHUB_REPO:
            return
        prs = await github_api.get_open_pull_requests()
        if prs is None:
            return

        channel = self.bot.get_channel(ch_id)
        new_ids = []
        for pr in prs:
            if pr["number"] in self._seen:
                continue
            new_ids.append(pr["number"])
            if channel:
                await channel.send(
                    f"📬 **New PR #{pr['number']}**: {pr['title']}\n"
                    f"by **{pr['user']['login']}** → `{pr['base']['ref']}`\n{pr['html_url']}"
                )
        if new_ids:
            self._seen.update(new_ids)
            _save_seen(self._seen)

    @poll_prs.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ReviewAgent(bot))
