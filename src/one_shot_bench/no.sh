#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../../scripts/run_codex_box.sh" "$@"

# Source .env file at repository root if it exists
ENV_FILE="${SCRIPT_DIR}/../../../.env"
if [ -f "$ENV_FILE" ]; then
    echo "Loading environment from $ENV_FILE"
    # Temporarily disable strict mode for sourcing .env
    set +u
    set -a  # Export all variables
    source "$ENV_FILE" 2>/dev/null || true
    set +a  # Stop exporting
    set -u  # Re-enable strict mode
fi

# Configuration
TASK_PATH="${1:-}"
TIMEOUT="${2:-1800}"
TOKEN_LIMIT="${3:-100000}"
PROXY_PORT="${PROXY_PORT:-18080}"

# Show usage
if [ -z "$TASK_PATH" ]; then
    cat <<EOF
Usage: $0 <task_path> [timeout] [token_limit]

Arguments:
  task_path    Path to task directory containing tb_meta.json
  timeout      Timeout in seconds (default: 1800)
  token_limit  Token limit (default: 100000)

Environment:
  OPENAI_API_KEY    Required. Your OpenAI API key
  PROXY_PORT        MITM proxy port (default: 18080)
  CODEX_MODEL       Model to use (default: claude-sonnet)
  MAX_TURNS         Max conversation turns (default: 50)

Example:
  $0 ../../data/tasks/prepared/add-lm-tracing-readme 600 50000
EOF
    exit 1
fi

# Normalize and optionally auto-prepare created tasks to prepared format
TASK_PATH="$(cd "$TASK_PATH" && pwd)"
if [ ! -f "$TASK_PATH/tb_meta.json" ]; then
    log "ERROR: No tb_meta.json found in $TASK_PATH"
    exit 1
fi

# If this looks like a created task (no Dockerfile), convert it to prepared automatically
if [ ! -f "$TASK_PATH/Dockerfile" ]; then
    log "Detected created task. Preparing for evaluation..."
    # Derive prepared dir name from task_id with timestamp removed
    TASK_ID_FROM_META=$(json_extract "$TASK_PATH/tb_meta.json" ".task_id")
    BASE_NAME=$(python3 - << 'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
tid = json.load(open(p)) .get("task_id", "")
print(tid.rsplit("_", 2)[0] if tid else "")
PY
"$TASK_PATH/tb_meta.json")
    if [ -z "$BASE_NAME" ]; then
        BASE_NAME="$(basename "$TASK_PATH")"
    fi
    PREPARED_DIR="${REPO_ROOT}/data/tasks/prepared/${BASE_NAME}"
    if [ -d "$PREPARED_DIR" ] && [ -f "$PREPARED_DIR/tb_meta.json" ]; then
        log "Prepared task already exists: $PREPARED_DIR"
    else
        log "Preparing task into: $PREPARED_DIR"
        if ! uv run python "${SCRIPT_DIR}/prepare_task_for_eval.py" "$TASK_PATH"; then
            log "ERROR: Failed to prepare created task at $TASK_PATH"
            exit 1
        fi
    fi
    TASK_PATH="$PREPARED_DIR"
fi

# Check prerequisites
log "Checking prerequisites..."
check_prerequisites || exit 1

# Try to find the Codex API key automatically
if [ -z "${OPENAI_API_KEY:-}" ]; then
    log "Looking for Codex API key..."
    
    # First check if there's an API key in auth.json
    if [ -f "$HOME/.codex/auth.json" ]; then
        API_KEY_FROM_AUTH=$(jq -r '.api_key // .OPENAI_API_KEY // empty' "$HOME/.codex/auth.json" 2>/dev/null || true)
        if [ -n "$API_KEY_FROM_AUTH" ] && [ "$API_KEY_FROM_AUTH" != "null" ]; then
            export OPENAI_API_KEY="$API_KEY_FROM_AUTH"
            log "Found API key in ~/.codex/auth.json"
        fi
    fi
    
    # If still no key, try to extract it from a running codex process or recent requests
    if [ -z "${OPENAI_API_KEY:-}" ]; then
        # Check recent proxy logs for successful requests with sk-proj keys
        if [ -f "/tmp/codex_mitm.out" ]; then
            EXTRACTED_KEY=$(grep -o 'Bearer sk-proj-[A-Za-z0-9_-]*' /tmp/codex_mitm.out 2>/dev/null | tail -1 | cut -d' ' -f2)
            if [ -n "$EXTRACTED_KEY" ]; then
                export OPENAI_API_KEY="$EXTRACTED_KEY"
                log "Extracted API key from proxy logs"
            fi
        fi
    fi
    
    # If still no key, warn the user
    if [ -z "${OPENAI_API_KEY:-}" ]; then
        log "⚠️  No API key found. Codex may use OAuth tokens which lack proper scopes."
        log "To fix: Either set OPENAI_API_KEY or ensure codex login creates an API key"
    else
        log "✅ Using API key: ${OPENAI_API_KEY:0:7}...${OPENAI_API_KEY: -4}"
    fi
