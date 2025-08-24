#!/bin/bash
set -euo pipefail

# Container trace health check script
# This script verifies that the tracing system is working properly inside the container

SESSION_ID="${RUN_ID:-unknown_session}"
RAW_DB="${RAW_TRACE_DB:-/app/traces/container_raw.db}"

echo "[health-check] [${SESSION_ID}] Starting trace system health check..."

# Check 1: Is proxy running?
PROXY_PID=$(cat /tmp/container_mitm.pid 2>/dev/null || echo "")
if [ -n "$PROXY_PID" ] && kill -0 "$PROXY_PID" 2>/dev/null; then
    echo "[health-check] [${SESSION_ID}] ✅ Proxy is running (PID: $PROXY_PID)"
else
    echo "[health-check] [${SESSION_ID}] ❌ Proxy is not running"
fi

# Check 2: Is proxy responding?
if curl -s --connect-timeout 3 --max-time 5 http://localhost:18080 >/dev/null 2>&1; then
    echo "[health-check] [${SESSION_ID}] ✅ Proxy is responding on port 18080"
else
    echo "[health-check] [${SESSION_ID}] ❌ Proxy is not responding on port 18080"
fi

# Check 3: Database accessibility
if [ -f "$RAW_DB" ]; then
    if [ -r "$RAW_DB" ] && [ -w "$RAW_DB" ]; then
        echo "[health-check] [${SESSION_ID}] ✅ Trace database exists and is accessible"

        # Check database content
        if command -v sqlite3 >/dev/null 2>&1; then
            TRACE_COUNT=$(sqlite3 "$RAW_DB" "SELECT COUNT(*) FROM traces;" 2>/dev/null || echo "0")
            echo "[health-check] [${SESSION_ID}] ✅ Database has $TRACE_COUNT trace records"
        fi
    else
        echo "[health-check] [${SESSION_ID}] ❌ Trace database exists but has permission issues"
    fi
else
    echo "[health-check] [${SESSION_ID}] ❌ Trace database does not exist"
fi

# Check 4: Environment variables
REQUIRED_VARS=("RAW_TRACE_DB" "HTTP_PROXY" "HTTPS_PROXY")
for var in "${REQUIRED_VARS[@]}"; do
    if [ -n "${!var:-}" ]; then
        echo "[health-check] [${SESSION_ID}] ✅ $var is set"
    else
        echo "[health-check] [${SESSION_ID}] ❌ $var is not set"
    fi
done

# Check 5: Tracer script exists
if [ -f "/app/src/local_tracing/mitm_tracer.py" ]; then
    echo "[health-check] [${SESSION_ID}] ✅ MITM tracer script exists"
else
    echo "[health-check] [${SESSION_ID}] ❌ MITM tracer script not found"
fi

# Check 6: Test proxy functionality
echo "[health-check] [${SESSION_ID}] Testing proxy functionality..."
if curl -s --connect-timeout 5 --max-time 10 -x http://localhost:18080 https://httpbin.org/get >/dev/null 2>&1; then
    echo "[health-check] [${SESSION_ID}] ✅ Proxy can handle HTTPS traffic"
else
    echo "[health-check] [${SESSION_ID}] ❌ Proxy cannot handle HTTPS traffic"
fi

echo "[health-check] [${SESSION_ID}] Health check completed"

# Return appropriate exit code
if [ -n "$PROXY_PID" ] && kill -0 "$PROXY_PID" 2>/dev/null && [ -f "$RAW_DB" ]; then
    echo "[health-check] [${SESSION_ID}] ✅ Trace system appears healthy"
    exit 0
else
    echo "[health-check] [${SESSION_ID}] ❌ Trace system has issues"
    exit 1
fi
