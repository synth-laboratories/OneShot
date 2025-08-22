#!/bin/bash
set -euo pipefail

# Export container traces to host mount with session delta analysis
# This script is called from box_bootstrap.sh after codex execution

RAW_DB="${RAW_TRACE_DB:-/app/traces/container_raw.db}"
CLEAN_DB="${CLEAN_DB:-/app/traces/container_clean.db}"
HOST_TRACES_DIR="${HOST_TRACES_DIR:-/runs/traces}"

# Also export to artifacts directory for inclusion in final task record
ARTIFACTS_TRACES_DIR="/app/artifacts/traces"
SESSION_ID="${RUN_ID:-unknown_session}"
TIMESTAMP=$(date '+%Y-%m-%dT%H:%M:%SZ')

echo "[trace-export] [${SESSION_ID}] Starting trace export at ${TIMESTAMP}..."

# Create host traces directory
mkdir -p "${HOST_TRACES_DIR}"

# Create artifacts traces directory for final task record
mkdir -p "${ARTIFACTS_TRACES_DIR}"

# Session info
echo "[trace-export] [${SESSION_ID}] Session Information:" | tee "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt"
echo "  Session ID: ${SESSION_ID}" | tee -a "${HOST_TRACES_DIR}/session_info.txt"
echo "  Export Time: ${TIMESTAMP}" | tee -a "${HOST_TRACES_DIR}/session_info.txt"
echo "  Task ID: ${TASK_ID:-unknown}" | tee -a "${HOST_TRACES_DIR}/session_info.txt"
echo "  Container: $(hostname)" | tee -a "${HOST_TRACES_DIR}/session_info.txt"

# Analyze git changes (delta)
echo "[trace-export] [${SESSION_ID}] Analyzing session delta..." | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt"

cd /app/repo
GIT_CHANGES=$(git status --porcelain)
if [ -n "${GIT_CHANGES}" ]; then
    echo "[trace-export] [${SESSION_ID}] Git Changes Summary:" | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt"
    echo "${GIT_CHANGES}" | while read -r line; do
        status="${line:0:2}"
        file="${line:2}"
        case "${status}" in
            "M ") echo "  Modified: ${file}" | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt" ;;
            "A ") echo "  Added: ${file}" | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt" ;;
            "D ") echo "  Deleted: ${file}" | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt" ;;
            "R ") echo "  Renamed: ${file}" | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt" ;;
            "??" ) echo "  Untracked: ${file}" | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt" ;;
            *) echo "  Other (${status}): ${file}" | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt" ;;
        esac
    done
else
    echo "[trace-export] [${SESSION_ID}] No git changes detected" | tee -a "${HOST_TRACES_DIR}/session_info.txt" "${ARTIFACTS_TRACES_DIR}/session_info.txt"
fi

# Copy raw trace database if it exists
if [ -f "${RAW_DB}" ]; then
    cp "${RAW_DB}" "${HOST_TRACES_DIR}/container_raw.db"
    cp "${RAW_DB}" "${ARTIFACTS_TRACES_DIR}/container_raw.db"
    DB_SIZE=$(du -h "${RAW_DB}" | cut -f1)
    echo "[trace-export] [${SESSION_ID}] ✓ Exported raw traces (${DB_SIZE})"
else
    echo "[trace-export] [${SESSION_ID}] ! No raw trace database found at ${RAW_DB}"
fi

# Export traces as JSON for easy analysis
if [ -f "${RAW_DB}" ] && command -v sqlite3 >/dev/null 2>&1; then
    echo "[trace-export] [${SESSION_ID}] Converting traces to JSON format..."

    # Try to export traces with timeout to avoid hanging
    timeout 30 sqlite3 "${RAW_DB}" << 'EOF' > "${HOST_TRACES_DIR}/traces.jsonl" 2>&1
.mode json
SELECT * FROM traces ORDER BY ts_ms;
EOF

    # Check if the export was successful
    if [ $? -eq 0 ] && [ -s "${HOST_TRACES_DIR}/traces.jsonl" ]; then
        # Copy JSON to artifacts as well
        cp "${HOST_TRACES_DIR}/traces.jsonl" "${ARTIFACTS_TRACES_DIR}/traces.jsonl" 2>/dev/null || true
        TRACE_COUNT=$(wc -l < "${HOST_TRACES_DIR}/traces.jsonl" 2>/dev/null || echo "0")
        echo "[trace-export] [${SESSION_ID}] ✓ Exported ${TRACE_COUNT} traces as JSON"
    else
        echo "[trace-export] [${SESSION_ID}] ⚠️  Trace export failed or database is locked"
        TRACE_COUNT="0"

        # Create empty JSON file as placeholder
        echo "[]" > "${HOST_TRACES_DIR}/traces.jsonl"
        cp "${HOST_TRACES_DIR}/traces.jsonl" "${ARTIFACTS_TRACES_DIR}/traces.jsonl" 2>/dev/null || true
    fi
else
    echo "[trace-export] [${SESSION_ID}] ⚠️  No raw database or sqlite3 not available"
    TRACE_COUNT="0"
fi

# Copy proxy logs
if [ -f /tmp/container_mitm.log ]; then
    cp /tmp/container_mitm.log "${HOST_TRACES_DIR}/container_mitm.log"
    cp /tmp/container_mitm.log "${ARTIFACTS_TRACES_DIR}/container_mitm.log"
    LOG_SIZE=$(wc -l < /tmp/container_mitm.log 2>/dev/null || echo "0")
    echo "[trace-export] [${SESSION_ID}] ✓ Exported proxy logs (${LOG_SIZE} lines)"
fi

