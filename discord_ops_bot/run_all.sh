#!/usr/bin/env bash
# run_all.sh — Launch all 5 agents as separate bot processes.
# Each runs under its own Discord bot identity (token), in the background.
# Logs -> logs/<agent>.log. Ctrl-C this script to stop all of them.
set -e
cd "$(dirname "$0")"
mkdir -p logs

AGENTS=(ops ci review deploy research)
PIDS=()

cleanup() {
  echo "Stopping agents..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

for agent in "${AGENTS[@]}"; do
  var="DISCORD_BOT_TOKEN_$(echo "$agent" | tr '[:lower:]' '[:upper:]')"
  if ! grep -q "^${var}=.\+" .env 2>/dev/null; then
    echo "skip $agent: $var not set in .env"
    continue
  fi
  echo "starting $agent -> logs/${agent}.log"
  ./venv/bin/python bot.py --agent "$agent" >> "logs/${agent}.log" 2>&1 &
  PIDS+=($!)
done

echo "Running ${#PIDS[@]} agent(s). Press Ctrl-C to stop."
wait
