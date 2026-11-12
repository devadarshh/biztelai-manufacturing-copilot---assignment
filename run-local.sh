#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
VITE="$PROJECT_ROOT/frontend/node_modules/.bin/vite"
BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5174}"
LOCAL_QDRANT_PATH="${LOCAL_QDRANT_PATH:-$PROJECT_ROOT/storage/local_dev_qdrant}"

if [ ! -x "$PYTHON" ]; then
  echo "Python environment not found. Create .venv and install backend/requirements.txt first." >&2
  exit 1
fi

if [ ! -x "$VITE" ]; then
  echo "Frontend dependencies not found. Run npm --prefix frontend install first." >&2
  exit 1
fi

cleanup() {
  if [ -n "${frontend_pid:-}" ]; then
    kill "$frontend_pid" 2>/dev/null || true
  fi
  if [ -n "${backend_pid:-}" ]; then
    kill "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

(
  cd "$PROJECT_ROOT/backend"
  DOCUMENT_STORAGE_MODE=local \
  DOCUMENT_STORAGE_PATH="$PROJECT_ROOT/sample-docs" \
  DOCUMENT_UPLOAD_PATH="$PROJECT_ROOT/storage/local_uploads" \
  DOCUMENT_EXCLUDE_FILES=Sample_Questions.pdf \
  HF_EMBEDDING_PROVIDER=local \
  QDRANT_MODE=local \
  INGESTION_MODE=sync \
  QDRANT_STORAGE_PATH="$LOCAL_QDRANT_PATH" \
  APP_HOST=127.0.0.1 \
  APP_PORT="$BACKEND_PORT" \
  "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
) &
backend_pid=$!

echo "Starting backend and waiting for health check..."
backend_ready=false
for attempt in $(seq 1 60); do
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    echo "Backend stopped before becoming ready. Check the backend startup output above." >&2
    exit 1
  fi
  if curl --silent --fail "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null; then
    backend_ready=true
    break
  fi
  sleep 1
done

if [ "$backend_ready" != true ]; then
  echo "Backend did not become ready within 60 seconds; frontend was not started." >&2
  exit 1
fi

(
  cd "$PROJECT_ROOT/frontend"
  VITE_API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" \
  "$VITE" --host 0.0.0.0 --port "$FRONTEND_PORT" --strictPort
) &
frontend_pid=$!

echo "Starting local app. The company PDFs are loaded from sample-docs; Sample_Questions.pdf is excluded."
echo "Frontend: http://localhost:$FRONTEND_PORT"
echo "Backend:  http://127.0.0.1:$BACKEND_PORT"
echo "Backend is ready. Click Re-index before asking questions; the local embedding model downloads on first use."

wait "$frontend_pid"
