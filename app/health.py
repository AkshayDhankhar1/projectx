"""
health.py — Service Health Endpoint

GET /health

This is the FIRST endpoint an on-call engineer checks when something
seems wrong. It must answer three questions:
1. Is the API running and responsive?
2. Is the database connected and writable?
3. Is data flowing from the detection pipeline? (stale feed check)

Returns:
- "healthy": everything works
- "degraded": API works but some data feeds are stale
- "unhealthy": database is down or critical issues
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
from app.models import HealthResponse, StoreHealth
from app.database import get_db, get_uptime, check_database_health
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])

STALE_FEED_MINUTES = 10  # No events for 10 min = stale


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check the overall health of the Store Intelligence API.
    
    This endpoint should NEVER throw an exception — even if the
    database is down, it returns a structured response describing
    the problem. This is critical for monitoring systems.
    """
    # Check database connectivity
    db_connected = await check_database_health()

    if not db_connected:
        # Database is down — return unhealthy but still respond
        return HealthResponse(
            status="unhealthy",
            uptime_seconds=round(get_uptime(), 1),
            database_connected=False,
            stores=[],
        )

    # If DB is up, gather per-store health info
    try:
        db = await get_db()
        now = datetime.now(timezone.utc)

        # Get the last event timestamp and event count per store
        rows = await db.execute_fetchall(
            """
            SELECT store_id, 
                   MAX(timestamp) as last_event,
                   COUNT(*) as event_count
            FROM events
            GROUP BY store_id
            """
        )

        stores: list[StoreHealth] = []
        has_stale = False

        for row in rows:
            store_id = row[0]
            last_event_str = row[1]
            event_count = row[2]

            # Parse the last event timestamp
            last_event_at = None
            is_stale = False
            if last_event_str:
                try:
                    last_event_at = datetime.fromisoformat(last_event_str)
                    if last_event_at.tzinfo is None:
                        last_event_at = last_event_at.replace(tzinfo=timezone.utc)
                    # Check if feed is stale (>10 min since last event)
                    minutes_since = (now - last_event_at).total_seconds() / 60
                    is_stale = minutes_since > STALE_FEED_MINUTES
                    if is_stale:
                        has_stale = True
                except (ValueError, TypeError):
                    pass

            stores.append(StoreHealth(
                store_id=store_id,
                last_event_at=last_event_at,
                event_count=event_count,
                is_stale=is_stale,
            ))

        # Determine overall status
        status = "healthy"
        if has_stale:
            status = "degraded"  # API works but data is stale

        return HealthResponse(
            status=status,
            uptime_seconds=round(get_uptime(), 1),
            database_connected=True,
            stores=stores,
        )

    except Exception as e:
        logger.error("health_check_failed", error=str(e))
        return HealthResponse(
            status="unhealthy",
            uptime_seconds=round(get_uptime(), 1),
            database_connected=False,
            stores=[],
        )
