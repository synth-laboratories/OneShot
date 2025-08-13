#!/bin/bash
# Setup MCP server for OpenAI Codex CLI
# This configures ~/.codex/config.toml to use our OneShot MCP server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_SERVER_PATH="$SCRIPT_DIR/mcp_oneshot_server.py"
CONFIG_FILE="$HOME/.codex/config.toml"
CONFIG_DIR="$(dirname "$CONFIG_FILE")"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Setting up MCP for OpenAI Codex...${NC}"

# Create config directory
mkdir -p "$CONFIG_DIR"

# Check if config exists and backup if needed
if [ -f "$CONFIG_FILE" ]; then
    echo -e "${YELLOW}Backing up existing config to ${CONFIG_FILE}.backup${NC}"
    cp "$CONFIG_FILE" "${CONFIG_FILE}.backup"
fi

# Add or update OneShot MCP server in config
echo -e "${GREEN}Configuring MCP server in ~/.codex/config.toml${NC}"

# Check if mcp_servers.oneshot already exists
if grep -q "\[mcp_servers.oneshot\]" "$CONFIG_FILE" 2>/dev/null; then
    echo -e "${YELLOW}OneShot MCP server already configured, updating...${NC}"
    # Remove old oneshot config
    sed -i.tmp '/\[mcp_servers.oneshot\]/,/^\[/{ /^\[/!d; }' "$CONFIG_FILE"
    sed -i.tmp '/\[mcp_servers.oneshot\]/d' "$CONFIG_FILE"
    rm -f "${CONFIG_FILE}.tmp"
fi

# Append OneShot MCP server config
cat >> "$CONFIG_FILE" << EOF

[mcp_servers.oneshot]
command = "python3"
args = ["$MCP_SERVER_PATH"]
env = { "PYTHONUNBUFFERED" = "1" }
EOF

echo -e "${GREEN}✓ MCP server configured${NC}"
echo ""
echo "Configuration written to: $CONFIG_FILE"
echo "MCP server path: $MCP_SERVER_PATH"
echo ""
echo -e "${GREEN}Available MCP tools will be:${NC}"
echo "  • repo.start_task.v1 - Start tracking a task"
echo "  • repo.end_task.v1 - End and save the task"
echo "  • repo.check_readiness.v1 - Check git status"
echo "  • repo.autofix_readiness.v1 - Fix git issues"
echo ""
echo -e "${GREEN}To verify setup:${NC}"
echo "1. Run: codex"
echo "2. Ask: 'What tools do you have access to?'"
echo "3. Check logs: tail -F ~/.codex/log/codex-tui.log"
echo ""
echo -e "${GREEN}To test MCP server directly:${NC}"
echo "npx @modelcontextprotocol/inspector --cli python3 $MCP_SERVER_PATH"