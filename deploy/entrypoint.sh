#!/bin/sh
set -e
echo "== alembic upgrade head =="
alembic upgrade head
echo "== start uvicorn =="
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
