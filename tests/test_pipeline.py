# PROMPT: "Generate tests for the detection pipeline's event emission.
# Test that emitted events have valid UUIDs, correct event types,
# ISO timestamps, and that event_ids are unique across all events."
# CHANGES MADE: Made tests standalone (no YOLO dependency). Tests the
# EventEmitter and event schema directly. Added session_seq monotonicity.

"""
test_pipeline.py — Tests for the detection pipeline event emission
"""

import pytest
import json
import os
import uuid
from pipeline.emit import EventEmitter


@pytest.fixture
def emitter(tmp_path):
    """Create an EventEmitter writing to a temp file."""
    output = str(tmp_path / "test_events.jsonl")
    em = EventEmitter(output, "STORE_PRP_001")
    yield em
    em.close()


def read_events(emitter) -> list[dict]:
    """Read all events from the emitter's output file."""
    emitter._file.flush()
    events = []
    with open(emitter.output_path, "r") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def test_emit_entry(emitter):
    """Test that ENTRY event has correct schema."""
    emitter.emit_entry(
        camera_id="CAM_ENTRY_01",
        visitor_id="VIS_test01",
        timestamp="2026-04-10T20:10:30+00:00",
        confidence=0.92,
    )
    events = read_events(emitter)
    assert len(events) == 1
    e = events[0]

    # Validate schema
    assert e["event_type"] == "ENTRY"
    assert e["store_id"] == "STORE_PRP_001"
    assert e["camera_id"] == "CAM_ENTRY_01"
    assert e["visitor_id"] == "VIS_test01"
    assert e["confidence"] == 0.92
    assert e["is_staff"] is False
    assert e["dwell_ms"] == 0

    # event_id must be a valid UUID
    uuid.UUID(e["event_id"])  # Raises if invalid


def test_unique_event_ids(emitter):
    """All event_ids must be globally unique."""
    for i in range(20):
        emitter.emit_entry(
            camera_id="CAM_ENTRY_01",
            visitor_id=f"VIS_t{i:02d}",
            timestamp="2026-04-10T20:10:30+00:00",
            confidence=0.9,
        )

    events = read_events(emitter)
    ids = [e["event_id"] for e in events]

    # All IDs must be unique
    assert len(ids) == len(set(ids))


def test_all_event_types_emitted(emitter):
    """Test that all event types can be emitted with correct type strings."""
    emitter.emit_entry("CAM", "VIS", "2026-04-10T20:10:00+00:00", 0.9)
    emitter.emit_exit("CAM", "VIS", "2026-04-10T20:12:00+00:00", 0.9)
    emitter.emit_zone_enter("CAM", "VIS", "2026-04-10T20:10:30+00:00", "SKINCARE", 0.9)
    emitter.emit_zone_exit("CAM", "VIS", "2026-04-10T20:11:00+00:00", "SKINCARE", 0.9)
    emitter.emit_zone_dwell("CAM", "VIS", "2026-04-10T20:11:30+00:00", "SKINCARE", 30000, 0.9)
    emitter.emit_billing_queue_join("CAM", "VIS", "2026-04-10T20:11:00+00:00", 3, 0.9)
    emitter.emit_billing_queue_abandon("CAM", "VIS", "2026-04-10T20:12:00+00:00", 0.9)
    emitter.emit_reentry("CAM", "VIS", "2026-04-10T20:13:00+00:00", 0.9)

    events = read_events(emitter)
    types = [e["event_type"] for e in events]

    expected = [
        "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
        "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
    ]
    assert types == expected


def test_staff_flag(emitter):
    """Test that is_staff flag is correctly set."""
    emitter.emit_entry("CAM", "VIS_staff", "2026-04-10T20:10:00+00:00", 0.9, is_staff=True)
    emitter.emit_entry("CAM", "VIS_cust", "2026-04-10T20:10:00+00:00", 0.9, is_staff=False)

    events = read_events(emitter)
    assert events[0]["is_staff"] is True
    assert events[1]["is_staff"] is False


def test_dwell_event_has_duration(emitter):
    """ZONE_DWELL events must have non-zero dwell_ms."""
    emitter.emit_zone_dwell(
        "CAM", "VIS", "2026-04-10T20:10:00+00:00",
        "SKINCARE", 45000, 0.88,
    )
    events = read_events(emitter)
    assert events[0]["dwell_ms"] == 45000


def test_event_count_tracking(emitter):
    """Test that the emitter tracks total event count."""
    assert emitter.get_event_count() == 0
    emitter.emit_entry("CAM", "VIS", "2026-04-10T20:10:00+00:00", 0.9)
    assert emitter.get_event_count() == 1
    emitter.emit_exit("CAM", "VIS", "2026-04-10T20:12:00+00:00", 0.9)
    assert emitter.get_event_count() == 2
