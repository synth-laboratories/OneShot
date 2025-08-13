#!/bin/bash
# Orchestrator script for running Codex with OneShot task creation
# This script sets up the environment, starts necessary servers, and runs Codex

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TOOL_SERVER_PORT=8080
PROXY_PORT=18080
RUN_ID="oneshot_$(date +%Y%m%d_%H%M%S)_$$"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Cleanup function
cleanup() {
    log_info "Cleaning up..."
    
    # Kill tool server if running
    if [ ! -z "$TOOL_SERVER_PID" ]; then
        kill $TOOL_SERVER_PID 2>/dev/null || true
    fi
    
    # Kill proxy workers if we started them
    if [ "$START_PROXY" = "true" ]; then
        pkill -f "mitm_proxy.py" 2>/dev/null || true
        pkill -f "trace_cleaner.py" 2>/dev/null || true
    fi
    
    # Remove state file if exists
    rm -f /tmp/oneshot_state.json
}

trap cleanup EXIT

# Parse arguments
TASK_TITLE=""
TASK_NOTES=""
DOCKER_MODE=false
START_PROXY=false
USE_MCP=false
INSTRUCTIONS_FILE=""

usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Options:
    -t, --title TITLE           Task title (required)
    -n, --notes NOTES          Task notes/instructions
    -f, --file FILE            File containing instructions (alternative to -n)
    -d, --docker               Run in Docker container (OneShot mode)
    -p, --start-proxy          Start proxy workers (if not already running)
    -m, --mcp                  Use MCP server instead of HTTP
    -h, --help                 Show this help message

Examples:
    # Simple task creation
    $0 -t "Add README section" -n "Add a section about testing to README.md"
    
    # With instructions from file
    $0 -t "Implement feature" -f instructions.md
    
    # Docker mode with proxy
    $0 -t "Fix bug" -n "Fix the login bug" -d -p
    
    # Using MCP server
    $0 -t "Refactor code" -n "Refactor the auth module" -m

EOF
    exit 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--title)
            TASK_TITLE="$2"
            shift 2
            ;;
        -n|--notes)
            TASK_NOTES="$2"
            shift 2
            ;;
        -f|--file)
            INSTRUCTIONS_FILE="$2"
            shift 2
            ;;
        -d|--docker)
            DOCKER_MODE=true
            shift
            ;;
        -p|--start-proxy)
            START_PROXY=true
            shift
            ;;
        -m|--mcp)
            USE_MCP=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate arguments
if [ -z "$TASK_TITLE" ]; then
    log_error "Task title is required"
    usage
fi

# Load instructions from file if provided
if [ ! -z "$INSTRUCTIONS_FILE" ]; then
    if [ -f "$INSTRUCTIONS_FILE" ]; then
        TASK_NOTES="$(cat "$INSTRUCTIONS_FILE")"
    else
        log_error "Instructions file not found: $INSTRUCTIONS_FILE"
        exit 1
    fi
fi

# Default notes if not provided
if [ -z "$TASK_NOTES" ]; then
    TASK_NOTES="Complete the task: $TASK_TITLE"
fi

# Export run ID for tracing
export RUN_ID="$RUN_ID"

log_info "Starting OneShot task creation"
log_info "Run ID: $RUN_ID"
log_info "Task: $TASK_TITLE"

# Check prerequisites
cd "$REPO_ROOT"

# Check if we're in a git repo
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    log_error "Not in a git repository"
    exit 1
fi

# Start proxy workers if requested
if [ "$START_PROXY" = "true" ]; then
    log_info "Starting proxy workers..."
    
    # Start local workers from this repo
    if [ -f "$REPO_ROOT/scripts/start_synth_workers.sh" ]; then
        "$REPO_ROOT/scripts/start_synth_workers.sh" &
        sleep 2
        log_info "Proxy workers start requested"
    else
        log_warn "scripts/start_synth_workers.sh not found, skipping proxy startup"
    fi
fi

# Check if proxy is available
if curl -s -o /dev/null -w "%{http_code}" http://localhost:$PROXY_PORT/health | grep -q "200\|401"; then
    log_info "Proxy is available on port $PROXY_PORT"
    export HTTPS_PROXY="http://localhost:$PROXY_PORT"
    export HTTP_PROXY="http://localhost:$PROXY_PORT"
else
    log_warn "Proxy not available, proceeding without proxy"
fi

# Start tool server
if [ "$USE_MCP" = "true" ]; then
    log_info "MCP mode selected - ensure MCP server is configured in ~/.codex/config.toml"
    
    # Create MCP config example if not exists
    CODEX_CONFIG_DIR="$HOME/.codex"
    if [ ! -f "$CODEX_CONFIG_DIR/config.toml" ]; then
        mkdir -p "$CODEX_CONFIG_DIR"
        cat > "$CODEX_CONFIG_DIR/config.toml.example" << EOF
