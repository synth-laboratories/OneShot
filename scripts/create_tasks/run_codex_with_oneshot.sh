#!/bin/bash
# Run codex-synth with OneShot MCP tools available
# This starts an interactive session where the agent has access to start-task and end-task tools

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PROXY_PORT=18080
RUN_ID="oneshot_$(date +%Y%m%d_%H%M%S)_$$"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

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
cd "$REPO_ROOT"
export RUN_ID="$RUN_ID"

# Check/setup proxy if requested
if [ "$USE_PROXY" = "true" ]; then
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:$PROXY_PORT/health 2>/dev/null | grep -q "200\|401"; then
        log_info "Proxy available on port $PROXY_PORT"
        export HTTPS_PROXY="http://localhost:$PROXY_PORT"
        export HTTP_PROXY="http://localhost:$PROXY_PORT"
    else
        log_warn "Proxy not available. Start it with: cd ../.. && ./run_synth_workers.sh"
        log_info "Continuing without proxy..."
    fi
fi

# Ensure MCP server is configured
MCP_SERVER_PATH="$SCRIPT_DIR/mcp_oneshot_server.py"
MCP_CONFIG_FILE="$HOME/.codex-synth/mcp_settings.json"
MCP_CONFIG_DIR="$(dirname "$MCP_CONFIG_FILE")"

# Create MCP configuration
mkdir -p "$MCP_CONFIG_DIR"

log_info "Setting up MCP configuration..."
cat > "$MCP_CONFIG_FILE" << EOF
{
  "mcpServers": {
    "oneshot": {
      "command": "python3",
      "args": ["$MCP_SERVER_PATH"],
      "env": {
        "RUN_ID": "$RUN_ID",
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
EOF

log_info "MCP server configured at: $MCP_CONFIG_FILE"

# Create instructions template
INSTRUCTIONS_FILE="/tmp/oneshot_instructions_${RUN_ID}.md"

if [ -n "$TASK_TITLE" ]; then
    cat > "$INSTRUCTIONS_FILE" << EOF
# Task: $TASK_TITLE

${TASK_DESCRIPTION:-Please complete this task.}

## IMPORTANT: Task Management Instructions

You have access to MCP tools for task tracking. You MUST:

1. **IMMEDIATELY** call the \`repo.start_task.v1\` tool with:
   - task_title: "$TASK_TITLE"
   - notes: A brief description of what you plan to do
   - labels: Any relevant labels (e.g., ["feature"], ["bugfix"], ["refactor"])

2. Work on the task as requested.

3. **WHEN FINISHED**, call the \`repo.end_task.v1\` tool with:
   - summary: Brief summary of what was accomplished
   - labels: Any additional labels

These tools will:
- Create git commits at start and end
- Capture the diff of all changes
- Generate a task directory with all artifacts
- Export trace data for review

Available MCP tools:
- \`repo.start_task.v1\` - Start a new task (call this FIRST)
- \`repo.end_task.v1\` - End the current task (call this LAST)
- \`repo.check_readiness.v1\` - Check if git worktree is ready
- \`repo.autofix_readiness.v1\` - Auto-fix common git issues

Example usage:
\`\`\`
Tool: repo.start_task.v1
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

1. **START** by calling \`repo.start_task.v1\` with:
   - task_title: A descriptive title
   - notes: What you plan to do
   - labels: Relevant labels

2. **END** by calling \`repo.end_task.v1\` with:
   - summary: What was accomplished
   - labels: Any additional labels

These tools will:
- Create git commits at start and end
- Capture the diff of all changes
- Generate a task directory with all artifacts
- Export trace data for review

Available MCP tools:
- \`repo.start_task.v1\` - Start a new task
- \`repo.end_task.v1\` - End the current task
- \`repo.check_readiness.v1\` - Check if git worktree is ready
- \`repo.autofix_readiness.v1\` - Auto-fix common git issues

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
echo ""

if [ -n "$TASK_TITLE" ]; then
    # If we have a task, pass it as initial input
    echo "Please complete the task described above. Remember to use start_task and end_task!" | codex-synth
else
    # Just start interactive session
    codex-synth
fi