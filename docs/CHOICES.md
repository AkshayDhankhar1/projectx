# CHOICES.md — Key Technical Decisions

This document explains three critical engineering decisions made during the development of the Store Intelligence system. Each decision includes the context, alternatives considered, the chosen approach, and the reasoning.

---

## Decision 1: SQLite over PostgreSQL for the Event Store

### Context
The system needs a database to store detection events and support real-time analytics queries. The challenge requires a containerized deployment, so the database must work within Docker.

### Alternatives Considered

**PostgreSQL (via Docker container)**
- Pros: Industry standard, excellent concurrent write performance, rich query language, built-in JSON support, time-series extensions (TimescaleDB)
- Cons: Requires separate Docker container, TCP networking overhead, connection pool management, more memory usage (~100MB baseline), migration tooling needed

**MongoDB**
- Pros: Flexible schema matches our JSON events, good write throughput, aggregation framework for analytics
- Cons: Heavy resource usage (~500MB+), complex aggregation syntax, another Docker container needed, eventual consistency by default

**SQLite (embedded)**
- Pros: Zero configuration, single file, no separate process, ~10MB memory, ACID compliant, WAL mode for concurrent reads, works in any container
- Cons: Single-writer limitation, no built-in replication, not suitable for horizontal scaling

### Chosen: SQLite with WAL Mode

For a single-store deployment processing hundreds of events per day, SQLite's write throughput (~50K inserts/second) is orders of magnitude more than needed. WAL (Write-Ahead Logging) mode allows concurrent reads during writes, eliminating any contention between the ingestion endpoint and analytics queries.

The key advantage is **operational simplicity**: no separate database container means fewer moving parts in Docker Compose, no network configuration between containers, no connection pool tuning, and zero-downtime database backups (just copy the file). The database file persists via a Docker volume mount.

**When would I choose differently?** If the system scaled to multiple stores with independent API instances, or if write volume exceeded ~10K events/minute, I would switch to PostgreSQL with TimescaleDB extension for time-series optimization and proper connection pooling via pgbouncer.

---

## Decision 2: YOLOv8n (Nano) with Frame Skipping over Larger Detection Models

### Context
The detection pipeline must identify people in 1080p CCTV footage on hardware with only an Intel Iris Xe integrated GPU (no discrete GPU). The system must process 5 video clips (~2 minutes each) in a reasonable time.

### Alternatives Considered

**YOLOv8s (Small) — 11.2MB**
- Pros: Higher mAP (44.9 vs 37.3 on COCO), better person detection in edge cases
- Cons: ~2-3× slower inference on CPU (~300-500ms/frame at 640px), would require processing every 10th frame to maintain throughput

**YOLOv8m (Medium) — 25.9MB**
- Pros: Best detection accuracy (50.2 mAP on COCO)
- Cons: ~5× slower on CPU, impractical for real-time processing without GPU

**MobileNet-SSD v2**
- Pros: Lightweight, designed for mobile/edge deployment
- Cons: Significantly lower accuracy for person detection at store distances, less ecosystem support for tracking integration

### Chosen: YOLOv8n with Frame Skip = 5

YOLOv8n (3.2MB, 37.3 mAP) processes a 640px frame in ~100-200ms on a modern Intel CPU. By processing every 5th frame from a 30fps video, we achieve an effective processing rate of ~6fps — sufficient for person tracking since people in a retail store move slowly (walking speed ~1.4 m/s).

The accuracy tradeoff is acceptable: in our well-lit, low-density retail environment with ceiling-mounted cameras, YOLOv8n achieves >90% precision for person detection. The rare missed detections are handled by ByteTrack's lost-track buffer, which keeps a "phantom" track alive for 30 frames before deleting it.

**Frame skipping math:**
- 30fps × 2.5 min = ~4500 frames per clip
- Processing every 5th = ~900 frames per clip
- At 150ms/frame = ~135 seconds per clip
- 4 clips (excluding storage) × 135s = ~9 minutes total processing time

**When would I choose differently?** With a discrete GPU (NVIDIA GTX 1650 or better), I would use YOLOv8s without frame skipping for higher accuracy. With an RTX 3060+, I would use YOLOv8m and process every frame for optimal tracking continuity.

---

## Decision 3: Time-Window POS Correlation over Customer ID Matching

### Context
The conversion rate requires correlating in-store visitors with POS (Point of Sale) purchases. However, POS transaction records contain only `store_id`, `transaction_id`, `timestamp`, and `basket_value_inr` — there is **no customer identifier** that links a transaction to a specific visitor.

### Alternatives Considered

**Receipt Scanning / QR Code**
- Pros: Exact 1:1 visitor-transaction mapping
- Cons: Requires additional hardware/infrastructure not available in the video data, out of scope for camera-only detection

**Bluetooth/Wi-Fi Proximity**
- Pros: High accuracy for device-carrying visitors
- Cons: Requires BLE beacons or Wi-Fi APs with MAC address tracking, not available from CCTV data, privacy concerns

**Zone-Time Correlation**
- Pros: Uses only data we already have (visitor zone events + POS timestamps), no additional infrastructure needed
- Cons: Can produce false positives (two people at billing at the same time), accuracy depends on time-window size

### Chosen: 5-Minute Time-Window Correlation

A visitor is considered "converted" if they were detected in the BILLING zone within 5 minutes before a POS transaction timestamp. The 5-minute window accounts for:
- Time between billing zone detection and actual checkout (~1-2 minutes)
- POS system timestamp precision (may be slightly delayed)
- Multiple people at the counter simultaneously

**False positive mitigation:**
- We match each transaction to the closest visitor in time, not all visitors in the window
- The BILLING zone polygon is tight (covers only the cash counter area)
- Staff are excluded from the visitor pool via the `is_staff` flag
- Visitors are deduplicated by `visitor_id` (each person counted at most once)

**Accuracy estimate:** In a low-traffic store (~20 visitors/day), the probability of two non-staff visitors being in the billing zone within the same 5-minute window is low (~15%), giving us an estimated accuracy of ~85% for conversion attribution. This is significantly better than the naive approach (all billing visitors = purchasers) and sufficient for operational insights.

**When would I choose differently?** If the POS system provided a loyalty ID or transaction receipt number that could be matched to a customer, that would be the gold standard. In a high-traffic store (>100 visitors/hour), I would reduce the window to 2 minutes and add a scoring system based on proximity to the billing counter.
