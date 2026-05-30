"""
main.py — FastAPI Application Entry Point

This is the heart of the Store Intelligence API. It:
1. Creates the FastAPI application instance
2. Configures structured logging (JSON format for production)
3. Sets up middleware for request tracing and latency measurement
4. Includes all endpoint routers (ingestion, metrics, funnel, etc.)
5. Manages the application lifecycle (startup/shutdown)
6. Provides the WebSocket endpoint for the live dashboard

How FastAPI works:
- You define functions and "decorate" them with @app.get() or @app.post()
- FastAPI automatically converts the function's return value to JSON
- It also automatically validates request data using Pydantic models
- It generates interactive API docs at /docs (Swagger UI)
"""

import time
import uuid
import json
import structlog
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_database, close_database
from app.pos_loader import load_pos_data
from app.websocket_manager import ws_manager

# Import endpoint routers
from app.ingestion import router as ingestion_router
from app.metrics import router as metrics_router
from app.funnel import router as funnel_router
from app.heatmap import router as heatmap_router
from app.anomalies import router as anomalies_router
from app.health import router as health_router


# ============================================================
# Structured Logging Configuration
# ============================================================
# structlog outputs logs as JSON objects, making them easy to parse
# by log aggregation tools (ELK Stack, Datadog, Grafana Loki).
#
# Instead of: "INFO: Request to /health took 12ms"
# We get:     {"event": "request_completed", "path": "/health", "latency_ms": 12, ...}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()  # Pretty output for dev; change to JSONRenderer for prod
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


# ============================================================
# Application Lifespan (Startup + Shutdown)
# ============================================================
# The lifespan context manager runs code BEFORE the app starts
# accepting requests (startup) and AFTER it stops (shutdown).
# This is where we initialize the database and load POS data.

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle.
    
    Startup: initialize database, load POS data
    Shutdown: close database connection
    """
    logger.info("app_starting", version="1.0.0")
    
    # STARTUP: runs before first request
    await init_database()
    load_pos_data()
    
    logger.info("app_ready", message="Store Intelligence API is ready")
    
    yield  # ← App runs and handles requests here
    
    # SHUTDOWN: runs when app stops
    await close_database()
    logger.info("app_stopped")


# ============================================================
# Create the FastAPI Application
# ============================================================
app = FastAPI(
    title="Store Intelligence API",
    description="Real-time retail analytics from CCTV detection pipeline",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# CORS Middleware
# ============================================================
# CORS (Cross-Origin Resource Sharing) allows the dashboard
# (running on port 3000) to make API calls to the API (port 8000).
# Without this, the browser would block cross-origin requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # Allow all origins (fine for internal tool)
    allow_credentials=True,
    allow_methods=["*"],        # Allow all HTTP methods
    allow_headers=["*"],        # Allow all headers
)


# ============================================================
# Request Logging Middleware
# ============================================================
# This middleware runs for EVERY request. It:
# 1. Generates a unique trace_id for request tracing
# 2. Measures request latency
# 3. Logs structured data about the request

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Log every request with structured data.
    
    Every log entry includes:
    - trace_id: unique ID to trace this request across log entries
    - method: HTTP method (GET, POST, etc.)
    - path: URL path (/health, /stores/STORE_PRP_001/metrics, etc.)
    - latency_ms: how long the request took in milliseconds
    - status_code: HTTP response code (200, 404, 500, etc.)
    """
    # Generate a unique ID for this request
    trace_id = str(uuid.uuid4())[:8]
    
    # Record the start time
    start_time = time.time()
    
    # Try to extract store_id from the URL path (if present)
    store_id = None
    path_parts = request.url.path.split("/")
    if "stores" in path_parts:
        idx = path_parts.index("stores")
        if idx + 1 < len(path_parts):
            store_id = path_parts[idx + 1]
    
    try:
        # Process the request (call the actual endpoint handler)
        response = await call_next(request)
        
        # Calculate how long it took
        latency_ms = round((time.time() - start_time) * 1000, 1)
        
        # Log the completed request
        logger.info(
            "request_completed",
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            store_id=store_id,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
        
        # Add trace_id to response headers for debugging
        response.headers["X-Trace-ID"] = trace_id
        
        return response
        
    except Exception as e:
        # If something goes wrong, log the error and return 500
        latency_ms = round((time.time() - start_time) * 1000, 1)
        logger.error(
            "request_failed",
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            error=str(e),
            latency_ms=latency_ms,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "trace_id": trace_id,
            },
        )


# ============================================================
# Global Exception Handler
# ============================================================
# Catch any unhandled exception and return a structured error
# response instead of a raw stack trace (which is a security risk).

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unhandled exceptions with structured error responses.
    
    In production, you NEVER want raw stack traces in API responses.
    They can reveal internal implementation details to attackers.
    """
    logger.error("unhandled_exception", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": "An unexpected error occurred. Check logs for details.",
        },
    )


# ============================================================
# Register Endpoint Routers
# ============================================================
# Each router defines a group of related endpoints.
# Including them here makes their endpoints available on the app.

app.include_router(ingestion_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)


# ============================================================
# WebSocket Endpoint for Live Dashboard
# ============================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates.
    
    The dashboard connects here and receives:
    - New events as they are ingested
    - Periodic metric updates
    
    The connection stays open until the client disconnects.
    """
    await ws_manager.connect(websocket)
    try:
        # Keep the connection alive — wait for messages from client
        while True:
            # We don't expect messages FROM the dashboard,
            # but we need to keep the loop running to detect disconnects
            data = await websocket.receive_text()
            # Client can send "ping" to keep connection alive
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ============================================================
# Root Endpoint
# ============================================================
@app.get("/")
async def root():
    """Root endpoint — basic API information."""
    return {
        "service": "Store Intelligence API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
