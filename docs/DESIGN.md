# Store Intelligence API — DESIGN.md

## Overview

The Store Intelligence API transforms raw CCTV footage from a Purplle beauty retail store into actionable business insights. The system processes video from 5 cameras (entry, 2 floor, billing, storage), detects and tracks visitors using computer vision, and exposes real-time analytics through a RESTful API with a live dashboard.

## Architecture

### System Flow

```
Raw CCTV Clips → Detection Pipeline → events.jsonl → API Ingestion → SQLite → Analytics Endpoints → Dashboard
```

### Three-Layer Architecture

#### Layer 1: Detection Pipeline (Offline)
Processes video clips on-demand using YOLOv8n (nano model, 3.2MB) for person detection and ByteTrack for multi-object tracking. This layer:
- Runs independently from the API (offline batch processing)
- Produces structured events in JSONL format (one JSON object per line)
- Handles zone detection via polygon point-in-polygon checks
- Classifies staff using HSV color histogram analysis of clothing
- Detects re-entries via cosine similarity of appearance features

**Why offline, not streaming?** The challenge provides video files, not live feeds. Processing clips offline decouples detection from serving, allowing the API to start serving immediately with pre-computed events. In production, this would be replaced with a frame-streaming pipeline.

#### Layer 2: Intelligence API (Online)
A FastAPI application backed by SQLite that:
- Ingests events via `POST /events/ingest` (idempotent, batch, partial success)
- Computes analytics on-the-fly from raw events (no materialized views)
- Serves 6 endpoints: health, metrics, funnel, heatmap, anomalies, ingestion
- Provides structured JSON logging with trace_id for request tracing
- Broadcasts new events to connected dashboards via WebSocket

**Why compute on read?** With the expected data volume (hundreds of events per store per day), computing metrics from raw events on each request is fast enough (<10ms for SQLite queries) and eliminates the complexity of maintaining materialized views or caches that could become stale.

#### Layer 3: Live Dashboard (Real-time)
A vanilla HTML/CSS/JS dashboard that:
- Connects to the API via WebSocket for instant event updates
- Polls analytics endpoints every 5 seconds for metric refreshes
- Renders metric cards, conversion funnel, zone heatmap, and anomaly feed
- Uses dark glassmorphism design with smooth animations

### Key Technical Decisions

#### SQLite over PostgreSQL
SQLite stores the entire database in a single file, requires zero configuration, and has no separate server process. For a single-store deployment, SQLite provides sufficient write throughput (~50K inserts/second) and excellent read performance. This eliminates the need for a separate database container in Docker Compose, simplifying deployment.

#### YOLOv8n over Larger Models
The nano variant (3.2MB) achieves adequate person detection accuracy while running at ~100-200ms per frame on CPU. Combined with frame skipping (processing every 5th frame), this makes the pipeline viable on integrated graphics (Intel Iris Xe). The tradeoff is slightly lower detection recall in crowded scenes, which is acceptable for our low-density retail environment.

#### ByteTrack over DeepSORT
ByteTrack is simpler and faster than DeepSORT while achieving better tracking accuracy on benchmarks. Its key innovation: using low-confidence detections for track matching, which reduces identity switches when people are partially occluded. DeepSORT requires a separate Re-ID model (adding GPU cost), while ByteTrack uses only IoU-based matching.

#### Normalized Zone Coordinates
Zone polygons are stored as normalized coordinates (0.0 to 1.0) rather than pixel coordinates. This makes zone definitions resolution-independent — the same zones work whether the video is 1080p or 4K, without recalibration.

## AI-Assisted Development

AI tools were used extensively in this project:

1. **Architecture Design**: Used AI to explore tradeoffs between different detection models (YOLOv8n vs YOLOv5s vs MobileNet-SSD), tracking algorithms (ByteTrack vs DeepSORT vs SORT), and database choices (SQLite vs PostgreSQL vs TimescaleDB). The AI provided quantitative comparisons (model size, inference speed, accuracy metrics) that informed the final selection.

2. **Code Generation**: FastAPI endpoint boilerplate, Pydantic model definitions, and SQLite schema were generated with AI assistance and then reviewed for correctness. The structured logging middleware pattern was adapted from an AI-suggested template.

3. **Test Case Design**: AI helped identify edge cases (all-staff clips, re-entry deduplication, empty store metrics) that might have been missed in manual test planning. Each test was reviewed to ensure it tests meaningful behavior, not implementation details.

4. **Documentation**: This DESIGN.md and CHOICES.md were drafted with AI assistance for structure and clarity, then edited for accuracy and personal voice.

5. **Prompt Engineering**: Detection pipeline prompts were iteratively refined to handle the specific camera angles and store layout of the Purplle store.

All AI-generated code was reviewed line-by-line and modified where necessary. The architectural decisions reflect genuine engineering judgment about the tradeoffs involved.

## Data Flow

### Event Schema
Every detection event follows a standardized schema:
```json
{
  "event_id": "UUID v4 — globally unique",
  "store_id": "STORE_PRP_001",
  "camera_id": "CAM_ENTRY_01",
  "visitor_id": "VIS_c8a2f1 — per-session token",
  "event_type": "ENTRY | EXIT | ZONE_ENTER | ZONE_EXIT | ZONE_DWELL | BILLING_QUEUE_JOIN | BILLING_QUEUE_ABANDON | REENTRY",
  "timestamp": "ISO-8601 UTC",
  "zone_id": "SKINCARE | MAKEUP | BILLING | null",
  "dwell_ms": 0,
  "is_staff": false,
  "confidence": 0.92,
  "metadata": { "queue_depth": null, "sku_zone": null, "session_seq": 1 }
}
```

### Conversion Rate Calculation
POS transactions have no customer_id, so we correlate visitors with purchases using a time-window approach: a visitor in the BILLING zone within 5 minutes before a POS transaction timestamp counts as "converted".

### Staff Detection
Staff are identified by combining two heuristics: (1) dark clothing detected via HSV histogram analysis of the upper body region, and (2) persistent presence in >60% of processed frames. Both must agree to classify someone as staff, reducing false positives.

## Observability

### Structured Logging
Every request generates a structured log entry with:
- `trace_id`: 8-character UUID for request tracing
- `method`, `path`, `status_code`: HTTP request details
- `latency_ms`: end-to-end request processing time
- `store_id`: extracted from URL for filtering

### Health Monitoring
The `/health` endpoint reports:
- API process status and uptime
- Database connectivity
- Per-store event feed freshness (stale if >10 minutes)
- Overall status: healthy → degraded → unhealthy

## Deployment

Docker Compose orchestrates two containers:
1. **api**: Python 3.12-slim + FastAPI + SQLite (port 8000)
2. **dashboard**: nginx:alpine serving static files (port 3000)

SQLite's single-file nature eliminates the need for a separate database container, reducing operational complexity and resource usage.
