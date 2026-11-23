#!/usr/bin/env bash
# Start the FastAPI + LangGraph backend on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/../backend"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created backend/.env from .env.example - add your OPENAI_API_KEY and SEC_USER_AGENT."
fi
uv sync --quiet
exec uv run uvicorn app.api.main:app --reload --port 8000
