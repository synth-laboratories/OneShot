"""
Minimal mitmproxy addon to capture OpenAI API requests/responses into SQLite.
Also provides a /health endpoint for checking proxy status.

Database layout (raw):
  data/traces/v3/raw_synth_ai.db/traces.sqlite3 table 'traces' with columns:
    id TEXT PRIMARY KEY, ts_ms INTEGER, method TEXT, url TEXT,
    request_json TEXT, response_json TEXT, meta_json TEXT
"""

import json
import os
import sqlite3
import time
from pathlib import Path

from mitmproxy import http  # type: ignore


RAW_DB = Path(os.environ.get("RAW_TRACE_DB", "data/traces/v3/raw_synth_ai.db/traces.sqlite3"))


def _ensure_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
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


class Tracer:
    def __init__(self) -> None:
        _ensure_db(RAW_DB)

    def request(self, flow: http.HTTPFlow) -> None:  # type: ignore
        """Handle incoming requests - check for health endpoint"""
        # Handle health check endpoint
        if flow.request.path == "/health" and flow.request.host == "localhost":
            flow.response = http.Response.make(
                200,
                b'{"status":"ok","service":"mitmproxy-tracer"}',
                {"Content-Type": "application/json"}
            )
            return
        
        # Let other requests pass through normally
        return

    def response(self, flow: http.HTTPFlow) -> None:  # type: ignore
        """Capture API requests/responses to database"""
        # Skip health check responses (already handled in request())
        if flow.request.path == "/health" and flow.request.host == "localhost":
            return
            
        try:
            ts_ms = int(time.time() * 1000)
            rid = flow.id
            method = (flow.request.method or "").upper()
            url = flow.request.pretty_url or ""

            # Capture JSON if present, otherwise store as string
            req_text = flow.request.get_text(strict=False) or ""
            resp_text = flow.response.get_text(strict=False) if flow.response else ""

            def as_json_string(text: str) -> str:
                try:
                    obj = json.loads(text)
                    return json.dumps(obj, ensure_ascii=False)
                except Exception:
                    return json.dumps({"_raw": text}, ensure_ascii=False)

            request_json = as_json_string(req_text)
            response_json = as_json_string(resp_text)
            meta_json = json.dumps(
                {
                    "status_code": getattr(flow.response, "status_code", None),
                    "headers": dict(flow.response.headers) if flow.response else {},
                    "session_id": os.environ.get("RUN_ID", "session_unknown"),
                },
                ensure_ascii=False,
            )

            with sqlite3.connect(str(RAW_DB)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO traces (id, ts_ms, method, url, request_json, response_json, meta_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rid, ts_ms, method, url, request_json, response_json, meta_json),
                )
                conn.commit()
        except Exception:
            # Best-effort tracer; avoid crashing mitmproxy
            pass


addons = [Tracer()]


