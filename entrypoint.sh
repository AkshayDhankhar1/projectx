#!/bin/bash
# ============================================================
# entrypoint.sh — Docker container startup script
# ============================================================
# This script runs when the Docker container starts.
# It performs two tasks:
# 1. Auto-ingest events from events.jsonl into the API (background)
# 2. Start the API server (foreground)
# ============================================================

# Determine port (use PORT env var, or default to 8000 if not set)
PORT=${PORT:-8000}

# Pre-populate the SQLite database synchronously before starting the server
if [ -f /app/data/events.jsonl ]; then
    echo "Pre-populating database from events.jsonl..."
    python /app/scripts/db_prepopulate.py
else
    echo "No events.jsonl found — starting server directly."
fi

# Start the API server in the foreground (production mode)
echo "Starting API server on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers 1