[mcp_servers.oneshot]
command = "python3"
args = ["$SCRIPT_DIR/mcp_oneshot_server.py"]
env = { RUN_ID = "${RUN_ID}" }
EOF
        log_info "Created example MCP config at $CODEX_CONFIG_DIR/config.toml.example"
        log_warn "Please configure MCP server in $CODEX_CONFIG_DIR/config.toml"
    fi
else
    log_info "Starting HTTP tool server on port $TOOL_SERVER_PORT..."
    
    python3 "$SCRIPT_DIR/tool_server.py" --port $TOOL_SERVER_PORT &
    TOOL_SERVER_PID=$!
    
    # Wait for server to start
    sleep 2
    
    # Verify server is running
    if ! curl -s http://localhost:$TOOL_SERVER_PORT/health | grep -q "healthy"; then
        log_error "Tool server failed to start"
        exit 1
    fi
    
    log_info "Tool server started successfully (PID: $TOOL_SERVER_PID)"
fi

# Prepare Codex prompt
if [ "$USE_MCP" = "true" ]; then
    CODEX_PROMPT="You have access to MCP tools for task management. 

IMPORTANT: Immediately call the tool 'repo.start_task.v1' with:
- task_title: '$TASK_TITLE'
- notes: The instructions below

After completing the task, call 'repo.end_task.v1' with a brief summary.

Instructions:
$TASK_NOTES"
else
    CODEX_PROMPT="You have access to HTTP endpoints for task management at http://localhost:$TOOL_SERVER_PORT

IMPORTANT: Immediately make a POST request to http://localhost:$TOOL_SERVER_PORT/start-task with:
{
  \"task_title\": \"$TASK_TITLE\",
  \"notes\": \"See instructions below\"
}

After completing the task, make a POST request to http://localhost:$TOOL_SERVER_PORT/end-task with:
{
  \"summary\": \"Brief summary of what was done\"
}

Instructions:
$TASK_NOTES"
fi

# Create prompt file
PROMPT_FILE="/tmp/oneshot_prompt_${RUN_ID}.txt"
echo "$CODEX_PROMPT" > "$PROMPT_FILE"

log_info "Prompt saved to: $PROMPT_FILE"

# Run Codex
if [ "$DOCKER_MODE" = "true" ]; then
    log_info "Running Codex in Docker container..."
    
    # Docker run command with proper proxy setup
    docker run -it --rm \
        -v "$REPO_ROOT:/workspace" \
        -w /workspace \
        -e HTTPS_PROXY="http://host.docker.internal:$PROXY_PORT" \
        -e HTTP_PROXY="http://host.docker.internal:$PROXY_PORT" \
        -e RUN_ID="$RUN_ID" \
        --add-host host.docker.internal:host-gateway \
        codex-oneshot \
        bash -c "cat $PROMPT_FILE | codex"
else
    log_info "Running Codex on host..."
    
    # Check if codex is available
    if ! command -v codex &> /dev/null; then
        log_error "Codex CLI not found. Please install it first."
        log_info "Installation: npm install -g @anthropic/codex"
        exit 1
    fi
    
    # Run codex with the prompt
    cat "$PROMPT_FILE" | codex
fi

# Check if task was completed
if [ -f /tmp/oneshot_state.json ]; then
    log_warn "Task was started but not completed (state file still exists)"
    log_info "State file: /tmp/oneshot_state.json"
    
    # Show task details
    TASK_SLUG=$(jq -r '.task_slug' /tmp/oneshot_state.json)
    log_info "Task slug: $TASK_SLUG"
else
    # Look for created task directory
    CREATED_DIR="$REPO_ROOT/data/tasks/created"
    if [ -d "$CREATED_DIR" ]; then
        LATEST_TASK=$(ls -t "$CREATED_DIR" | head -1)
        if [ ! -z "$LATEST_TASK" ]; then
            log_info "Task completed successfully!"
            log_info "Task directory: $CREATED_DIR/$LATEST_TASK"
            
            # Show summary
            echo ""
            log_info "Task Summary:"
            echo "============="
            
            if [ -f "$CREATED_DIR/$LATEST_TASK/notes.md" ]; then
                head -20 "$CREATED_DIR/$LATEST_TASK/notes.md"
            fi
            
            echo ""
            log_info "Next steps:"
            echo "1. Review the diff: $CREATED_DIR/$LATEST_TASK/diff.patch"
            echo "2. Fill out the evaluation rubric: $CREATED_DIR/$LATEST_TASK/evaluation/rubric_template.md"
            echo "3. Implement tests: $CREATED_DIR/$LATEST_TASK/evaluation/tests_skeleton/"
            echo "4. Validate trace data: $CREATED_DIR/$LATEST_TASK/trace/"
        fi
    fi
fi

log_info "OneShot task creation completed"