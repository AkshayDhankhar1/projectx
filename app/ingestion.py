"""
ingestion.py — Event Ingestion Endpoint

POST /events/ingest

This is the entry point for all detection events into our system.
The detection pipeline sends batches of events here, and we:
1. Validate each event against our Pydantic schema
2. Check for duplicates using event_id (idempotent design)
3. Store valid events in SQLite
4. Broadcast new events to the live dashboard via WebSocket
5. Return a summary: how many accepted, how many rejected, and why

Key design decisions:
- IDEMPOTENT: calling twice with the same events produces the same result.
  We use INSERT OR IGNORE — if event_id already exists, SQLite skips it.
- PARTIAL SUCCESS: if 3 out of 5 events are valid, we accept those 3
  and report the 2 failures in the response.
"""

import json
from fastapi import APIRouter, HTTPException
from app.models import Event, EventBatch, IngestResponse, IngestError
from app.database import get_db
from app.websocket_manager import ws_manager
import structlog

logger = structlog.get_logger(__name__)

# ============================================================
# Router — a way to group related endpoints together
# ============================================================
# Instead of defining routes on the main app, we use a Router.
# The main app then "includes" this router. This keeps code organized.
router = APIRouter(tags=["ingestion"])


@router.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(batch: EventBatch) -> IngestResponse:
    """Ingest a batch of detection events.
    
    Accepts up to 500 events. Each event is validated, deduplicated
    by event_id, and stored in the database. Returns a summary of
    what was accepted and what was rejected.
    
    This endpoint is IDEMPOTENT: sending the same event twice will
    not create a duplicate. The second send will be silently skipped.
    """
    try:
        db = await get_db()
    except RuntimeError:
        # Database not available — return HTTP 503 (Service Unavailable)
        raise HTTPException(
            status_code=503,
            detail={"error": "Database unavailable", "retry_after": 5},
        )

    accepted = 0
    rejected = 0
    errors: list[IngestError] = []
    new_events: list[dict] = []  # For WebSocket broadcast

    for event in batch.events:
        try:
            # Convert the event's metadata to a JSON string for storage.
            # SQLite doesn't have a native JSON column type, so we store
            # the metadata as a text string and parse it back when reading.
            metadata_json = json.dumps(event.metadata.model_dump())

            # INSERT OR IGNORE: if this event_id already exists in the
            # database, SQLite will silently skip this insert.
            # This is what makes the endpoint idempotent.
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO events 
                    (event_id, store_id, camera_id, visitor_id, event_type,
                     timestamp, zone_id, dwell_ms, is_staff, confidence,
                     metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.event_id),
                    event.store_id,
                    event.camera_id,
                    event.visitor_id,
                    event.event_type,
                    event.timestamp.isoformat(),
                    event.zone_id,
                    event.dwell_ms,
                    1 if event.is_staff else 0,
                    event.confidence,
                    metadata_json,
                ),
            )

            # rowcount tells us if the INSERT actually happened.
            # If it was a duplicate (IGNORE'd), rowcount = 0.
            if cursor.rowcount > 0:
                accepted += 1
                # Prepare event data for WebSocket broadcast
                new_events.append(event.model_dump(mode="json"))
            else:
                # Event already existed — still counts as "accepted"
                # because idempotent means "same result either way"
                accepted += 1

        except Exception as e:
            # This event failed — record the error but continue
            # processing the rest of the batch (partial success)
            rejected += 1
            errors.append(IngestError(
                event_id=str(event.event_id),
                reason=str(e),
            ))
            logger.warning(
                "event_ingestion_failed",
                event_id=str(event.event_id),
                error=str(e),
            )

    # Commit all successful inserts in one batch (much faster than
    # committing after each individual insert)
    await db.commit()

    # Broadcast new events to connected dashboards
    if new_events:
        await ws_manager.broadcast({
            "type": "new_events",
            "events": new_events,
            "count": len(new_events),
        })

    logger.info(
        "events_ingested",
        accepted=accepted,
        rejected=rejected,
        total=len(batch.events),
    )

    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        errors=errors,
    )
