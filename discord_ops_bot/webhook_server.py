#!/usr/bin/env python3
"""
webhook_server.py — Lightweight relay: GitHub + local scripts -> Discord.

Runs independently of bot.py (no Discord gateway connection needed) using
the incoming webhook URLs created by setup_server.py.

Endpoints:
  POST /github   — GitHub webhook (push, pull_request, workflow_run)
                   -> #ci-cd / #code-review
  POST /deploy   — {target, version, status, message} -> #deployments
                   and appended to data/deploys.json (read by /deploy slash cmd)
  POST /research — {title, body} -> #research-alerts
  GET  /healthz  — liveness check

Usage:
  ./venv/bin/uvicorn webhook_server:app --host 0.0.0.0 --port 8787

Point your GitHub repo's webhook (Settings -> Webhooks) at:
  http://<host>:8787/github
  Content type: application/json
  Secret: same value as GITHUB_WEBHOOK_SECRET in .env
  Events: Pushes, Pull requests, Workflow runs
"""
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import requests
from fastapi import FastAPI, Header, HTTPException, Request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import config

app = FastAPI(title="ops-bot webhook relay")

DEPLOYS_FILE = config.DATA_DIR / "deploys.json"

STATUS_EMOJI = {
    "success": "✅", "failure": "❌", "cancelled": "⚪",
    "skipped": "⏭️", "timed_out": "⏱️", "action_required": "⚠️",
}


def post_to_webhook(url: str, content: str):
    if not url:
        return
    try:
        requests.post(url, json={"content": content[:1900]}, timeout=10)
    except requests.RequestException:
        pass  # best-effort; don't crash the relay on a Discord hiccup


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not config.GITHUB_WEBHOOK_SECRET:
        return True  # no secret configured -> skip verification (dev mode)
    if not signature:
        return False
    mac = hmac.new(config.GITHUB_WEBHOOK_SECRET.encode(), msg=body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/github")
async def github_webhook(request: Request, x_github_event: str = Header(default=""),
                          x_hub_signature_256: str | None = Header(default=None)):
    body = await request.body()
    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="bad signature")

    payload = json.loads(body)

    if x_github_event == "push":
        ref = payload.get("ref", "")
        pusher = payload.get("pusher", {}).get("name", "someone")
        commits = payload.get("commits", [])
        repo = payload.get("repository", {}).get("full_name", "")
        msg = f"📦 **{pusher}** pushed {len(commits)} commit(s) to `{ref}` on `{repo}`"
        for c in commits[:5]:
            msg += f"\n• {c['message'].splitlines()[0]} ({c['id'][:7]})"
        post_to_webhook(config.webhook_url("ci-cd"), msg)

    elif x_github_event == "pull_request":
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        if action in ("opened", "ready_for_review", "reopened"):
            msg = (f"📬 **PR #{pr['number']} {action}**: {pr['title']}\n"
                   f"by **{pr['user']['login']}** → `{pr['base']['ref']}`\n{pr['html_url']}")
            post_to_webhook(config.webhook_url("code-review"), msg)
        elif action == "closed" and pr.get("merged"):
            msg = f"🔀 **PR #{pr['number']} merged**: {pr['title']}\n{pr['html_url']}"
            post_to_webhook(config.webhook_url("code-review"), msg)

    elif x_github_event == "workflow_run":
        wr = payload.get("workflow_run", {})
        if payload.get("action") == "completed":
            emoji = STATUS_EMOJI.get(wr.get("conclusion"), "🟡")
            msg = (f"{emoji} **{wr['name']}** (#{wr['run_number']}) on `{wr['head_branch']}` "
                   f"— **{wr['conclusion']}**\n{wr['html_url']}")
            post_to_webhook(config.webhook_url("ci-cd"), msg)

    return {"ok": True}


@app.post("/deploy")
async def deploy(request: Request):
    """Body: {"target": "prod", "version": "v7", "status": "success", "message": "..."}"""
    payload = await request.json()
    record = {
        "target": payload.get("target", "unknown"),
        "version": payload.get("version", ""),
        "status": payload.get("status", "unknown"),
        "message": payload.get("message", ""),
        "timestamp": payload.get("timestamp") or time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    deploys = []
    if DEPLOYS_FILE.exists():
        deploys = json.loads(DEPLOYS_FILE.read_text())
    deploys.append(record)
    DEPLOYS_FILE.write_text(json.dumps(deploys[-200:], indent=2))

    emoji = "✅" if record["status"] == "success" else "❌" if record["status"] == "failure" else "🟡"
    post_to_webhook(config.webhook_url("deployments"),
                     f"{emoji} **{record['target']}** — {record['version']}\n{record['message']}")
    return {"ok": True}


@app.post("/research")
async def research(request: Request):
    """Body: {"title": "...", "body": "..."}"""
    payload = await request.json()
    title = payload.get("title", "Research update")
    body = payload.get("body", "")
    post_to_webhook(config.webhook_url("research-alerts"), f"📊 **{title}**\n{body}")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.WEBHOOK_SERVER_PORT)
