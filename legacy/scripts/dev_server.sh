#!/usr/bin/env bash
# Archived server only; current application uses pnpm dev and Docker8000.
set -euo pipefail
LEGACY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE_ROOT="$(dirname "$LEGACY_ROOT")"
set -a
source "$WORKSPACE_ROOT/.env"
set +a
: "${DATABASE_URL:?Set an explicit legacy database URL}"
export UPLOAD_DIR="${UPLOAD_DIR:-$WORKSPACE_ROOT/data/uploads}"
cd "$LEGACY_ROOT"
exec "$WORKSPACE_ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "${LEGACY_PORT:-8010}" --reload
