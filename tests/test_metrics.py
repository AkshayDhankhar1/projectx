# PROMPT: "Generate pytest tests for store metrics endpoint that returns
# unique visitors (excluding staff), conversion rate, avg dwell per zone,
# queue depth, and abandonment rate. Include edge cases: empty store,
# all-staff clip, zero purchases."
# CHANGES MADE: Added test for metrics after ingesting mixed staff/customer
# events. Added zero-purchase store test. Simplified fixture setup.

"""
test_metrics.py — Tests for GET /stores/{id}/metrics

Tests the real-time metrics computation including visitor counting,
conversion rate, dwell times, and queue metrics.
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
    """Fresh test database for each test."""
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
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.92,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    event.update(overrides)
    return event


async def ingest(client, events):
    return await client.post("/events/ingest", json={"events": events})


@pytest.mark.asyncio
async def test_empty_store_metrics():
    """Metrics for a store with zero events should return valid response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/stores/STORE_PRP_001/metrics")
        assert r.status_code == 200
        data = r.json()
        assert data["unique_visitors"] == 0
        assert data["conversion_rate"] == 0.0
        assert data["abandonment_rate"] == 0.0


@pytest.mark.asyncio
async def test_visitor_count_excludes_staff():
    """Staff events should NOT be counted in unique visitors."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            make_event(visitor_id="VIS_cust01", is_staff=False),
            make_event(visitor_id="VIS_cust02", is_staff=False),
            make_event(visitor_id="VIS_staff01", is_staff=True),
        ]
        await ingest(client, events)

        r = await client.get("/stores/STORE_PRP_001/metrics")
        data = r.json()
        # Only 2 customers, staff excluded
        assert data["unique_visitors"] == 2


@pytest.mark.asyncio
async def test_all_staff_clip():
    """A clip with only staff should show 0 visitors."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            make_event(visitor_id="VIS_staff01", is_staff=True),
            make_event(visitor_id="VIS_staff02", is_staff=True),
        ]
        await ingest(client, events)

        r = await client.get("/stores/STORE_PRP_001/metrics")
        data = r.json()
        assert data["unique_visitors"] == 0
        assert data["conversion_rate"] == 0.0


@pytest.mark.asyncio
async def test_metrics_with_dwell():
    """Test that dwell times are correctly computed per zone."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            make_event(
                event_type="ZONE_DWELL",
                zone_id="SKINCARE",
                dwell_ms=30000,
                visitor_id="VIS_c1",
            ),
            make_event(
                event_type="ZONE_DWELL",
                zone_id="SKINCARE",
                dwell_ms=60000,
                visitor_id="VIS_c2",
            ),
        ]
        await ingest(client, events)

        r = await client.get("/stores/STORE_PRP_001/metrics")
        data = r.json()

        # Check that SKINCARE zone has avg dwell of 45000ms
        skincare = next(
            (z for z in data["avg_dwell_by_zone"] if z["zone_id"] == "SKINCARE"),
            None,
        )
        assert skincare is not None
        assert skincare["avg_dwell_ms"] == 45000.0


@pytest.mark.asyncio
async def test_zero_purchase_conversion():
    """Store with visitors but no billing activity → conversion = 0."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = [
            make_event(visitor_id="VIS_c1"),
            make_event(visitor_id="VIS_c2"),
            make_event(visitor_id="VIS_c3"),
        ]
        await ingest(client, events)

        r = await client.get("/stores/STORE_PRP_001/metrics")
        data = r.json()
        assert data["unique_visitors"] == 3
        # No billing zone activity → conversion = 0
        assert data["conversion_rate"] == 0.0
