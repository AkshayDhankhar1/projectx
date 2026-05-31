# Store Intelligence API

Real-time retail analytics from CCTV footage. Detects visitors, tracks zones, computes conversion funnels, and powers a live dashboard.

## Quick Start — Docker (Recommended)

```bash
# Clone and start everything in one command
git clone https://github.com/AkshayDhankhar1/projectx.git
cd projectx
docker compose up --build -d

# API:         http://localhost:8000
# Dashboard:   http://localhost:3000
# API Docs:    http://localhost:8000/docs
```

The API automatically ingests pre-computed detection events on startup. No manual steps needed.

## Running the Detection Pipeline

The detection pipeline processes raw CCTV clips and produces structured events:

```bash
# 1. Install pipeline dependencies
pip install -r pipeline/requirements.txt

# 2. Run detection on all clips (takes ~10 min on CPU)
python pipeline/detect.py \
    --clips-dir ./Resources/ \
    --layout ./data/store_layout.json \
    --output ./data/events.jsonl \
    --frame-skip 5 \
    --confidence 0.3

# Output: data/events.jsonl (382 events from 4 cameras)
```

The pipeline uses YOLOv8n (nano) for person detection and ByteTrack for tracking. See CHOICES.md for model selection reasoning.

## Running the API Locally (Without Docker)

```bash
# 1. Install API dependencies
pip install -r requirements.txt

# 2. Start the API
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. Ingest events
python scripts/ingest_events.py

# 4. Verify
python assertions.py
```

## Live Dashboard

The dashboard is available at **http://localhost:3000** when running via Docker Compose.

Features:
- Real-time metric cards (visitors, conversion rate, queue depth, revenue, abandonment)
- Conversion funnel with drop-off percentages
- Zone heatmap with normalized activity scores
- Active anomaly feed with severity levels
- Live event stream via WebSocket

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Service health check (status, uptime, DB connectivity) |
| `POST` | `/events/ingest` | Batch event ingestion (idempotent, up to 500 events) |
| `GET` | `/stores/{id}/metrics` | Real-time store metrics (visitors, conversion, dwell, queue) |
| `GET` | `/stores/{id}/funnel` | Conversion funnel (Entry -> Zone -> Billing -> Purchase) |
| `GET` | `/stores/{id}/heatmap` | Zone activity heatmap with normalized scores |
| `GET` | `/stores/{id}/anomalies` | Operational anomaly detection |
| `WS` | `/ws` | WebSocket for live dashboard updates |

Store ID: `STORE_PRP_001`

## Testing

```bash
# Run all tests (34 tests)
python -m pytest tests/ -v

# Run with coverage report
python -m pytest tests/ -v --cov=app --cov-report=term-missing

# Run 10 API assertions (requires API running on port 8000)
python assertions.py
```

## Project Structure

```
store-intelligence/
|-- DESIGN.md               # Architecture + AI-assisted decisions
|-- CHOICES.md               # 3 key decisions with full reasoning
|-- README.md                # This file
|-- Dockerfile               # API container image
|-- docker-compose.yml       # Multi-service orchestration
|-- entrypoint.sh            # Auto-ingest on container startup
|-- requirements.txt         # API dependencies
|-- assertions.py            # 10 automated test assertions
|-- pytest.ini               # Test configuration
|
|-- app/                     # FastAPI Intelligence API
|   |-- main.py              # App entrypoint + middleware
|   |-- models.py            # Pydantic data models
|   |-- database.py          # SQLite async layer (WAL mode)
|   |-- ingestion.py         # POST /events/ingest (idempotent, batch)
|   |-- metrics.py           # GET /stores/{id}/metrics
|   |-- funnel.py            # GET /stores/{id}/funnel
|   |-- heatmap.py           # GET /stores/{id}/heatmap
|   |-- anomalies.py         # GET /stores/{id}/anomalies
|   |-- health.py            # GET /health
|   |-- pos_loader.py        # POS transaction CSV loader
|   +-- websocket_manager.py # WebSocket broadcast manager
|
|-- pipeline/                # Detection Pipeline
|   |-- detect.py            # YOLOv8n + ByteTrack main script
|   |-- tracker.py           # Session management + Re-ID logic
|   |-- emit.py              # Event schema + JSONL emission
|   |-- run.bat / run.sh     # One-command pipeline runners
|   +-- requirements.txt     # Pipeline dependencies
|
|-- dashboard/               # Live Web Dashboard
|   |-- index.html           # Dashboard UI
|   |-- style.css            # Dark glassmorphism theme
|   |-- app.js               # Dashboard logic + WebSocket
|   +-- nginx.conf           # Nginx reverse proxy config
|
|-- data/                    # Data Files
|   |-- store_layout.json    # Zone polygons + camera mapping
|   |-- pos_transactions.csv # Real POS transaction records (24 txns)
|   +-- events.jsonl         # Detection output (382 events)
|
|-- tests/                   # Test Suite (34 tests, 78% coverage)
|   |-- test_ingestion.py    # Event ingestion tests
|   |-- test_metrics.py      # Metrics computation tests
|   |-- test_funnel.py       # Conversion funnel tests
|   |-- test_heatmap.py      # Zone heatmap tests
|   |-- test_anomalies.py    # Anomaly detection tests
|   |-- test_health.py       # Health endpoint tests
|   +-- test_pipeline.py     # Event emission tests
|
+-- docs/                    # Documentation (mirror)
    |-- DESIGN.md
    +-- CHOICES.md
```

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Detection | YOLOv8n + ByteTrack | CPU-friendly, 3.2MB model, handles occlusion |
| API | FastAPI + SQLite (WAL) | Async, auto-docs, zero-config DB |
| Dashboard | Vanilla HTML/CSS/JS | No build step, WebSocket live updates |
| Container | Docker Compose | One-command deployment |
| Testing | pytest + pytest-asyncio | 34 tests, 78% coverage |
| Logging | structlog | Structured JSON with trace_id |
