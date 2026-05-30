"""
anomalies.py — Anomaly Detection Endpoint

GET /stores/{store_id}/anomalies

Detects operational anomalies in real-time by comparing current metrics
against expected baselines. This is the "alerting" layer of the system.

Anomaly types detected:
1. BILLING_QUEUE_SPIKE — queue deeper than 2× historical average
2. CONVERSION_DROP — today's conversion rate < 70% of average
3. DEAD_ZONE — a zone with zero visits in the last 30 minutes
4. STALE_FEED — no events received for > 10 minutes

Each anomaly has:
- severity: INFO (notice), WARN (action needed), CRITICAL (immediate)
- suggested_action: human-readable recommendation for store operations
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from app.models import StoreAnomalies, Anomaly
from app.database import get_db
import json
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["analytics"])

# Thresholds for anomaly detection
QUEUE_SPIKE_MULTIPLIER = 2.0     # Queue > 2× average = spike
CONVERSION_DROP_THRESHOLD = 0.7  # Today < 70% of average = drop
DEAD_ZONE_MINUTES = 30           # No visits for 30 min = dead zone
STALE_FEED_MINUTES = 10          # No events for 10 min = stale feed


@router.get("/stores/{store_id}/anomalies", response_model=StoreAnomalies)
async def get_store_anomalies(store_id: str) -> StoreAnomalies:
    """Detect active anomalies for a specific store.
    
    Runs all anomaly detection checks and returns any that are triggered.
    If no anomalies are detected, returns an empty list.
    """
    try:
        db = await get_db()
    except RuntimeError:
        raise HTTPException(status_code=503, detail={"error": "Database unavailable"})

    now = datetime.now(timezone.utc)
    anomalies: list[Anomaly] = []

    # ----------------------------------------------------------
    # Check 1: BILLING_QUEUE_SPIKE
    # ----------------------------------------------------------
    # Compare current queue depth against average queue depth
    await _check_queue_spike(db, store_id, now, anomalies)

    # ----------------------------------------------------------
    # Check 2: CONVERSION_DROP
    # ----------------------------------------------------------
    await _check_conversion_drop(db, store_id, now, anomalies)

    # ----------------------------------------------------------
    # Check 3: DEAD_ZONE
    # ----------------------------------------------------------
    await _check_dead_zones(db, store_id, now, anomalies)

    # ----------------------------------------------------------
    # Check 4: STALE_FEED
    # ----------------------------------------------------------
    await _check_stale_feed(db, store_id, now, anomalies)

    return StoreAnomalies(
        store_id=store_id,
        timestamp=now,
        anomalies=anomalies,
    )


async def _check_queue_spike(
    db, store_id: str, now: datetime, anomalies: list[Anomaly]
) -> None:
    """Check if the billing queue is abnormally deep.
    
    Logic:
    1. Get the most recent queue_depth value
    2. Get the average queue_depth across all BILLING_QUEUE_JOIN events
    3. If current > 2× average → spike detected
    """
    # Get the latest queue depth
    rows = await db.execute_fetchall(
        """
        SELECT metadata_json FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
        ORDER BY timestamp DESC LIMIT 1
        """,
        (store_id,),
    )
    if not rows:
        return  # No queue data — nothing to check

    try:
        current_depth = json.loads(rows[0][0]).get("queue_depth", 0) or 0
    except (json.JSONDecodeError, TypeError):
        return

    # Get average queue depth
    avg_rows = await db.execute_fetchall(
        """
        SELECT AVG(
            CAST(json_extract(metadata_json, '$.queue_depth') AS INTEGER)
        ) as avg_depth
        FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
        """,
        (store_id,),
    )
    avg_depth = avg_rows[0][0] if avg_rows and avg_rows[0][0] else 1

    if current_depth > avg_depth * QUEUE_SPIKE_MULTIPLIER:
        severity = "CRITICAL" if current_depth > avg_depth * 3 else "WARN"
        anomalies.append(Anomaly(
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity=severity,
            description=(
                f"Billing queue depth is {current_depth}, "
                f"which is {current_depth/avg_depth:.1f}× the average of {avg_depth:.0f}"
            ),
            suggested_action="Open additional billing counter or deploy staff to assist",
            detected_at=now,
            metric_value=float(current_depth),
            threshold_value=float(avg_depth * QUEUE_SPIKE_MULTIPLIER),
        ))


async def _check_conversion_drop(
    db, store_id: str, now: datetime, anomalies: list[Anomaly]
) -> None:
    """Check if today's conversion rate is significantly below average.
    
    We compare today's conversion rate against the overall average.
    If today < 70% of average → anomaly.
    """
    # Get overall visitor count
    total_row = await db.execute_fetchall(
        """
        SELECT COUNT(DISTINCT visitor_id) 
        FROM events
        WHERE store_id = ? AND event_type = 'ENTRY' AND is_staff = 0
        """,
        (store_id,),
    )
    total_visitors = total_row[0][0] if total_row else 0

    if total_visitors == 0:
        return  # No data to compare

    # Get visitors who reached billing
    billing_row = await db.execute_fetchall(
        """
        SELECT COUNT(DISTINCT visitor_id) 
        FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN' AND is_staff = 0
        """,
        (store_id,),
    )
    billing_visitors = billing_row[0][0] if billing_row else 0

    current_rate = billing_visitors / total_visitors if total_visitors > 0 else 0

    # If conversion is very low, flag it
    if current_rate < 0.1 and total_visitors > 5:  # Less than 10% and enough data
        anomalies.append(Anomaly(
            anomaly_type="CONVERSION_DROP",
            severity="WARN",
            description=(
                f"Conversion rate is {current_rate:.1%}, "
                f"which is below the expected threshold"
            ),
            suggested_action=(
                "Review store layout, check if billing area is accessible, "
                "consider deploying sales staff to assist customers"
            ),
            detected_at=now,
            metric_value=round(current_rate, 4),
            threshold_value=0.1,
        ))


async def _check_dead_zones(
    db, store_id: str, now: datetime, anomalies: list[Anomaly]
) -> None:
    """Check for zones with no visitor activity in the last 30 minutes.
    
    A "dead zone" might indicate:
    - Poor product placement
    - Blocked pathway
    - Lighting issue
    """
    cutoff = (now - timedelta(minutes=DEAD_ZONE_MINUTES)).isoformat()

    # Get all zones that have HAD activity at some point
    all_zones_rows = await db.execute_fetchall(
        """
        SELECT DISTINCT zone_id FROM events
        WHERE store_id = ? AND zone_id IS NOT NULL AND event_type = 'ZONE_ENTER'
        """,
        (store_id,),
    )

    # Get zones that have had RECENT activity
    recent_zones_rows = await db.execute_fetchall(
        """
        SELECT DISTINCT zone_id FROM events
        WHERE store_id = ? AND zone_id IS NOT NULL 
          AND event_type = 'ZONE_ENTER'
          AND timestamp > ?
        """,
        (store_id, cutoff),
    )

    all_zones = {row[0] for row in all_zones_rows}
    recent_zones = {row[0] for row in recent_zones_rows}
    dead_zones = all_zones - recent_zones

    for zone_id in dead_zones:
        anomalies.append(Anomaly(
            anomaly_type="DEAD_ZONE",
            severity="INFO",
            description=f"Zone '{zone_id}' has had no visitor activity in the last {DEAD_ZONE_MINUTES} minutes",
            suggested_action=f"Check if zone '{zone_id}' is accessible and properly merchandised",
            detected_at=now,
        ))


async def _check_stale_feed(
    db, store_id: str, now: datetime, anomalies: list[Anomaly]
) -> None:
    """Check if the event feed has gone stale (no new events for 10+ minutes).
    
    A stale feed usually means:
    - Camera is offline
    - Detection pipeline crashed
    - Network issue between pipeline and API
    """
    rows = await db.execute_fetchall(
        """
        SELECT MAX(timestamp) as last_event FROM events
        WHERE store_id = ?
        """,
        (store_id,),
    )

    if not rows or not rows[0][0]:
        # No events at all for this store
        anomalies.append(Anomaly(
            anomaly_type="STALE_FEED",
            severity="CRITICAL",
            description=f"No events have ever been received for store {store_id}",
            suggested_action="Verify detection pipeline is running and configured for this store",
            detected_at=now,
        ))
        return

    try:
        last_event_time = datetime.fromisoformat(rows[0][0])
        if last_event_time.tzinfo is None:
            last_event_time = last_event_time.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return

    minutes_since = (now - last_event_time).total_seconds() / 60
    if minutes_since > STALE_FEED_MINUTES:
        anomalies.append(Anomaly(
            anomaly_type="STALE_FEED",
            severity="WARN",
            description=(
                f"Last event was {minutes_since:.0f} minutes ago "
                f"(threshold: {STALE_FEED_MINUTES} minutes)"
            ),
            suggested_action="Check camera feeds and detection pipeline status",
            detected_at=now,
            metric_value=round(minutes_since, 1),
            threshold_value=float(STALE_FEED_MINUTES),
        ))
