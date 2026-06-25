#!/usr/bin/env bash
# Claude Desktop — macOS / Linux launcher
# Usage: ./start.sh [--dev]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 找 Python ────────────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYTHON="$cmd"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "❌  Python not found. Please install Python 3.8+ and re-run."
  exit 1
fi

PYVER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✔  Python $PYVER ($PYTHON)"

# ── 安裝 Python 依賴（若尚未安裝）───────────────────────────────────────────
if ! "$PYTHON" -c "import aiohttp, aiohttp_cors" &>/dev/null 2>&1; then
  echo "📦  Installing Python dependencies..."
  "$PYTHON" -m pip install -r "$SCRIPT_DIR/backend/requirements.txt" --quiet
fi

# ── 啟動後端 ─────────────────────────────────────────────────────────────────
echo "🚀  Starting backend on http://localhost:8765 ..."
cd "$SCRIPT_DIR/backend"
"$PYTHON" main.py &
BACKEND_PID=$!
trap "kill $BACKEND_PID 2>/dev/null || true" EXIT

# ── 開發模式 vs 正式模式 ─────────────────────────────────────────────────────
if [[ "${1:-}" == "--dev" ]]; then
  echo "🔧  Dev mode: starting Angular HMR server..."
  cd "$SCRIPT_DIR/frontend"
  npm run start &
  FRONTEND_PID=$!
  trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true" EXIT

  echo ""
  echo "   Backend:  http://localhost:8765"
  echo "   Frontend: http://localhost:4200  (HMR)"
  echo ""
  echo "   Waiting 12 s for Angular to compile..."
  sleep 12

  cd "$SCRIPT_DIR"
  npx electron . --dev
else
  echo ""
  echo "   Backend: http://localhost:8765"
  echo ""
  echo "   Launch the Electron app or run:  npm run electron"
  wait $BACKEND_PID
fi
