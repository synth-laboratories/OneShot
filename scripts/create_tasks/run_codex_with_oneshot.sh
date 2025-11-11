#!/bin/bash
# Run codex-synth with OneShot MCP tools available
# This starts an interactive session where the agent has access to start-task and end-task tools

# Don't use set -e - we want to handle errors explicitly and show helpful messages
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [[ -z "${RUN_ID:-}" ]]; then
    RUN_ID="oneshot_$(date +%Y%m%d_%H%M%S)_$$"
fi
WORKDIR="$REPO_ROOT"

# Find an available port for this task instance (unique per RUN_ID)
# Use a port in the range 18080-18180, based on hash of RUN_ID
find_available_port() {
    local base_port=18080
    local port_range=100
    # Generate a port based on RUN_ID hash (modulo port_range)
    local hash=$(echo -n "$RUN_ID" | shasum -a 256 | cut -d' ' -f1 | head -c 8)
    local port_offset=$((0x$hash % port_range))
    local candidate_port=$((base_port + port_offset))
    
    # Check if port is available, if not try next ports
    for i in {0..9}; do
        local test_port=$((candidate_port + i))
        if ! lsof -nP -iTCP:$test_port -sTCP:LISTEN >/dev/null 2>&1; then
            echo $test_port
            return 0
        fi
    done
    
    # Fallback: use base_port if all in range are taken
    echo $base_port
}

PROXY_PORT=$(find_available_port)
PROXY_PID_FILE="/tmp/oneshot_mitm_proxy_${RUN_ID}.pid"
PROXY_LOG_FILE="/tmp/oneshot_mitm_proxy_${RUN_ID}.log"

# Colors (needed for cleanup function)
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Cleanup function to kill proxy on exit
cleanup_proxy() {
    if [ -f "$PROXY_PID_FILE" ]; then
        local pid=$(cat "$PROXY_PID_FILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            log_info "Cleaning up MITM proxy (PID: $pid) on port $PROXY_PORT..."
            kill "$pid" 2>/dev/null || true
            sleep 0.5
            kill -9 "$pid" 2>/dev/null || true
            rm -f "$PROXY_PID_FILE"
        fi
    fi
}

# Register cleanup trap
trap cleanup_proxy EXIT INT TERM

# Parse arguments
TASK_TITLE=""
TASK_DESCRIPTION=""
USE_PROXY=true

while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--title)
            TASK_TITLE="$2"
            shift 2
            ;;
        -d|--description)
            TASK_DESCRIPTION="$2"
            shift 2
            ;;
        --workdir)
            WORKDIR="$2"
            shift 2
            ;;
        --no-proxy)
            USE_PROXY=false
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -t, --title TITLE           Task title"
            echo "  -d, --description DESC      Task description"
            echo "  --workdir DIR               Directory to run codex-synth in (default: OneShot repo)"
            echo "  --no-proxy                  Don't use proxy"
            echo ""
            echo "This script:"
            echo "1. Ensures MCP server is configured"
            echo "2. Sets up proxy (if available)"
            echo "3. Runs codex-synth with OneShot instructions"
            exit 0
            ;;
        *)
            log_warn "Unknown option: $1"
            shift
            ;;
    esac
done

# Setup environment
if [ ! -d "$WORKDIR" ]; then
    log_warn "Workdir not found: $WORKDIR (defaulting to OneShot repo: $REPO_ROOT)"
    WORKDIR="$REPO_ROOT"
fi
cd "$WORKDIR"
export RUN_ID="$RUN_ID"

# Load .env file if it exists in the working directory
# This allows synth-ai commands to access API keys and other environment variables
# Also check for .env in repo root (synth-ai convention)
ENV_FILES=()
if [ -f "$WORKDIR/.env" ]; then
    ENV_FILES+=("$WORKDIR/.env")
fi
if [ -f "$WORKDIR/examples/rl/.env" ]; then
    ENV_FILES+=("$WORKDIR/examples/rl/.env")
fi

