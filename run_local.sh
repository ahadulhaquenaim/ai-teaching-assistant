#!/usr/bin/env bash
# Start the backend (FastAPI :8000) and the frontend (Streamlit :8501) for local use.
# Usage: ./run_local.sh      Stop both with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")"

for dir in backend frontend; do
  if [[ ! -x "$dir/.venv/bin/python" ]]; then
    echo "Missing $dir/.venv. Create it first (see README: Quick start)." >&2
    exit 1
  fi
done
if [[ ! -f backend/.env ]]; then
  echo "Missing backend/.env. Copy backend/.env.example and fill in your keys." >&2
  exit 1
fi

# A GOOGLE_API_KEY exported by your shell would override backend/.env.
unset GOOGLE_API_KEY

(cd backend && exec .venv/bin/uvicorn app.main:app --port 8000) &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM

echo "Waiting for the backend..."
until curl -sf http://localhost:8000/health >/dev/null; do
  kill -0 "$BACKEND_PID" 2>/dev/null || { echo "Backend failed to start." >&2; exit 1; }
  sleep 1
done
echo "Backend ready: http://localhost:8000 (API docs: /docs)"

cd frontend
echo "Frontend: http://localhost:8501  (open it in your browser; Ctrl+C stops everything)"
# Headless skips Streamlit's first-run email prompt. Not `exec`: the script must
# stay alive so the trap can stop the backend.
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}" \
  .venv/bin/streamlit run app.py --server.headless true --browser.gatherUsageStats false
