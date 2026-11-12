#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
VITE="$PROJECT_ROOT/frontend/node_modules/.bin/vite"
BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5174}"

if [ ! -x "$PYTHON" ]; then
  echo "Python environment not found. Create .venv and install backend/requirements.txt first." >&2
  exit 1
fi

if [ ! -x "$VITE" ]; then
  echo "Frontend dependencies not found. Run npm --prefix frontend install first." >&2
  exit 1
fi

export INGESTION_MODE=queue
export DOCUMENT_STORAGE_MODE=supabase
export QDRANT_MODE=cloud
export HF_EMBEDDING_PROVIDER="${HF_EMBEDDING_PROVIDER:-local}"

(
  cd "$PROJECT_ROOT/backend"
  "$PYTHON" - <<'PY'
import sys

from app.config import Settings
from app.services.ingestion_queue import create_ingestion_queue

settings = Settings()
required = {
    "GEMINI_API_KEY": settings.GEMINI_API_KEY,
    "SUPABASE_URL": settings.SUPABASE_URL,
    "SUPABASE_SERVICE_ROLE_KEY": settings.SUPABASE_SERVICE_ROLE_KEY,
    "QDRANT_URL": settings.QDRANT_URL,
    "QDRANT_API_KEY": settings.QDRANT_API_KEY,
    "REDIS_URL": settings.REDIS_URL,
}
missing = [name for name, value in required.items() if not value]
if missing:
    print("Set these variables in the private root .env: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)

try:
    create_ingestion_queue(settings)
except Exception as exc:
    print(
        f"Queue preflight failed ({type(exc).__name__}). Check Redis and shared-service configuration.",
        file=sys.stderr,
    )
    raise SystemExit(1)

print("Queue and shared-service configuration are ready.")
PY
)

cleanup() {
  for process_pid in "${frontend_pid:-}" "${backend_pid:-}" "${worker_pid:-}"; do
    if [ -n "$process_pid" ]; then
      kill "$process_pid" 2>/dev/null || true
    fi
  done
  for process_pid in "${frontend_pid:-}" "${backend_pid:-}" "${worker_pid:-}"; do
    if [ -n "$process_pid" ]; then
      wait "$process_pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(
  cd "$PROJECT_ROOT/backend"
  "$PYTHON" -m app.worker
) &
worker_pid=$!

sleep 1
if ! kill -0 "$worker_pid" 2>/dev/null; then
  echo "Ingestion worker stopped during startup. Check the worker output above." >&2
  exit 1
fi

(
  cd "$PROJECT_ROOT/backend"
  "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
) &
backend_pid=$!

echo "Waiting for backend and worker..."
backend_ready=false
for attempt in $(seq 1 60); do
  if ! kill -0 "$worker_pid" 2>/dev/null; then
    echo "Ingestion worker stopped. Check the worker output above." >&2
    exit 1
  fi
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    echo "Backend stopped before becoming ready. Check the backend output above." >&2
    exit 1
  fi
  if curl --silent --fail "http://127.0.0.1:$BACKEND_PORT/api/health" |
    "$PYTHON" -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("status") == "ok" else 1)' 2>/dev/null; then
    backend_ready=true
    break
  fi
  sleep 1
done

if [ "$backend_ready" != true ]; then
  echo "Backend did not become healthy within 60 seconds; frontend was not started." >&2
  exit 1
fi

(
  cd "$PROJECT_ROOT/frontend"
  VITE_API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" \
    "$VITE" --host 0.0.0.0 --port "$FRONTEND_PORT" --strictPort
) &
frontend_pid=$!

echo "Frontend: http://localhost:$FRONTEND_PORT"
echo "Backend:  http://127.0.0.1:$BACKEND_PORT"
echo "Worker:   listening on the configured Redis ingestion queue"
wait "$frontend_pid"
