"""
heatmap.py — Zone Activity Heatmap Endpoint

GET /stores/{store_id}/heatmap

Returns zone-level activity data for rendering a store heatmap.
Each zone gets:
- visit_count: how many times visitors entered this zone
- avg_dwell_ms: average time spent in the zone (milliseconds)
- normalized_score: 0-100 scale where 100 = busiest zone
- data_confidence: "high" if enough data, "low" if sparse

The normalized_score uses min-max normalization:
  score = (zone_visits / max_zone_visits) * 100

This makes it easy for the dashboard to map scores to colors:
  0-25: cool (blue) → low traffic
  25-50: warm (green) → moderate
  50-75: hot (orange) → busy
  75-100: very hot (red) → peak traffic
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from app.models import StoreHeatmap, HeatmapZone
from app.database import get_db
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["analytics"])

# Minimum sessions needed for "high" confidence rating
MIN_SESSIONS_FOR_CONFIDENCE = 20


@router.get("/stores/{store_id}/heatmap", response_model=StoreHeatmap)
async def get_store_heatmap(store_id: str) -> StoreHeatmap:
    """Get zone activity heatmap data for a specific store."""
    try:
        db = await get_db()
    except RuntimeError:
        raise HTTPException(status_code=503, detail={"error": "Database unavailable"})

    # ----------------------------------------------------------
    # Get total sessions for data confidence calculation
    # ----------------------------------------------------------
    session_row = await db.execute_fetchall(
        """
        SELECT COUNT(DISTINCT visitor_id) 
        FROM events
        WHERE store_id = ? AND event_type = 'ENTRY' AND is_staff = 0
        """,
        (store_id,),
    )
    total_sessions = session_row[0][0] if session_row else 0

    # ----------------------------------------------------------
    # Get visit count and average dwell per zone
    # ----------------------------------------------------------
    # ZONE_ENTER gives us visit frequency
    visit_rows = await db.execute_fetchall(
        """
        SELECT zone_id, COUNT(*) as visit_count
        FROM events
        WHERE store_id = ? AND event_type = 'ZONE_ENTER' AND is_staff = 0
          AND zone_id IS NOT NULL
        GROUP BY zone_id
        """,
        (store_id,),
    )

    # ZONE_DWELL gives us dwell time
    dwell_rows = await db.execute_fetchall(
        """
        SELECT zone_id, AVG(dwell_ms) as avg_dwell
        FROM events
        WHERE store_id = ? AND event_type = 'ZONE_DWELL' AND is_staff = 0
          AND zone_id IS NOT NULL
        GROUP BY zone_id
        """,
        (store_id,),
    )

    # Build lookup dictionaries for fast access
    visit_counts: dict[str, int] = {}
    for row in visit_rows:
        visit_counts[row[0]] = row[1]

    dwell_times: dict[str, float] = {}
    for row in dwell_rows:
        dwell_times[row[0]] = row[1]

    # ----------------------------------------------------------
    # Normalize scores to 0-100 scale
    # ----------------------------------------------------------
    # Find the maximum visit count — this zone gets score 100
    max_visits = max(visit_counts.values()) if visit_counts else 1

    # Determine data confidence based on total session count
    confidence = "high" if total_sessions >= MIN_SESSIONS_FOR_CONFIDENCE else "low"

    # ----------------------------------------------------------
    # Build heatmap zones
    # ----------------------------------------------------------
    # Combine all zone IDs from both visits and dwells
    all_zones = set(visit_counts.keys()) | set(dwell_times.keys())

    zones = []
    for zone_id in sorted(all_zones):
        visits = visit_counts.get(zone_id, 0)
        avg_dwell = dwell_times.get(zone_id, 0.0)

        # Normalized score: proportion of max visits, scaled to 0-100
        normalized = round((visits / max_visits) * 100, 1) if max_visits > 0 else 0.0

        zones.append(HeatmapZone(
            zone_id=zone_id,
            visit_count=visits,
            avg_dwell_ms=round(avg_dwell, 1),
            normalized_score=normalized,
            data_confidence=confidence,
        ))

    return StoreHeatmap(
        store_id=store_id,
        timestamp=datetime.now(timezone.utc),
        zones=zones,
    )
