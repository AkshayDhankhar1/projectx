import sqlite3
import os
import json

DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/store.db")
EVENTS_FILE = "data/events.jsonl"

def prepopulate():
    if not os.path.exists(EVENTS_FILE):
        print(f"No events file found at {EVENTS_FILE}, skipping prepopulate.")
        return

    print(f"Prepopulating database {DATABASE_PATH}...")
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Create table and indexes (same schema as database.py)
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id        TEXT PRIMARY KEY,
            store_id        TEXT NOT NULL,
            camera_id       TEXT NOT NULL,
            visitor_id      TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            zone_id         TEXT,
            dwell_ms        INTEGER DEFAULT 0,
            is_staff        INTEGER DEFAULT 0,
            confidence      REAL NOT NULL,
            metadata_json   TEXT,
            ingested_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_store_time 
        ON events(store_id, timestamp)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_store_type 
        ON events(store_id, event_type)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_visitor 
        ON events(visitor_id)
    """)

    # Read events from jsonl
    events = []
    with open(EVENTS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception as e:
                    print(f"Skipping malformed line: {e}")

    # Insert events using INSERT OR IGNORE for idempotency
    print(f"Found {len(events)} events. Inserting into database...")
    inserted = 0
    for e in events:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO events (
                    event_id, store_id, camera_id, visitor_id, event_type, 
                    timestamp, zone_id, dwell_ms, is_staff, confidence, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                e["event_id"],
                e["store_id"],
                e["camera_id"],
                e["visitor_id"],
                e["event_type"],
                e["timestamp"],
                e.get("zone_id"),
                e.get("dwell_ms", 0),
                1 if e.get("is_staff", False) else 0,
                e["confidence"],
                json.dumps(e.get("metadata", {}))
            ))
            if cursor.rowcount > 0:
                inserted += 1
        except Exception as ex:
            print(f"Failed to insert event {e.get('event_id')}: {ex}")

    conn.commit()
    conn.close()
    print(f"Successfully inserted {inserted} new events. Total events processed: {len(events)}")

if __name__ == "__main__":
    prepopulate()
