"""
pos_loader.py — POS Transaction Data Loader

Loads Point-of-Sale transaction records from the CSV file into memory.
These transactions are used to calculate conversion rate:
  conversion_rate = visitors_who_purchased / total_unique_visitors

The POS data has NO customer_id — we correlate transactions with visitors
by matching timestamps: a visitor who was in the billing zone within a
5-minute window before a transaction timestamp counts as a "converted" visitor.

This module loads the CSV once on startup and provides fast lookup functions.
"""

import csv
import os
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import structlog

logger = structlog.get_logger(__name__)

# ============================================================
# POS Transaction data class
# ============================================================
@dataclass
class POSTransaction:
    """One POS (Point-of-Sale) transaction record.
    
    Fields come directly from the CSV columns:
    - store_id: which store (e.g., "STORE_PRP_001")
    - transaction_id: unique transaction ID (e.g., "TXN_00001")  
    - timestamp: when the transaction happened (UTC)
    - basket_value_inr: total purchase amount in Indian Rupees
    """
    store_id: str
    transaction_id: str
    timestamp: datetime
    basket_value_inr: float


# ============================================================
# Global state — loaded transactions
# ============================================================
_transactions: list[POSTransaction] = []


# ============================================================
# CSV file path — configurable via environment variable
# ============================================================
POS_CSV_PATH = os.environ.get("POS_CSV_PATH", "data/pos_transactions.csv")


def load_pos_data() -> None:
    """Load POS transactions from the CSV file into memory.
    
    Called once on app startup. Stores all transactions in a list
    for fast in-memory lookups during metric computation.
    """
    global _transactions
    _transactions = []

    if not os.path.exists(POS_CSV_PATH):
        logger.warning("pos_file_not_found", path=POS_CSV_PATH)
        return

    with open(POS_CSV_PATH, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            txn = POSTransaction(
                store_id=row["store_id"].strip(),
                transaction_id=row["transaction_id"].strip(),
                timestamp=datetime.fromisoformat(
                    row["timestamp"].strip().replace("Z", "+00:00")
                ),
                basket_value_inr=float(row["basket_value_inr"].strip()),
            )
            _transactions.append(txn)

    logger.info("pos_data_loaded", count=len(_transactions))


def get_transactions_for_store(
    store_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[POSTransaction]:
    """Get POS transactions for a specific store within a time window.
    
    Args:
        store_id: Filter by this store ID
        start_time: Only include transactions at or after this time
        end_time: Only include transactions at or before this time
    
    Returns:
        List of matching POSTransaction objects
    """
    results = []
    for txn in _transactions:
        # Filter by store
        if txn.store_id != store_id:
            continue
        # Filter by start time (if specified)
        if start_time and txn.timestamp < start_time:
            continue
        # Filter by end time (if specified)
        if end_time and txn.timestamp > end_time:
            continue
        results.append(txn)
    return results


def get_all_transactions() -> list[POSTransaction]:
    """Return all loaded POS transactions."""
    return _transactions
