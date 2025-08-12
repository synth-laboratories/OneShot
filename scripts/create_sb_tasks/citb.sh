#!/bin/bash
# Run OpenAI Codex with CITB MCP tools configured
# This ensures MCP server is configured and runs codex-synth (which wraps codex)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MCP_SERVER_PATH="$SCRIPT_DIR/mcp_citb_server.py"
RUN_ID="citb_$(date +%Y%m%d_%H%M%S)_$$"

# Ensure MCP is configured for codex
"$SCRIPT_DIR/setup_codex_mcp.sh"

# Set run ID for this session
export RUN_ID="$RUN_ID"

cd "$REPO_ROOT"

echo "=================================="
echo "CITB-enabled Codex Session"
echo "Run ID: $RUN_ID"
echo "=================================="
echo ""
echo "MCP tools available:"
echo "  • repo_start_task - Start tracking a task"
echo "  • repo_end_task - End and save the task"
echo "  • repo_check_readiness - Check git status"
echo "  • repo_autofix_readiness - Fix git issues"
echo ""
echo "IMPORTANT: Always call start_task when beginning work"
echo "          and end_task when finished!"
echo ""
echo "Starting codex-synth (with proxy and MCP)..."
echo "----------------------------------"

# Run codex-synth which sets up proxy and runs codex
exec codex-synth "$@"