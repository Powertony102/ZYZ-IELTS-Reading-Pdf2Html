#!/usr/bin/env bash
# Start the IELTS Reading Dashboard server
# Usage: bash dashboard/start.sh [--port 8080]

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-7777}"
if [ "$1" = "--port" ] && [ -n "${2:-}" ]; then
  PORT="$2"
fi

echo "Starting IELTS Reading Dashboard..."
echo "Dashboard URL: http://127.0.0.1:${PORT}/dashboard/index.html"
echo ""

python3 dashboard/server.py --root . --port "$PORT"
