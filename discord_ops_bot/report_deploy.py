#!/usr/bin/env python3
"""
report_deploy.py — CLI helper for deploy scripts to log a deploy event.

Writes directly to data/deploys.json (read by the /deploy slash command) and,
if webhook_server.py is running, also posts to #deployments via its /deploy
endpoint.

Usage:
  ./venv/bin/python report_deploy.py --target prod --version v7 \
      --status success --message "Deployed v7 config, PF 2.14 OOS"
"""
import argparse
import json
import time
from pathlib import Path

import requests

from shared import config

DEPLOYS_FILE = config.DATA_DIR / "deploys.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="e.g. prod, eval, paper")
    ap.add_argument("--version", required=True, help="e.g. v7, es-v2")
    ap.add_argument("--status", choices=["success", "failure", "pending"], default="success")
    ap.add_argument("--message", default="")
    ap.add_argument("--webhook-host", default=f"http://localhost:{config.WEBHOOK_SERVER_PORT}",
                    help="webhook_server.py base URL (set to '' to skip)")
    args = ap.parse_args()

    record = {
        "target": args.target, "version": args.version,
        "status": args.status, "message": args.message,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    deploys = []
    if DEPLOYS_FILE.exists():
        deploys = json.loads(DEPLOYS_FILE.read_text())
    deploys.append(record)
    DEPLOYS_FILE.write_text(json.dumps(deploys[-200:], indent=2))
    print(f"Logged: {record}")

    if args.webhook_host:
        try:
            requests.post(f"{args.webhook_host}/deploy", json=record, timeout=5)
            print("Posted to webhook_server.")
        except requests.RequestException as e:
            print(f"(webhook_server not reachable: {e})")


if __name__ == "__main__":
    main()
