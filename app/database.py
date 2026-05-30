"""
database.py — SQLite Database Layer for Store Intelligence API

SQLite is a lightweight, file-based database. Unlike PostgreSQL or MySQL,
it doesn't need a separate server process — the entire database lives in
a single file (store.db). This makes it perfect for containerized deployments.

We use 'aiosqlite' which is an async wrapper around SQLite, meaning our
database queries won't block other API requests from being processed.

Key concepts:
- Connection pool: we keep one connection open for the lifetime of the app
- WAL mode: Write-Ahead Logging allows concurrent reads while writing
- UNIQUE constraint on event_id: makes our ingest endpoint idempotent
  (inserting the same event twice won't create duplicates)
"""

import aiosqlite
import os
import time
import structlog

# Get a structured logger for this module
logger = structlog.get_logger(__name__)

# ============================================================
# Global state — the database connection
# ============================================================
# We store the connection as a module-level variable so all endpoints
# can access it. It's initialized in init_database() on app startup.
_db_connection: aiosqlite.Connection | None = None
_start_time: float = time.time()


# ============================================================
# Database path — configurable via environment variable
# ============================================================
DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/store.db")


async def init_database() -> None:
    """Create the database connection and tables on app startup.
    
    This runs once when FastAPI starts (via the lifespan handler).
    It creates the events table if it doesn't exist, along with
    indexes for fast querying by store_id, timestamp, and visitor_id.
    """
    global _db_connection

    logger.info("initializing_database", path=DATABASE_PATH)

    # Ensure the directory for the database file exists
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)

    # Open an async SQLite connection
    _db_connection = await aiosqlite.connect(DATABASE_PATH)

    # Enable WAL (Write-Ahead Logging) mode for better concurrent performance.
    # WAL allows readers to not block writers and vice versa.
    await _db_connection.execute("PRAGMA journal_mode=WAL")

    # Return query results as dictionaries instead of tuples.
    # This means we can access columns by name: row["event_id"]
    _db_connection.row_factory = aiosqlite.Row

    # Create the events table if it doesn't already exist.
    # The UNIQUE constraint on event_id ensures idempotent ingestion:
    # inserting the same event twice will silently skip the duplicate.
    await _db_connection.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id        TEXT PRIMARY KEY,
            store_id        TEXT NOT NULL,
            camera_id       TEXT NOT NULL,
            visitor_id      TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            zone_id         TEXT,
            dwell_ms        INTEGER DEFAULT 0,
            is_staff        INTEGER DEFAULT 0,
            confidence      REAL NOT NULL,
            metadata_json   TEXT,
            ingested_at     TEXT DEFAULT (datetime('now'))
        )
    """)

    # Create indexes for fast querying.
    # Without indexes, every query would scan the entire table (slow).
    # With indexes, SQLite can jump directly to matching rows.
    await _db_connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_store_time 
        ON events(store_id, timestamp)
    """)
    await _db_connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_store_type 
        ON events(store_id, event_type)
    """)
    await _db_connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_visitor 
        ON events(visitor_id)
    """)

    await _db_connection.commit()
    logger.info("database_initialized", tables=["events"])


async def get_db() -> aiosqlite.Connection:
    """Get the database connection.
    
    Returns the global connection. If the database hasn't been initialized
    (or the connection was lost), raises a RuntimeError which the API
    catches and converts to HTTP 503 (Service Unavailable).
    """
    if _db_connection is None:
        raise RuntimeError("Database not initialized")
    return _db_connection


async def close_database() -> None:
    """Close the database connection on app shutdown.
    
    Called automatically when FastAPI stops (via the lifespan handler).
    """
    global _db_connection
    if _db_connection:
        await _db_connection.close()
        _db_connection = None
        logger.info("database_closed")


def get_uptime() -> float:
    """Return the number of seconds since the app started."""
    return time.time() - _start_time


async def check_database_health() -> bool:
    """Quick health check — can we query the database?
    
    Returns True if a simple SELECT works, False otherwise.
    Used by the /health endpoint.
    """
    try:
        db = await get_db()
        await db.execute("SELECT 1")
        return True
    except Exception:
        return False
