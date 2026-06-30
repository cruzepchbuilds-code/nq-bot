#!/usr/bin/env python3
"""
setup_launchd.py — make the 5 Discord agent bots persistent via macOS launchd.

Generates one LaunchAgent plist per agent
(~/Library/LaunchAgents/com.cruzcapital.discordbot.<agent>.plist) that:
  - runs `venv/bin/python bot.py --agent <agent>` from this directory
  - starts automatically on login (RunAtLoad)
  - auto-restarts if the process crashes or exits (KeepAlive)
  - logs stdout/stderr to logs/<agent>.log / logs/<agent>.err.log

Usage:
    ./venv/bin/python setup_launchd.py            # install + (re)start all 5
    ./venv/bin/python setup_launchd.py --status   # show launchctl status
    ./venv/bin/python setup_launchd.py --uninstall  # stop + remove all 5

Idempotent — safe to re-run. Re-running reloads the plist (so config/path
changes take effect) and restarts the agent.

Note: kills any existing `bot.py --agent <name>` processes started via
run_all.sh/nohup before installing the launchd job, so you don't end up
with two processes fighting over the same bot token/gateway session.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

AGENTS = ["ops", "ci", "review", "deploy", "research"]

ROOT      = Path(__file__).resolve().parent
VENV_PY   = ROOT / "venv" / "bin" / "python"
# NOTE: launchd's posix_spawn fails with exit 78 (EX_CONFIG) when
# StandardOutPath/StandardErrorPath point inside ~/Desktop (TCC blocks
# launchd's file-descriptor setup for the protected Desktop folder, even
# though the spawned process itself can read/write there fine once running).
# So logs for launchd-managed runs go to ~/Library/Logs instead of
# discord_ops_bot/logs/ (which run_all.sh/nohup still use).
LOGS_DIR  = Path.home() / "Library" / "Logs" / "CruzCapitalBots"
UID       = os.getuid()
LA_DIR    = Path.home() / "Library" / "LaunchAgents"

LABEL_FMT = "com.cruzcapital.discordbot.{agent}"

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>exec ./venv/bin/python bot.py --agent {agent}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{cwd}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>15</integer>
    <key>StandardOutPath</key>
    <string>{out_log}</string>
    <key>StandardErrorPath</key>
    <string>{err_log}</string>
</dict>
</plist>
"""


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def kill_nohup_processes(agent: str):
    """Kill any `bot.py --agent <agent>` process not managed by launchd."""
    out = run(["pgrep", "-f", f"bot.py --agent {agent}"]).stdout.strip()
    for pid in out.splitlines():
        pid = pid.strip()
        if not pid:
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
            print(f"  killed stray pid {pid} ({agent})")
        except ProcessLookupError:
            pass


def plist_path(agent: str) -> Path:
    return LA_DIR / f"{LABEL_FMT.format(agent=agent)}.plist"


def install(agent: str):
    label = LABEL_FMT.format(agent=agent)
    LOGS_DIR.mkdir(exist_ok=True)
    LA_DIR.mkdir(parents=True, exist_ok=True)

    content = PLIST_TEMPLATE.format(
        label=label,
        agent=agent,
        cwd=str(ROOT),
        out_log=str(LOGS_DIR / f"{agent}.log"),
        err_log=str(LOGS_DIR / f"{agent}.err.log"),
    )

    plist = plist_path(agent)
    plist.write_text(content)

    # Unload any previous instance of this job (ignore errors if not loaded)
    run(["launchctl", "bootout", f"gui/{UID}/{label}"])

    # Kill any stray nohup'd process for this agent
    kill_nohup_processes(agent)

    # Bootstrap (load) the job into the user's GUI domain
    r = run(["launchctl", "bootstrap", f"gui/{UID}", str(plist)])
    if r.returncode != 0:
        print(f"  ✗ {agent}: bootstrap failed — {r.stderr.strip()}")
        return False

    run(["launchctl", "enable", f"gui/{UID}/{label}"])
    run(["launchctl", "kickstart", "-k", f"gui/{UID}/{label}"])
    print(f"  ✓ {agent}: installed + started ({plist.name})")
    return True


def uninstall(agent: str):
    label = LABEL_FMT.format(agent=agent)
    plist = plist_path(agent)
    run(["launchctl", "bootout", f"gui/{UID}/{label}"])
    if plist.exists():
        plist.unlink()
    print(f"  ✓ {agent}: stopped + removed")


def status():
    r = run(["launchctl", "list"])
    lines = [l for l in r.stdout.splitlines() if "cruzcapital" in l]
    if not lines:
        print("  (no cruzcapital launchd jobs found)")
        return
    print(f"  {'PID':<8} {'STATUS':<8} LABEL")
    for line in lines:
        parts = line.split("\t")
        pid, status_code, label = (parts + ["", "", ""])[:3]
        print(f"  {pid:<8} {status_code:<8} {label}")


def main():
    args = sys.argv[1:]

    if "--status" in args:
        print("launchd status:")
        status()
        return

    if "--uninstall" in args:
        print("Uninstalling all agent launchd jobs...")
        for agent in AGENTS:
            uninstall(agent)
        return

    print(f"Installing launchd jobs for {len(AGENTS)} agents "
          f"(python={VENV_PY}, cwd={ROOT})")
    ok = 0
    for agent in AGENTS:
        if install(agent):
            ok += 1

    print(f"\n{ok}/{len(AGENTS)} agents installed.")
    print("\nStatus:")
    status()
    print(f"\nLogs: {LOGS_DIR}/<agent>.log  /  {LOGS_DIR}/<agent>.err.log")
    print("Agents now auto-start on login and auto-restart on crash.")
    print("Re-run this script any time after editing bot.py/.env to reload.")


if __name__ == "__main__":
    main()
