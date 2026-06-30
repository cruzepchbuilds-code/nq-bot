#!/usr/bin/env python3
"""
setup_verification.py — RestoreCord verification gate scaffolding.

Creates (idempotently):
  - "Verified" role — this is the role RestoreCord's bot should grant to a
    member after they complete OAuth2 verification at your RestoreCord link.
    Give this role's ID to RestoreCord's dashboard as the "verified role".
  - #verify channel — under the GENERAL category, visible to everyone
    (read-only), with an embed containing verification instructions and
    (once configured) a "Verify Now" link button pointing at your
    RestoreCord URL.

Re-run any time after setting RESTORECORD_VERIFY_URL in .env to refresh the
embed with the real verification link (edits the existing embed in place
rather than re-posting).

SCOPE — NEW MEMBERS ONLY:
  This script does NOT touch any existing channel's @everyone permissions.
  Current members/customers keep whatever access they already have.
  Gating existing channels behind the "Verified" role (so brand-new members
  see ONLY #verify until they're verified) is a separate, deliberate step —
  see README "Verification" for the full RestoreCord setup + the channel-
  gating checklist. Do that only after RestoreCord is live and the
  "Verified" role sits BELOW RestoreCord's bot role in the role list
  (Server Settings -> Roles), since a bot can only grant roles below its own.

Usage:
  ./venv/bin/python setup_verification.py
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import config

API = "https://discord.com/api/v10"

VIEW_CHANNEL = 1 << 10   # 1024
SEND_MESSAGES = 1 << 11  # 2048
READ_HISTORY = 1 << 16   # 65536
READ_ONLY_ALLOW = VIEW_CHANNEL | READ_HISTORY

VERIFIED_ROLE_NAME = "Verified"
VERIFY_CHANNEL_NAME = "verify"
GENERAL_CATEGORY_HINT = "GENERAL"

ENV_PATH = config.ROOT / ".env"


def update_env(updates: dict[str, str]):
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0]
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    for key, val in updates.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def build_embed(verify_url: str) -> dict:
    if verify_url:
        desc = (
            "Welcome to **CruzCapital**!\n\n"
            "Click **Verify Now** below to complete verification. Once "
            "verified, you'll be assigned the **Verified** role and unlock "
            "the rest of the server."
        )
    else:
        desc = (
            "Welcome to **CruzCapital**!\n\n"
            "To gain full access to the server, complete verification using "
            "the link below. Once verified, you'll be assigned the "
            "**Verified** role and unlock the rest of the server.\n\n"
            "**Verification link:** _(pending setup — RestoreCord link goes "
            "here once `RESTORECORD_VERIFY_URL` is set in `.env`)_"
        )
    return {
        "title": "🔒 Server Verification",
        "description": desc,
        "color": 0x2ECC71,
        "footer": {"text": "CruzCapital • Verification"},
    }


def build_components(verify_url: str) -> list:
    if not verify_url:
        return []
    return [{
        "type": 1,  # action row
        "components": [{
            "type": 2,       # button
            "style": 5,      # link
            "label": "Verify Now",
            "url": verify_url,
        }],
    }]


def main():
    admin_token = config.agent_token("ops")
    if not admin_token:
        print("ERROR: DISCORD_BOT_TOKEN_OPS (or DISCORD_BOT_TOKEN) not set in .env")
        sys.exit(1)
    if not config.DISCORD_GUILD_ID:
        print("ERROR: DISCORD_GUILD_ID not set in .env")
        sys.exit(1)

    headers = {"Authorization": f"Bot {admin_token}"}
    guild_id = config.DISCORD_GUILD_ID
    everyone_id = str(guild_id)

    # ── 1. "Verified" role ───────────────────────────────────────────────
    r = requests.get(f"{API}/guilds/{guild_id}/roles", headers=headers)
    r.raise_for_status()
    roles = r.json()
    verified = next((ro for ro in roles if ro["name"] == VERIFIED_ROLE_NAME), None)
    if verified:
        role_id = verified["id"]
        print(f"  Verified role exists: {role_id}")
    else:
        r = requests.post(f"{API}/guilds/{guild_id}/roles", headers=headers, json={
            "name": VERIFIED_ROLE_NAME,
            "permissions": "0",
            "color": 0x2ECC71,
            "hoist": False,
            "mentionable": False,
        })
        r.raise_for_status()
        role_id = r.json()["id"]
        print(f"  Created Verified role: {role_id}")

    # ── 2. #verify channel ───────────────────────────────────────────────
    r = requests.get(f"{API}/guilds/{guild_id}/channels", headers=headers)
    r.raise_for_status()
    channels = r.json()
    chan = next((c for c in channels if c["type"] == 0 and c["name"] == VERIFY_CHANNEL_NAME), None)

    if chan:
        chan_id = chan["id"]
        print(f"  #verify exists: {chan_id}")
    else:
        general_cat = next(
            (c for c in channels if c["type"] == 4 and GENERAL_CATEGORY_HINT in c["name"].upper()),
            None,
        )
        body = {
            "name": VERIFY_CHANNEL_NAME,
            "type": 0,
            "topic": "Verify here to unlock the rest of CruzCapital.",
            "position": 0,
            "permission_overwrites": [
                {"id": everyone_id, "type": 0, "allow": str(READ_ONLY_ALLOW), "deny": str(SEND_MESSAGES)},
                {"id": role_id, "type": 0, "allow": str(READ_ONLY_ALLOW), "deny": str(SEND_MESSAGES)},
            ],
        }
        if general_cat:
            body["parent_id"] = general_cat["id"]
        r = requests.post(f"{API}/guilds/{guild_id}/channels", headers=headers, json=body)
        r.raise_for_status()
        chan_id = r.json()["id"]
        print(f"  Created #verify: {chan_id}")

    update_env({"ROLE_VERIFIED": role_id, "CHANNEL_VERIFY": chan_id})

    # ── 3. Post / refresh the verification embed ────────────────────────
    verify_url = config.RESTORECORD_VERIFY_URL
    embed = build_embed(verify_url)
    components = build_components(verify_url)

    r = requests.get(f"{API}/channels/{chan_id}/messages?limit=20", headers=headers)
    r.raise_for_status()
    existing = next(
        (m for m in r.json()
         if m.get("author", {}).get("id") == _bot_id(headers)
         and m.get("embeds") and m["embeds"][0].get("title") == "🔒 Server Verification"),
        None,
    )

    payload = {"embeds": [embed], "components": components}
    if existing:
        r = requests.patch(f"{API}/channels/{chan_id}/messages/{existing['id']}", headers=headers, json=payload)
        print(f"  Updated verification embed -> {r.status_code}")
    else:
        r = requests.post(f"{API}/channels/{chan_id}/messages", headers=headers, json=payload)
        print(f"  Posted verification embed -> {r.status_code}")

    print()
    if verify_url:
        print(f"  Verify link is live: {verify_url}")
    else:
        print("  No RESTORECORD_VERIFY_URL set yet — embed shows a placeholder.")
        print("  After configuring RestoreCord, set RESTORECORD_VERIFY_URL in .env")
        print("  and re-run this script to add the 'Verify Now' button.")
    print(f"\n  ROLE_VERIFIED={role_id}")
    print(f"  CHANNEL_VERIFY={chan_id}")
    print("\n  Next: invite RestoreCord's bot, set the verified role in its")
    print("  dashboard to the ID above, and make sure RestoreCord's bot role")
    print("  sits ABOVE 'Verified' in Server Settings -> Roles.")


_bot_id_cache = None


def _bot_id(headers):
    global _bot_id_cache
    if _bot_id_cache is None:
        r = requests.get(f"{API}/users/@me", headers=headers)
        r.raise_for_status()
        _bot_id_cache = r.json()["id"]
    return _bot_id_cache


if __name__ == "__main__":
    main()
