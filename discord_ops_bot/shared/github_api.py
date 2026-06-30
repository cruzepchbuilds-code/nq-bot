"""Minimal async GitHub REST API helpers (workflow runs + pull requests)."""
import aiohttp

from shared import config

API = "https://api.github.com"


def _headers():
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return headers


async def get_workflow_runs(per_page: int = 5):
    """Return list of recent workflow run dicts, or None if not configured / error."""
    if not config.GITHUB_REPO:
        return None
    url = f"{API}/repos/{config.GITHUB_REPO}/actions/runs?per_page={per_page}"
    async with aiohttp.ClientSession(headers=_headers()) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("workflow_runs", [])


async def get_open_pull_requests(per_page: int = 10):
    """Return list of open PR dicts, or None if not configured / error."""
    if not config.GITHUB_REPO:
        return None
    url = f"{API}/repos/{config.GITHUB_REPO}/pulls?state=open&per_page={per_page}"
    async with aiohttp.ClientSession(headers=_headers()) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            return await resp.json()


async def get_commit_status(ref: str = "main"):
    """Return combined status for a ref, or None."""
    if not config.GITHUB_REPO:
        return None
    url = f"{API}/repos/{config.GITHUB_REPO}/commits/{ref}/status"
    async with aiohttp.ClientSession(headers=_headers()) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
