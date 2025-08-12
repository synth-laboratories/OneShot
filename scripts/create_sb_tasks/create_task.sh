#!/bin/bash
# Create a task by running codex-synth with CITB tools and instructions
# Usage: ./create_task.sh "Your task description here"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MCP_SERVER_PATH="$SCRIPT_DIR/mcp_citb_server.py"
RUN_ID="citb_$(date +%Y%m%d_%H%M%S)_$$"
PROXY_PORT=18080

# Get user's task from arguments
USER_TASK="$*"

if [ -z "$USER_TASK" ]; then
    echo "Usage: $0 <task description>"
    echo ""
    echo "Example: $0 Add a README section about testing"
    exit 1
fi

# Setup MCP configuration
MCP_CONFIG_FILE="$HOME/.codex-synth/mcp_settings.json"
mkdir -p "$(dirname "$MCP_CONFIG_FILE")"

cat > "$MCP_CONFIG_FILE" << EOF
{
  "mcpServers": {
    "citb": {
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

# Setup proxy if available
if curl -s -o /dev/null -w "%{http_code}" http://localhost:$PROXY_PORT/health 2>/dev/null | grep -q "200\|401"; then
    export HTTPS_PROXY="http://localhost:$PROXY_PORT"
    export HTTP_PROXY="http://localhost:$PROXY_PORT"
    echo "✓ Proxy enabled"
fi

# Generate task title from description (first few words)
TASK_TITLE="$(echo "$USER_TASK" | cut -d' ' -f1-5)"

# Create the full prompt with CITB instructions
FULL_PROMPT="You have MCP tools available for task tracking. 

CRITICAL: You MUST immediately call the tool 'repo.start_task.v1' with:
{
  \"task_title\": \"$TASK_TITLE\",
  \"notes\": \"User requested: $USER_TASK\",
  \"labels\": [\"created\"]
}

After completing the task below, you MUST call 'repo.end_task.v1' with:
{
  \"summary\": \"<brief summary of what was done>\",
  \"labels\": [\"completed\"]
}

These tools will create git commits, capture diffs, and save all artifacts.

Now, please complete this task:
$USER_TASK"

cd "$REPO_ROOT"
export RUN_ID="$RUN_ID"

echo "=================================="
echo "CITB Task Creation"
echo "Run ID: $RUN_ID"
echo "Task: $USER_TASK"
echo "=================================="
echo ""

# Pass the full prompt to codex-synth via stdin
echo "$FULL_PROMPT" | codex-synth

echo ""
echo "=================================="
echo "Task session completed"
echo "Check for output in: development/codex_coach/synth_bench/tasks/created/"
echo "=================================="