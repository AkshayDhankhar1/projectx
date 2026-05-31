"""
emit.py — Event Schema and Emission

This module handles creating and writing structured detection events.
Every event follows the exact schema required by the challenge:

{
    "event_id":   "uuid-v4",              # globally unique
    "store_id":   "STORE_PRP_001",        # from store_layout.json
    "camera_id":  "CAM_ENTRY_01",         # which camera
    "visitor_id": "VIS_c8a2f1",           # per-session visitor token
    "event_type": "ZONE_DWELL",           # what happened
    "timestamp":  "2026-04-10T20:10:30Z", # ISO-8601 UTC
    "zone_id":    "SKINCARE",             # which zone (null for ENTRY/EXIT)
    "dwell_ms":   8400,                   # duration in milliseconds
    "is_staff":   false,                  # staff classification
    "confidence": 0.91,                   # detection confidence
    "metadata":   { ... }                 # extra context
}

Events are written to a JSONL file (one JSON object per line)
and optionally POSTed to the API for live streaming.
"""

import json
import uuid
import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class EventMetadata:
    """Extra contextual data for an event."""
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 1


@dataclass
class DetectionEvent:
    """One detection event from the pipeline.
    
    This dataclass mirrors the required event schema exactly.
    When we convert it to JSON, it produces the expected format.
    """
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str  # ISO-8601 string
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 0.0
    metadata: dict = field(default_factory=lambda: {
        "queue_depth": None,
        "sku_zone": None,
        "session_seq": 1,
    })


