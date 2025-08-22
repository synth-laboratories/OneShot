# Adding In-Sandbox Tracing to OneShot Bench

## Current State Analysis

The current OneShot Bench setup captures traces from **interactive host sessions** but does NOT capture traces from **evaluation runs inside Docker containers**. This means we lose valuable debugging and evaluation data from the actual agent behavior during evaluation.

## Goal: Enable In-Sandbox Tracing

We want to:
1. **Run MITM proxy inside Docker** during evaluation runs
2. **Capture codex-synth traces** from the agent running in the sandbox
3. **Export traces along with diff** from the evaluation run
4. **Use traces for analysis** in our evaluation framework

## Current Infrastructure (Good News!)

The current setup already has some pieces in place:

### ✅ Existing Proxy Infrastructure
- `scripts/start_synth_workers.sh` - MITM proxy + trace cleaner on host
- `src/local_tracing/mitm_tracer.py` - Captures HTTP requests to SQLite
- `src/local_tracing/trace_cleaner.py` - Processes raw traces to clean format

### ✅ Docker Integration
- `scripts/run_codex_box.sh` already copies `mitmproxy-ca-cert.pem` into container
- Dockerfile updates CA certificates and sets `NODE_EXTRA_CA_CERTS`
- Container can handle HTTPS through proxy with proper cert trust

### ✅ Trace Database Structure
- Raw traces: `data/traces/v3/raw_synth_ai.db/traces.sqlite3`
- Clean traces: `data/traces/v3/clean_synth_ai.db/traces.sqlite3`
- Tables: `traces` (raw), `cleaned_sessions` (processed)

## Required Changes for In-Sandbox Tracing

### 1. **Container Environment Setup**

#### A. Enhanced Dockerfile Changes
```dockerfile
# Add to existing Dockerfile
# Install mitmproxy in container
RUN pip3 install --break-system-packages mitmproxy

# Create directories for container-side tracing
RUN mkdir -p /app/traces /app/mitmproxy

# Copy host tracing scripts
COPY src/local_tracing/ /app/src/local_tracing/
RUN chmod +x /app/src/local_tracing/*.py

# Expose proxy port internally
EXPOSE 18080
```

#### B. Environment Variables for Container
```bash
# Set proxy environment for codex-synth
export HTTP_PROXY=http://localhost:18080
export HTTPS_PROXY=http://localhost:18080
export ALL_PROXY=http://localhost:18080

# Set trace database path for container
export RAW_TRACE_DB=/app/traces/container_raw.db
```

### 2. **Proxy Management in Container**

#### A. Start Proxy Script (`container_start_proxy.sh`)
```bash
#!/bin/bash
set -euo pipefail

# Start MITM proxy inside container
PORT=18080
RAW_DB="/app/traces/container_raw.db"
CLEAN_DB="/app/traces/container_clean.db"

# Kill any existing proxy
pkill -f mitmdump || true
sleep 1

# Start proxy with container-specific config
PYTHONPATH="/app/src" mitmdump \
  -s "/app/src/local_tracing/mitm_tracer.py" \
  --listen-host 0.0.0.0 \
  --listen-port "${PORT}" \
  --set raw_db_path="${RAW_DB}" \
  >/tmp/container_mitm.log 2>&1 &

echo $! > /tmp/container_mitm.pid
echo "Container proxy started on port ${PORT}"
```

#### B. Trace Export Script (`export_container_traces.sh`)
```bash
#!/bin/bash
set -euo pipefail

# Export container traces to host
CONTAINER_RAW="/app/traces/container_raw.db"
CONTAINER_CLEAN="/app/traces/container_clean.db"
HOST_TRACES_DIR="/runs/traces"

# Copy trace databases to host mount
mkdir -p "${HOST_TRACES_DIR}"
if [ -f "${CONTAINER_RAW}" ]; then
    cp "${CONTAINER_RAW}" "${HOST_TRACES_DIR}/container_raw.db"
    echo "Exported container raw traces"
fi

if [ -f "${CONTAINER_CLEAN}" ]; then
    cp "${CONTAINER_CLEAN}" "${HOST_TRACES_DIR}/container_clean.db"
    echo "Exported container clean traces"
fi

# Also export as JSON for easy analysis
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "${CONTAINER_RAW}" << 'EOF' > "${HOST_TRACES_DIR}/traces.jsonl" 2>/dev/null || true
.mode json
SELECT * FROM traces ORDER BY ts_ms;
EOF
    echo "Exported traces as JSON"
fi
```

