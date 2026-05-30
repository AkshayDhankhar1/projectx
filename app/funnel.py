"""
funnel.py — Conversion Funnel Endpoint

GET /stores/{store_id}/funnel

Builds a session-based conversion funnel showing how visitors
progress through the store:

  Entry → Zone Visit → Billing Queue → Purchase

The unit is the VISITOR (by distinct visitor_id), not raw events.
This means:
- If a visitor triggers 5 ZONE_ENTER events, they count as 1 at "Zone Visit"
- If a visitor re-enters (REENTRY event), they still count as 1 visitor
- Drop-off % shows what fraction of visitors were lost at each stage

Example output:
  Entry:         20 visitors (100%)
  Zone Visit:    18 visitors (drop-off: 10%)  
  Billing Queue:  8 visitors (drop-off: 55.6%)
  Purchase:       5 visitors (drop-off: 37.5%)
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from app.models import StoreFunnel, FunnelStage
from app.database import get_db
from app.pos_loader import get_transactions_for_store
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["analytics"])

CONVERSION_WINDOW_MINUTES = 5


@router.get("/stores/{store_id}/funnel", response_model=StoreFunnel)
async def get_store_funnel(store_id: str) -> StoreFunnel:
    """Get the conversion funnel for a specific store.
    
    Each stage counts DISTINCT visitor_ids (not events).
    Re-entries do not double-count visitors.
    """
    try:
        db = await get_db()
    except RuntimeError:
        raise HTTPException(status_code=503, detail={"error": "Database unavailable"})

    # ----------------------------------------------------------
    # Stage 1: ENTRY — how many unique visitors entered the store
    # ----------------------------------------------------------
    row = await db.execute_fetchall(
        """
        SELECT COUNT(DISTINCT visitor_id) 
        FROM events
        WHERE store_id = ? AND event_type IN ('ENTRY', 'REENTRY') AND is_staff = 0
        """,
        (store_id,),
    )
    entry_count = row[0][0] if row else 0

    # ----------------------------------------------------------
    # Stage 2: ZONE VISIT — visitors who visited at least one zone
    # ----------------------------------------------------------
    # A visitor who had any ZONE_ENTER event counts here
    row = await db.execute_fetchall(
        """
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id = ? AND event_type = 'ZONE_ENTER' AND is_staff = 0
        """,
        (store_id,),
    )
    zone_visit_count = row[0][0] if row else 0

    # ----------------------------------------------------------
    # Stage 3: BILLING QUEUE — visitors who joined the billing queue
    # ----------------------------------------------------------
    row = await db.execute_fetchall(
        """
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN' AND is_staff = 0
        """,
        (store_id,),
    )
    billing_queue_count = row[0][0] if row else 0

    # ----------------------------------------------------------
    # Stage 4: PURCHASE — visitors correlated with POS transactions
    # ----------------------------------------------------------
    # Same logic as in metrics.py: check if visitor was in BILLING zone
    # within 5 minutes before a POS transaction timestamp
    billing_visitors_rows = await db.execute_fetchall(
        """
        SELECT DISTINCT visitor_id, timestamp
        FROM events
        WHERE store_id = ?
          AND zone_id = 'BILLING'
          AND is_staff = 0
          AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL', 'BILLING_QUEUE_JOIN')
        """,
        (store_id,),
    )

    transactions = get_transactions_for_store(store_id)
    converted_visitors = set()
    for txn in transactions:
        window_start = txn.timestamp - timedelta(minutes=CONVERSION_WINDOW_MINUTES)
        window_end = txn.timestamp
        for bv_row in billing_visitors_rows:
            visitor_id = bv_row[0]
            try:
                visitor_time = datetime.fromisoformat(bv_row[1])
                if visitor_time.tzinfo is None:
                    visitor_time = visitor_time.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if window_start <= visitor_time <= window_end:
                converted_visitors.add(visitor_id)

    purchase_count = len(converted_visitors)

    # ----------------------------------------------------------
    # Build the funnel stages with drop-off percentages
    # ----------------------------------------------------------
    def calc_dropoff(current: int, previous: int) -> float:
        """Calculate what % of visitors were LOST between stages.
        
        Example: if 20 entered and 18 visited zones,
        drop_off = (20 - 18) / 20 * 100 = 10%
        """
        if previous == 0:
            return 0.0
        return round((previous - current) / previous * 100, 1)

    stages = [
        FunnelStage(
            stage="Entry",
            count=entry_count,
            drop_off_percent=0.0,  # No drop-off at the first stage
        ),
        FunnelStage(
            stage="Zone Visit",
            count=zone_visit_count,
            drop_off_percent=calc_dropoff(zone_visit_count, entry_count),
        ),
        FunnelStage(
            stage="Billing Queue",
            count=billing_queue_count,
            drop_off_percent=calc_dropoff(billing_queue_count, zone_visit_count),
        ),
        FunnelStage(
            stage="Purchase",
            count=purchase_count,
            drop_off_percent=calc_dropoff(purchase_count, billing_queue_count),
        ),
    ]

    return StoreFunnel(
        store_id=store_id,
        timestamp=datetime.now(timezone.utc),
        stages=stages,
        total_sessions=entry_count,
    )
