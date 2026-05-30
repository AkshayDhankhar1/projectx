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
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY data/ ./data/

# Expose port 8000 for the API
# This is documentation — it tells Docker users which port to map
EXPOSE 8000

# Health check — Docker will periodically call this to verify the
# container is working. If it fails 3 times, Docker restarts it.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the API server using uvicorn
# --host 0.0.0.0: listen on all network interfaces (required in containers)
# --port 8000: the port number
# --workers 2: run 2 worker processes for better throughput
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
