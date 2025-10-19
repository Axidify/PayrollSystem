#!/bin/sh
# Simple health check wrapper — returns 200 if the FastAPI server is reachable on localhost and PORT
PORT=${PORT:-8000}
MAX_RETRIES=5
RETRY_DELAY=1
for i in $(seq 1 $MAX_RETRIES); do
  if nc -z localhost "$PORT"; then
    exit 0
  fi
  sleep $RETRY_DELAY
done
exit 1
