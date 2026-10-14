#!/usr/bin/env bash
# ProjectStack development server — starts FastAPI backend + Vite frontend
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo ""
echo "  ProjectStack"
echo "  ─────────────────────────────────────"

# ── Python deps ───────────────────────────────────────────────────────────────
echo "  Checking Python dependencies..."
cd "$ROOT_DIR"
pip install -q fastapi "uvicorn[standard]" python-multipart 2>/dev/null

# ── Node deps ─────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
if [ ! -d node_modules ]; then
  echo "  Installing Node dependencies..."
  npm install --silent
fi

# ── Start backend ─────────────────────────────────────────────────────────────
cd "$ROOT_DIR"
python3 -m uvicorn projectstack.api.main:app --reload --port 8001 --log-level warning &
BACKEND_PID=$!
echo "  Backend  →  http://localhost:8001"

# Port 8001 matches the /api proxy target in vite.config.ts
# Wait briefly for backend to initialise
sleep 1

# ── Start frontend ────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
echo "  Frontend →  http://localhost:6292"
echo ""
echo "  Open http://localhost:6292 in your browser."
echo "  Press Ctrl+C to stop both servers."
echo ""

cleanup() {
  echo ""
  echo "  Stopping servers..."
  kill "$BACKEND_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

npm run dev