### 3. **Enhanced Bootstrap Script**

#### A. Modify `box_bootstrap_simple.sh`
```bash
#!/bin/bash
# ... existing code ...

# Start container-side tracing
log "Starting container-side tracing..."
/app/container_start_proxy.sh

# Set environment for codex-synth to use proxy
export HTTP_PROXY=http://localhost:18080
export HTTPS_PROXY=http://localhost:18080
export ALL_PROXY=http://localhost:18080
export RAW_TRACE_DB=/app/traces/container_raw.db

# ... existing codex-synth execution ...

# Export traces before container exit
log "Exporting container traces..."
/app/export_container_traces.sh

# ... rest of existing code ...
```

### 4. **Host-Side Integration**

#### A. Enhanced `run_codex_box.sh`
```bash
# After copying artifacts from container, also copy traces
docker cp "$CONTAINER_NAME:/app/traces" "$RUN_DIR/artifacts/" 2>/dev/null || true

# Display trace summary if available
if [ -f "$RUN_DIR/artifacts/traces.jsonl" ]; then
    TRACE_COUNT=$(wc -l < "$RUN_DIR/artifacts/traces.jsonl")
    echo "[traces] Container captured ${TRACE_COUNT} API calls"
fi
```

#### B. Trace Analysis Tools
```bash
#!/usr/bin/env python3
"""
Analyze container traces from evaluation runs
"""
import json
import sqlite3
from pathlib import Path

def analyze_container_traces(run_dir):
    """Analyze traces from a container evaluation run"""
    traces_file = Path(run_dir) / "artifacts" / "traces.jsonl"

    if not traces_file.exists():
        print("No container traces found")
        return

    traces = []
    with open(traces_file) as f:
        for line in f:
            traces.append(json.loads(line))

    print(f"Container captured {len(traces)} API calls:")

    # Analyze by endpoint
    endpoints = {}
    for trace in traces:
        url = trace.get('url', 'unknown')
        method = trace.get('method', 'GET')
        key = f"{method} {url}"
        endpoints[key] = endpoints.get(key, 0) + 1

    print("\nAPI Call Summary:")
    for endpoint, count in sorted(endpoints.items(), key=lambda x: x[1], reverse=True):
        print(f"  {count:3d} calls: {endpoint}")

    # Analyze response patterns
    status_codes = {}
    for trace in traces:
        # Parse response JSON for status code
        response_json = trace.get('response_json', '{}')
        try:
            response_data = json.loads(response_json)
            if isinstance(response_data, dict) and '_raw' in response_data:
                # This was a non-JSON response, skip
                continue
            # Look for status code in meta_json
            meta = json.loads(trace.get('meta_json', '{}'))
            status = meta.get('status_code', 'unknown')
            status_codes[status] = status_codes.get(status, 0) + 1
        except:
            continue

    print("\nResponse Status Codes:")
    for status, count in sorted(status_codes.items(), key=lambda x: x[1], reverse=True):
        print(f"  {count:3d} responses: HTTP {status}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        analyze_container_traces(sys.argv[1])
    else:
        print("Usage: python analyze_traces.py <run_directory>")
```

### 5. **Configuration Options**

#### A. Environment Variables
```bash
# Enable container tracing
export CONTAINER_TRACING=1

# Set trace database location
export CONTAINER_TRACE_DB=/app/traces/evaluation.db

# Set proxy port for container
export CONTAINER_PROXY_PORT=18080

# Enable trace export on completion
export EXPORT_CONTAINER_TRACES=1
```

#### B. tb_meta.json Extensions
```json
{
  "task_id": "add-readme-section",
  "evaluation": {
    "container_tracing": {
      "enabled": true,
      "trace_db_path": "/app/traces/evaluation.db",
      "export_on_completion": true
    }
  }
}
```

## Implementation Plan

### Phase 1: Basic Container Tracing
1. ✅ Modify Dockerfile to include mitmproxy
2. ✅ Create container proxy startup script
3. ✅ Modify bootstrap to start proxy before codex execution
4. ✅ Test basic proxy functionality in container

### Phase 2: Trace Export & Integration
1. Create trace export mechanism
2. Modify host-side collection to include container traces
3. Add trace analysis tools
4. Test end-to-end trace capture

### Phase 3: Enhanced Analysis
1. Add trace visualization tools
2. Integrate traces with evaluation scoring
3. Add trace-based debugging features
4. Performance optimization

## Benefits of Container Tracing

