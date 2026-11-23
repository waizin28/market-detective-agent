#!/usr/bin/env bash
# Start the React dashboard on http://localhost:5173 (proxies /api to :8000)
set -euo pipefail
cd "$(dirname "$0")/../frontend"
[ -d node_modules ] || npm install --no-audit --no-fund
exec npm run dev
