#!/usr/bin/env python3
"""
invite_link.py — Print an OAuth2 invite URL for an agent's bot application.

Usage:
  ./venv/bin/python invite_link.py ci
  ./venv/bin/python invite_link.py ops --admin   # full Administrator perms

Default permission set (sufficient for slash commands + posting embeds):
  View Channels, Send Messages, Embed Links, Read Message History,
  Use Application Commands, Manage Webhooks
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import config

# View Channel(1024) + Send Messages(2048) + Embed Links(16384)
# + Read Message History(65536) + Manage Webhooks(536870912)
# + Use Application Commands(2147483648)
DEFAULT_PERMS = 1024 + 2048 + 16384 + 65536 + 536870912 + 2147483648
ADMIN_PERMS = 8


def client_id_from_token(token: str) -> str:
    seg = token.split(".")[0]
    seg += "=" * (-len(seg) % 4)
    return base64.b64decode(seg).decode()


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in config.AGENTS:
        print(f"Usage: {sys.argv[0]} <{'|'.join(config.AGENTS)}> [--admin]")
        sys.exit(1)

    agent = sys.argv[1]
    admin = "--admin" in sys.argv[2:]
    token = config.agent_token(agent)
    if not token:
        print(f"ERROR: DISCORD_BOT_TOKEN_{agent.upper()} not set in .env")
        sys.exit(1)

    client_id = client_id_from_token(token)
    perms = ADMIN_PERMS if admin else DEFAULT_PERMS
    url = (f"https://discord.com/api/oauth2/authorize?client_id={client_id}"
           f"&permissions={perms}&scope=bot%20applications.commands")
    print(f"{config.AGENTS[agent]['display']} (id={client_id}):")
    print(url)


if __name__ == "__main__":
    main()
