"""
models.py — Pydantic Data Models for Store Intelligence API

Pydantic models define the SHAPE of data that flows through our API.
Think of them as strict contracts: if incoming data doesn't match,
FastAPI automatically returns a 422 error with details about what's wrong.

Key concept: Each class below inherits from BaseModel.
Every field has a type annotation (str, int, float, etc.).
Pydantic validates the data automatically when you create an instance.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from uuid import UUID
from enum import Enum


# ============================================================
# Event Type Catalogue
# ============================================================
# These are all the event types our detection pipeline can emit.
# Using Literal type means Pydantic will reject any value not in this list.

EVENT_TYPES = Literal[
    "ENTRY",                  # Visitor crosses entry threshold — inbound
    "EXIT",                   # Visitor crosses entry threshold — outbound
    "ZONE_ENTER",             # Visitor enters a named zone
    "ZONE_EXIT",              # Visitor leaves a named zone
    "ZONE_DWELL",             # Visitor in zone continuously for 30+ seconds
    "BILLING_QUEUE_JOIN",     # Visitor enters billing zone with queue
    "BILLING_QUEUE_ABANDON",  # Visitor leaves billing without purchase
    "REENTRY",                # Same visitor re-enters after prior EXIT
]


# ============================================================
# Event Metadata — nested object inside each event
# ============================================================
class EventMetadata(BaseModel):
    """Extra contextual data attached to each event.
    
    - queue_depth: how many people are in the billing queue (if applicable)
    - sku_zone: the product zone label from store_layout.json
    - session_seq: ordinal position of this event within the visitor's session
      (1st event = 1, 2nd = 2, etc.)
    """
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 1


# ============================================================
# Core Event Model — the main data structure
# ============================================================
class Event(BaseModel):
    """One detection event from the pipeline.
    
    This is the fundamental unit of data in our system.
    Every detection, zone change, or entry/exit produces one Event.
    
    Fields:
        event_id:    Globally unique UUID v4 identifier
        store_id:    Which store (e.g., "STORE_PRP_001")
        camera_id:   Which camera produced this event
        visitor_id:  Unique token per visitor session (e.g., "VIS_a1b2c3")
        event_type:  What happened (ENTRY, EXIT, ZONE_DWELL, etc.)
        timestamp:   When it happened (ISO-8601 UTC)
        zone_id:     Which zone (null for ENTRY/EXIT events)
        dwell_ms:    Duration in milliseconds (0 for instantaneous events)
        is_staff:    True if this person is store staff (excluded from metrics)
        confidence:  Detection model's confidence score (0.0 to 1.0)
        metadata:    Additional context (queue depth, session sequence, etc.)
    """
    event_id: UUID
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EVENT_TYPES
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = EventMetadata()

    # model_config replaces the old class Config pattern in Pydantic v2
    # "extra = ignore" means if extra fields are present, silently ignore them
    model_config = {"extra": "ignore"}


# ============================================================
# Request/Response Models
# ============================================================

class EventBatch(BaseModel):
    """Batch of events for the POST /events/ingest endpoint.
    
    Accepts up to 500 events at once for efficient bulk ingestion.
    """
    events: list[Event] = Field(max_length=500)


class IngestError(BaseModel):
    """Details about a single event that failed validation or ingestion."""
    event_id: Optional[str] = None
    reason: str


class IngestResponse(BaseModel):
    """Response from POST /events/ingest.
    
    Supports partial success: some events may be accepted while others
    are rejected due to validation errors.
    """
    accepted: int
    rejected: int
    errors: list[IngestError] = []


# ============================================================
# Metrics Response Models
# ============================================================

class ZoneDwell(BaseModel):
    """Average dwell time for a single zone."""
    zone_id: str
    avg_dwell_ms: float
    visit_count: int


class StoreMetrics(BaseModel):
    """Real-time metrics for a store.
    
    These are computed fresh from the events database on every request.
    No caching — always reflects the latest data.
    """
    store_id: str
    timestamp: datetime             # When these metrics were computed
    unique_visitors: int            # Count of distinct non-staff visitors today
    conversion_rate: float          # Visitors who purchased / total visitors
    avg_dwell_by_zone: list[ZoneDwell]  # Dwell time breakdown per zone
    current_queue_depth: int        # Latest billing queue depth
    abandonment_rate: float         # Queue abandoners / queue joiners
    total_transactions: int         # POS transaction count for today
    total_revenue: float            # Sum of basket_value_inr for today


# ============================================================
# Funnel Response Models
# ============================================================

class FunnelStage(BaseModel):
    """One stage of the conversion funnel."""
    stage: str              # e.g., "Entry", "Zone Visit", "Billing Queue", "Purchase"
    count: int              # Number of unique visitors at this stage
    drop_off_percent: float # % of visitors lost between this and previous stage


class StoreFunnel(BaseModel):
    """Conversion funnel for a store.
    
    Unit is the VISITOR SESSION, not raw events.
    A visitor who re-enters is counted once (by distinct visitor_id).
    """
    store_id: str
    timestamp: datetime
    stages: list[FunnelStage]
    total_sessions: int


# ============================================================
# Heatmap Response Models
# ============================================================

class HeatmapZone(BaseModel):
    """Activity data for one zone in the heatmap."""
    zone_id: str
    visit_count: int
    avg_dwell_ms: float
    normalized_score: float   # 0-100 scale (100 = busiest zone)
    data_confidence: str      # "high" if >= 20 sessions, else "low"


class StoreHeatmap(BaseModel):
    """Zone activity heatmap for a store."""
    store_id: str
    timestamp: datetime
    zones: list[HeatmapZone]


# ============================================================
# Anomaly Response Models
# ============================================================

class Anomaly(BaseModel):
    """One detected operational anomaly."""
    anomaly_type: str          # e.g., "BILLING_QUEUE_SPIKE"
    severity: Literal["INFO", "WARN", "CRITICAL"]
    description: str           # Human-readable explanation
    suggested_action: str      # What to do about it
    detected_at: datetime
    metric_value: Optional[float] = None   # The anomalous value
    threshold_value: Optional[float] = None  # What's considered normal


class StoreAnomalies(BaseModel):
    """Active anomalies for a store."""
    store_id: str
    timestamp: datetime
    anomalies: list[Anomaly]


# ============================================================
# Health Response Models
# ============================================================

class StoreHealth(BaseModel):
    """Health info for one store's data feed."""
    store_id: str
    last_event_at: Optional[datetime] = None
    event_count: int = 0
    is_stale: bool = False    # True if last event > 10 minutes ago


class HealthResponse(BaseModel):
    """Service health status.
    
    This is the first endpoint an on-call engineer checks.
    It must be accurate and informative.
    """
    status: Literal["healthy", "degraded", "unhealthy"]
    uptime_seconds: float
    database_connected: bool
    stores: list[StoreHealth]
    version: str = "1.0.0"
