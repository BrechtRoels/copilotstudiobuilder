#!/usr/bin/env bash
# Start backend (FastAPI, $BACKEND_PORT default 8010) and frontend (Vite :5173). Ctrl+C stops both.
set -euo pipefail
export BACKEND_PORT="${BACKEND_PORT:-8010}"
cd "$(dirname "$0")"

[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example. Add your GENAI_API_KEY (or set USE_MOCK_AI=true)."; }

(cd backend && uv sync -q && uv run uvicorn app.main:app --reload --port "$BACKEND_PORT") &
BACK=$!
(cd frontend && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run dev) &
FRONT=$!
trap 'kill $BACK $FRONT 2>/dev/null' EXIT INT TERM
echo "Copilot Studio Builder → http://localhost:5173"
wait
