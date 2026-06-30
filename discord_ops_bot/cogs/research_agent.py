"""Research Agent — NQ/ES strategy research results, posted to #research-alerts.

Watches brain/*.md and results/*.md in the trading bot project for changes
(e.g. new walk-forward / backtest reports written by research scripts) and
posts a summary when they update. Also exposes slash commands to fetch the
latest report or kick off a backtest run.
"""
import asyncio
import json
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from shared import config

MTIME_FILE = config.DATA_DIR / "research_mtimes.json"

WATCHED_GLOBS = ["brain/*.md", "results/*.md"]

# Predefined, safe backtest commands runnable via /research run
RUNNABLE = {
    "nq_walk_forward": (["python3", "walk_forward.py", "data/nq_full.csv"], "NQ walk-forward (2022-2026)"),
    "nq_backtest":     (["python3", "backtest.py", "data/nq_1min.csv"], "NQ backtest (2024-2026)"),
    "es_walk_forward": (["python3", "es_backtest.py"], "ES walk-forward"),
    "monte_carlo":     (["python3", "monte_carlo.py"], "Monte Carlo stress test"),
}


def _load_mtimes() -> dict:
    if MTIME_FILE.exists():
        return json.loads(MTIME_FILE.read_text())
    return {}


def _save_mtimes(d: dict):
    MTIME_FILE.write_text(json.dumps(d))


class ResearchAgent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._mtimes = _load_mtimes()
        self.watch_reports.start()

    def cog_unload(self):
        self.watch_reports.cancel()

    @app_commands.command(name="research", description="Show the most recently updated research report")
    async def research(self, interaction: discord.Interaction):
        proj = config.NQ_BOT_PROJECT_DIR
        candidates = []
        for pattern in WATCHED_GLOBS:
            candidates.extend(proj.glob(pattern))
        if not candidates:
            await interaction.response.send_message("No research reports found.", ephemeral=True)
            return

        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        text = latest.read_text()
        await interaction.response.send_message(
            f"📄 **{latest.relative_to(proj)}** (updated {time.ctime(latest.stat().st_mtime)})\n"
            f"```\n{text[:1800]}\n```"
        )

    @app_commands.command(name="run", description="Run a predefined backtest/research script")
    @app_commands.describe(job="Which job to run")
    @app_commands.choices(job=[app_commands.Choice(name=k, value=k) for k in RUNNABLE])
    async def run(self, interaction: discord.Interaction, job: app_commands.Choice[str]):
        cmd, label = RUNNABLE[job.value]
        await interaction.response.send_message(f"▶️ Starting **{label}**... (`{' '.join(cmd)}`)")

        async def _run():
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(config.NQ_BOT_PROJECT_DIR),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            text = out.decode(errors="replace")
            tail = text[-1800:] if len(text) > 1800 else text
            status = "✅ finished" if proc.returncode == 0 else f"❌ exit {proc.returncode}"
            await interaction.followup.send(f"{status}: **{label}**\n```\n{tail}\n```")

        asyncio.create_task(_run())

    @tasks.loop(minutes=5)
    async def watch_reports(self):
        ch_id = config.channel_id("research-alerts")
        if not ch_id:
            return
        channel = self.bot.get_channel(ch_id)
        proj = config.NQ_BOT_PROJECT_DIR

        changed = []
        for pattern in WATCHED_GLOBS:
            for path in proj.glob(pattern):
                key = str(path.relative_to(proj))
                mtime = path.stat().st_mtime
                if self._mtimes.get(key) != mtime:
                    if key in self._mtimes:  # don't spam on first run
                        changed.append(path)
                    self._mtimes[key] = mtime

        if changed:
            _save_mtimes(self._mtimes)
            if channel:
                for path in changed:
                    text = path.read_text()
                    await channel.send(
                        f"📊 **Updated: `{path.relative_to(proj)}`**\n```\n{text[:1500]}\n```"
                    )
        else:
            _save_mtimes(self._mtimes)

    @watch_reports.before_loop
    async def before_watch(self):
        await self.bot.wait_until_ready()
        # Seed mtimes on first run so we don't dump every existing report.
        if not self._mtimes:
            proj = config.NQ_BOT_PROJECT_DIR
            for pattern in WATCHED_GLOBS:
                for path in proj.glob(pattern):
                    self._mtimes[str(path.relative_to(proj))] = path.stat().st_mtime
            _save_mtimes(self._mtimes)


async def setup(bot: commands.Bot):
    await bot.add_cog(ResearchAgent(bot))
