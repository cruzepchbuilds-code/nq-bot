#!/bin/bash
# start_all.sh — Launch NQ + ES paper traders simultaneously
#
# Each symbol gets its own log file:
#   live/paper_trades_NQ.csv
#   live/paper_trades_ES.csv
#
# Usage:
#   ./start_all.sh           # both NQ and ES (default)
#   ./start_all.sh nq        # NQ only
#   ./start_all.sh es        # ES only
#   ./start_all.sh --live    # both, with live orders

set -e

LIVE_FLAG=""
if [[ "$*" == *"--live"* ]]; then
    LIVE_FLAG="--live"
    echo "[start_all] WARNING: live orders enabled"
fi

RUN_NQ=true
RUN_ES=true
if [[ "$1" == "nq" ]]; then RUN_ES=false; fi
if [[ "$1" == "es" ]]; then RUN_NQ=false; fi

mkdir -p logs

echo "[start_all] Starting at $(date '+%Y-%m-%d %H:%M:%S')"

if $RUN_NQ; then
    echo "[start_all] Launching NQ bot → logs/nq.log"
    python live/paper_trading.py --symbol NQ $LIVE_FLAG \
        >> logs/nq.log 2>&1 &
    NQ_PID=$!
    echo "[start_all] NQ PID: $NQ_PID"
fi

if $RUN_ES; then
    echo "[start_all] Launching ES bot → logs/es.log"
    python live/paper_trading.py --symbol ES $LIVE_FLAG \
        >> logs/es.log 2>&1 &
    ES_PID=$!
    echo "[start_all] ES PID: $ES_PID"
fi

echo "[start_all] Both bots running. Ctrl+C to stop all."
echo ""

# Trap Ctrl+C and kill both processes
cleanup() {
    echo ""
    echo "[start_all] Stopping..."
    if $RUN_NQ && kill -0 $NQ_PID 2>/dev/null; then
        kill $NQ_PID && echo "[start_all] NQ stopped"
    fi
    if $RUN_ES && kill -0 $ES_PID 2>/dev/null; then
        kill $ES_PID && echo "[start_all] ES stopped"
    fi
    exit 0
}
trap cleanup INT TERM

# Wait for both
if $RUN_NQ && $RUN_ES; then
    wait $NQ_PID $ES_PID
elif $RUN_NQ; then
    wait $NQ_PID
elif $RUN_ES; then
    wait $ES_PID
fi
