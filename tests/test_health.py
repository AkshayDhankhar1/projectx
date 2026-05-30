# PROMPT: "Generate tests for a /health endpoint. Test healthy when DB
# connected, stale feed warning when last event >10 min, and degraded
# status. Also test that it never throws exceptions."
# CHANGES MADE: Added test for database_connected flag. Simplified stale
# feed test.

"""
test_health.py — Tests for GET /health
"""

import pytest
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


@pytest.mark.asyncio
async def test_health_returns_200():
    """Health endpoint must always return 200, even with issues."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_health_structure():
    """Health response must have all required fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        data = r.json()
        assert "status" in data
        assert "uptime_seconds" in data
        assert "database_connected" in data
        assert "stores" in data
        assert "version" in data


@pytest.mark.asyncio
async def test_healthy_with_db():
    """When DB is connected and no data, status should be healthy."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        data = r.json()
        assert data["status"] == "healthy"
        assert data["database_connected"] is True


@pytest.mark.asyncio
async def test_health_shows_stale_feed():
    """Stores with old events should show is_stale=true."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        import uuid
        # Ingest an event with old timestamp
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_PRP_001",
            "camera_id": "CAM_ENTRY_01",
            "visitor_id": "VIS_test01",
            "event_type": "ENTRY",
            "timestamp": "2026-04-10T19:00:00+00:00",
            "confidence": 0.9,
            "metadata": {"session_seq": 1},
        }
        await client.post("/events/ingest", json={"events": [event]})

        r = await client.get("/health")
        data = r.json()

        # Status should be degraded due to stale feed
        assert data["status"] == "degraded"
        assert len(data["stores"]) > 0
        assert data["stores"][0]["is_stale"] is True
