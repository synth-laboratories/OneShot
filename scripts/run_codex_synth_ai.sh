#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/synth_models.sh"

# Load secrets from .env if present
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env"
  set +a
  echo "[env] Loaded environment variables from ${REPO_ROOT}/.env"
else
  echo "[env] No .env file found at ${REPO_ROOT}/.env"
fi

RUN_ID="${RUN_ID:-$(date +%Y%m%d__%H-%M-%S)}"
RUN_DIR="${REPO_ROOT}/data/runs/${RUN_ID}"

TASK_PATH_INPUT="${1:-}"
if [[ -z "$TASK_PATH_INPUT" ]]; then
    echo "Usage: $0 <path-to-task>"
    exit 1
fi

# Normalize to absolute path
if [[ "${TASK_PATH_INPUT}" != /* ]]; then
    TASK_PATH_INPUT="${REPO_ROOT}/${TASK_PATH_INPUT}"
fi

echo "[run_codex_synth_ai] Task path: ${TASK_PATH_INPUT}"

# Check if task exists
if [[ ! -d "$TASK_PATH_INPUT" ]]; then
    echo "❌ Error: Task directory not found: $TASK_PATH_INPUT"
    exit 1
fi

# Get model (default to synth-small)
MODEL="${OPENAI_MODEL:-synth-small}"
LOCAL_BACKEND_URL="${SYNTH_BASE_URL:-http://host.docker.internal:8000/api/synth-research}"

# Determine provider and wire_api based on URL
# If URL is OpenAI, use openai provider; otherwise use synth provider
USE_OPENAI_PROVIDER=false
if [[ "$LOCAL_BACKEND_URL" == "https://api.openai.com"* ]]; then
    USE_OPENAI_PROVIDER=true
fi

# Determine wire_api based on URL or explicit option
# If URL contains "/responses", use responses API, otherwise use chat
WIRE_API="${WIRE_API:-}"
if [[ -z "$WIRE_API" ]]; then
    if [[ "$LOCAL_BACKEND_URL" == *"/responses"* ]] || [[ "$LOCAL_BACKEND_URL" == *"/responses" ]]; then
        WIRE_API="responses"
    else
        WIRE_API="chat"  # Default to chat
    fi
fi

# For responses API, Codex appends /v1/responses to base_url
# So we need to remove /responses from the URL if present (regardless of how WIRE_API was set)
if [[ "$WIRE_API" == "responses" ]]; then
    LOCAL_BACKEND_URL="${LOCAL_BACKEND_URL%/responses}"
    LOCAL_BACKEND_URL="${LOCAL_BACKEND_URL%/responses/}"
fi
echo "[config] Wire API: ${WIRE_API} (detected from URL: ${SYNTH_BASE_URL:-http://host.docker.internal:8000/api/synth-research})"
echo "[config] Provider: ${USE_OPENAI_PROVIDER:+openai}${USE_OPENAI_PROVIDER:-synth}"
echo "[config] Base URL: ${LOCAL_BACKEND_URL} (Codex will append /v1/responses if wire_api=responses)"

# Check if local backend is running (skip for OpenAI endpoints)
if [[ "$USE_OPENAI_PROVIDER" != "true" ]]; then
    if ! curl -s -f "http://127.0.0.1:8000/health" > /dev/null 2>&1; then
        echo "⚠️  Warning: Local backend not reachable at http://127.0.0.1:8000/health"
        echo "   Make sure the backend is running:"
        echo "   cd /Users/joshpurtell/Documents/GitHub/monorepo/backend && nohup uv run uvicorn app.routes.main:app --reload --host 127.0.0.1 --port 8000 > /tmp/synth_backend.log 2>&1 &"
        exit 1
    fi
fi

# Check for required API keys based on provider
if [[ "$USE_OPENAI_PROVIDER" == "true" ]]; then
    # For OpenAI provider, use OPENAI_API_KEY
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
        echo "❌ Error: OPENAI_API_KEY not set (required for OpenAI provider)"
        echo "   Set it in ${REPO_ROOT}/.env or export it: export OPENAI_API_KEY=sk-..."
        exit 1
    fi
    echo "[env] Using OpenAI provider with OPENAI_API_KEY"
else
    # For synth provider, use SYNTH_API_KEY
    if [[ -z "${SYNTH_API_KEY:-}" ]]; then
        echo "❌ Error: SYNTH_API_KEY not set"
        echo "   Set it in ${REPO_ROOT}/.env or export it: export SYNTH_API_KEY=sk-synth-..."
        echo ""
        echo "   NOTE: The backend validates API keys against the database."
        echo "   For local dev, you need a valid Synth API key (not an OpenAI key)."
        exit 1
    fi
    
    # Verify SYNTH_API_KEY was loaded from .env
    echo "[env] SYNTH_API_KEY loaded: ${SYNTH_API_KEY:0:10}...${SYNTH_API_KEY: -4} (length: ${#SYNTH_API_KEY})"
    
    # For synth models, Codex uses OPENAI_API_KEY but backend expects SYNTH_API_KEY
    # So we set OPENAI_API_KEY to SYNTH_API_KEY (Codex will send it to backend)
    export OPENAI_API_KEY="${SYNTH_API_KEY}"
    echo "[env] Set OPENAI_API_KEY from SYNTH_API_KEY for Codex"
    
    # Verify the key looks like a Synth API key (starts with sk-synth- or sk-)
    if [[ ! "$SYNTH_API_KEY" =~ ^sk-(synth-|live_)? ]]; then
        echo "⚠️  Warning: SYNTH_API_KEY doesn't look like a Synth API key"
        echo "   Expected format: sk-synth-... or sk-..."
        echo "   Got prefix: ${SYNTH_API_KEY:0:10}..."
        echo ""
        echo "   The backend will validate this key against the database."
        echo "   If validation fails, you may need to:"
        echo "   1. Use a valid Synth API key"
        echo "   2. Modify the backend to bypass validation for local dev"
    fi
fi

# Check if Docker is running
if ! docker_is_running; then
    echo "❌ Error: Docker daemon is not running"
    exit 1
fi

# Check if task is prepared - has Dockerfile
if [[ ! -f "$TASK_PATH_INPUT/Dockerfile" ]]; then
    echo "⚠️  Warning: Task doesn't have Dockerfile. Preparing task..."
    export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"
    uv run python -m one_shot.prepare_task_for_eval "$TASK_PATH_INPUT"
    SLUG="$(basename "$TASK_PATH_INPUT")"
    TMP_SLUG="${SLUG%_*}"
    BASE_SLUG="${TMP_SLUG%_*}"
    if [[ -d "${REPO_ROOT}/data/tasks/prepared/${BASE_SLUG}" ]]; then
        TASK_PATH_INPUT="${REPO_ROOT}/data/tasks/prepared/${BASE_SLUG}"
    else
        TASK_PATH_INPUT="${REPO_ROOT}/data/tasks/prepared/${SLUG}"
    fi
    if [[ ! -d "$TASK_PATH_INPUT" ]]; then
        echo "❌ Error: Failed to prepare task"
        exit 1
    fi
    echo "✅ Prepared task at: $TASK_PATH_INPUT"
fi

echo "🚀 Running Codex with synth-ai runtime in Docker"
echo "   Model: ${MODEL}"
echo "   Backend: ${LOCAL_BACKEND_URL}"
echo ""

mkdir -p "$RUN_DIR"

# Build Docker image
echo "[build] Building Docker image..."
export DOCKER_BUILDKIT=1
BUILD_ARGS=(-t oneshot-task-synth-ai)
docker build "${BUILD_ARGS[@]}" "$TASK_PATH_INPUT"

# Prepare Codex config using synth-ai codex style
CODEX_HOME_DIR="${HOME}/.codex"
mkdir -p "$CODEX_HOME_DIR"

# Build config overrides using same logic as synth-ai codex
# Include wire_api configuration
if [[ "$USE_OPENAI_PROVIDER" == "true" ]]; then
    # Use OpenAI provider directly
    cat > "$CODEX_HOME_DIR/config.toml" <<EOF
model_provider = "openai"
default_model = "${MODEL}"

[model_providers.openai]
wire_api = "${WIRE_API}"
EOF
else
    # Use synth provider
    cat > "$CODEX_HOME_DIR/config.toml" <<EOF
model_provider = "synth"
default_model = "${MODEL}"

[model_providers.synth]
name = "Synth"
base_url = "${LOCAL_BACKEND_URL}"
env_key = "OPENAI_API_KEY"
wire_api = "${WIRE_API}"
EOF
fi

echo "[config] Configured Codex with synth-ai runtime"
echo "[config] Backend: ${LOCAL_BACKEND_URL}"
echo "[config] Model: ${MODEL}"
echo "[config] Wire API: ${WIRE_API}"

# Prepare Docker run options
DOCKER_RUN_OPTS=(
    --rm
    -v "$RUN_DIR:/runs"
    -v "$RUN_DIR/artifacts:/app/artifacts"
    --security-opt seccomp=unconfined
    --security-opt apparmor=unconfined
    --cap-add SYS_ADMIN
    --cap-add SYS_PTRACE
    -v "$CODEX_HOME_DIR:/root/.codex"
    -e "OPENAI_MODEL=${MODEL}"
    -e "OPENAI_API_KEY=${OPENAI_API_KEY}"
    -e "SYNTH_API_KEY=${SYNTH_API_KEY}"
    --add-host host.docker.internal:host-gateway
    --log-driver none
)

# Add host.docker.internal for macOS/Linux
if [[ "$(uname)" == "Darwin" ]] || [[ "$(uname)" == "Linux" ]]; then
    DOCKER_RUN_OPTS+=(--add-host host.docker.internal:host-gateway)
fi

# Run container
echo "[run] Starting container..."
SANITIZED_RUN_ID=$(echo "$RUN_ID" | sed 's/[^a-zA-Z0-9_.-]/_/g')
CONTAINER_NAME="oneshot_synth_ai_${SANITIZED_RUN_ID}"
mkdir -p "$RUN_DIR/logs" "$RUN_DIR/artifacts"

# Write run metadata
START_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TASK_BASENAME=$(basename "${TASK_PATH_INPUT}")
cat > "$RUN_DIR/metadata.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "task_dir": "${TASK_PATH_INPUT}",
  "task_id": "${TASK_BASENAME}",
  "start_time": "${START_TIME_UTC}",
  "model": "${MODEL}",
  "backend_url": "${LOCAL_BACKEND_URL}"
}
EOF

# Run container (box_bootstrap.sh will handle codex exec)
docker run --name "$CONTAINER_NAME" "${DOCKER_RUN_OPTS[@]}" oneshot-task-synth-ai 2>&1 | \
    grep -v "codex_otel::otel_event_manager" | \
    grep -v "INFO codex" | \
    grep -v "^$" || true
EXIT_CODE=${PIPESTATUS[0]}

END_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Copy container artifacts/logs
if docker ps -a -q -f name="$CONTAINER_NAME" | grep -q .; then
    docker cp "$CONTAINER_NAME:/app/artifacts/." "$RUN_DIR/artifacts/" 2>/dev/null || true
    docker logs "$CONTAINER_NAME" > "$RUN_DIR/logs/container_full.log" 2>&1 || true
    if [[ ! -s "$RUN_DIR/artifacts/diff.patch" ]]; then
        if [[ -s "$RUN_DIR/artifacts/container_git_diff_from_baseline.patch" ]]; then
            cp -f "$RUN_DIR/artifacts/container_git_diff_from_baseline.patch" "$RUN_DIR/artifacts/diff.patch" 2>/dev/null || true
        elif [[ -s "$RUN_DIR/artifacts/container_git_diff.patch" ]]; then
            cp -f "$RUN_DIR/artifacts/container_git_diff.patch" "$RUN_DIR/artifacts/diff.patch" 2>/dev/null || true
        fi
    fi
fi

# Save results
cat > "$RUN_DIR/results.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "task_dir": "${TASK_PATH_INPUT}",
  "exit_code": ${EXIT_CODE},
  "start_time": "${START_TIME_UTC}",
  "end_time": "${END_TIME_UTC}",
  "model": "${MODEL}",
  "backend_url": "${LOCAL_BACKEND_URL}"
}
EOF

# Print diff if available
if [[ -f "$RUN_DIR/artifacts/diff.patch" && -s "$RUN_DIR/artifacts/diff.patch" ]]; then
    echo ""
    echo "📝 Diff submitted:"
    echo "---"
    head -50 "$RUN_DIR/artifacts/diff.patch"
    if [[ $(wc -l < "$RUN_DIR/artifacts/diff.patch") -gt 50 ]]; then
        echo "... (truncated)"
    fi
    echo "---"
else
    echo ""
    echo "⚠️  No diff found. Checking for diff files..."
    # Check for any diff files
    if [[ -d "$RUN_DIR/artifacts" ]]; then
        echo "   Artifacts directory contents:"
        ls -lah "$RUN_DIR/artifacts/" | head -10 || true
        # Check for alternative diff locations
        if [[ -f "$RUN_DIR/artifacts/container_git_diff_from_baseline.patch" ]]; then
            echo "   Found container_git_diff_from_baseline.patch"
        fi
        if [[ -f "$RUN_DIR/artifacts/container_git_diff.patch" ]]; then
            echo "   Found container_git_diff.patch"
        fi
    fi
fi

echo ""
echo "✅ Codex execution completed (exit code: ${EXIT_CODE})"
echo "   Run directory: $RUN_DIR"

exit $EXIT_CODE
