#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/synth_models.sh"

# Always load secrets from .env if present
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env"
  set +a
  echo "[run_opencode_box] Loaded environment variables from ${REPO_ROOT}/.env"
else
  echo "[run_opencode_box] Warning: .env file not found at ${REPO_ROOT}/.env"
fi

# Mode selection: local or docker (default: docker)
OPencode_MODE="${OPencode_MODE:-docker}"

TASK_PATH_INPUT="${1:-}"
if [[ -z "$TASK_PATH_INPUT" ]]; then
    echo "Usage: $0 <path-to-task>"
    echo ""
    echo "This script launches OpenCode configured to use OpenAI API directly."
    echo "It's similar to run_codex_box.sh but launches OpenCode instead."
    echo ""
    echo "Modes:"
    echo "  OPencode_MODE=docker   - Run OpenCode in Docker sandbox (default)"
    echo "  OPencode_MODE=local    - Run OpenCode locally"
    echo ""
    echo "Options:"
    echo "  OPENAI_MODEL    - Model to use (default: gpt-5-nano)"
    echo "                    OpenAI models: gpt-5-nano, gpt-5-mini, etc."
    echo "                    Synth models: synth-small, synth-medium"
    echo ""
    echo "API keys are automatically loaded from: ${REPO_ROOT}/.env"
    echo "  OPENAI_API_KEY  - Required for OpenAI models"
    echo "  SYNTH_API_KEY   - Required for synth models"
    exit 1
fi

