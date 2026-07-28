#!/usr/bin/env bash
set -Eeuo pipefail

DATA_ROOT="${NEO_NILE_DATA_ROOT:-/workspace/neo-nile}"
APP_PORT="${NEO_NILE_PORT:-8000}"
ACE_PORT="${ACESTEP_API_PORT:-8001}"

mkdir -p \
  "$DATA_ROOT/checkpoints" \
  "$DATA_ROOT/projects" \
  "$DATA_ROOT/exports" \
  "$DATA_ROOT/database" \
  "$DATA_ROOT/cache/huggingface" \
  "$DATA_ROOT/cache/triton" \
  "$DATA_ROOT/cache/torchinductor" \
  "$DATA_ROOT/tmp" \
  "$DATA_ROOT/logs"

# The official image uses /app/checkpoints. Keep that path, but persist its
# contents on the RunPod /workspace volume.
if [ -e /app/checkpoints ] && [ ! -L /app/checkpoints ]; then
  cp -an /app/checkpoints/. "$DATA_ROOT/checkpoints/" 2>/dev/null || true
  rm -rf /app/checkpoints
fi
if [ ! -L /app/checkpoints ]; then
  ln -s "$DATA_ROOT/checkpoints" /app/checkpoints
fi

export ACESTEP_CHECKPOINTS_DIR="$DATA_ROOT/checkpoints"
export HF_HOME="$DATA_ROOT/cache/huggingface"
export XDG_CACHE_HOME="$DATA_ROOT/cache"
export ACESTEP_TMPDIR="$DATA_ROOT/tmp"
export TRITON_CACHE_DIR="$DATA_ROOT/cache/triton"
export TORCHINDUCTOR_CACHE_DIR="$DATA_ROOT/cache/torchinductor"

ACE_LOG="$DATA_ROOT/logs/ace-step.log"
APP_LOG="$DATA_ROOT/logs/neo-nile.log"

echo "========================================================"
echo " Neo Nile Studio V1"
echo " Web UI       : 0.0.0.0:${APP_PORT}"
echo " ACE-Step API : 127.0.0.1:${ACE_PORT}"
echo " Persistent   : ${DATA_ROOT}"
echo "========================================================"

cleanup() {
  if [ -n "${ACE_PID:-}" ]; then
    kill "$ACE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd /app
/app/.venv/bin/python -m acestep.api_server \
  --host 127.0.0.1 \
  --port "$ACE_PORT" \
  >>"$ACE_LOG" 2>&1 &
ACE_PID=$!

cd /opt/neo-nile
exec /app/.venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$APP_PORT" \
  --workers 1 \
  --proxy-headers \
  2>&1 | tee -a "$APP_LOG"
