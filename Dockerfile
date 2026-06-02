# ============================================================
# Dockerfile for Store Intelligence API
# ============================================================
# A Dockerfile is a recipe for creating a Docker "image" —
# a snapshot of an environment with all dependencies installed.
# When you run this image, it becomes a "container" — an isolated
# process with its own filesystem, network, and resources.
#
# Think of it like a virtual machine, but much lighter and faster.
# ============================================================

# Start from a slim Python 3.12 base image (~50MB vs ~1GB for full)
# "slim" removes unnecessary system packages to keep the image small
FROM python:3.12-slim

# Set environment variables:
# PYTHONDONTWRITEBYTECODE=1 → Don't create .pyc bytecode cache files
# PYTHONUNBUFFERED=1 → Print output immediately (don't buffer stdout)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set the working directory inside the container
# All subsequent commands will run relative to /app
WORKDIR /app

# Copy and install dependencies FIRST (Docker caching optimization).
# Docker rebuilds from the first changed line. Since requirements.txt
# changes rarely, this layer is cached and pip install is skipped
# on subsequent builds — saving minutes of build time.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir requests

# Copy application code and data
COPY app/ ./app/
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Expose port 8000 for the API
# This is documentation — it tells Docker users which port to map
EXPOSE 8000

# Health check — Docker will periodically call this to verify the
# container is working. If it fails 3 times, Docker restarts it.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import os, urllib.request; port = os.environ.get('PORT', '8000'); urllib.request.urlopen(f'http://localhost:{port}/health')" || exit 1

# Pre-populate the SQLite database, then start the API server in the foreground
CMD python scripts/db_prepopulate.py && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