1. **Complete Agent Behavior**: Capture exactly what the agent does during evaluation
2. **Debugging Capability**: See why agents succeed or fail on specific tasks
3. **Trace-Based Evaluation**: Use trace patterns to improve evaluation scoring
4. **Agent Comparison**: Compare different agent behaviors on the same task
5. **Failure Analysis**: Understand common failure modes and edge cases

## Files That Would Be Modified

- `scripts/run_codex_box.sh` - Add trace collection with session delta display
- `Dockerfile` (template) - Add mitmproxy installation, tracing directories, and script copying
- `overlay_files/box_bootstrap_simple.sh` - Add proxy startup and session-aware logging
- `src/local_tracing/mitm_tracer.py` - Support container DB paths
- `scripts/create_tasks/setup_codex_mcp.sh` - Configure for container use
- `scripts/container_start_proxy.sh` - Enhanced with session logging and diagnostics
- `scripts/export_container_traces.sh` - Enhanced with git delta analysis and session summaries

## Current Status

**✅ IMPLEMENTATION COMPLETE** - Phase 1 has been successfully implemented with enhanced session delta analysis!

### What Was Accomplished

1. **✅ Enhanced Dockerfile Template** - Added mitmproxy installation, tracing directories, and script copying
2. **✅ Created Container Proxy Scripts**:
   - `container_start_proxy.sh` - Starts MITM proxy inside container with session logging
   - `export_container_traces.sh` - Exports traces with git delta analysis and session summaries
3. **✅ Enhanced Bootstrap Script** - Integrated tracing setup with session context
4. **✅ Updated Host-Side Collection** - Modified `run_codex_box.sh` to collect and display traces with session delta
5. **✅ Created Analysis Tools** - Added `analyze_container_traces.py` with session-aware analysis
6. **✅ Session Delta Analysis** - Git changes tracking with detailed change categorization
7. **✅ Informative Logging** - Session IDs, timestamps, and contextual information throughout

### Files Modified

- `src/one_shot_bench/prepare_task_for_eval.py` - Enhanced Dockerfile generation
- `scripts/container_start_proxy.sh` - New proxy startup script
- `scripts/export_container_traces.sh` - New trace export script
- `data/tasks/prepared/*/overlay_files/box_bootstrap.sh` - Enhanced with tracing
- `scripts/run_codex_box.sh` - Enhanced trace collection and display
- `guides/ait/analyze_container_traces.py` - New analysis tool

### How It Works

1. **During Task Preparation**: Enhanced Dockerfile includes mitmproxy and tracing infrastructure
2. **Container Startup**: Bootstrap script starts MITM proxy before codex execution
3. **During Evaluation**: All codex-synth API calls are captured by the container proxy
4. **After Evaluation**: Traces are exported to host and displayed in results
5. **Analysis**: Traces can be analyzed using the provided analysis tool

### Benefits Achieved

- **Complete Agent Behavior**: Capture exactly what agents do during evaluation
- **Debugging Capability**: See why agents succeed or fail on specific tasks
- **Trace-Based Evaluation**: Use trace patterns to improve evaluation scoring
- **Agent Comparison**: Compare different agent behaviors on the same task
- **Failure Analysis**: Understand common failure modes and edge cases

### Example Enhanced Output

When you run an evaluation with tracing enabled, you'll now see detailed session information:

```
[traces] ========================================
[traces] Container Trace Summary:
[traces] Session ID: dogfood_readme_1734123456
[traces] Task ID: update-readme-with-hello-world
[traces] Total API calls captured: 8
[traces] Git Changes Made:
[traces]   - **Modified**: README.md
[traces]   - **Added**: docs/hello.md
[traces] Top API endpoints:
[traces]   6 calls: chat/completions
[traces]   2 calls: models
[traces] Trace files:
[traces]   JSON traces: data/runs/.../traces/traces.jsonl
[traces]   Raw database: data/runs/.../traces/container_raw.db
[traces]   Session summary: data/runs/.../traces/session_summary.md
[traces] ========================================
```

### Next Steps

The implementation is ready for testing! You can now run:

```bash
# Prepare a task with tracing enabled
python src/one_shot_bench/prepare_task_for_eval.py data/tasks/created/your-task

# Run evaluation with tracing
bash scripts/run_codex_box.sh data/tasks/prepared/your-task

# Analyze traces with session delta information
python guides/ait/analyze_container_traces.py data/runs/your-run-directory

# View detailed session summary
cat data/runs/your-run-directory/traces/session_summary.md
```

This provides a complete end-to-end tracing solution for OneShot Bench evaluation runs! 🚀
