"""Shared configuration loader for the ops Discord bot + webhook server."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)

# ── Per-agent bot identities ────────────────────────────────────────────────
# Each agent is its own Discord Application/bot user with its own token,
# name, and avatar (see generate_avatars.py / set_identity.py). "ops" falls
# back to the original DISCORD_BOT_TOKEN for backward compatibility.
AGENTS = {
    "ops":      {"display": "Ops Agent",      "cog": "cogs.ops_agent",      "role": "Ops Agent"},
    "ci":       {"display": "CI Agent",       "cog": "cogs.ci_agent",       "role": "CI Agent"},
    "review":   {"display": "Review Agent",   "cog": "cogs.review_agent",   "role": "Review Agent"},
    "deploy":   {"display": "Deploy Agent",   "cog": "cogs.deploy_agent",   "role": "Deploy Agent"},
    "research": {"display": "Research Agent", "cog": "cogs.research_agent", "role": "Research Agent"},
}


def agent_token(agent: str) -> str:
    key = f"DISCORD_BOT_TOKEN_{agent.upper()}"
    tok = os.getenv(key, "")
    if not tok and agent == "ops":
        tok = DISCORD_BOT_TOKEN  # backward-compat fallback
    return tok

# Channel name -> env var name. setup_server.py creates these channels and
# writes their IDs back into .env under these keys.
CHANNEL_ENV_KEYS = {
    "ci-cd": "CHANNEL_CI_CD",
    "code-review": "CHANNEL_CODE_REVIEW",
    "deployments": "CHANNEL_DEPLOYMENTS",
    "research-alerts": "CHANNEL_RESEARCH_ALERTS",
    "bot-status": "CHANNEL_BOT_STATUS",
    "ops-log": "CHANNEL_OPS_LOG",
    "general": "CHANNEL_GENERAL",
    "announcements": "CHANNEL_ANNOUNCEMENTS",
    "agent-general": "CHANNEL_AGENT_GENERAL",
    # Quant trading desk (setup_quant_desk.py)
    "pre-market": "CHANNEL_PRE_MARKET",
    "signals": "CHANNEL_SIGNALS",
    "trade-log": "CHANNEL_TRADE_LOG",
    "daily-pnl": "CHANNEL_DAILY_PNL",
    "risk-alerts": "CHANNEL_RISK_ALERTS",
    "account-status": "CHANNEL_ACCOUNT_STATUS",
    "nq-strategy": "CHANNEL_NQ_STRATEGY",
    "es-strategy": "CHANNEL_ES_STRATEGY",
    "walk-forward": "CHANNEL_WALK_FORWARD",
    "brain-insights": "CHANNEL_BRAIN_INSIGHTS",
    # Verification (setup_verification.py)
    "verify": "CHANNEL_VERIFY",
}

# agent name -> the one channel where ONLY that agent's bot may post
# (configure_channels.py locks @everyone's send permission on these and
# grants send permission to the matching agent role)
AGENT_HOME_CHANNEL = {
    "ops": "ops-log",
    "ci": "ci-cd",
    "review": "code-review",
    "deploy": "deployments",
    "research": "research-alerts",
}

WEBHOOK_ENV_KEYS = {
    "ci-cd": "WEBHOOK_CI_CD",
    "code-review": "WEBHOOK_CODE_REVIEW",
    "deployments": "WEBHOOK_DEPLOYMENTS",
    "research-alerts": "WEBHOOK_RESEARCH_ALERTS",
}


def channel_id(name: str) -> int:
    """Return the configured channel ID for a logical channel name, or 0."""
    key = CHANNEL_ENV_KEYS.get(name)
    val = os.getenv(key, "") if key else ""
    return int(val) if val else 0


def webhook_url(name: str) -> str:
    key = WEBHOOK_ENV_KEYS.get(name)
    return os.getenv(key, "") if key else ""


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

WEBHOOK_SERVER_PORT = int(os.getenv("WEBHOOK_SERVER_PORT", "8787"))

NQ_BOT_PROJECT_DIR = Path(os.getenv("NQ_BOT_PROJECT_DIR", str(ROOT.parent)))

# ── Verification (RestoreCord) ───────────────────────────────────────────────
ROLE_VERIFIED = int(os.getenv("ROLE_VERIFIED", "0") or 0)
RESTORECORD_VERIFY_URL = os.getenv("RESTORECORD_VERIFY_URL", "")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
