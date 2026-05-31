#!/bin/bash
# ============================================================
# entrypoint.sh — Docker container startup script
# ============================================================
# This script runs when the Docker container starts.
# It performs two tasks:
# 1. Auto-ingest events from events.jsonl into the API (background)
# 2. Start the API server (foreground)
# ============================================================

# Start the API server in the background temporarily
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 &
API_PID=$!

# Wait for the API to be ready
echo "Waiting for API to start..."
for i in $(seq 1 30); do
    if python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" 2>/dev/null; then
        echo "API is ready."
        break
    fi
    sleep 1
done

# Auto-ingest events if events.jsonl exists
if [ -f /app/data/events.jsonl ]; then
    echo "Auto-ingesting events from events.jsonl..."
    python /app/scripts/ingest_events.py --api-url http://localhost:8000 --events-file /app/data/events.jsonl
    echo "Ingestion complete."
else
    echo "No events.jsonl found — API is ready for manual ingestion."
fi

# Stop the background API
kill $API_PID 2>/dev/null
wait $API_PID 2>/dev/null

# Start the API server in the foreground (production mode)
echo "Starting API server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
