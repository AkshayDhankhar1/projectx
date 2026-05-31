# CHOICES.md — Key Technical Decisions

This document explains three critical engineering decisions made during the development of the Store Intelligence system. Each decision includes the context, alternatives considered, the chosen approach, and the reasoning.

---

## Decision 1: YOLOv8n (Nano) with Frame Skipping over Larger Detection Models

### Context
The detection pipeline must identify people in 1080p CCTV footage on hardware with only an Intel Iris Xe integrated GPU (no discrete GPU). The system must process 5 video clips (~2.5 minutes each) in a reasonable time.

### Alternatives Considered

**YOLOv8s (Small) — 11.2MB**
- Pros: Higher mAP (44.9 vs 37.3 on COCO), better person detection in edge cases like partial occlusion
- Cons: ~2-3x slower inference on CPU (~300-500ms/frame at 640px), would require processing every 10th frame

**YOLOv8m (Medium) — 25.9MB**
- Pros: Best detection accuracy (50.2 mAP on COCO), handles crowded scenes well
- Cons: ~5x slower on CPU, impractical without GPU

**MobileNet-SSD v2**
- Pros: Lightweight, designed for mobile/edge deployment
- Cons: Significantly lower accuracy for person detection at store distances, less tracking integration

### Chosen: YOLOv8n with Frame Skip = 5

YOLOv8n (3.2MB, 37.3 mAP) processes a 640px frame in ~100-200ms on a modern Intel CPU. By processing every 5th frame from a 30fps video, we achieve an effective rate of ~6fps — sufficient for tracking since people in a retail store walk slowly (~1.4 m/s).

The accuracy tradeoff is acceptable: in our well-lit, low-density retail environment with ceiling-mounted cameras, YOLOv8n achieves >90% precision for person detection. The rare missed detections are handled by ByteTrack's lost-track buffer, which keeps a "phantom" track alive for 30 frames before deleting it.

**Frame skipping math:**
- 30fps x 2.5 min = ~4500 frames per clip
- Processing every 5th = ~900 frames per clip
- At 150ms/frame = ~135 seconds per clip
- 4 clips (excluding storage) x 135s = ~9 minutes total processing time

**Where AI suggested something I disagreed with:** AI initially recommended YOLOv8s for "better accuracy", but after benchmarking locally, the 3x slower inference made the full pipeline take 25+ minutes instead of 10 minutes. I chose nano because the accuracy difference was marginal in our well-lit, low-density store environment, and processing time matters for iteration speed during development.

**When would I choose differently?** With a discrete GPU (NVIDIA GTX 1650+), I would use YOLOv8s without frame skipping for higher recall. With an RTX 3060+, I would use YOLOv8m and process every frame.

---

## Decision 2: Event Schema Design — Flat Denormalized Events over Nested Session Objects

### Context
The detection pipeline must emit structured events that feed into the Intelligence API. The schema must support 6 analytics queries: visitor count, conversion rate, funnel, heatmap, anomalies, and queue depth. The schema design determines what the API can compute and how accurately.

### Alternatives Considered

**Nested Session Objects**
```json
{
  "session_id": "...",
  "visitor_id": "VIS_abc123",
  "entries": [...],
  "zone_visits": [...],
  "billing_events": [...]
}
```
- Pros: Complete session context in one record, natural for funnel analysis
- Cons: Requires session to be "closed" before emitting (can't stream events incrementally), complex to update as new events occur, session boundaries are ambiguous (when does a session end?)

**Relational Tables (separate entries, zone_visits, billing_events)**
- Pros: Normalized, no redundancy, SQL-friendly JOINs
- Cons: Requires multiple tables, complex JOINs for analytics, harder to ingest incrementally

**Flat Denormalized Events (chosen)**
```json
{
  "event_id": "UUID",
  "store_id": "STORE_PRP_001",
  "camera_id": "CAM_ENTRY_01",
  "visitor_id": "VIS_abc123",
  "event_type": "ZONE_ENTER",
  "timestamp": "ISO-8601",
  "zone_id": "SKINCARE",
  "dwell_ms": 0,
  "is_staff": false,
  "confidence": 0.92,
  "metadata": {"queue_depth": null, "sku_zone": "SKINCARE", "session_seq": 1}
}
```
- Pros: Each event is self-contained, streamable, easy to ingest incrementally, single-table queries for all analytics
- Cons: Redundant fields (store_id repeated), session reconstruction requires GROUP BY visitor_id

### Chosen: Flat Denormalized Events

I chose flat events because they map directly to how the detection pipeline produces data: one detection → one event. This enables:
1. **Incremental ingestion** — events can be POSTed as they're produced, not batched by session
2. **Idempotency** — each event has a UUID, enabling safe replay and deduplication via `INSERT OR IGNORE`
3. **Simple analytics** — every metric (visitor count, funnel, heatmap) is a single SQL query with GROUP BY, no JOINs needed
4. **Streaming** — events flow through WebSocket to the dashboard in real-time

The `event_type` field (ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY) encodes the full state machine of a visitor's journey. The `metadata` field provides extensibility for queue_depth and sku_zone without schema changes.

**Session deduplication**: The `visitor_id` is a per-session token assigned by the tracker. Re-entry detection uses cosine similarity on HSV color histograms to assign the same `visitor_id` to a returning visitor, preventing double-counting in the funnel.

**Where AI helped**: AI suggested including a `session_seq` field in metadata to enable ordering events within a session. This was a good suggestion — it allows reconstructing the visitor's journey in sequence without relying solely on timestamps, which can be ambiguous when two events happen in the same frame.

---

## Decision 3: SQLite with WAL Mode over PostgreSQL for the Event Store

### Context
The system needs a database to store detection events and support real-time analytics queries. The challenge requires a containerized deployment via `docker compose up`.

### Alternatives Considered

**PostgreSQL (via Docker container)**
- Pros: Industry standard, excellent concurrent write performance, rich query language, time-series extensions (TimescaleDB)
- Cons: Requires separate Docker container (+100MB memory), TCP networking overhead between containers, connection pool management, migration tooling needed, more complex docker-compose.yml

**MongoDB**
- Pros: Flexible schema matches our JSON events, good write throughput, aggregation framework
- Cons: Heavy resource usage (~500MB+), complex aggregation syntax for funnel/heatmap queries, another Docker container needed

**SQLite (embedded)**
- Pros: Zero configuration, single file, no separate process, ~10MB memory, ACID compliant, WAL mode for concurrent reads
- Cons: Single-writer limitation, no built-in replication, not suitable for horizontal scaling

### Chosen: SQLite with WAL Mode

For a single-store deployment processing hundreds of events per day, SQLite's write throughput (~50K inserts/second) is orders of magnitude more than needed. WAL (Write-Ahead Logging) mode allows concurrent reads during writes, eliminating contention between the ingestion endpoint and analytics queries.

The key advantage is **operational simplicity**: no separate database container means fewer moving parts in Docker Compose, no network configuration between containers, no connection pool tuning, and zero-downtime backups (just copy the file). The database file persists via a Docker volume mount (`./data:/app/data`).

**Performance validation**: During testing, ingesting 382 events took <1 second, and all analytics queries (metrics, funnel, heatmap, anomalies) completed in <20ms each. This is well within acceptable latency for a real-time dashboard polling every 5 seconds.

**When would I choose differently?** If the system scaled to multiple stores with independent API instances, or if write volume exceeded ~10K events/minute, I would switch to PostgreSQL with TimescaleDB for time-series optimization and proper connection pooling via pgbouncer.