# Enhanced trace analysis with session context
if [ -f "${HOST_TRACES_DIR}/traces.jsonl" ] && [ "${TRACE_COUNT}" -gt 0 ]; then
    echo "[trace-export] [${SESSION_ID}] Session ${SESSION_ID} captured ${TRACE_COUNT} API calls" | tee -a "${HOST_TRACES_DIR}/session_info.txt"

    # Detailed trace analysis
    if command -v jq >/dev/null 2>&1; then
        echo "[trace-export] [${SESSION_ID}] Detailed Trace Analysis:" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        echo "  Session ID: ${SESSION_ID}" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        echo "  Total API calls: ${TRACE_COUNT}" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        echo "  Export timestamp: ${TIMESTAMP}" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"

        # Count by method with percentages
        echo "  API Methods:" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        jq -r '.method' "${HOST_TRACES_DIR}/traces.jsonl" | sort | uniq -c | sort -nr | head -5 | \
        while read count method; do
            percentage=$(echo "scale=1; ${count} * 100 / ${TRACE_COUNT}" | bc -l 2>/dev/null || echo "0")
            echo "    ${method}: ${count} calls (${percentage}%)" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        done

        # Top endpoints with session context
        echo "  Top Endpoints:" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        jq -r '.url' "${HOST_TRACES_DIR}/traces.jsonl" | \
            sed 's|https://api.openai.com/v1/||' | \
            sort | uniq -c | sort -nr | head -5 | \
        while read count endpoint; do
            percentage=$(echo "scale=1; ${count} * 100 / ${TRACE_COUNT}" | bc -l 2>/dev/null || echo "0")
            echo "    ${endpoint}: ${count} calls (${percentage}%)" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        done

        # Response analysis
        echo "  Response Status Codes:" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        jq -r '.meta_json | fromjson | .status_code' "${HOST_TRACES_DIR}/traces.jsonl" 2>/dev/null | \
            sort | uniq -c | sort -nr | head -5 | \
        while read count status; do
            [ "${status}" != "null" ] && echo "    HTTP ${status}: ${count} responses" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        done

        # Agent behavior insights
        echo "  Agent Behavior Insights:" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        if [ "${TRACE_COUNT}" -gt 0 ]; then
            echo "    ✓ Agent actively used AI capabilities" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        fi
        if [ "${TRACE_COUNT}" -gt 5 ]; then
            echo "    ✓ Agent made multiple API calls (iterative behavior)" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        fi

        # Calculate session duration from traces
        FIRST_CALL=$(jq -r '.[0].ts_ms' "${HOST_TRACES_DIR}/traces.jsonl" 2>/dev/null)
        LAST_CALL=$(jq -r '.[-1].ts_ms' "${HOST_TRACES_DIR}/traces.jsonl" 2>/dev/null)
        if [ -n "${FIRST_CALL}" ] && [ -n "${LAST_CALL}" ] && [ "${FIRST_CALL}" != "null" ] && [ "${LAST_CALL}" != "null" ]; then
            SESSION_DURATION=$(( (LAST_CALL - FIRST_CALL) / 1000 ))
            echo "    ⏱️  Session duration: ${SESSION_DURATION} seconds" | tee -a "${HOST_TRACES_DIR}/trace_summary.txt"
        fi

    fi
else
    echo "[trace-export] [${SESSION_ID}] No traces were captured during session ${SESSION_ID}" | tee -a "${HOST_TRACES_DIR}/session_info.txt"
fi

# Create comprehensive session summary in both locations
cat > "${HOST_TRACES_DIR}/session_summary.md" << EOF
# Session Summary: ${SESSION_ID}

## Session Information
- **Session ID**: ${SESSION_ID}
- **Task ID**: ${TASK_ID:-unknown}
- **Export Time**: ${TIMESTAMP}
- **API Calls**: ${TRACE_COUNT}

## Git Changes Summary
$(if [ -n "${GIT_CHANGES}" ]; then
    echo "${GIT_CHANGES}" | while read -r line; do
        status="\${line:0:2}"
        file="\${line:2}"
        case "\${status}" in
            "M ") echo "- **Modified**: \${file}" ;;
            "A ") echo "- **Added**: \${file}" ;;
            "D ") echo "- **Deleted**: \${file}" ;;
            "R ") echo "- **Renamed**: \${file}" ;;
            "??" ) echo "- **Untracked**: \${file}" ;;
            *) echo "- **Other** (\${status}): \${file}" ;;
        esac
    done
else
    echo "- No git changes detected"
fi)

## Files Exported
- \`container_raw.db\` - Raw SQLite trace database
- \`traces.jsonl\` - JSON-formatted traces
- \`container_mitm.log\` - MITM proxy logs
- \`session_info.txt\` - Session metadata
- \`trace_summary.txt\` - Detailed trace analysis

---
*Generated by OneShot Bench container tracing system*
EOF

# Copy session summary to artifacts
cp "${HOST_TRACES_DIR}/session_summary.md" "${ARTIFACTS_TRACES_DIR}/session_summary.md"

# Copy analysis files to artifacts if they exist
if [ -f "${HOST_TRACES_DIR}/trace_summary.txt" ]; then
    cp "${HOST_TRACES_DIR}/trace_summary.txt" "${ARTIFACTS_TRACES_DIR}/trace_summary.txt"
fi

if [ -f "${HOST_TRACES_DIR}/detailed_analysis.json" ]; then
    cp "${HOST_TRACES_DIR}/detailed_analysis.json" "${ARTIFACTS_TRACES_DIR}/detailed_analysis.json"
fi

echo "[trace-export] [${SESSION_ID}] ✓ Created session summary at ${HOST_TRACES_DIR}/session_summary.md"
echo "[trace-export] [${SESSION_ID}] ✓ All trace files included in final task record"
echo "[trace-export] [${SESSION_ID}] Trace export completed for session ${SESSION_ID}"