# Normalize to absolute path
if [[ "${TASK_PATH_INPUT}" != /* ]]; then
    TASK_PATH_INPUT="${REPO_ROOT}/${TASK_PATH_INPUT}"
fi

echo "[run_opencode_box] Mode: ${OPencode_MODE}"
echo "[run_opencode_box] Task path: ${TASK_PATH_INPUT}"

# Determine model (default to gpt-5-nano like codex_box)
MODEL="${OPENAI_MODEL:-gpt-5-nano}"

# Check if this is a synth model
IS_SYNTH_MODEL=false
if is_synth_model "$MODEL"; then
    IS_SYNTH_MODEL=true
fi

echo "[run_opencode_box] Using model: ${MODEL}"
if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
    echo "[run_opencode_box] Model type: Synth (requires SYNTH_API_KEY)"
    if [[ -z "${SYNTH_API_KEY:-}" ]]; then
        echo "Error: SYNTH_API_KEY not found in environment" >&2
        echo "Please set SYNTH_API_KEY in your .env file: ${REPO_ROOT}/.env" >&2
        exit 1
    fi
    export SYNTH_API_KEY
    # Synth base URL (default to dev backend unless overridden)
    SYNTH_BASE_URL="${SYNTH_BASE_URL:-$(get_default_synth_base_url)}"
    export SYNTH_BASE_URL
    echo "[run_opencode_box] SYNTH_API_KEY loaded from .env"
else
    echo "[run_opencode_box] Model type: OpenAI (requires OPENAI_API_KEY)"
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
        echo "Error: OPENAI_API_KEY not found in environment" >&2
        echo "Please set OPENAI_API_KEY in your .env file: ${REPO_ROOT}/.env" >&2
        exit 1
    fi
    export OPENAI_API_KEY
    echo "[run_opencode_box] OPENAI_API_KEY loaded from .env"
fi

# Extract prompt from task (similar to how Codex does it)
PROMPT=""
if [[ -f "${TASK_PATH_INPUT}/overlay_files/LM_INSTRUCTIONS.md" ]]; then
    PROMPT="$(cat "${TASK_PATH_INPUT}/overlay_files/LM_INSTRUCTIONS.md")"
elif [[ -f "${TASK_PATH_INPUT}/tb_meta.json" ]]; then
    if command -v jq >/dev/null 2>&1; then
        PROMPT="$(jq -r '.lm.instructions // empty' "${TASK_PATH_INPUT}/tb_meta.json")"
    else
        echo "Warning: jq not found, cannot extract prompt from tb_meta.json" >&2
    fi
fi

if [[ -z "$PROMPT" ]]; then
    echo "Error: No LM instructions found; cannot run headlessly." >&2
    echo "Expected one of:" >&2
    echo "  - ${TASK_PATH_INPUT}/overlay_files/LM_INSTRUCTIONS.md" >&2
    echo "  - ${TASK_PATH_INPUT}/tb_meta.json (with .lm.instructions)" >&2
    exit 1
fi

echo "[run_opencode_box] Extracted prompt (${#PROMPT} characters)"

# Export RUST_LOG to suppress noisy logs
export RUST_LOG="${RUST_LOG:-codex_otel::otel_event_manager=warn}"

# Ensure OPENAI_API_KEY is exported
export OPENAI_API_KEY

# Run based on mode
if [[ "$OPencode_MODE" == "docker" ]]; then
    # Docker mode: run in sandbox
    RUN_ID="${RUN_ID:-$(date +%Y%m%d__%H-%M-%S)}"
    RUN_DIR="${REPO_ROOT}/data/runs/${RUN_ID}"
    mkdir -p "$RUN_DIR/logs" "$RUN_DIR/artifacts"
    
    # Auto-prepare created tasks to prepared if needed
    if [[ -f "$TASK_PATH_INPUT/tb_meta.json" && ! -f "$TASK_PATH_INPUT/Dockerfile" ]]; then
        echo "[run_opencode_box] Detected created task. Preparing for evaluation..."
        export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"
        uv run python -m one_shot.prepare_task_for_eval "$TASK_PATH_INPUT"
        SLUG="$(basename "$TASK_PATH_INPUT")"
        TMP_SLUG="${SLUG%_*}"
        BASE_SLUG="${TMP_SLUG%_*}"
        if [[ -d "${REPO_ROOT}/data/tasks/prepared/${BASE_SLUG}" ]]; then
            PREPARED_DIR="${REPO_ROOT}/data/tasks/prepared/${BASE_SLUG}"
        else
            PREPARED_DIR="${REPO_ROOT}/data/tasks/prepared/${SLUG}"
        fi
        if [[ -d "$PREPARED_DIR" ]]; then
            TASK_PATH_INPUT="$PREPARED_DIR"
            echo "[run_opencode_box] Prepared task at: $TASK_PATH_INPUT"
        fi
    fi
    
    if [[ ! -f "$TASK_PATH_INPUT/Dockerfile" ]]; then
        echo "Error: Dockerfile not found at ${TASK_PATH_INPUT}/Dockerfile" >&2
        echo "Task must be prepared first. Run: uv run python -m one_shot.prepare_task_for_eval <task>" >&2
        exit 1
    fi
    
    if ! docker_is_running; then
        echo "Error: Docker daemon is not running" >&2
        exit 1
    fi
    
    echo "[run_opencode_box] Building Docker image..."
    export DOCKER_BUILDKIT=1
    BUILD_ARGS=(-t oneshot-opencode-task)
    if [[ "${DOCKER_NO_CACHE:-0}" == "1" ]]; then
        BUILD_ARGS+=(--no-cache)
        echo "[run_opencode_box] Using --no-cache (DOCKER_NO_CACHE=1)"
    fi
    if [[ -n "${PRIVATE_GITHUB_PAT:-}" ]]; then
        BUILD_ARGS+=(--build-arg "GITHUB_PAT=${PRIVATE_GITHUB_PAT}")
    fi
    docker build "${BUILD_ARGS[@]}" "$TASK_PATH_INPUT"
    
    echo "[run_opencode_box] Setting up Docker container..."
    
    DOCKER_RUN_OPTS=(--rm -v "$RUN_DIR:/runs" -v "$RUN_DIR/artifacts:/app/artifacts")
    
    # Relax security similar to run_codex_box.sh
    DOCKER_RUN_OPTS+=( --security-opt seccomp=unconfined --security-opt apparmor=unconfined --cap-add SYS_ADMIN --cap-add SYS_PTRACE )
    
    # Pass environment variables
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        DOCKER_RUN_OPTS+=( -e "SYNTH_API_KEY=${SYNTH_API_KEY}" )
        DOCKER_RUN_OPTS+=( -e "SYNTH_BASE_URL=${SYNTH_BASE_URL}" )
        DOCKER_RUN_OPTS+=( -e "IS_SYNTH_MODEL=true" )
    else
        DOCKER_RUN_OPTS+=( -e "OPENAI_API_KEY=${OPENAI_API_KEY}" )
        DOCKER_RUN_OPTS+=( -e "IS_SYNTH_MODEL=false" )
    fi
    DOCKER_RUN_OPTS+=( -e "OPENAI_MODEL=${MODEL}" )
    DOCKER_RUN_OPTS+=( -e "RUST_LOG=${RUST_LOG}" )
    if [[ -n "${PRIVATE_GITHUB_PAT:-}" ]]; then
        DOCKER_RUN_OPTS+=( -e "PRIVATE_GITHUB_PAT=${PRIVATE_GITHUB_PAT}" )
    fi
    
    # Create OpenCode config directory and mount it
    OPencode_HOME_DIR="$RUN_DIR/opencode_home"
    CONFIG_DIR="$OPencode_HOME_DIR/.config/opencode"
    AUTH_DIR="$OPencode_HOME_DIR/.local/share/opencode"
    mkdir -p "$CONFIG_DIR" "$AUTH_DIR"
    
    # Write auth.json based on model type
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        cat > "$AUTH_DIR/auth.json" <<EOF
{
  "synth": {
    "type": "api",
    "key": "${SYNTH_API_KEY}"
  }
}
EOF
        python3 - "$CONFIG_DIR/auth.json" "$SYNTH_API_KEY" <<'PY'
import json, os, sys

path = sys.argv[1]
api_key = sys.argv[2]

data = {}
if os.path.exists(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}

data.setdefault("synth", {})
data["synth"]["apiKey"] = api_key

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
    else
        cat > "$AUTH_DIR/auth.json" <<EOF
{
  "openai": {
    "type": "api",
    "key": "${OPENAI_API_KEY}"
  }
}
EOF
        python3 - "$CONFIG_DIR/auth.json" "$OPENAI_API_KEY" <<'PY'
import json, os, sys

path = sys.argv[1]
api_key = sys.argv[2]

data = {}
if os.path.exists(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}

data.setdefault("openai", {})
data["openai"]["apiKey"] = api_key

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
    fi
    
    # Write opencode.json based on model type
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        cat > "$CONFIG_DIR/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "synth": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Synth",
      "options": {
        "baseURL": "${SYNTH_BASE_URL}",
        "apiKey": "${SYNTH_API_KEY}"
      },
      "models": {
        "${MODEL}": {}
      }
    }
  },
  "model": "synth/${MODEL}"
}
EOF
        echo "[run_opencode_box] Mounted OpenCode config with model: synth/${MODEL}"
    else
        cat > "$CONFIG_DIR/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "openai": {
      "npm": "@ai-sdk/openai",
      "name": "OpenAI",
      "options": {
        "baseURL": "https://api.openai.com/v1",
        "apiKey": "{env:OPENAI_API_KEY}"
      },
      "models": {
        "${MODEL}": {
          "name": "GPT-5 Nano"
        }
      }
    }
  },
  "model": "openai/${MODEL}"
}
EOF
        echo "[run_opencode_box] Mounted OpenCode config with model: openai/${MODEL}"
    fi
    
    DOCKER_RUN_OPTS+=( -v "$OPencode_HOME_DIR/.config:/root/.config" )
    DOCKER_RUN_OPTS+=( -v "$OPencode_HOME_DIR/.local:/root/.local" )
    
    # Sanitize RUN_ID for Docker container name
    SANITIZED_RUN_ID=$(echo "$RUN_ID" | sed 's/[^a-zA-Z0-9_.-]/_/g')
    CONTAINER_NAME="oneshot_opencode_${SANITIZED_RUN_ID}"
    
    # Write run metadata
    START_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$RUN_DIR/metadata.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "task_dir": "${TASK_PATH_INPUT}",
  "task_id": "$(basename "${TASK_PATH_INPUT}")",
  "start_time": "${START_TIME_UTC}",
  "mode": "docker"
}
EOF
    
    # Write prompt to a file for safe passing to container
    PROMPT_FILE="$RUN_DIR/prompt.txt"
    printf '%s' "$PROMPT" > "$PROMPT_FILE"
    DOCKER_RUN_OPTS+=( -v "$PROMPT_FILE:/app/prompt.txt:ro" )
    
    echo "[run_opencode_box] Starting container..."
    echo "[run_opencode_box] Installing OpenCode in container and running..."
    
    # Run container with OpenCode installation and execution
    # We'll override the CMD to install OpenCode and run it
    docker run --name "$CONTAINER_NAME" "${DOCKER_RUN_OPTS[@]}" oneshot-opencode-task \
        bash -c "
            # Install OpenCode if not already installed
            if ! command -v opencode >/dev/null 2>&1; then
                echo '[container] Installing OpenCode via npm...'
                npm install -g opencode-ai || {
                    echo '[container] npm install failed, trying bun...'
                    if command -v bun >/dev/null 2>&1; then
                        bun add -g opencode-ai || {
                            echo '[container] Failed to install OpenCode' >&2
                            exit 1
                        }
                    else
                        echo '[container] bun not found, npm install failed' >&2
                        exit 1
                    fi
                }
            fi
            
            # Verify OpenCode is available
            if ! command -v opencode >/dev/null 2>&1; then
                echo '[container] Error: OpenCode not found after installation' >&2
                exit 1
            fi
            
            echo '[container] OpenCode installed, version:'
            opencode --version || true
            
            # Determine working directory
            WORK_DIR='/app'
            if [[ -d '/app/repo' ]]; then
                WORK_DIR='/app/repo'
            fi
            cd \"\$WORK_DIR\"
            echo '[container] Working directory: '\$(pwd)
            
            # Read prompt from file
            PROMPT_CONTENT=\"\$(cat /app/prompt.txt)\"
            
            # Run OpenCode headlessly
            echo '[container] Running OpenCode with prompt...'
            if [[ '${IS_SYNTH_MODEL}' == 'true' ]]; then
                MODEL_ARG='synth/${MODEL}'
            else
                MODEL_ARG='openai/${MODEL}'
            fi
            opencode run --model \"\$MODEL_ARG\" \"\$PROMPT_CONTENT\" || {
                EXIT_CODE=\$?
                echo \"[container] OpenCode exited with code: \$EXIT_CODE\" >&2
                exit \$EXIT_CODE
            }
        " 2>&1 | \
        grep -v "codex_otel::otel_event_manager" | \
        grep -v "^$" || true
    
    EXIT_CODE=${PIPESTATUS[0]}
    END_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    
    # Copy container artifacts/logs
    if docker ps -a -q -f name="$CONTAINER_NAME" | grep -q .; then
        docker cp "$CONTAINER_NAME:/app/artifacts/." "$RUN_DIR/artifacts/" 2>/dev/null || true
        docker logs "$CONTAINER_NAME" > "$RUN_DIR/logs/container_full.log" 2>&1 || true
    fi
    
    # Save results
    cat > "$RUN_DIR/results.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "task_dir": "${TASK_PATH_INPUT}",
  "exit_code": ${EXIT_CODE},
  "start_time": "${START_TIME_UTC}",
  "end_time": "${END_TIME_UTC}",
  "mode": "docker"
}
EOF
    
    echo "[run_opencode_box] Run artifacts in: $RUN_DIR"
    exit $EXIT_CODE
    
else
    # Local mode: run on host
    echo "[run_opencode_box] Running in local mode..."
    
    # Find OpenCode binary
    OPencode_BIN=""
    if command -v opencode >/dev/null 2>&1; then
        OPencode_BIN="opencode"
    elif [[ -f "/Applications/OpenCode.app/Contents/MacOS/opencode" ]]; then
        OPencode_BIN="/Applications/OpenCode.app/Contents/MacOS/opencode"
    elif [[ -f "${HOME}/.bun/bin/opencode" ]]; then
        OPencode_BIN="${HOME}/.bun/bin/opencode"
    elif [[ -f "${HOME}/.local/bin/opencode" ]]; then
        OPencode_BIN="${HOME}/.local/bin/opencode"
    else
        echo "Error: OpenCode binary not found" >&2
        echo "Install OpenCode with one of:" >&2
        echo "  brew install opencode" >&2
        echo "  bun add -g opencode-ai" >&2
        echo "  npm i -g opencode-ai" >&2
        echo "  curl -fsSL https://opencode.ai/install | bash" >&2
        exit 1
    fi
    
    echo "[run_opencode_box] Found OpenCode at: ${OPencode_BIN}"
    
    # Verify OpenCode is runnable
    if ! "$OPencode_BIN" --version >/dev/null 2>&1; then
        echo "Error: OpenCode binary is not runnable" >&2
        exit 1
    fi
    
    # Configure OpenCode based on model type
    CONFIG_DIR="${HOME}/.config/opencode"
    AUTH_DIR="${HOME}/.local/share/opencode"
    CONFIG_PATH="${CONFIG_DIR}/opencode.json"
    AUTH_PATH="${AUTH_DIR}/auth.json"
    
    mkdir -p "$CONFIG_DIR"
    mkdir -p "$AUTH_DIR"
    
    # Write auth.json based on model type
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        cat > "$AUTH_PATH" <<EOF
{
  "synth": {
    "type": "api",
    "key": "${SYNTH_API_KEY}"
  }
}
EOF
        echo "[run_opencode_box] Configured auth.json at ${AUTH_PATH} (synth)"
        python3 - "$CONFIG_DIR/auth.json" "$SYNTH_API_KEY" <<'PY'
import json, os, sys

path = sys.argv[1]
api_key = sys.argv[2]

data = {}
if os.path.exists(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}

data.setdefault("synth", {})
data["synth"]["apiKey"] = api_key

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
    else
        cat > "$AUTH_PATH" <<EOF
{
  "openai": {
    "type": "api",
    "key": "${OPENAI_API_KEY}"
  }
}
EOF
        echo "[run_opencode_box] Configured auth.json at ${AUTH_PATH} (openai)"
        python3 - "$CONFIG_DIR/auth.json" "$OPENAI_API_KEY" <<'PY'
import json, os, sys

path = sys.argv[1]
api_key = sys.argv[2]

data = {}
if os.path.exists(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}

data.setdefault("openai", {})
data["openai"]["apiKey"] = api_key

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
PY
    fi
    
    # Write opencode.json configuration based on model type
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        cat > "$CONFIG_PATH" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "synth": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Synth",
      "options": {
        "baseURL": "${SYNTH_BASE_URL}",
        "apiKey": "${SYNTH_API_KEY}"
      },
      "models": {
        "${MODEL}": {}
      }
    }
  },
  "model": "synth/${MODEL}"
}
EOF
        echo "[run_opencode_box] Configured opencode.json at ${CONFIG_PATH}"
        echo "[run_opencode_box] Model: synth/${MODEL}"
    else
        cat > "$CONFIG_PATH" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "openai": {
      "npm": "@ai-sdk/openai",
      "name": "OpenAI",
      "options": {
        "baseURL": "https://api.openai.com/v1",
        "apiKey": "{env:OPENAI_API_KEY}"
      },
      "models": {
        "${MODEL}": {
          "name": "GPT-5 Nano"
        }
      }
    }
  },
  "model": "openai/${MODEL}"
}
EOF
        echo "[run_opencode_box] Configured opencode.json at ${CONFIG_PATH}"
        echo "[run_opencode_box] Model: openai/${MODEL}"
    fi
    
    # Determine working directory (use repo if it exists, otherwise task directory)
    WORK_DIR="${TASK_PATH_INPUT}"
    if [[ -d "${TASK_PATH_INPUT}/repo" ]]; then
        WORK_DIR="${TASK_PATH_INPUT}/repo"
        echo "[run_opencode_box] Using repo directory: ${WORK_DIR}"
    else
        echo "[run_opencode_box] Using task directory: ${WORK_DIR}"
    fi
    
    # Launch OpenCode headlessly with the prompt
    echo "[run_opencode_box] Running OpenCode headlessly..."
    cd "$WORK_DIR"
    echo "[run_opencode_box] Working directory: $(pwd)"
    
    # Determine model argument based on model type
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        MODEL_ARG="synth/${MODEL}"
    else
        MODEL_ARG="openai/${MODEL}"
    fi
    echo "[run_opencode_box] Model: ${MODEL_ARG}"
    
    # Run OpenCode headlessly with the prompt
    # Note: opencode run accepts the message as positional arguments
    # We pass the prompt as a single quoted argument to handle multi-line content
    # Ensure environment variables are available for OpenCode
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        SYNTH_API_KEY="${SYNTH_API_KEY}" "$OPencode_BIN" run --model "$MODEL_ARG" "$PROMPT" || {
            EXIT_CODE=$?
            echo "[run_opencode_box] OpenCode exited with code: $EXIT_CODE" >&2
            exit $EXIT_CODE
        }
    else
        OPENAI_API_KEY="${OPENAI_API_KEY}" "$OPencode_BIN" run --model "$MODEL_ARG" "$PROMPT" || {
            EXIT_CODE=$?
            echo "[run_opencode_box] OpenCode exited with code: $EXIT_CODE" >&2
            exit $EXIT_CODE
        }
    fi
fi

