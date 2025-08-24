"""
Trace cleaner: reads raw DB, groups rows by session_id, and writes a normalized
"session" JSON record into the clean DB, similar to synth-research.

Usage:
  uv run -m local_tracing.trace_cleaner data/traces/v3/raw_synth_ai.db/traces.sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 5 15
"""

import sqlite3
import sys
import time
from pathlib import Path


def ensure_schema(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        # Raw table (for passthrough when needed)
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
        # Cleaned sessions table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cleaned_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT,
                formatted_json TEXT
            );
            """
        )
        conn.commit()


def _load_raw_rows(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, ts_ms, method, url, request_json, response_json, meta_json FROM traces ORDER BY ts_ms ASC"
    ).fetchall()


def _extract_session_id(meta_json_text: str) -> str:
    try:
        import json

        meta = json.loads(meta_json_text)
        sid = meta.get("session_id")
        return sid or "session_unknown"
    except Exception:
        return "session_unknown"


def _build_clean_session(session_id: str, rows: list) -> tuple[str, str, str]:
    # Build a minimal normalized session JSON compatible with downstream tooling
    import json
    from datetime import datetime

    created_at = None
    events = []
    for (_id, ts_ms, _method, _url, request_json, response_json, _meta_json) in rows:
        if created_at is None and ts_ms:
            created_at = datetime.utcfromtimestamp((ts_ms or 0) / 1000.0).isoformat()
        # Keep raw request/response as nested objects
        try:
            req = json.loads(request_json)
        except Exception:
            req = {"_raw": request_json}
        try:
            resp = json.loads(response_json)
        except Exception:
            resp = {"_raw": response_json}
        events.append({"request": req, "response": resp, "ts_ms": ts_ms})

    formatted = {
        "session_id": session_id,
        "created_at": created_at,
        "traces": events,
    }
    return session_id, created_at or "", json.dumps(formatted, ensure_ascii=False)


def clean_once(raw_db: Path, clean_db: Path) -> int:
    with sqlite3.connect(str(raw_db)) as src, sqlite3.connect(str(clean_db)) as dst:
        dst.execute("PRAGMA journal_mode=WAL;")
        dst.commit()

        rows = _load_raw_rows(src)
        # Group rows by session_id
        by_session: dict[str, list] = {}
        for r in rows:
            sid = _extract_session_id(r[6])  # meta_json at index 6
            by_session.setdefault(sid, []).append(r)

        inserted = 0
        for sid, srows in by_session.items():
            session_id, created_at, formatted_json = _build_clean_session(sid, srows)
            dst.execute(
                "INSERT OR REPLACE INTO cleaned_sessions (session_id, created_at, formatted_json) VALUES (?, ?, ?)",
                (session_id, created_at, formatted_json),
            )
            inserted += 1
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

    print(f"[cleaner] normalizing from {raw_db} -> {clean_db} every {poll_secs}s")

    # Handle container shutdown signals gracefully
    import signal
    import os

    def signal_handler(signum, frame):
        print(f"[cleaner] Received signal {signum}, shutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    consecutive_errors = 0
    max_consecutive_errors = 5

    try:
        while True:
            try:
                n = clean_once(raw_db, clean_db)
                if n:
                    print(f"[cleaner] updated {n} sessions")
                consecutive_errors = 0  # Reset error count on success
            except sqlite3.OperationalError as e:
                consecutive_errors += 1
                print(f"[cleaner] Database error (attempt {consecutive_errors}/{max_consecutive_errors}): {e}")
                if consecutive_errors >= max_consecutive_errors:
                    print("[cleaner] Too many database errors, shutting down...")
                    return 1
            except Exception as e:
                consecutive_errors += 1
                print(f"[cleaner] Unexpected error (attempt {consecutive_errors}/{max_consecutive_errors}): {e}")
                if consecutive_errors >= max_consecutive_errors:
                    print("[cleaner] Too many errors, shutting down...")
                    return 1

            time.sleep(poll_secs)

    except KeyboardInterrupt:
        print("[cleaner] Interrupted by user")
        return 0
    except SystemExit:
        print("[cleaner] System exit requested")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


