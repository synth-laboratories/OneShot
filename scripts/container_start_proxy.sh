#!/bin/bash
set -euo pipefail

# Start MITM proxy inside container for tracing codex-synth activity
# This script is called from box_bootstrap.sh before codex execution

PORT="${CONTAINER_PROXY_PORT:-18080}"
RAW_DB="${RAW_TRACE_DB:-/app/traces/container_raw.db}"
CLEAN_DB="${CLEAN_DB:-/app/traces/container_clean.db}"
SESSION_ID="${RUN_ID:-unknown_session}"
TIMESTAMP=$(date '+%Y-%m-%dT%H:%M:%SZ')

# Ensure trace directory exists
mkdir -p "$(dirname "$RAW_DB")"

# Kill any existing proxy processes in container
echo "[container-proxy] [${SESSION_ID}] Cleaning up existing proxy processes..."
pkill -f mitmdump || true
sleep 1

# Log session information
echo "[container-proxy] [${SESSION_ID}] ======================================" | tee -a /tmp/container_mitm.log
echo "[container-proxy] [${SESSION_ID}] Starting MITM proxy session ${SESSION_ID}" | tee -a /tmp/container_mitm.log
echo "[container-proxy] [${SESSION_ID}] Timestamp: ${TIMESTAMP}" | tee -a /tmp/container_mitm.log
echo "[container-proxy] [${SESSION_ID}] Port: ${PORT}" | tee -a /tmp/container_mitm.log
echo "[container-proxy] [${SESSION_ID}] Raw traces: ${RAW_DB}" | tee -a /tmp/container_mitm.log
echo "[container-proxy] [${SESSION_ID}] Task ID: ${TASK_ID:-unknown}" | tee -a /tmp/container_mitm.log
echo "[container-proxy] [${SESSION_ID}] Container: $(hostname)" | tee -a /tmp/container_mitm.log
echo "[container-proxy] [${SESSION_ID}] ======================================" | tee -a /tmp/container_mitm.log

# Set environment for the tracer
export RAW_TRACE_DB="$RAW_DB"

# Start mitmdump with the tracer script
echo "[container-proxy] [${SESSION_ID}] Launching mitmdump with tracer..."

# Set environment variables for better error handling
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# Start mitmdump with error handling
PYTHONPATH="/app/src" timeout 3600 mitmdump \
  -s "/app/src/local_tracing/mitm_tracer.py" \
  --listen-host 0.0.0.0 \
  --listen-port "${PORT}" \
  --set upstream_cert=false \
  --set ssl_insecure=true \
  --set stream_large_bodies=1 \
  --set flow_detail=4 \
  --set termlog_verbosity=info \
  >>/tmp/container_mitm.log 2>&1 &

# Save PID for cleanup
PROXY_PID=$!
echo $PROXY_PID > /tmp/container_mitm.pid
echo "[container-proxy] [${SESSION_ID}] Proxy started with PID ${PROXY_PID}"

# Monitor proxy process health
(
    sleep 5  # Give it time to start
    if ! kill -0 $PROXY_PID 2>/dev/null; then
        echo "[container-proxy] [${SESSION_ID}] ❌ CRITICAL: Proxy process died immediately" | tee -a /tmp/container_mitm.log
        echo "[container-proxy] [${SESSION_ID}] Check /tmp/container_mitm.log for details" | tee -a /tmp/container_mitm.log
    else
        echo "[container-proxy] [${SESSION_ID}] ✅ Proxy process is healthy" | tee -a /tmp/container_mitm.log
    fi
) &

# Give it a moment to start up
sleep 3

# Verify proxy is running and log the status
if ! curl -s --connect-timeout 3 --max-time 5 http://localhost:${PORT} >/dev/null 2>&1; then
    echo "[container-proxy] [${SESSION_ID}] ❌ WARNING: Proxy not responding on port ${PORT}" | tee -a /tmp/container_mitm.log
    echo "[container-proxy] [${SESSION_ID}] This may cause tracing to fail" | tee -a /tmp/container_mitm.log
else
    echo "[container-proxy] [${SESSION_ID}] ✅ Proxy is responding on port ${PORT}" | tee -a /tmp/container_mitm.log
fi

# Verify database is writable and create directory structure
DB_DIR=$(dirname "$RAW_DB")
if [ -w "$DB_DIR" ] && touch "${RAW_DB}.test" 2>/dev/null; then
    rm -f "${RAW_DB}.test"
    echo "[container-proxy] [${SESSION_ID}] ✅ Trace database location is writable" | tee -a /tmp/container_mitm.log
else
    echo "[container-proxy] [${SESSION_ID}] ❌ WARNING: Cannot write to trace database location" | tee -a /tmp/container_mitm.log
    echo "[container-proxy] [${SESSION_ID}] Path: $DB_DIR" | tee -a /tmp/container_mitm.log
    echo "[container-proxy] [${SESSION_ID}] Creating directory and setting permissions..." | tee -a /tmp/container_mitm.log

    # Try to create directory and set permissions
    mkdir -p "$DB_DIR" 2>/dev/null || true
    chmod 755 "$DB_DIR" 2>/dev/null || true

    # Try again
    if touch "${RAW_DB}.test" 2>/dev/null; then
        rm -f "${RAW_DB}.test"
        echo "[container-proxy] [${SESSION_ID}] ✅ Fixed database location" | tee -a /tmp/container_mitm.log
    else
        echo "[container-proxy] [${SESSION_ID}] ❌ CRITICAL: Cannot fix database location" | tee -a /tmp/container_mitm.log
    fi
fi

# Check if tracer script exists
if [ -f "/app/src/local_tracing/mitm_tracer.py" ]; then
    echo "[container-proxy] [${SESSION_ID}] ✅ MITM tracer script found" | tee -a /tmp/container_mitm.log
else
    echo "[container-proxy] [${SESSION_ID}] ❌ WARNING: MITM tracer script not found" | tee -a /tmp/container_mitm.log
fi

echo "[container-proxy] [${SESSION_ID}] Container tracing environment configured for session ${SESSION_ID}" | tee -a /tmp/container_mitm.log
echo "[container-proxy] [${SESSION_ID}] Ready to capture codex-synth API calls" | tee -a /tmp/container_mitm.log
