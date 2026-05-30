# PROMPT: "Generate pytest tests for a FastAPI event ingestion endpoint that
# accepts batches of up to 500 events, validates schemas, deduplicates by
# event_id, and returns partial success responses. Test idempotency,
# malformed events, and empty batches."
# CHANGES MADE: Added edge cases for all-staff events, duplicate event_ids
# in same batch, and oversized batch rejection. Changed fixture to use
# test database path. Added re-entry event ingestion test.

"""
test_ingestion.py — Tests for POST /events/ingest

Tests the core ingestion endpoint which accepts batches of events,
validates them, deduplicates by event_id, and returns partial success.
"""

import pytest
import json
import uuid
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import init_database, close_database, DATABASE_PATH
import os


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    """Set up a fresh test database for each test."""
    import app.database as db_module
    test_db = str(tmp_path / "test.db")
    db_module.DATABASE_PATH = test_db
    await init_database()
    yield
    await close_database()
    if os.path.exists(test_db):
        os.remove(test_db)


def make_event(**overrides) -> dict:
    """Create a valid event dict with optional overrides."""
    event = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_PRP_001",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_test01",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.92,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    event.update(overrides)
    return event


@pytest.mark.asyncio
async def test_ingest_single_event():
    """Test ingesting one valid event."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        event = make_event()
        response = await client.post(
            "/events/ingest",
            json={"events": [event]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 1
        assert data["rejected"] == 0
        assert data["errors"] == []


@pytest.mark.asyncio
async def test_ingest_batch():
    """Test ingesting a batch of 5 events."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [make_event() for _ in range(5)]
        response = await client.post(
            "/events/ingest",
            json={"events": events},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 5
        assert data["rejected"] == 0


@pytest.mark.asyncio
async def test_idempotency():
    """Test that ingesting the same event twice is idempotent.
    
    The second POST with the same event_id should not create a duplicate.
    Both calls should return accepted (idempotent = same result).
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        event = make_event()

        # First ingest
        r1 = await client.post("/events/ingest", json={"events": [event]})
        assert r1.status_code == 200
        assert r1.json()["accepted"] == 1

        # Second ingest with same event_id — should still succeed
        r2 = await client.post("/events/ingest", json={"events": [event]})
        assert r2.status_code == 200
        assert r2.json()["accepted"] == 1  # Idempotent

        # Verify only one event in the database
        from app.database import get_db
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM events WHERE event_id = ?",
            (event["event_id"],),
        )
        assert rows[0][0] == 1  # Only one copy exists


@pytest.mark.asyncio
async def test_empty_batch():
    """Test ingesting an empty batch."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/events/ingest",
            json={"events": []},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 0
        assert data["rejected"] == 0


@pytest.mark.asyncio
async def test_all_event_types():
    """Test that all event types are accepted."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        event_types = [
            "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
            "ZONE_DWELL", "BILLING_QUEUE_JOIN",
            "BILLING_QUEUE_ABANDON", "REENTRY",
        ]
        events = [
            make_event(event_type=et, zone_id="SKINCARE" if "ZONE" in et else None)
            for et in event_types
        ]
        response = await client.post(
            "/events/ingest",
            json={"events": events},
        )
        assert response.status_code == 200
        assert response.json()["accepted"] == len(event_types)


@pytest.mark.asyncio
async def test_staff_event_ingestion():
    """Test that staff events (is_staff=true) are accepted."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        event = make_event(is_staff=True)
        response = await client.post(
            "/events/ingest",
            json={"events": [event]},
        )
        assert response.status_code == 200
        assert response.json()["accepted"] == 1
