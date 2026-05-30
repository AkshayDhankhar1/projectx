"""
metrics.py — Real-Time Store Metrics Endpoint

GET /stores/{store_id}/metrics

Computes live analytics for a store by querying the events database.
Every metric is calculated fresh on each request — no stale cache.

Metrics computed:
1. unique_visitors: COUNT DISTINCT visitor_id (excluding staff)
2. conversion_rate: visitors who purchased / total visitors
3. avg_dwell_by_zone: average dwell time per zone
4. current_queue_depth: latest billing queue depth
5. abandonment_rate: queue abandoners / queue joiners
6. total_transactions & revenue: from POS data

The conversion rate correlation works like this:
- We look at each POS transaction timestamp
- We check if any visitor was in the BILLING zone within 5 minutes before that transaction
- If yes, that visitor counts as "converted"
- conversion_rate = converted_visitors / total_unique_visitors
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from app.models import StoreMetrics, ZoneDwell
from app.database import get_db
from app.pos_loader import get_transactions_for_store
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["analytics"])

# The time window for correlating visitors with POS transactions.
# A visitor in the billing zone within 5 minutes before a transaction
# is considered a "converted" visitor.
CONVERSION_WINDOW_MINUTES = 5


@router.get("/stores/{store_id}/metrics", response_model=StoreMetrics)
async def get_store_metrics(store_id: str) -> StoreMetrics:
    """Get real-time metrics for a specific store.
    
    All metrics exclude events where is_staff=true, because staff
    movement should not inflate customer analytics.
    """
    try:
        db = await get_db()
    except RuntimeError:
        raise HTTPException(status_code=503, detail={"error": "Database unavailable"})

    # ----------------------------------------------------------
    # 1. Count unique visitors (exclude staff)
    # ----------------------------------------------------------
    # We count distinct visitor_ids that have an ENTRY event.
    # DISTINCT ensures re-entries with same visitor_id aren't double-counted.
    row = await db.execute_fetchall(
        """
        SELECT COUNT(DISTINCT visitor_id) as count
        FROM events
        WHERE store_id = ? AND event_type = 'ENTRY' AND is_staff = 0
        """,
        (store_id,),
    )
    unique_visitors = row[0][0] if row else 0

    # ----------------------------------------------------------
    # 2. Calculate conversion rate
    # ----------------------------------------------------------
    # Step A: Get all visitors who were in the BILLING zone
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

    # Step B: Get POS transactions for this store
    transactions = get_transactions_for_store(store_id)

    # Step C: For each transaction, check if any visitor was in billing
    # zone within 5 minutes before the transaction time
    converted_visitors = set()
    for txn in transactions:
        window_start = txn.timestamp - timedelta(minutes=CONVERSION_WINDOW_MINUTES)
        window_end = txn.timestamp
        for bv_row in billing_visitors_rows:
            visitor_id = bv_row[0]
            try:
                visitor_time = datetime.fromisoformat(bv_row[1])
                # Make timezone-aware if not already
                if visitor_time.tzinfo is None:
                    visitor_time = visitor_time.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if window_start <= visitor_time <= window_end:
                converted_visitors.add(visitor_id)

    # Conversion rate: avoid division by zero for empty stores
    conversion_rate = (
        len(converted_visitors) / unique_visitors
        if unique_visitors > 0
        else 0.0
    )

    # ----------------------------------------------------------
    # 3. Average dwell time by zone
    # ----------------------------------------------------------
    # We compute the average dwell_ms for ZONE_DWELL events per zone.
    dwell_rows = await db.execute_fetchall(
        """
        SELECT zone_id, AVG(dwell_ms) as avg_dwell, COUNT(*) as visit_count
        FROM events
        WHERE store_id = ? AND event_type = 'ZONE_DWELL' AND is_staff = 0
          AND zone_id IS NOT NULL
        GROUP BY zone_id
        """,
        (store_id,),
    )
    avg_dwell_by_zone = [
        ZoneDwell(
            zone_id=row[0],
            avg_dwell_ms=round(row[1], 1),
            visit_count=row[2],
        )
        for row in dwell_rows
    ]

    # ----------------------------------------------------------
    # 4. Current queue depth
    # ----------------------------------------------------------
    # Get the most recent BILLING_QUEUE_JOIN event's queue_depth
    queue_row = await db.execute_fetchall(
        """
        SELECT metadata_json FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
        ORDER BY timestamp DESC LIMIT 1
        """,
        (store_id,),
    )
    current_queue_depth = 0
    if queue_row:
        import json
        try:
            meta = json.loads(queue_row[0][0])
            current_queue_depth = meta.get("queue_depth", 0) or 0
        except (json.JSONDecodeError, TypeError):
            pass

    # ----------------------------------------------------------
    # 5. Abandonment rate
    # ----------------------------------------------------------
    # abandonment_rate = BILLING_QUEUE_ABANDON / BILLING_QUEUE_JOIN
    join_row = await db.execute_fetchall(
        """
        SELECT COUNT(*) FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN' AND is_staff = 0
        """,
        (store_id,),
    )
    abandon_row = await db.execute_fetchall(
        """
        SELECT COUNT(*) FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_ABANDON' AND is_staff = 0
        """,
        (store_id,),
    )
    queue_joins = join_row[0][0] if join_row else 0
    queue_abandons = abandon_row[0][0] if abandon_row else 0
    abandonment_rate = (
        queue_abandons / queue_joins if queue_joins > 0 else 0.0
    )

    # ----------------------------------------------------------
    # 6. Transaction totals from POS data
    # ----------------------------------------------------------
    total_transactions = len(transactions)
    total_revenue = sum(t.basket_value_inr for t in transactions)

    return StoreMetrics(
        store_id=store_id,
        timestamp=datetime.now(timezone.utc),
        unique_visitors=unique_visitors,
        conversion_rate=round(conversion_rate, 4),
        avg_dwell_by_zone=avg_dwell_by_zone,
        current_queue_depth=current_queue_depth,
        abandonment_rate=round(abandonment_rate, 4),
        total_transactions=total_transactions,
        total_revenue=round(total_revenue, 2),
    )
