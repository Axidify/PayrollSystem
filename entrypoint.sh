#!/bin/sh
set -e
# Print useful debug info for startup logs
echo "[entrypoint] starting at $(date -u)"
echo "[entrypoint] working dir: $(pwd)"
echo "[entrypoint] python: $(python -V 2>&1)"
echo "[entrypoint] pip packages:"
pip --disable-pip-version-check list || true
echo "[entrypoint] environment vars:"
env | sort

# Default port if not set
if [ -z "$PORT" ]; then
  PORT=8000
fi

echo "[entrypoint] launching gunicorn on 0.0.0.0:$PORT"

exec gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:$PORT --workers 2 --log-level info