fi

# Generate run ID and create directories
RUN_ID=$(generate_run_id)
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_DIR="${REPO_ROOT}/data/runs/${RUN_ID}"
mkdir -p "$RUN_DIR"/{logs,artifacts}

log "Starting run: $RUN_ID"
log "Task: $(basename "$TASK_PATH")"
log "Timeout: ${TIMEOUT}s, Token limit: $TOKEN_LIMIT"

# Save run metadata
cat > "$RUN_DIR/metadata.json" <<EOF
{
  "run_id": "$RUN_ID",
  "task_path": "$TASK_PATH",
  "task_id": "$(basename "$TASK_PATH")",
  "timeout": $TIMEOUT,
  "token_limit": $TOKEN_LIMIT,
  "proxy_port": $PROXY_PORT,
  "start_time": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

# Check if proxy is running
PROXY_HOST=$(get_proxy_host)
if curl -s -x "http://localhost:${PROXY_PORT}" https://api.openai.com/v1/models >/dev/null 2>&1; then
    log "✅ Proxy detected on port $PROXY_PORT"
    PROXY_URL="http://${PROXY_HOST}:${PROXY_PORT}"
    
    # Capture initial proxy state
    if [ -f "/tmp/codex_mitm.out" ]; then
        tail -100 /tmp/codex_mitm.out > "$RUN_DIR/logs/host_proxy_initial.txt" 2>/dev/null || true
    fi
else
    log "⚠️  No proxy on port $PROXY_PORT - API calls won't be captured"
    PROXY_URL=""
fi

# Copy mitmproxy CA certificate if it exists
if [ -f "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" ]; then
    log "Copying mitmproxy CA certificate to build context..."
    cp "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" "$TASK_PATH/mitmproxy-ca-cert.pem"
else
    log "⚠️  No mitmproxy CA certificate found at ~/.mitmproxy/mitmproxy-ca-cert.pem"
fi

# Copy .env file to build context if available
if [ -f "$ENV_FILE" ]; then
    log "Copying .env file to build context..."
    cp "$ENV_FILE" "$TASK_PATH/.env"
else
    log "⚠️  No .env file found at $ENV_FILE"
    # Create empty .env file to prevent Docker build failure
    touch "$TASK_PATH/.env"
fi

# Copy local codex installation to build context
log "Copying local codex installation..."
CODEX_PATH=$(which codex)
if [ -n "$CODEX_PATH" ]; then
    # Resolve the actual codex package location
    CODEX_REAL_PATH=$(realpath "$CODEX_PATH")
    CODEX_PACKAGE_PATH=$(dirname $(dirname "$CODEX_REAL_PATH"))
    
    # Check common locations for the @openai/codex package
    if [ -d "$CODEX_PACKAGE_PATH/lib/node_modules/@openai/codex" ]; then
        CODEX_PACKAGE_PATH="$CODEX_PACKAGE_PATH/lib/node_modules/@openai/codex"
    elif [ -d "$CODEX_PACKAGE_PATH/@openai/codex" ]; then
        CODEX_PACKAGE_PATH="$CODEX_PACKAGE_PATH/@openai/codex"
    fi
    
    if [ -d "$CODEX_PACKAGE_PATH" ]; then
        log "Found codex at: $CODEX_PACKAGE_PATH"
        # Copy entire directory structure
        cp -r "$CODEX_PACKAGE_PATH" "$TASK_PATH/codex-files"
        log "✅ Copied codex installation"
    else
        log "ERROR: Could not find codex package directory!"
        exit 1
    fi
else
    log "ERROR: codex command not found!"
    exit 1
fi

# Build container
log "Building container..."
CONTAINER_NAME="codex_box_${RUN_ID}"
IMAGE_NAME="codex_box:${RUN_ID}"

log "Building image $IMAGE_NAME..."
log "This may take a few minutes on first run..."

# Build with real-time output
if ! docker build \
    --build-arg GIT_URL="$(json_extract "$TASK_PATH/tb_meta.json" ".repo.git_url")" \
    --build-arg GIT_BRANCH="$(json_extract "$TASK_PATH/tb_meta.json" ".repo.branch")" \
    --build-arg GIT_COMMIT="$(json_extract "$TASK_PATH/tb_meta.json" ".repo.start_commit_sha")" \
    --build-arg TASK_ID="$(basename "$TASK_PATH")" \
    -t "$IMAGE_NAME" \
    "$TASK_PATH" 2>&1 | tee "$RUN_DIR/logs/docker_build.log"; then
    
    log "ERROR: Container build failed!"
    log "Last 20 lines of error:"
    tail -20 "$RUN_DIR/logs/docker_build.log"
    exit 1
fi

log "✅ Container built successfully"

# Start container
log "Starting container..."
# Check if ~/.codex exists for mounting
CODEX_MOUNT=""
if [ -d "$HOME/.codex" ]; then
    log "Mounting ~/.codex for authentication..."
    CODEX_MOUNT="-v $HOME/.codex:/root/.codex"
else
    log "⚠️  No ~/.codex directory found - codex may not be authenticated"
fi

# Save initial MITM proxy state and record start time for trace queries
if [ -f "/tmp/codex_mitm.out" ]; then
    cp /tmp/codex_mitm.out "$RUN_DIR/logs/mitm_trace_start.txt"
fi

# Record session start time for synth trace queries
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUN_DIR/session_start_time.txt"

docker run \
    --name "$CONTAINER_NAME" \
    --rm \
    -d \
    --security-opt seccomp=unconfined \
    --security-opt apparmor=unconfined \
    --cap-add SYS_ADMIN \
    --cap-add SYS_PTRACE \
    -e "OPENAI_API_KEY=${OPENAI_API_KEY:-${CODEX_API_KEY:-}}" \
    -e "TASK_ID=$(basename "$TASK_PATH")" \
    -e "OPENAI_MODEL=${OPENAI_MODEL:-gpt-5-mini}" \
    -e "AGENT_TIMEOUT_SEC=${TIMEOUT}" \
    -e "CODEX_DISABLE_SANDBOX=1" \
    -e "CODEX_SANDBOX_DISABLE_LANDLOCK=1" \
    ${PROXY_URL:+-e "HTTP_PROXY=$PROXY_URL"} \
    ${PROXY_URL:+-e "HTTPS_PROXY=$PROXY_URL"} \
    ${PROXY_URL:+-e "ALL_PROXY=$PROXY_URL"} \
    ${PROXY_URL:+-e "NO_PROXY=localhost,127.0.0.1,::1"} \
    ${CODEX_MOUNT} \
    "$IMAGE_NAME" \
    > "$RUN_DIR/logs/container_id.txt"

if [ $? -ne 0 ]; then
    log "ERROR: Failed to start container"
    exit 1
fi

CONTAINER_ID=$(cat "$RUN_DIR/logs/container_id.txt")
log "✅ Container started: $CONTAINER_NAME"

# Start watchdog monitor
log "Starting watchdog monitor..."
python3 "${SCRIPT_DIR}/watchdog.py" \
    "$CONTAINER_NAME" \
    "$RUN_DIR" \
    --timeout "$TIMEOUT" \
    --token-limit "$TOKEN_LIMIT" \
    > "$RUN_DIR/logs/watchdog.log" 2>&1 &

WATCHDOG_PID=$!
log "Watchdog PID: $WATCHDOG_PID"

# Function to cleanup on exit
cleanup() {
    local exit_code=$?
    log "Cleaning up (exit code: $exit_code)..."
    
    # Clean up copied files from build context
    rm -rf "$TASK_PATH/codex-files" 2>/dev/null || true
    rm -f "$TASK_PATH/mitmproxy-ca-cert.pem" 2>/dev/null || true
    
    # Stop watchdog
    if [ -n "${WATCHDOG_PID:-}" ] && kill -0 $WATCHDOG_PID 2>/dev/null; then
        kill $WATCHDOG_PID 2>/dev/null || true
    fi
    
    # Capture final artifacts (even if container stopped)
    log "Capturing final artifacts..."
    
    # Try to get artifacts from container (running or stopped)
    if docker ps -a -q -f name="$CONTAINER_NAME" | grep -q .; then
        docker cp "$CONTAINER_NAME:/app/artifacts/." "$RUN_DIR/artifacts/" 2>/dev/null || log "Failed to copy artifacts"
        docker logs "$CONTAINER_NAME" > "$RUN_DIR/logs/container_full.log" 2>&1 || log "Failed to get container logs"
        
        # Also try to get the repo diff
        docker exec "$CONTAINER_NAME" bash -c "cd /app/repo && git diff HEAD" > "$RUN_DIR/artifacts/repo_diff.patch" 2>/dev/null || true
        docker exec "$CONTAINER_NAME" bash -c "cd /app/repo && git status" > "$RUN_DIR/artifacts/repo_status.txt" 2>/dev/null || true
    fi
    
    # Capture full MITM proxy trace
    if [ -f "/tmp/codex_mitm.out" ]; then
        cp /tmp/codex_mitm.out "$RUN_DIR/logs/mitm_trace_full.txt" 2>/dev/null || true
        
        # Extract just the new requests since container started
        if [ -f "$RUN_DIR/logs/mitm_trace_start.txt" ]; then
            start_lines=$(wc -l < "$RUN_DIR/logs/mitm_trace_start.txt")
            tail -n +$((start_lines + 1)) /tmp/codex_mitm.out > "$RUN_DIR/logs/mitm_trace_session.txt" 2>/dev/null || true
        fi
    fi
    
    # Capture synth trace databases
    TRACE_DIR="$SCRIPT_DIR/../../data/traces/v3"
    if [ -d "$TRACE_DIR" ]; then
        log "Capturing synth trace databases..."
        
        # Copy raw trace database
        if [ -f "$TRACE_DIR/raw_synth_ai.db/traces.sqlite3" ]; then
            cp "$TRACE_DIR/raw_synth_ai.db/traces.sqlite3" "$RUN_DIR/artifacts/raw_traces.sqlite3" 2>/dev/null || true
            log "Captured raw traces database"
        fi
        
        # Copy clean trace database
        if [ -f "$TRACE_DIR/clean_synth_ai.db/traces.sqlite3" ]; then
            cp "$TRACE_DIR/clean_synth_ai.db/traces.sqlite3" "$RUN_DIR/artifacts/clean_traces.sqlite3" 2>/dev/null || true
            log "Captured clean traces database"
        fi
        
        # Export traces for the most recent session
        if [ -f "$TRACE_DIR/raw_synth_ai.db/traces.sqlite3" ]; then
            # Get the most recent session ID from raw traces
            RECENT_SESSION=$(sqlite3 "$TRACE_DIR/raw_synth_ai.db/traces.sqlite3" \
                "SELECT session_id FROM traces ORDER BY ts_ms DESC LIMIT 1;" 2>/dev/null || echo "")
            
            if [ -n "$RECENT_SESSION" ]; then
                log "Found most recent session: $RECENT_SESSION"
                
                # Export all raw traces for this session
                sqlite3 "$TRACE_DIR/raw_synth_ai.db/traces.sqlite3" \
                    "SELECT json_object(
                        'id', id,
                        'ts_ms', ts_ms,
                        'session_id', session_id,
                        'provider', provider,
                        'model', model,
                        'request', json(request_json),
                        'response', json(response_json),
                        'meta', json(meta_json)
                    ) FROM traces 
                    WHERE session_id = '$RECENT_SESSION'
                    ORDER BY ts_ms ASC;" > "$RUN_DIR/artifacts/raw_session_traces.jsonl" 2>/dev/null || true
                
                RAW_COUNT=$(sqlite3 "$TRACE_DIR/raw_synth_ai.db/traces.sqlite3" \
                    "SELECT COUNT(*) FROM traces WHERE session_id = '$RECENT_SESSION';" 2>/dev/null || echo "0")
                log "Exported $RAW_COUNT raw traces for session $RECENT_SESSION"
                
                # Now clean the raw traces we just exported using the same logic as trace_cleaner
                if [ -f "$RUN_DIR/artifacts/raw_session_traces.jsonl" ] && command -v python3 >/dev/null 2>&1; then
                    log "Cleaning raw traces using trace_cleaner logic..."
                    
                    # First, rebuild the raw session from the JSONL exactly as trace_cleaner does
                    python3 << EOF > "$RUN_DIR/artifacts/clean_session_trace.json" 2>&1
import sys
import json
from datetime import datetime

# Add repo to path for the converter import
sys.path.insert(0, '$SCRIPT_DIR/../..')

try:
    # Read the raw traces JSONL we exported
    with open('$RUN_DIR/artifacts/raw_session_traces.jsonl', 'r') as f:
        traces = []
        for line in f:
            if line.strip():
                trace_obj = json.loads(line)
                # Rebuild the trace dict exactly as it was in the database
                traces.append({
                    "id": trace_obj["id"],
                    "ts_ms": trace_obj["ts_ms"],
                    "session_id": trace_obj["session_id"],
                    "provider": trace_obj["provider"],
                    "model": trace_obj["model"],
                    "request_json": json.dumps(trace_obj["request"]) if isinstance(trace_obj["request"], dict) else trace_obj["request"],
                    "response_json": json.dumps(trace_obj["response"]) if isinstance(trace_obj["response"], dict) else trace_obj["response"],
                    "meta_json": json.dumps(trace_obj["meta"]) if isinstance(trace_obj["meta"], dict) else trace_obj["meta"],
                })
    
    if not traces:
        print("No traces found", file=sys.stderr)
        sys.exit(1)
    
    # Build raw_session exactly as trace_cleaner.export_session_raw does
    raw_session = {
        "session_id": traces[0]["session_id"],
        "created_at": datetime.utcfromtimestamp(traces[0]["ts_ms"] / 1000).isoformat(),
        "traces": traces
    }
    
    # Use the exact same converter as trace_cleaner
    from development.codex_coach.convert_session_to_synth_v3 import build_converted_trace
    formatted = build_converted_trace(raw_session)
    
    # Output the formatted trace
    print(json.dumps(formatted, indent=2))
    
except Exception as e:
    import traceback
    print(f"Error: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
EOF
                    
                    if [ -s "$RUN_DIR/artifacts/clean_session_trace.json" ]; then
                        log "Successfully cleaned traces"
                    else
                        log "Failed to clean traces - check clean_session_trace.json for errors"
                    fi
                fi
            fi
        fi
    fi
    
    # Stop and remove container
    if docker ps -a -q -f name="$CONTAINER_NAME" | grep -q .; then
        docker stop "$CONTAINER_NAME" 2>/dev/null || true
        docker rm "$CONTAINER_NAME" 2>/dev/null || true
    fi
    
    # Generate summary
    generate_summary
    
    # Run evaluation to generate scoring results (use uv for synth-ai access)
    if [ -f "${SCRIPT_DIR}/evaluate_run.py" ] && [ -f "$TASK_PATH/tb_meta.json" ]; then
        log "Running evaluation to generate scoring report..."
        if uv run python "${SCRIPT_DIR}/evaluate_run.py" "$RUN_DIR" "$TASK_PATH"; then
            log "✅ Evaluation complete!"
            
            # Display the score from the evaluation
            if [ -f "$RUN_DIR/evaluation_results.json" ]; then
                SCORE=$(python3 -c "import json; print(f\"{json.load(open('$RUN_DIR/evaluation_results.json'))['evaluation']['total_score']:.0%}\")" 2>/dev/null || echo "N/A")
                log "📊 FINAL SCORE: $SCORE"
                log "📄 Scoring report: $RUN_DIR/scoring_results.md"
                
                # Display LLM evaluation results if present
                log ""
                log "========================================" 
                log "🤖 LLM Evaluation Results"
                log "========================================"
                
                # Check for container LLM evaluation
                if [ -f "$RUN_DIR/artifacts/tb_evaluation_results.json" ]; then
                    python3 -c "
import json
with open('$RUN_DIR/artifacts/tb_evaluation_results.json') as f:
    data = json.load(f)
    
# Check if LLM evaluation exists
if 'llm_evaluation' in data and data['llm_evaluation']:
    print('Container LLM Evaluation (gpt-4o-mini):')
    print('-' * 40)
    for rubric_id, rubric_data in data['llm_evaluation'].get('rubrics', {}).items():
        score = rubric_data.get('score', 0) * 100
        reasoning = rubric_data.get('reasoning', 'No reasoning provided')
        status = '✅' if score >= 100 else '⚠️' if score >= 50 else '❌'
        print(f'{status} {rubric_id}: {score:.0f}%')
        print(f'   → {reasoning}')
        print()
else:
    print('No container LLM evaluation found')
" 2>/dev/null || log "Could not parse container LLM results"
                fi
                
                # Check for host LLM evaluation
                if [ -f "$RUN_DIR/evaluation_results.json" ]; then
                    python3 -c "
import json
with open('$RUN_DIR/evaluation_results.json') as f:
    data = json.load(f)
    
# Check if host LLM evaluation exists
if 'lm_evaluation' in data and data['lm_evaluation']:
    print()
    print('Host LLM Evaluation (gpt-5-nano):')
    print('-' * 40)
    weighted_score = data['lm_evaluation'].get('weighted_score', 0) * 100
    print(f'Overall LLM Score: {weighted_score:.0f}%')
    print()
    
    for rubric in data['lm_evaluation'].get('rubric_scores', []):
        rubric_id = rubric.get('rubric_id', 'unknown')
        score = rubric.get('score', 0) * 100
        reasoning = rubric.get('reasoning', 'No reasoning provided')
        status = '✅' if score >= 100 else '⚠️' if score >= 50 else '❌'
        print(f'{status} {rubric_id}: {score:.0f}%')
        print(f'   → {reasoning}')
        if 'evidence' in rubric and rubric['evidence']:
            print(f'   Evidence: {rubric[\"evidence\"][0][:100]}...' if len(rubric['evidence'][0]) > 100 else f'   Evidence: {rubric[\"evidence\"][0]}')
        print()
" 2>/dev/null || log "Could not parse host LLM results"
                fi
                
                log "========================================"
            fi
        else
            log "⚠️  Evaluation failed"
        fi
    fi
    
    log "Run complete. Results in: $RUN_DIR"
}

# Generate run summary
generate_summary() {
    local end_time="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    
    # Check results
    local status="unknown"
    local exit_code="${exit_code:-1}"
    
    if [ -f "$RUN_DIR/logs/watchdog.log" ]; then
        # Get watchdog exit code
        wait $WATCHDOG_PID 2>/dev/null || true
        exit_code=$?
        
        case $exit_code in
            0) status="completed" ;;
            1) status="failed" ;;
            2) status="timeout" ;;
            3) status="token_limit" ;;
            4) status="container_died" ;;
            5) status="manual_stop" ;;
            *) status="unknown" ;;
        esac
    fi
    
    # Check for diff
    local has_changes="false"
    if [ -f "$RUN_DIR/artifacts/diff.patch" ] && [ -s "$RUN_DIR/artifacts/diff.patch" ]; then
        has_changes="true"
    fi
    
    # Check test results
    local tests_passed=0
    local tests_failed=0
    if [ -f "$RUN_DIR/artifacts/pytest.txt" ]; then
        tests_passed=$(grep -oE "[0-9]+ passed" "$RUN_DIR/artifacts/pytest.txt" | grep -oE "[0-9]+" | tail -1 || echo "0")
        tests_failed=$(grep -oE "[0-9]+ failed" "$RUN_DIR/artifacts/pytest.txt" | grep -oE "[0-9]+" | tail -1 || echo "0")
    fi
    
    # Create results JSON
    cat > "$RUN_DIR/results.json" <<EOF
{
  "run_id": "$RUN_ID",
  "task_id": "$(basename "$TASK_PATH")",
  "status": "$status",
  "exit_code": $exit_code,
  "start_time": "$(json_extract "$RUN_DIR/metadata.json" ".start_time")",
  "end_time": "$end_time",
  "has_changes": $has_changes,
  "tests": {
    "passed": $tests_passed,
    "failed": $tests_failed
  }
}
EOF
    
    # Create human-readable summary
    cat > "$RUN_DIR/summary.txt" <<EOF
========================================
Codex-in-the-Box Run Summary
========================================
Run ID:      $RUN_ID
Task:        $(basename "$TASK_PATH")
Status:      $status (exit code: $exit_code)
Start:       $(json_extract "$RUN_DIR/metadata.json" ".start_time")
End:         $end_time
Changes:     $has_changes
Tests:       $tests_passed passed, $tests_failed failed

Artifacts:
$(ls -la "$RUN_DIR/artifacts/" 2>/dev/null | tail -n +2 || echo "  (none)")

Logs:
$(ls -la "$RUN_DIR/logs/" 2>/dev/null | tail -n +2 || echo "  (none)")
========================================
EOF
    
    cat "$RUN_DIR/summary.txt"
}

# Set up cleanup trap
trap cleanup EXIT

# Wait for watchdog to complete
log "Monitoring container (timeout: ${TIMEOUT}s)..."
wait $WATCHDOG_PID
WATCHDOG_EXIT=$?

log "Watchdog exited with code: $WATCHDOG_EXIT"
exit $WATCHDOG_EXIT