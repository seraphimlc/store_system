#!/bin/sh
set -e
echo "== alembic upgrade head =="
alembic upgrade head
echo "== start uvicorn =="
exec python -m app.web_runner
