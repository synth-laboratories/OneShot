"""
Minimal trace cleaner: copies rows from raw DB to clean DB with the same schema.

Usage:
  uv run -m local_tracing.trace_cleaner data/traces/v3/raw_synth_ai.db/traces.sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 5 15
    where args are: RAW_DB CLEAN_DB POLL_SECS SESSION_IDLE_SECS (last two are accepted but unused here)
"""

import sqlite3
import sys
import time
from pathlib import Path


def ensure_schema(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traces (
                id TEXT PRIMARY KEY,
                ts_ms INTEGER,
                method TEXT,
                url TEXT,
                request_json TEXT,
                response_json TEXT,
                meta_json TEXT
            );
            """
        )
        conn.commit()


def copy_once(raw_db: Path, clean_db: Path) -> int:
    with sqlite3.connect(str(raw_db)) as src, sqlite3.connect(str(clean_db)) as dst:
        # Copy new rows by id
        dst.execute("PRAGMA journal_mode=WAL;")
        dst.commit()
        res = src.execute("SELECT id, ts_ms, method, url, request_json, response_json, meta_json FROM traces")
        rows = res.fetchall()
        inserted = 0
        for r in rows:
            try:
                dst.execute(
                    "INSERT OR IGNORE INTO traces (id, ts_ms, method, url, request_json, response_json, meta_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    r,
                )
                inserted += dst.total_changes
            except Exception:
                pass
        dst.commit()
        return inserted


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: trace_cleaner.py RAW_DB CLEAN_DB [poll_secs] [session_idle_secs]", file=sys.stderr)
        return 2
    raw_db = Path(sys.argv[1])
    clean_db = Path(sys.argv[2])
    poll_secs = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    ensure_schema(raw_db)
    ensure_schema(clean_db)

    print(f"[cleaner] copying from {raw_db} -> {clean_db} every {poll_secs}s")
    try:
        while True:
            n = copy_once(raw_db, clean_db)
            if n:
                print(f"[cleaner] inserted {n} rows")
            time.sleep(poll_secs)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


