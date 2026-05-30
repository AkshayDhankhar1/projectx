# Store Intelligence API 🏪📊

Real-time retail analytics from CCTV footage. Detects visitors, tracks zones, computes conversion funnels, and powers a live dashboard.

## Quick Start (5 Commands)

```bash
# 1. Install API dependencies
pip install -r requirements.txt

# 2. Run detection pipeline (processes CCTV clips → events.jsonl)
pip install -r pipeline/requirements.txt
python pipeline/detect.py --clips-dir ./Resources/ --layout ./data/store_layout.json --output ./data/events.jsonl

# 3. Start the API
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. Ingest events into the API
python scripts/ingest_events.py

# 5. Open dashboard
# Visit http://localhost:8000/docs for API docs
# Visit the dashboard at dashboard/index.html (or via Docker at localhost:3000)
```

## Docker Deployment

```bash
# Start everything with one command
docker compose up --build -d

# API:       http://localhost:8000
# Dashboard: http://localhost:3000
# API Docs:  http://localhost:8000/docs

# Stop everything
docker compose down
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Service health check |
| `POST` | `/events/ingest` | Batch event ingestion (idempotent) |
| `GET` | `/stores/{id}/metrics` | Real-time store metrics |
| `GET` | `/stores/{id}/funnel` | Conversion funnel |
| `GET` | `/stores/{id}/heatmap` | Zone activity heatmap |
| `GET` | `/stores/{id}/anomalies` | Operational anomaly detection |
| `WS` | `/ws` | WebSocket for live dashboard |

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=app --cov-report=term-missing

# Run assertions
python assertions.py
```

## Project Structure

```
├── app/                    # FastAPI Intelligence API
│   ├── main.py             # App entrypoint + middleware
│   ├── models.py           # Pydantic data models
│   ├── database.py         # SQLite async layer
│   ├── ingestion.py        # POST /events/ingest
│   ├── metrics.py          # GET /stores/{id}/metrics
│   ├── funnel.py           # GET /stores/{id}/funnel
│   ├── heatmap.py          # GET /stores/{id}/heatmap
│   ├── anomalies.py        # GET /stores/{id}/anomalies
│   ├── health.py           # GET /health
│   ├── pos_loader.py       # POS transaction CSV loader
│   └── websocket_manager.py # WebSocket manager
├── pipeline/               # Detection Pipeline
│   ├── detect.py           # YOLOv8n + ByteTrack main script
│   ├── tracker.py          # Session management + Re-ID
│   ├── emit.py             # Event schema + JSONL emission
│   ├── run.bat / run.sh    # One-command pipeline runner
│   └── requirements.txt    # Pipeline dependencies
├── dashboard/              # Live Web Dashboard
│   ├── index.html          # Dashboard HTML
│   ├── style.css           # Glassmorphism dark theme
│   ├── app.js              # Dashboard logic + WebSocket
│   └── nginx.conf          # Nginx proxy config
├── data/                   # Data Files
│   ├── store_layout.json   # Store zones + camera mapping
│   ├── pos_transactions.csv # POS transaction records
│   └── events.jsonl        # Detection output (generated)
├── tests/                  # Test Suite (29 tests)
│   ├── test_ingestion.py   # Ingest tests
│   ├── test_metrics.py     # Metrics tests
│   ├── test_funnel.py      # Funnel tests
│   ├── test_anomalies.py   # Anomaly tests
│   ├── test_health.py      # Health tests
│   └── test_pipeline.py    # Pipeline tests
├── docs/                   # Documentation
│   ├── DESIGN.md           # Architecture overview
│   └── CHOICES.md          # Technical decisions
├── scripts/
│   └── ingest_events.py    # Event ingestion helper
├── Dockerfile              # API container image
├── docker-compose.yml      # Multi-service orchestration
├── requirements.txt        # API dependencies
├── assertions.py           # 10 test assertions
└── README.md               # This file
```

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Detection | YOLOv8n + ByteTrack | CPU-friendly, 3.2MB model |
| API | FastAPI + SQLite | Async, auto-docs, zero-config DB |
| Dashboard | Vanilla HTML/CSS/JS | No build tools, WebSocket live updates |
| Container | Docker Compose | One-command deployment |
| Testing | pytest + pytest-asyncio | 29 tests, async support |
| Logging | structlog | Structured JSON logging |
