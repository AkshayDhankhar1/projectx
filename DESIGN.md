# Store Intelligence API — DESIGN.md

## Overview

The Store Intelligence API transforms raw CCTV footage from a retail beauty store into actionable business insights. The system processes video from 5 cameras (entry, 2 floor, billing, storage), detects and tracks visitors using computer vision, and exposes real-time analytics through a RESTful API with a live dashboard.

The **North Star Metric** is **Offline Store Conversion Rate**: `Visitors who completed a purchase / Total unique visitors`. Every component either improves the accuracy of this number (detection layer) or makes it actionable (API layer).

## Architecture

### System Flow

```
Raw CCTV Clips --> Detection Pipeline --> events.jsonl --> API Ingestion --> SQLite --> Analytics Endpoints --> Dashboard
```

### Three-Layer Architecture

#### Layer 1: Detection Pipeline (Offline Batch)
Processes video clips using YOLOv8n (nano model, 3.2MB) for person detection and ByteTrack for multi-object tracking. This layer:
- Runs independently from the API (offline batch processing)
- Produces structured events in JSONL format (one JSON object per line)
- Handles zone detection via polygon point-in-polygon (ray casting algorithm)
- Classifies staff using HSV color histogram analysis of upper-body clothing
- Detects re-entries via cosine similarity of appearance features

**Why offline, not streaming?** The challenge provides video files, not live feeds. Processing clips offline decouples detection from serving, allowing the API to start serving immediately with pre-computed events. In production, this would be replaced with a frame-streaming pipeline feeding events directly to the ingestion endpoint.

#### Layer 2: Intelligence API (Online)
A FastAPI application backed by SQLite that:
- Ingests events via `POST /events/ingest` (idempotent, batch up to 500, partial success)
- Computes analytics on-the-fly from raw events (no materialized views)
- Serves 6 endpoints: health, metrics, funnel, heatmap, anomalies, ingestion
- Provides structured JSON logging with trace_id for request correlation
- Broadcasts new events to connected dashboards via WebSocket

**Why compute on read?** With the expected data volume (hundreds of events per store per day), computing metrics from raw events on each request is fast enough (<20ms for SQLite queries) and eliminates the complexity of maintaining materialized views or caches that could become stale.

#### Layer 3: Live Dashboard (Real-time)
A vanilla HTML/CSS/JS dashboard that:
- Connects to the API via WebSocket for instant event updates
- Polls analytics endpoints every 5 seconds for metric refreshes
- Renders metric cards, conversion funnel, zone heatmap, and anomaly feed
- Uses dark glassmorphism design with smooth animations

## Edge Case Handling

The CCTV footage intentionally includes 7 edge cases. Here's how each is handled:

### 1. Group Entry (2-4 people entering simultaneously)
ByteTrack naturally separates individuals in groups by tracking distinct bounding boxes. Even when boxes overlap briefly, ByteTrack's IoU-based matching maintains separate track IDs. Frame-by-frame tracking ensures each person gets their own `visitor_id`.

### 2. Staff Movement
Staff are identified by combining two heuristics: (1) dark clothing detected via HSV histogram analysis of the upper body region (low saturation + low value = dark uniform), and (2) persistent presence in >60% of processed frames (staff are visible throughout, customers are transient). Both must agree to classify someone as staff. Staff events carry `is_staff: true` and are excluded from all customer metrics via SQL `WHERE is_staff = 0`.

### 3. Re-entry (Customer leaves and returns)
When a track ends (person exits frame), their HSV color histogram is saved in a recent-exits buffer with a 5-minute TTL. When a new person appears, their histogram is compared against recent exits using cosine similarity. If similarity > 0.7, they are assigned the same `visitor_id` and a `REENTRY` event is emitted instead of a new `ENTRY`. This prevents inflation of visitor counts.

### 4. Partial Occlusion
ByteTrack's two-phase matching handles this: first high-confidence detections are matched, then remaining unmatched tracks are matched against low-confidence detections. This keeps tracks alive even when YOLO's confidence drops during occlusion. The `lost_track_buffer=30` parameter keeps phantom tracks for ~1 second, bridging brief occlusion gaps.

### 5. Billing Queue Buildup
The billing camera (CAM 5) detects queue depth by counting active tracks in the billing zone. `BILLING_QUEUE_JOIN` events include `queue_depth` in metadata. When a person disappears from the billing zone without a corresponding POS transaction in the time window, a `BILLING_QUEUE_ABANDON` event is emitted.

