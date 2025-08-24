from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime
import sqlite3
import logging

logger = logging.getLogger(__name__)


class TraceExporter:
    """Export cleaned traces from the database"""

    @staticmethod
    def export_session(run_id: str = None, start_time: datetime = None, end_time: datetime = None) -> Dict[str, Any]:
        db_path = Path('data/traces/v3/clean_synth_ai.db/traces.sqlite3')

        if not db_path.exists():
            logger.warning(f"Clean trace database not found at {db_path}")
            return {
                "session_id": run_id or f"session_{datetime.now().isoformat()}",
                "traces": [],
                "count": 0,
                "note": "Trace database not found",
            }

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("PRAGMA table_info(traces)")
            columns_info = cursor.fetchall()
            column_names = [col[1] for col in columns_info]

            query = "SELECT * FROM traces WHERE 1=1"
            params: List[str] = []

            if run_id and 'run_id' in column_names:
                query += " AND run_id = ?"
                params.append(run_id)

            if start_time and 'timestamp' in column_names:
                query += " AND timestamp >= ?"
                params.append(start_time.isoformat())

            if end_time and 'timestamp' in column_names:
                query += " AND timestamp <= ?"
                params.append(end_time.isoformat())

            query += " ORDER BY timestamp DESC LIMIT 1000" if 'timestamp' in column_names else " LIMIT 1000"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            columns = [desc[0] for desc in cursor.description]

            traces = []
            for row in rows:
                trace = dict(zip(columns, row))
                traces.append(trace)

            conn.close()

            return {
                "session_id": run_id or f"session_{datetime.now().isoformat()}",
                "traces": traces,
                "count": len(traces),
            }

        except Exception as e:
            logger.error(f"Failed to export trace: {str(e)}")
            return {
                "session_id": run_id or f"session_{datetime.now().isoformat()}",
                "traces": [],
                "count": 0,
                "error": str(e),
            }


