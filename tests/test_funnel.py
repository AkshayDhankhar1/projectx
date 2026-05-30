# PROMPT: "Generate tests for a conversion funnel endpoint. The funnel has
# 4 stages: Entry → Zone Visit → Billing Queue → Purchase. Test session
# deduplication (re-entries don't double-count), drop-off percentages, and
# that all stages are present even when empty."
# CHANGES MADE: Added re-entry deduplication test with REENTRY event type.
# Added test for correct drop-off calculation.

"""
test_funnel.py — Tests for GET /stores/{id}/funnel
"""

import pytest
import uuid
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import init_database, close_database
import app.database as db_module
import os


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    test_db = str(tmp_path / "test.db")
    db_module.DATABASE_PATH = test_db
    await init_database()
    yield
    await close_database()
    if os.path.exists(test_db):
        os.remove(test_db)


def make_event(**overrides) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_PRP_001",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_test01",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": None, "dwell_ms": 0, "is_staff": False,
        "confidence": 0.92,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    event.update(overrides)
    return event


@pytest.mark.asyncio
async def test_empty_funnel():
    """Funnel with no events should have all stages at 0."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/stores/STORE_PRP_001/funnel")
        assert r.status_code == 200
        data = r.json()
        assert len(data["stages"]) == 4
        for stage in data["stages"]:
            assert stage["count"] == 0


@pytest.mark.asyncio
async def test_funnel_flow():
    """Test a full funnel with visitors at each stage."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            # 3 visitors enter
            make_event(visitor_id="VIS_c1", event_type="ENTRY"),
            make_event(visitor_id="VIS_c2", event_type="ENTRY"),
            make_event(visitor_id="VIS_c3", event_type="ENTRY"),
            # 2 visit zones
            make_event(visitor_id="VIS_c1", event_type="ZONE_ENTER", zone_id="SKINCARE"),
            make_event(visitor_id="VIS_c2", event_type="ZONE_ENTER", zone_id="MAKEUP"),
            # 1 joins billing queue
            make_event(visitor_id="VIS_c1", event_type="BILLING_QUEUE_JOIN", zone_id="BILLING"),
        ]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/funnel")
        data = r.json()

        assert data["stages"][0]["count"] == 3   # Entry
        assert data["stages"][1]["count"] == 2   # Zone Visit
        assert data["stages"][2]["count"] == 1   # Billing Queue


@pytest.mark.asyncio
async def test_reentry_deduplication():
    """A re-entering visitor should NOT be double-counted in the funnel."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            # Visitor enters, exits, re-enters
            make_event(visitor_id="VIS_c1", event_type="ENTRY"),
            make_event(visitor_id="VIS_c1", event_type="EXIT"),
            make_event(visitor_id="VIS_c1", event_type="REENTRY"),
            # Another unique visitor
            make_event(visitor_id="VIS_c2", event_type="ENTRY"),
        ]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/funnel")
        data = r.json()

        # Should count 2 unique visitors (VIS_c1 + VIS_c2), not 3
        assert data["stages"][0]["count"] == 2


@pytest.mark.asyncio
async def test_drop_off_calculation():
    """Test that drop-off percentages are calculated correctly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            make_event(visitor_id=f"VIS_c{i}", event_type="ENTRY")
            for i in range(10)
        ] + [
            make_event(visitor_id=f"VIS_c{i}", event_type="ZONE_ENTER", zone_id="SKINCARE")
            for i in range(6)  # 6 out of 10 visit zones
        ]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/funnel")
        data = r.json()

        # Drop-off from Entry(10) to Zone Visit(6) = 40%
        zone_stage = data["stages"][1]
        assert zone_stage["drop_off_percent"] == 40.0