class EventEmitter:
    """Creates and writes events to a JSONL file.
    
    JSONL = JSON Lines format. Each line is one complete JSON object.
    This is efficient for streaming because you can append new events
    without reading the entire file.
    
    Usage:
        emitter = EventEmitter("data/events.jsonl", "STORE_PRP_001")
        emitter.emit_entry("CAM_ENTRY_01", "VIS_abc123", timestamp, 0.95)
    """

    def __init__(self, output_path: str, store_id: str):
        self.output_path = output_path
        self.store_id = store_id
        self.event_count = 0

        # Create output directory if needed
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # Open file in append mode (don't overwrite existing events)
        self._file = open(output_path, "a", encoding="utf-8")

    def _make_event(
        self,
        camera_id: str,
        visitor_id: str,
        event_type: str,
        timestamp: str,
        confidence: float,
        zone_id: Optional[str] = None,
        dwell_ms: int = 0,
        is_staff: bool = False,
        queue_depth: Optional[int] = None,
        sku_zone: Optional[str] = None,
        session_seq: int = 1,
    ) -> DetectionEvent:
        """Create a new event with a unique UUID."""
        self.event_count += 1
        return DetectionEvent(
            event_id=str(uuid.uuid4()),
            store_id=self.store_id,
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type=event_type,
            timestamp=timestamp,
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            is_staff=is_staff,
            confidence=confidence,
            metadata={
                "queue_depth": queue_depth,
                "sku_zone": sku_zone,
                "session_seq": session_seq,
            },
        )

    def _write_event(self, event: DetectionEvent) -> None:
        """Write one event as a JSON line to the output file."""
        data = asdict(event)
        # Convert numpy bools to Python bools (numpy bools aren't JSON serializable)
        data["is_staff"] = bool(data["is_staff"])
        line = json.dumps(data, ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()  # Ensure it's written immediately

    def emit_entry(
        self, camera_id: str, visitor_id: str, timestamp: str,
        confidence: float, is_staff: bool = False, session_seq: int = 1,
    ) -> None:
        """Emit an ENTRY event — visitor crosses entry threshold inbound."""
        event = self._make_event(
            camera_id=camera_id, visitor_id=visitor_id,
            event_type="ENTRY", timestamp=timestamp,
            confidence=confidence, is_staff=is_staff,
            session_seq=session_seq,
        )
        self._write_event(event)

    def emit_exit(
        self, camera_id: str, visitor_id: str, timestamp: str,
        confidence: float, is_staff: bool = False, session_seq: int = 1,
    ) -> None:
        """Emit an EXIT event — visitor crosses entry threshold outbound."""
        event = self._make_event(
            camera_id=camera_id, visitor_id=visitor_id,
            event_type="EXIT", timestamp=timestamp,
            confidence=confidence, is_staff=is_staff,
            session_seq=session_seq,
        )
        self._write_event(event)

    def emit_zone_enter(
        self, camera_id: str, visitor_id: str, timestamp: str,
        zone_id: str, confidence: float, is_staff: bool = False,
        sku_zone: str = None, session_seq: int = 1,
    ) -> None:
        """Emit a ZONE_ENTER event — visitor enters a named zone."""
        event = self._make_event(
            camera_id=camera_id, visitor_id=visitor_id,
            event_type="ZONE_ENTER", timestamp=timestamp,
            zone_id=zone_id, confidence=confidence,
            is_staff=is_staff, sku_zone=sku_zone,
            session_seq=session_seq,
        )
        self._write_event(event)

    def emit_zone_exit(
        self, camera_id: str, visitor_id: str, timestamp: str,
        zone_id: str, confidence: float, is_staff: bool = False,
        session_seq: int = 1,
    ) -> None:
        """Emit a ZONE_EXIT event — visitor leaves a named zone."""
        event = self._make_event(
            camera_id=camera_id, visitor_id=visitor_id,
            event_type="ZONE_EXIT", timestamp=timestamp,
            zone_id=zone_id, confidence=confidence,
            is_staff=is_staff, session_seq=session_seq,
        )
        self._write_event(event)

    def emit_zone_dwell(
        self, camera_id: str, visitor_id: str, timestamp: str,
        zone_id: str, dwell_ms: int, confidence: float,
        is_staff: bool = False, sku_zone: str = None,
        session_seq: int = 1,
    ) -> None:
        """Emit a ZONE_DWELL event — visitor in zone for 30+ continuous seconds."""
        event = self._make_event(
            camera_id=camera_id, visitor_id=visitor_id,
            event_type="ZONE_DWELL", timestamp=timestamp,
            zone_id=zone_id, dwell_ms=dwell_ms,
            confidence=confidence, is_staff=is_staff,
            sku_zone=sku_zone, session_seq=session_seq,
        )
        self._write_event(event)

    def emit_billing_queue_join(
        self, camera_id: str, visitor_id: str, timestamp: str,
        queue_depth: int, confidence: float, is_staff: bool = False,
        session_seq: int = 1,
    ) -> None:
        """Emit BILLING_QUEUE_JOIN — visitor enters billing zone with queue."""
        event = self._make_event(
            camera_id=camera_id, visitor_id=visitor_id,
            event_type="BILLING_QUEUE_JOIN", timestamp=timestamp,
            zone_id="BILLING", confidence=confidence,
            is_staff=is_staff, queue_depth=queue_depth,
            session_seq=session_seq,
        )
        self._write_event(event)

    def emit_billing_queue_abandon(
        self, camera_id: str, visitor_id: str, timestamp: str,
        confidence: float, is_staff: bool = False, session_seq: int = 1,
    ) -> None:
        """Emit BILLING_QUEUE_ABANDON — visitor leaves billing without purchase."""
        event = self._make_event(
            camera_id=camera_id, visitor_id=visitor_id,
            event_type="BILLING_QUEUE_ABANDON", timestamp=timestamp,
            zone_id="BILLING", confidence=confidence,
            is_staff=is_staff, session_seq=session_seq,
        )
        self._write_event(event)

    def emit_reentry(
        self, camera_id: str, visitor_id: str, timestamp: str,
        confidence: float, is_staff: bool = False, session_seq: int = 1,
    ) -> None:
        """Emit REENTRY — same visitor detected after a prior EXIT."""
        event = self._make_event(
            camera_id=camera_id, visitor_id=visitor_id,
            event_type="REENTRY", timestamp=timestamp,
            confidence=confidence, is_staff=is_staff,
            session_seq=session_seq,
        )
        self._write_event(event)

    def close(self) -> None:
        """Close the output file."""
        self._file.close()

    def get_event_count(self) -> int:
        """Return total events emitted so far."""
        return self.event_count