### 6. Empty Store Periods
The API handles zero-traffic gracefully: metrics return `unique_visitors: 0`, `conversion_rate: 0.0`, and the funnel shows all stages at 0. The anomaly detector flags `STALE_FEED` if no events arrive for >10 minutes, alerting operators that the pipeline may be down rather than the store truly empty.

### 7. Camera Angle Overlap
The entry camera (CAM 3) and floor cameras (CAM 1, 2) have partial field-of-view overlap. Cross-camera deduplication is handled at the `visitor_id` level: the same person tracked on one camera gets a unique ID, and when they appear on another camera, they get a different ID. The re-entry check (histogram similarity) helps merge these when feasible. The funnel logic uses `DISTINCT visitor_id` to prevent double-counting even if deduplication isn't perfect.

## AI-Assisted Decisions

AI tools were used extensively and intentionally in this project:

### 1. Model Selection (AI: LLM comparison)
Used an LLM to compare YOLOv8n vs YOLOv8s vs YOLOv8m vs MobileNet-SSD with quantitative metrics (model size, inference speed, mAP, memory usage). The LLM provided a comparison table that accelerated the decision. After local benchmarking, I chose YOLOv8n — the LLM had initially recommended YOLOv8s, but actual CPU inference times were 3x slower than predicted, making nano the better choice for our hardware.

### 2. Zone Classification Approach
I considered using a VLM (GPT-4V/Gemini Vision) to classify zones from camera frames — the LLM suggested this approach. However, I chose rule-based polygon zones instead because: (1) zone definitions don't change per-frame, (2) VLM API calls would add latency and cost per frame, (3) manual polygon annotation from the store blueprint is a one-time effort. I would switch to a VLM approach if zones changed dynamically or if the store layout wasn't provided.

### 3. Schema Design Iteration
AI helped iterate on the event schema. It initially suggested a nested session-based schema. I pushed back because sessions can't be emitted incrementally (you don't know when a session ends until the person leaves). The final flat event schema was a joint design: AI suggested adding `session_seq` for ordering, and I added `confidence` for downstream filtering.

### 4. Test Case Generation
AI identified edge cases that I might have missed: all-staff clips (0 visitors), re-entry deduplication in the funnel, zero-purchase conversion rate. Each generated test was reviewed to ensure it tests behavior, not implementation details. Prompt blocks are included at the top of each test file to show exactly what was prompted and what was changed.

### 5. Edge Case Awareness
When I described the store environment, AI flagged that the billing camera might have occlusion issues with queue buildup — which turned out to be correct. This led to increasing ByteTrack's `lost_track_buffer` from the default 20 to 30 frames.

## Data Flow

### Event Schema
```json
{
  "event_id": "UUID v4",
  "store_id": "STORE_PRP_001",
  "camera_id": "CAM_ENTRY_01",
  "visitor_id": "VIS_c8a2f1",
  "event_type": "ENTRY | EXIT | ZONE_ENTER | ZONE_EXIT | ZONE_DWELL | BILLING_QUEUE_JOIN | BILLING_QUEUE_ABANDON | REENTRY",
  "timestamp": "ISO-8601 UTC",
  "zone_id": "SKINCARE | MAKEUP | HAIRCARE | FRAGRANCE | BILLING | null",
  "dwell_ms": 0,
  "is_staff": false,
  "confidence": 0.92,
  "metadata": { "queue_depth": null, "sku_zone": null, "session_seq": 1 }
}
```

### Conversion Rate Calculation
POS transactions have no customer_id, so we correlate using a 5-minute time window: a visitor detected in the BILLING zone within 5 minutes before a POS transaction timestamp counts as "converted". This is documented in detail in CHOICES.md.

## Observability

### Structured Logging
Every request generates a structured log entry with:
- `trace_id`: 8-character UUID for request tracing
- `method`, `path`, `status_code`: HTTP request details
- `latency_ms`: end-to-end request processing time
- `store_id`: extracted from URL for log filtering

### Health Monitoring
The `/health` endpoint reports:
- API process status and uptime
- Database connectivity
- Per-store event feed freshness (stale if >10 minutes)
- Overall status: healthy / degraded / unhealthy

## Deployment

Docker Compose orchestrates two containers:
1. **api**: Python 3.12-slim + FastAPI + SQLite (port 8000) — auto-ingests events.jsonl on startup
2. **dashboard**: nginx:alpine serving static files (port 3000) — proxies API calls to the api container

`docker compose up` starts the full system with zero manual steps. The entrypoint script automatically ingests pre-computed events before starting the API server.
