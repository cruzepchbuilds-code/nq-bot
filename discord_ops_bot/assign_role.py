#!/usr/bin/env python3
"""
assign_role.py — Assign an agent's server role to its bot user.

Uses the Ops Agent's token (Administrator) to grant e.g. the "CI Agent"
role to the CI Agent bot, once it has joined the guild via its invite link.

Usage:
  ./venv/bin/python assign_role.py ci
  ./venv/bin/python assign_role.py review
  ./venv/bin/python assign_role.py deploy
  ./venv/bin/python assign_role.py research
"""
import base64
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import config

API = "https://discord.com/api/v10"


def client_id_from_token(token: str) -> str:
    seg = token.split(".")[0]
    seg += "=" * (-len(seg) % 4)
    return base64.b64decode(seg).decode()


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in config.AGENTS:
        print(f"Usage: {sys.argv[0]} <{'|'.join(a for a in config.AGENTS if a != 'ops')}>")
        sys.exit(1)

    agent = sys.argv[1]
    admin_token = config.agent_token("ops")
    if not admin_token:
        print("ERROR: DISCORD_BOT_TOKEN_OPS (or DISCORD_BOT_TOKEN) not set in .env")
        sys.exit(1)

    agent_tok = config.agent_token(agent)
    if not agent_tok:
        print(f"ERROR: DISCORD_BOT_TOKEN_{agent.upper()} not set in .env")
        sys.exit(1)

    user_id = client_id_from_token(agent_tok)
    role_name = config.AGENTS[agent]["role"]
    headers = {"Authorization": f"Bot {admin_token}"}
    guild_id = config.DISCORD_GUILD_ID

    # 1. Confirm the bot is a member of the guild
    r = requests.get(f"{API}/guilds/{guild_id}/members/{user_id}", headers=headers)
    if r.status_code == 404:
        print(f"ERROR: {config.AGENTS[agent]['display']} (id={user_id}) has not joined the server yet.\n"
              f"Run: ./venv/bin/python invite_link.py {agent}  and open the link first.")
        sys.exit(1)
    r.raise_for_status()

    # 2. Find the role by name
    r = requests.get(f"{API}/guilds/{guild_id}/roles", headers=headers)
    r.raise_for_status()
    roles = {role["name"]: role["id"] for role in r.json()}
    if role_name not in roles:
        print(f"ERROR: role '{role_name}' not found in guild. Run setup_server.py first.")
        sys.exit(1)
    role_id = roles[role_name]

    # 3. Assign the role
    r = requests.put(f"{API}/guilds/{guild_id}/members/{user_id}/roles/{role_id}", headers=headers)
    if r.status_code == 204:
        print(f"OK: granted '{role_name}' to {config.AGENTS[agent]['display']} (id={user_id})")
    elif r.status_code == 403:
        print(f"ERROR 403 Forbidden: Ops Agent's role must be positioned ABOVE '{role_name}' "
              f"in Server Settings -> Roles (drag Ops Agent higher), then retry.")
    else:
        print(f"ERROR {r.status_code}: {r.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
