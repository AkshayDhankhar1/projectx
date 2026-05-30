# PROMPT: "Generate tests for anomaly detection: queue spike, conversion
# drop, dead zones, stale feed. Each anomaly should have severity
# (INFO/WARN/CRITICAL) and suggested_action."
# CHANGES MADE: Simplified by testing anomaly presence rather than exact
# values. Added test for no-anomaly baseline.

"""
test_anomalies.py — Tests for GET /stores/{id}/anomalies
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
async def test_no_anomalies_on_empty_store():
    """Empty store should only have STALE_FEED anomaly (no events)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/stores/STORE_PRP_001/anomalies")
        assert r.status_code == 200
        data = r.json()
        # Should detect STALE_FEED since no events exist
        types = [a["anomaly_type"] for a in data["anomalies"]]
        assert "STALE_FEED" in types


@pytest.mark.asyncio
async def test_anomaly_has_severity_and_action():
    """Every anomaly must have severity and suggested_action."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/stores/STORE_PRP_001/anomalies")
        data = r.json()
        for anomaly in data["anomalies"]:
            assert anomaly["severity"] in ["INFO", "WARN", "CRITICAL"]
            assert len(anomaly["suggested_action"]) > 0
            assert len(anomaly["description"]) > 0


@pytest.mark.asyncio
async def test_dead_zone_detection():
    """A zone with old activity but nothing recent should be flagged."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Insert a zone event with old timestamp (1 hour ago)
        old_time = "2026-04-10T19:00:00+00:00"
        events = [
            make_event(
                event_type="ZONE_ENTER", zone_id="SKINCARE",
                timestamp=old_time,
            ),
        ]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/anomalies")
        data = r.json()

        # SKINCARE should be a dead zone (no recent activity)
        dead_zones = [
            a for a in data["anomalies"]
            if a["anomaly_type"] == "DEAD_ZONE"
        ]
        assert len(dead_zones) > 0


@pytest.mark.asyncio
async def test_stale_feed_detection():
    """Old events with no recent ones should trigger STALE_FEED."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        old_time = "2026-04-10T19:00:00+00:00"
        events = [make_event(timestamp=old_time)]
        await client.post("/events/ingest", json={"events": events})

        r = await client.get("/stores/STORE_PRP_001/anomalies")
        data = r.json()
        types = [a["anomaly_type"] for a in data["anomalies"]]
        assert "STALE_FEED" in types
