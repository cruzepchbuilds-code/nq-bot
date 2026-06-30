#!/usr/bin/env python3
"""
set_identity.py — Set a bot's username + avatar from assets/avatars/<agent>.png.

Usage:
  ./venv/bin/python set_identity.py ci
  ./venv/bin/python set_identity.py review
  ./venv/bin/python set_identity.py deploy
  ./venv/bin/python set_identity.py research
  ./venv/bin/python set_identity.py ops      # already done, but idempotent

Requires DISCORD_BOT_TOKEN_<AGENT> to be set in .env.
Note: Discord rate-limits username changes (~2/hour per app), so don't
re-run this too often.
"""
import base64
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import config

ASSETS = Path(__file__).parent / "assets" / "avatars"


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in config.AGENTS:
        print(f"Usage: {sys.argv[0]} <{'|'.join(config.AGENTS)}>")
        sys.exit(1)

    agent = sys.argv[1]
    token = config.agent_token(agent)
    if not token:
        print(f"ERROR: DISCORD_BOT_TOKEN_{agent.upper()} not set in .env")
        sys.exit(1)

    display = config.AGENTS[agent]["display"]
    img_path = ASSETS / f"{agent}.png"
    if not img_path.exists():
        print(f"ERROR: {img_path} not found — run generate_avatars.py first")
        sys.exit(1)

    b64 = base64.b64encode(img_path.read_bytes()).decode()
    r = requests.patch(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {token}"},
        json={"username": display, "avatar": f"data:image/png;base64,{b64}"},
    )
    if r.status_code == 200:
        data = r.json()
        print(f"OK: {agent} -> username='{data['username']}' id={data['id']} avatar set")
    else:
        print(f"ERROR {r.status_code}: {r.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
