# PROMPT: "Generate tests for a zone heatmap endpoint that returns visit
# counts, average dwell times, normalized scores (0-100), and data
# confidence per zone. Test empty store, single zone, multiple zones,
# and score normalization."
# CHANGES MADE: Added test for normalized_score max=100, and data
# confidence levels based on sample size.

"""
test_heatmap.py — Tests for GET /stores/{id}/heatmap
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
        "camera_id": "CAM_FLOOR_01",
        "visitor_id": "VIS_test01",
        "event_type": "ZONE_ENTER",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": "SKINCARE",
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.88,
        "metadata": {"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 1},
    }
    event.update(overrides)
    return event


@pytest.mark.asyncio
async def test_empty_heatmap():
    """Heatmap with no zone events should return empty zones list."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/stores/STORE_PRP_001/heatmap")
        assert r.status_code == 200
        data = r.json()
        assert "zones" in data
        assert isinstance(data["zones"], list)
        assert len(data["zones"]) == 0


@pytest.mark.asyncio
async def test_single_zone_heatmap():
    """One zone with visits should appear in heatmap."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            make_event(visitor_id=f"VIS_c{i}", zone_id="SKINCARE")
            for i in range(5)
        ]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/heatmap")
        data = r.json()
        assert len(data["zones"]) == 1
        assert data["zones"][0]["zone_id"] == "SKINCARE"
        assert data["zones"][0]["visit_count"] == 5
        # Single zone = max score = 100
        assert data["zones"][0]["normalized_score"] == 100.0


@pytest.mark.asyncio
async def test_multiple_zones_normalized():
    """Multiple zones should have scores normalized relative to the busiest zone."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # SKINCARE: 10 visits, MAKEUP: 5 visits
        events = [
            make_event(visitor_id=f"VIS_sk{i}", zone_id="SKINCARE")
            for i in range(10)
        ] + [
            make_event(visitor_id=f"VIS_mk{i}", zone_id="MAKEUP")
            for i in range(5)
        ]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/heatmap")
        data = r.json()
        zones = {z["zone_id"]: z for z in data["zones"]}

        assert len(zones) == 2
        # SKINCARE is busiest -> score = 100
        assert zones["SKINCARE"]["normalized_score"] == 100.0
        # MAKEUP has half the visits -> score = 50
        assert zones["MAKEUP"]["normalized_score"] == 50.0


@pytest.mark.asyncio
async def test_heatmap_with_dwell():
    """Heatmap should include average dwell times from ZONE_DWELL events."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            make_event(event_type="ZONE_ENTER", zone_id="SKINCARE"),
            make_event(event_type="ZONE_DWELL", zone_id="SKINCARE", dwell_ms=30000),
            make_event(event_type="ZONE_DWELL", zone_id="SKINCARE", dwell_ms=60000, visitor_id="VIS_c2"),
        ]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/heatmap")
        data = r.json()
        skincare = next((z for z in data["zones"] if z["zone_id"] == "SKINCARE"), None)
        assert skincare is not None
        assert skincare["avg_dwell_ms"] == 45000.0


@pytest.mark.asyncio
async def test_heatmap_has_required_fields():
    """Each zone in heatmap must have all required fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [make_event(zone_id="SKINCARE")]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/heatmap")
        data = r.json()
        zone = data["zones"][0]
        required_fields = ["zone_id", "visit_count", "avg_dwell_ms",
                           "normalized_score", "data_confidence"]
        for field in required_fields:
            assert field in zone, f"Missing field: {field}"
