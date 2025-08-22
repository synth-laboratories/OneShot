#!/bin/bash
set -euo pipefail

# Trace Session Monitor
# Monitor and analyze tracing sessions across the system

COMMAND="${1:-status}"

case "$COMMAND" in
    "status")
        echo "=== OneShot Bench Trace Session Status ==="
        echo

        # Check if proxy is running
        if [ -f /tmp/codex_mitm.pid ]; then
            PROXY_PID=$(cat /tmp/codex_mitm.pid)
            if kill -0 "$PROXY_PID" 2>/dev/null; then
                echo "✅ MITM Proxy: Running (PID: $PROXY_PID)"
            else
                echo "❌ MITM Proxy: PID file exists but process not running"
            fi
        else
            echo "❌ MITM Proxy: Not running"
        fi

        # Check trace cleaner
        if [ -f /tmp/trace_cleaner.pid ]; then
            CLEANER_PID=$(cat /tmp/trace_cleaner.pid)
            if kill -0 "$CLEANER_PID" 2>/dev/null; then
                echo "✅ Trace Cleaner: Running (PID: $CLEANER_PID)"
            else
                echo "❌ Trace Cleaner: PID file exists but process not running"
            fi
        else
            echo "❌ Trace Cleaner: Not running"
        fi

        # Check recent sessions
        echo
        echo "Recent Sessions:"
        if [ -f data/traces/v3/clean_synth_ai.db/traces.sqlite3 ]; then
            SESSION_COUNT=$(sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 "SELECT COUNT(*) FROM cleaned_sessions;" 2>/dev/null || echo "0")
            echo "  Database sessions: $SESSION_COUNT"

            # Show recent sessions
            echo "  Recent sessions:"
            sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 \
                "SELECT session_id, created_at FROM cleaned_sessions ORDER BY created_at DESC LIMIT 3;" 2>/dev/null | \
            while read -r session_id created_at; do
                echo "    $session_id - $created_at"
            done
        else
            echo "  No trace database found"
        fi

        # Check recent runs
        echo
        echo "Recent Evaluation Runs:"
        if [ -d data/runs ]; then
            ls -lt data/runs/ | head -5 | while read -r line; do
                if [[ $line == d* ]]; then
                    DIR_NAME=$(echo "$line" | awk '{print $9}')
                    if [ -f "data/runs/$DIR_NAME/traces/traces.jsonl" ]; then
                        TRACE_COUNT=$(wc -l < "data/runs/$DIR_NAME/traces/traces.jsonl")
                        echo "  $DIR_NAME - $TRACE_COUNT traces"
                    else
                        echo "  $DIR_NAME - no traces"
                    fi
                fi
            done
        else
            echo "  No runs directory found"
        fi
        ;;

    "cleanup")
        echo "=== Cleaning up trace sessions ==="

        # Kill processes
        echo "Stopping processes..."
        pkill -f "mitmdump.*mitm_tracer.py" || true
        pkill -f "trace_cleaner" || true

        # Remove PID files
        rm -f /tmp/codex_mitm.pid /tmp/trace_cleaner.pid

        # Clean log files
        echo "Cleaning log files..."
        : > /tmp/codex_mitm.out
        : > /tmp/trace_cleaner.out

        echo "✅ Cleanup completed"
        ;;

    "analyze")
        SESSION_ID="${2:-}"
        if [ -z "$SESSION_ID" ]; then
            echo "Usage: $0 analyze <session_id>"
            exit 1
        fi

        echo "=== Analyzing Session: $SESSION_ID ==="

        # Look for session in database
        SESSION_DATA=$(sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 \
            "SELECT created_at, formatted_json FROM cleaned_sessions WHERE session_id='$SESSION_ID';" 2>/dev/null)

        if [ -n "$SESSION_DATA" ]; then
            echo "Session found in database:"
            echo "$SESSION_DATA" | head -5
        else
            echo "Session not found in database"
        fi

        # Look for session in recent runs
        echo
        echo "Looking for session in recent runs..."
        find data/runs -name "session_info.txt" -exec grep -l "$SESSION_ID" {} \; 2>/dev/null | while read -r file; do
            RUN_DIR=$(dirname "$(dirname "$file")")
            echo "Found in run: $RUN_DIR"
            if [ -f "$RUN_DIR/traces/session_summary.md" ]; then
                echo "Session summary:"
                head -10 "$RUN_DIR/traces/session_summary.md"
            fi
        done
        ;;

    "list")
        echo "=== Available Sessions ==="
        if [ -f data/traces/v3/clean_synth_ai.db/traces.sqlite3 ]; then
            sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 \
                "SELECT session_id, created_at FROM cleaned_sessions ORDER BY created_at DESC;" 2>/dev/null
        else
            echo "No trace database found"
        fi
        ;;

    *)
        echo "Usage: $0 <command>"
        echo
        echo "Commands:"
        echo "  status     - Show current tracing system status"
        echo "  cleanup    - Stop all trace processes and clean up"
        echo "  analyze    - Analyze a specific session"
        echo "  list       - List all available sessions"
        echo
        echo "Examples:"
        echo "  $0 status"
        echo "  $0 analyze session_12345"
        echo "  $0 cleanup"
        ;;
esac