if [ ${#ENV_FILES[@]} -gt 0 ]; then
    log_info "Loading environment variables from .env file(s)..."
    for env_file in "${ENV_FILES[@]}"; do
        log_info "  Loading: $env_file"
        # Use a safe method to load .env file (skip comments and empty lines)
        while IFS= read -r line || [ -n "$line" ]; do
            # Skip comments and empty lines
            [[ "$line" =~ ^[[:space:]]*# ]] && continue
            [[ -z "${line// }" ]] && continue
            # Only export lines that look like KEY=VALUE
            if [[ "$line" =~ ^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*= ]]; then
                # Extract key and value separately to handle values with spaces/special chars
                key="${line%%=*}"
                key="${key// /}"  # Remove spaces
                value="${line#*=}"
                # Remove quotes if present
                value="${value#\"}"
                value="${value%\"}"
                value="${value#\'}"
                value="${value%\'}"
                if [ -n "$key" ] && [ -n "$value" ]; then
                    export "$key=$value" 2>/dev/null || true
                    if [[ "$key" =~ (KEY|SECRET|TOKEN) ]]; then
                        log_info "    ✓ Exported $key=***"
                    else
                        log_info "    ✓ Exported $key=$value"
                    fi
                fi
            fi
        done < "$env_file"
    done
    log_info "✓ Environment variables loaded from .env file(s)"
    
    # Verify critical variables are set
    MISSING_VARS=()
    for var in SYNTH_API_KEY ENVIRONMENT_API_KEY GROQ_API_KEY; do
        if [ -z "${!var:-}" ]; then
            MISSING_VARS+=("$var")
        fi
    done
    if [ ${#MISSING_VARS[@]} -gt 0 ]; then
        log_warn "⚠️  Missing environment variables: ${MISSING_VARS[*]}"
        log_warn "  These may be required for synth-ai commands to work"
    fi
else
    log_warn "⚠️  No .env file found in $WORKDIR or $WORKDIR/examples/rl/"
    log_warn "  Environment variables should be set in the shell or via TOML config"
fi

# Configure SSL certificates for Python and uv (needed for dataset downloads, PyPI, etc.)
# This ensures Python and uv can verify SSL certificates when downloading from Hugging Face, PyPI, etc.
log_info "Configuring SSL certificates for Python and uv..."
CERTIFI_PATH=$(python3 -c "import certifi; print(certifi.where())" 2>/dev/null || echo "")
if [ -n "$CERTIFI_PATH" ] && [ -f "$CERTIFI_PATH" ]; then
    export SSL_CERT_FILE="$CERTIFI_PATH"
    export REQUESTS_CA_BUNDLE="$CERTIFI_PATH"
    export CURL_CA_BUNDLE="$CERTIFI_PATH"
    # uv uses Rust's TLS stack - set UV_NATIVE_TLS=1 to use system certificates
    # Also set SSL_CERT_FILE so uv can find certifi's bundle if needed
    export UV_NATIVE_TLS=1
    export UV_SSL_CERT_FILE="$CERTIFI_PATH"
    log_info "✓ SSL certificates configured: $CERTIFI_PATH"
    log_info "✓ uv TLS configured: UV_NATIVE_TLS=1, UV_SSL_CERT_FILE=$CERTIFI_PATH"
else
    log_warn "⚠ certifi not found - SSL verification may fail for some downloads"
    log_warn "  Install certifi: pip install certifi"
    # Still try to use system TLS for uv
    export UV_NATIVE_TLS=1
fi

# Configure Hugging Face cache to avoid permission issues in sandboxed environments
# Use a writable location within the workspace instead of ~/.cache
HF_CACHE_DIR="$REPO_ROOT/.hf_cache"
mkdir -p "$HF_CACHE_DIR"
export HF_HOME="$HF_CACHE_DIR"
export HF_DATASETS_CACHE="$HF_CACHE_DIR/datasets"
export TRANSFORMERS_CACHE="$HF_CACHE_DIR/transformers"
export HF_HUB_CACHE="$HF_CACHE_DIR/hub"
# Ensure cache directories exist and are writable
mkdir -p "$HF_DATASETS_CACHE" "$TRANSFORMERS_CACHE" "$HF_HUB_CACHE"
log_info "✓ Hugging Face cache configured: $HF_CACHE_DIR"

# Check/setup proxy if requested
# MITM Proxy: Captures HTTP/HTTPS traffic from codex-synth for tracing
# - Uses mitmdump with mitm_tracer.py addon
# - Stores traces in SQLite DB
# - Codex-synth routes traffic through it via HTTPS_PROXY/HTTP_PROXY env vars
# - Started on a unique port per task instance, cleaned up on exit
if [ "$USE_PROXY" = "true" ]; then
    log_info "Starting MITM proxy for this task instance..."
    log_info "  Run ID: $RUN_ID"
    log_info "  Port: $PROXY_PORT (unique to this task)"
    
    # Start mitmdump directly (start_synth_workers.sh blocks on tail, so we can't use it)
    MITM_TRACER="$REPO_ROOT/src/local_tracing/mitm_tracer.py"
    if [ ! -f "$MITM_TRACER" ]; then
        log_error "MITM tracer script not found: $MITM_TRACER"
        log_error "REPO_ROOT resolved to: $REPO_ROOT"
        log_error "Cannot start proxy automatically. Please start it manually:"
        log_error "  $REPO_ROOT/scripts/start_synth_workers.sh"
        exit 1
    fi
    
    # Kill any existing proxy on this port (shouldn't happen with unique ports, but be safe)
    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -nP -iTCP:$PROXY_PORT -sTCP:LISTEN -t 2>/dev/null || true)"
        if [ -n "$pids" ]; then
            log_warn "Found existing listener on port $PROXY_PORT, killing it..."
            kill $pids 2>/dev/null || true
            sleep 1
        fi
    fi
    
    # Set RUN_ID in environment for tracer to use
    export RUN_ID
    
    # Start mitmdump with tracer (non-blocking, in background)
    log_info "Starting mitmdump on port $PROXY_PORT..."
    nohup env PYTHONPATH="$REPO_ROOT" RUN_ID="$RUN_ID" \
        mitmdump -s "$MITM_TRACER" \
        --listen-host 0.0.0.0 --listen-port "$PROXY_PORT" \
        --set upstream_cert=false \
        --set ssl_insecure=true \
        >"$PROXY_LOG_FILE" 2>&1 &
    PROXY_PID=$!
    echo $PROXY_PID > "$PROXY_PID_FILE"
    log_info "Proxy started with PID: $PROXY_PID"
    log_info "Logs: $PROXY_LOG_FILE"
    
    # Wait for proxy to start (check every second for up to 10 seconds)
    log_info "Waiting for proxy to become ready..."
    PROXY_READY=0
    for i in {1..10}; do
        sleep 1
        # Check health endpoint (mitmdump addon provides /health endpoint)
        if curl -s http://localhost:$PROXY_PORT/health 2>/dev/null | grep -q '"status":"ok"'; then
            log_info "✓ Proxy started successfully (took ${i}s)"
            export HTTPS_PROXY="http://localhost:$PROXY_PORT"
            export HTTP_PROXY="http://localhost:$PROXY_PORT"
            PROXY_READY=1
            break
        fi
        # Check if process is still alive
        if ! kill -0 $PROXY_PID 2>/dev/null; then
            log_error "Proxy process died. Check $PROXY_LOG_FILE:"
            tail -20 "$PROXY_LOG_FILE" 2>/dev/null || log_error "  (log file not found)"
            exit 1
        fi
    done
    
    if [ $PROXY_READY -eq 0 ]; then
        log_error "Proxy failed to start after 10 seconds"
        log_error "Check $PROXY_LOG_FILE for details:"
        tail -20 "$PROXY_LOG_FILE" 2>/dev/null || log_error "  (log file not found)"
        exit 1
    fi
    
    # Exclude PyPI and package repositories from proxy to avoid TLS issues with uv/pip
    # uv and pip need direct access to PyPI for package downloads (bypass proxy)
    NO_PROXY="pypi.org,pypi.python.org,files.pythonhosted.org,*.pypi.org,*.pythonhosted.org,localhost,127.0.0.1"
    export NO_PROXY
    log_info "✓ NO_PROXY set to exclude PyPI from proxy: $NO_PROXY"
else
    log_info "Proxy disabled (USE_PROXY=false) - tracing will not work"
fi

# Ensure MCP server is configured
# MCP Server: Provides tools (repo_start_task_v1, repo_end_task_v1) to codex-synth
# - Runs as stdio server (communicates via stdin/stdout)
# - Codex-synth spawns it automatically based on ~/.codex/config.toml
# - Config format: [mcp_servers.oneshot] section with command and args
# - Codex-synth discovers and connects automatically when it starts
MCP_SERVER_PATH="$SCRIPT_DIR/mcp_oneshot_server.py"
# codex-synth uses ~/.codex/config.toml (TOML format), not JSON
MCP_CONFIG_FILE="$HOME/.codex/config.toml"
MCP_CONFIG_DIR="$(dirname "$MCP_CONFIG_FILE")"

# Create MCP configuration directory
mkdir -p "$MCP_CONFIG_DIR"

log_info "Setting up MCP configuration..."
# Check if config.toml exists, if so backup and merge
if [ -f "$MCP_CONFIG_FILE" ]; then
    log_info "Existing config.toml found, backing up..."
    cp "$MCP_CONFIG_FILE" "${MCP_CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
fi

# Create or update config.toml with MCP server configuration
# Use TOML format for codex-synth
if [ -f "$MCP_CONFIG_FILE" ] && grep -q "\[mcp_servers.oneshot\]" "$MCP_CONFIG_FILE" 2>/dev/null; then
    log_info "Updating existing oneshot MCP server config..."
    # Remove old oneshot config section (simple sed approach)
    sed -i.tmp '/\[mcp_servers\.oneshot\]/,/^\[/{ /^\[/!d; }' "$MCP_CONFIG_FILE" 2>/dev/null || true
    sed -i.tmp '/\[mcp_servers\.oneshot\]/d' "$MCP_CONFIG_FILE" 2>/dev/null || true
    rm -f "${MCP_CONFIG_FILE}.tmp" 2>/dev/null || true
fi

# Append oneshot MCP server config
cat >> "$MCP_CONFIG_FILE" << EOF

[mcp_servers.oneshot]
command = "python3"
args = ["$MCP_SERVER_PATH"]
env = { RUN_ID = "$RUN_ID", PYTHONUNBUFFERED = "1" }
EOF

log_info "MCP server configured at: $MCP_CONFIG_FILE"

# Verify MCP server script exists and is executable
if [ ! -f "$MCP_SERVER_PATH" ]; then
    log_error "MCP server script not found: $MCP_SERVER_PATH"
    exit 1
fi

if [ ! -x "$MCP_SERVER_PATH" ]; then
    chmod +x "$MCP_SERVER_PATH"
    log_info "Made MCP server script executable"
fi

# Test that MCP server can be imported/run
log_info "Verifying MCP server can start..."
log_info "MCP server path: $MCP_SERVER_PATH"
log_info "Script dir: $SCRIPT_DIR"
log_info "Repo root: $REPO_ROOT"

# Suppress stderr to avoid debug logs from MCP SDK, only check exit code
MCP_TEST_OUTPUT=$(python3 -c "
import sys
from pathlib import Path
script_dir = Path('$SCRIPT_DIR')
sys.path.insert(0, str(script_dir))
try:
    import mcp_oneshot_server
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
" 2>/dev/null)

MCP_TEST_EXIT=$?
if [ $MCP_TEST_EXIT -ne 0 ]; then
    log_error "MCP server verification failed with exit code $MCP_TEST_EXIT"
    log_error "Output: $MCP_TEST_OUTPUT"
    exit 1
fi

# Check if output contains "OK" (may have other output from imports)
if echo "$MCP_TEST_OUTPUT" | grep -q "^OK$"; then
    log_info "✓ MCP server imports successfully"
else
    log_error "MCP server script failed to import: $MCP_TEST_OUTPUT"
    log_error "Please ensure Python dependencies are installed:"
    log_error "  cd $REPO_ROOT && uv pip install -e ."
    exit 1
fi

# Verify MCP config file was created correctly
if [ ! -f "$MCP_CONFIG_FILE" ]; then
    log_error "MCP config file was not created: $MCP_CONFIG_FILE"
    exit 1
fi

# Verify config file is valid TOML (basic check)
log_info "Verifying MCP config file..."
TOML_CHECK_FAILED=0
if ! python3 -c "import tomli; tomli.load(open('$MCP_CONFIG_FILE', 'rb'))" 2>/dev/null && ! python3 -c "import tomllib; tomllib.load(open('$MCP_CONFIG_FILE', 'rb'))" 2>/dev/null; then
    # If tomli/tomllib not available, at least check file exists and has content
    if [ ! -s "$MCP_CONFIG_FILE" ]; then
        log_error "MCP config file is empty: $MCP_CONFIG_FILE"
        exit 1
    fi
    log_warn "Could not validate TOML syntax (tomli/tomllib not available), but file exists"
    log_info "Config file content:"
    cat "$MCP_CONFIG_FILE" | head -20 || TOML_CHECK_FAILED=1
    if [ $TOML_CHECK_FAILED -ne 0 ]; then
        log_error "Failed to read config file"
        exit 1
    fi
else
    log_info "✓ MCP config file is valid TOML"
fi

log_info "✓ MCP server verified and ready"

# Final verification: Both proxy (if enabled) and MCP must be ready before launching codex-synth
log_info ""
log_info "=========================================="
log_info "PRE-FLIGHT CHECKS"
log_info "=========================================="

# Verify MITM Proxy (if enabled)
# The proxy captures HTTP/HTTPS traffic from codex-synth for tracing
# Codex-synth routes traffic through it via HTTPS_PROXY/HTTP_PROXY env vars
if [ "$USE_PROXY" = "true" ]; then
    # Check health endpoint (mitmdump addon provides /health endpoint)
    if curl -s http://localhost:$PROXY_PORT/health 2>/dev/null | grep -q '"status":"ok"'; then
        log_info "✓ MITM Proxy: Running on port $PROXY_PORT (tracing enabled)"
        log_info "  Traffic will be captured to: data/traces/v3/raw_synth_ai.db/traces.sqlite3"
        log_info "  Proxy will be cleaned up automatically when this session ends"
    else
        log_error "✗ MITM Proxy: Health check failed on port $PROXY_PORT"
        log_error "  Tracing will not work. Check $PROXY_LOG_FILE"
        exit 1
    fi
else
    log_info "⚠ MITM Proxy: Disabled (tracing will not work)"
fi

# Verify MCP Server configuration
# Note: Codex-synth will spawn the MCP server automatically when it starts
# We just need to ensure the config is correct - codex-synth reads ~/.codex/config.toml
# and spawns the server process defined in [mcp_servers.oneshot]
if [ -f "$MCP_CONFIG_FILE" ] && grep -q "\[mcp_servers.oneshot\]" "$MCP_CONFIG_FILE" 2>/dev/null; then
    log_info "✓ MCP Server: Configured in $MCP_CONFIG_FILE"
    log_info "  Codex-synth will spawn it automatically when starting"
    log_info "  Available tools: repo_start_task_v1, repo_end_task_v1, repo_check_readiness_v1, repo_autofix_readiness_v1"
else
    log_error "✗ MCP Server: Not properly configured in $MCP_CONFIG_FILE"
    log_error "  Codex-synth will not have access to task tracking tools"
    exit 1
fi

log_info "✓ All pre-flight checks passed"
log_info "=========================================="
log_info ""

# Create instructions template
INSTRUCTIONS_FILE="/tmp/oneshot_instructions_${RUN_ID}.md"

if [ -n "$TASK_TITLE" ]; then
    cat > "$INSTRUCTIONS_FILE" << EOF
# Task: $TASK_TITLE

${TASK_DESCRIPTION:-Please complete this task.}

## IMPORTANT: Task Management Instructions

You have access to MCP tools for task tracking. You MUST:

1. **IMMEDIATELY** call the \`repo_start_task_v1\` tool with:
   - task_title: "$TASK_TITLE"
   - notes: A brief description of what you plan to do
   - labels: Any relevant labels (e.g., ["feature"], ["bugfix"], ["refactor"])

2. Work on the task as requested.

3. **WHEN FINISHED**, call the \`repo_end_task_v1\` tool with:
   - summary: Brief summary of what was accomplished
   - labels: Any additional labels

These tools will:
- Create git commits at start and end
- Capture the diff of all changes
- Generate a task directory with all artifacts
- Export trace data for review

Available MCP tools:
- \`repo_start_task_v1\` - Start a new task (call this FIRST)
- \`repo_end_task_v1\` - End the current task (call this LAST)
- \`repo_check_readiness_v1\` - Check if git worktree is ready
- \`repo_autofix_readiness_v1\` - Auto-fix common git issues

Example usage:
\`\`\`
Tool: repo_start_task_v1
Arguments: {
  "task_title": "$TASK_TITLE",
  "notes": "Implementing the requested changes",
  "labels": ["feature"]
}
\`\`\`

Remember: ALWAYS call start_task first and end_task when done!
EOF
else
    cat > "$INSTRUCTIONS_FILE" << EOF
## OneShot Task Management Tools Available

You have access to MCP tools for task tracking. When working on any task:

1. **START** by calling \`repo_start_task_v1\` with:
   - task_title: A descriptive title
   - notes: What you plan to do
   - labels: Relevant labels

2. **END** by calling \`repo_end_task_v1\` with:
   - summary: What was accomplished
   - labels: Any additional labels

These tools will:
- Create git commits at start and end
- Capture the diff of all changes
- Generate a task directory with all artifacts
- Export trace data for review

Available MCP tools:
- \`repo_start_task_v1\` - Start a new task
- \`repo_end_task_v1\` - End the current task
- \`repo_check_readiness_v1\` - Check if git worktree is ready
- \`repo_autofix_readiness_v1\` - Auto-fix common git issues

ALWAYS use these tools to track your work!
EOF
fi

log_info "Instructions prepared at: $INSTRUCTIONS_FILE"
echo ""
cat "$INSTRUCTIONS_FILE"
echo ""
echo "=========================================="
echo ""

# Run codex-synth
log_info "Starting codex-synth with OneShot tools..."
log_info "Run ID: $RUN_ID"
log_info "Working directory: $WORKDIR"
log_info "MCP config: $MCP_CONFIG_FILE"
log_info "MCP server: $MCP_SERVER_PATH"
if [ "$USE_PROXY" = "true" ]; then
    log_info "Proxy configured: HTTPS_PROXY=$HTTPS_PROXY, HTTP_PROXY=$HTTP_PROXY"
else
    log_info "Proxy disabled"
fi
echo ""
echo "=========================================="
echo "CODEX-SYNTH INTERACTIVE SESSION STARTING"
echo "=========================================="
echo ""
echo "⚠️  IMPORTANT: MCP tools must be available for task tracking."
echo "   If codex-synth doesn't see repo_start_task_v1, check:"
echo "   1. MCP config at: $MCP_CONFIG_FILE"
echo "   2. MCP server logs: tail -f /tmp/oneshot_mcp_server.out"
echo "   3. Codex logs: ~/.codex/log/"
if [ "$USE_PROXY" = "true" ]; then
    echo ""
    echo "📡 Proxy configured: $HTTPS_PROXY"
    echo "   Traffic will be traced to: data/traces/v3/raw_synth_ai.db/traces.sqlite3"
fi
echo ""

if [ -n "$TASK_TITLE" ]; then
    # If we have a task, pass it as the initial prompt to codex-synth
    # This preloads the prompt into the TUI so Codex knows what to do
    log_info "Task: $TASK_TITLE"
    log_info "Preloading task prompt into codex-synth..."
    echo ""
    echo "The codex-synth TUI should appear below with the task preloaded."
    echo "You can interact with it to complete the task."
    echo ""
    
    # Build the initial prompt from title and description
    INITIAL_PROMPT="Task: $TASK_TITLE"
    if [ -n "$TASK_DESCRIPTION" ]; then
        INITIAL_PROMPT="$INITIAL_PROMPT

$TASK_DESCRIPTION

Remember to use repo_start_task_v1 at the beginning and repo_end_task_v1 when complete."
    fi
    
    # Pass prompt as argument - codex-synth will preload it into the TUI
    # Ensure proxy env vars are explicitly passed
    # For Rust-based codex binary, we need to install mitmproxy CA cert in macOS keychain
    # so the system trusts it (Rust HTTP clients use system trust store)
    # Use --dangerously-bypass-approvals-and-sandbox to skip git repo approval prompt
    # Read model from OPENAI_MODEL environment variable (set by run_re_bench.py from TOML config)
    MODEL_ARGS=()
    if [ -n "${OPENAI_MODEL:-}" ]; then
        MODEL_ARGS+=("-m" "${OPENAI_MODEL}")
        log_info "Using model: ${OPENAI_MODEL}"
        
        # For gpt-5-* models, set reasoning effort to medium (required)
        if [[ "${OPENAI_MODEL}" =~ ^gpt-5- ]]; then
            REASONING_EFFORT="${OPENAI_REASONING_EFFORT:-medium}"
            MODEL_ARGS+=("-c" "model_reasoning_effort=${REASONING_EFFORT}")
            MODEL_ARGS+=("-c" "reasoning.summaries=auto")
            log_info "Setting reasoning effort: ${REASONING_EFFORT} (required for gpt-5-* models)"
        fi
    fi
    
    if [ "$USE_PROXY" = "true" ]; then
        log_info "Launching codex-synth with proxy: HTTPS_PROXY=$HTTPS_PROXY"
        # Ensure mitmproxy CA cert is trusted by macOS (needed for Rust HTTP clients)
        MITM_CA_CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
        if [ -f "$MITM_CA_CERT" ]; then
            # Check if cert is already in keychain
            if ! security find-certificate -c "mitmproxy" -a ~/Library/Keychains/login.keychain-db >/dev/null 2>&1; then
                log_info "Installing mitmproxy CA cert into macOS keychain (needed for Rust HTTP clients)..."
                # Install cert into system keychain (requires sudo, but we'll try user keychain first)
                if security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db "$MITM_CA_CERT" 2>/dev/null; then
                    log_info "✓ Installed mitmproxy CA cert into user keychain"
                else
                    log_warn "Could not install cert into keychain (may need manual installation)"
                    log_warn "Run: security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db $MITM_CA_CERT"
                fi
            else
                log_info "mitmproxy CA cert already in keychain"
            fi
        else
            log_warn "mitmproxy CA cert not found at $MITM_CA_CERT"
        fi
        HTTPS_PROXY="$HTTPS_PROXY" HTTP_PROXY="$HTTP_PROXY" codex-synth --dangerously-bypass-approvals-and-sandbox "${MODEL_ARGS[@]}" "$INITIAL_PROMPT"
    else
        codex-synth --dangerously-bypass-approvals-and-sandbox "${MODEL_ARGS[@]}" "$INITIAL_PROMPT"
    fi
else
    # Just start interactive session
    # Ensure proxy env vars are explicitly passed
    # For Rust-based codex binary, we need to install mitmproxy CA cert in macOS keychain
    # Use --dangerously-bypass-approvals-and-sandbox to skip git repo approval prompt
    # Read model from OPENAI_MODEL environment variable (set by run_re_bench.py from TOML config)
    MODEL_ARGS=()
    if [ -n "${OPENAI_MODEL:-}" ]; then
        MODEL_ARGS+=("-m" "${OPENAI_MODEL}")
        log_info "Using model: ${OPENAI_MODEL}"
        
        # For gpt-5-* models, set reasoning effort to medium (required)
        if [[ "${OPENAI_MODEL}" =~ ^gpt-5- ]]; then
            REASONING_EFFORT="${OPENAI_REASONING_EFFORT:-medium}"
            MODEL_ARGS+=("-c" "model_reasoning_effort=${REASONING_EFFORT}")
            MODEL_ARGS+=("-c" "reasoning.summaries=auto")
            log_info "Setting reasoning effort: ${REASONING_EFFORT} (required for gpt-5-* models)"
        fi
    fi
    
    if [ "$USE_PROXY" = "true" ]; then
        log_info "Launching codex-synth with proxy: HTTPS_PROXY=$HTTPS_PROXY"
        # Ensure mitmproxy CA cert is trusted by macOS (needed for Rust HTTP clients)
        MITM_CA_CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
        if [ -f "$MITM_CA_CERT" ]; then
            # Check if cert is already in keychain
            if ! security find-certificate -c "mitmproxy" -a ~/Library/Keychains/login.keychain-db >/dev/null 2>&1; then
                log_info "Installing mitmproxy CA cert into macOS keychain (needed for Rust HTTP clients)..."
                # Install cert into user keychain
                if security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db "$MITM_CA_CERT" 2>/dev/null; then
                    log_info "✓ Installed mitmproxy CA cert into user keychain"
                else
                    log_warn "Could not install cert into keychain (may need manual installation)"
                    log_warn "Run: security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db $MITM_CA_CERT"
                fi
            else
                log_info "mitmproxy CA cert already in keychain"
            fi
        else
            log_warn "mitmproxy CA cert not found at $MITM_CA_CERT"
        fi
        HTTPS_PROXY="$HTTPS_PROXY" HTTP_PROXY="$HTTP_PROXY" codex-synth --dangerously-bypass-approvals-and-sandbox "${MODEL_ARGS[@]}"
    else
        codex-synth --dangerously-bypass-approvals-and-sandbox "${MODEL_ARGS[@]}"
    fi
fi

EXIT_CODE=$?
log_info "codex-synth session ended with exit code $EXIT_CODE"
exit $EXIT_CODE