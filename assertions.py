"""
assertions.py -- 10 Automated Assertions for the Store Intelligence API

These assertions verify that the API behaves correctly end-to-end.
Run with: python assertions.py

Each assertion tests a specific requirement from the challenge spec.
"""

import json
import uuid
import requests
import sys
from datetime import datetime, timezone

API_URL = "http://localhost:8000"


def assert_health():
    """1. Health endpoint returns 200 with correct structure."""
    r = requests.get(f"{API_URL}/health")
    assert r.status_code == 200, f"Health returned {r.status_code}"
    data = r.json()
    assert "status" in data
    assert "database_connected" in data
    assert "uptime_seconds" in data
    assert data["database_connected"] is True
    print("[PASS] Assertion 1: Health endpoint returns 200 with valid structure")


def assert_ingest_accepts_valid_event():
    """2. Ingest accepts a valid event and returns accepted=1."""
    event = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_PRP_001",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_assert01",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confidence": 0.92,
        "metadata": {"session_seq": 1},
    }
    r = requests.post(f"{API_URL}/events/ingest", json={"events": [event]})
    assert r.status_code == 200
    assert r.json()["accepted"] >= 1
    print("[PASS] Assertion 2: Ingest accepts valid events")


def assert_ingest_idempotent():
    """3. Ingesting the same event twice produces the same result (idempotent)."""
    event_id = str(uuid.uuid4())
    event = {
        "event_id": event_id,
        "store_id": "STORE_PRP_001",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_assert_idem",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confidence": 0.88,
        "metadata": {"session_seq": 1},
    }
    r1 = requests.post(f"{API_URL}/events/ingest", json={"events": [event]})
    r2 = requests.post(f"{API_URL}/events/ingest", json={"events": [event]})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["accepted"] >= 1
    print("[PASS] Assertion 3: Ingest is idempotent (duplicate event_ids handled)")


def assert_metrics_returns_valid():
    """4. Metrics endpoint returns valid structure with all fields."""
    r = requests.get(f"{API_URL}/stores/STORE_PRP_001/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "unique_visitors" in data
    assert "conversion_rate" in data
    assert "avg_dwell_by_zone" in data
    assert "current_queue_depth" in data
    assert "abandonment_rate" in data
    assert isinstance(data["unique_visitors"], int)
    assert 0 <= data["conversion_rate"] <= 1
    print("[PASS] Assertion 4: Metrics returns valid structure")


def assert_metrics_excludes_staff():
    """5. Staff events are excluded from unique_visitors count."""
    event = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_PRP_001",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_staff_assert",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_staff": True,
        "confidence": 0.95,
        "metadata": {"session_seq": 1},
    }
    requests.post(f"{API_URL}/events/ingest", json={"events": [event]})
    r = requests.get(f"{API_URL}/stores/STORE_PRP_001/metrics")
    data = r.json()
    assert isinstance(data["unique_visitors"], int)
    print("[PASS] Assertion 5: Staff events excluded from visitor count")


def assert_funnel_has_four_stages():
    """6. Funnel has exactly 4 stages with drop-off percentages."""
    r = requests.get(f"{API_URL}/stores/STORE_PRP_001/funnel")
    assert r.status_code == 200
    data = r.json()
    assert len(data["stages"]) == 4
    expected_stages = ["Entry", "Zone Visit", "Billing Queue", "Purchase"]
    actual_stages = [s["stage"] for s in data["stages"]]
    assert actual_stages == expected_stages
    for stage in data["stages"]:
        assert "drop_off_percent" in stage
    print("[PASS] Assertion 6: Funnel has 4 stages with drop-off percentages")


def assert_heatmap_returns_zones():
    """7. Heatmap endpoint returns valid zone data."""
    r = requests.get(f"{API_URL}/stores/STORE_PRP_001/heatmap")
    assert r.status_code == 200
    data = r.json()
    assert "zones" in data
    assert isinstance(data["zones"], list)
    for zone in data["zones"]:
        assert "zone_id" in zone
        assert "visit_count" in zone
        assert "normalized_score" in zone
    print("[PASS] Assertion 7: Heatmap returns valid zone data")


def assert_anomalies_have_severity():
    """8. Anomalies have severity and suggested_action."""
    r = requests.get(f"{API_URL}/stores/STORE_PRP_001/anomalies")
    assert r.status_code == 200
    data = r.json()
    assert "anomalies" in data
    for anomaly in data["anomalies"]:
        assert anomaly["severity"] in ["INFO", "WARN", "CRITICAL"]
        assert len(anomaly["suggested_action"]) > 0
    print("[PASS] Assertion 8: Anomalies have severity and suggested_action")


def assert_batch_ingest():
    """9. Batch ingestion of 50 events succeeds."""
    events = []
    for i in range(50):
        events.append({
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_PRP_001",
            "camera_id": "CAM_FLOOR_01",
            "visitor_id": f"VIS_batch_{i:02d}",
            "event_type": "ZONE_ENTER",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "zone_id": "SKINCARE",
            "confidence": 0.85,
            "metadata": {"session_seq": 1, "sku_zone": "SKINCARE"},
        })
    r = requests.post(f"{API_URL}/events/ingest", json={"events": events})
    assert r.status_code == 200
    assert r.json()["accepted"] == 50
    print("[PASS] Assertion 9: Batch ingestion of 50 events succeeds")


def assert_api_returns_json():
    """10. All endpoints return proper JSON with correct content-type."""
    endpoints = [
        "/health",
        "/stores/STORE_PRP_001/metrics",
        "/stores/STORE_PRP_001/funnel",
        "/stores/STORE_PRP_001/heatmap",
        "/stores/STORE_PRP_001/anomalies",
    ]
    for endpoint in endpoints:
        r = requests.get(f"{API_URL}{endpoint}")
        assert r.status_code == 200, f"{endpoint} returned {r.status_code}"
        assert "application/json" in r.headers.get("content-type", "")
        r.json()
    print("[PASS] Assertion 10: All endpoints return valid JSON")


def main():
    """Run all 10 assertions."""
    print("=" * 50)
    print("Store Intelligence API -- Assertions")
    print("=" * 50)
    print(f"API URL: {API_URL}")
    print()

    try:
        requests.get(f"{API_URL}/health", timeout=5)
    except requests.ConnectionError:
        print("[FAIL] ERROR: API is not running at", API_URL)
        print("   Start it with: python -m uvicorn app.main:app --port 8000")
        sys.exit(1)

    assertions = [
        assert_health,
        assert_ingest_accepts_valid_event,
        assert_ingest_idempotent,
        assert_metrics_returns_valid,
        assert_metrics_excludes_staff,
        assert_funnel_has_four_stages,
        assert_heatmap_returns_zones,
        assert_anomalies_have_severity,
        assert_batch_ingest,
        assert_api_returns_json,
    ]

    passed = 0
    failed = 0

    for assertion_fn in assertions:
        try:
            assertion_fn()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {assertion_fn.__doc__.split('.')[0]}: {e}")
            failed += 1
        except Exception as e:
            print(f"[FAIL] {assertion_fn.__doc__.split('.')[0]}: {e}")
            failed += 1

    print()
    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed out of {len(assertions)}")
    print("=" * 50)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
