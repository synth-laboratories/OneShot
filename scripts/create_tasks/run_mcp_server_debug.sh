#!/bin/bash
# Run the MCP server in debug mode with live logging
# This is for debugging - run in a separate terminal to monitor MCP activity

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_SERVER_PATH="$SCRIPT_DIR/mcp_oneshot_server.py"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}OneShot MCP Server - Debug Mode${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Server path:${NC} $MCP_SERVER_PATH"
echo -e "${BLUE}Log file:${NC} /tmp/oneshot_mcp_server.out"
echo -e "${BLUE}Working dir:${NC} $(pwd)"
echo ""
echo -e "${YELLOW}This server is configured in:${NC} ~/.codex/config.toml"
echo -e "${YELLOW}Running in stdio mode for MCP protocol${NC}"
echo ""
echo -e "${GREEN}Starting server...${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Create a named pipe for bidirectional communication
PIPE_IN="/tmp/mcp_pipe_in_$$"
PIPE_OUT="/tmp/mcp_pipe_out_$$"
mkfifo "$PIPE_IN" "$PIPE_OUT"

# Cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    rm -f "$PIPE_IN" "$PIPE_OUT"
    exit 0
}
trap cleanup EXIT INT TERM

# Run the MCP server with tee to show both stdin and stdout
echo -e "${BLUE}[Starting MCP server process]${NC}"
(
    # This runs the server and logs everything
    python3 "$MCP_SERVER_PATH" 2>&1 | while IFS= read -r line; do
        echo -e "${GREEN}[SERVER OUT]${NC} $line"
    done
) < "$PIPE_IN" > "$PIPE_OUT" &

SERVER_PID=$!

# Monitor input to the server
(
    while IFS= read -r line; do
        echo -e "${BLUE}[SERVER IN]${NC} $line"
        echo "$line"
    done < "$PIPE_OUT"
) &

# Also tail the log file if it exists
if [ -f /tmp/oneshot_mcp_server.out ]; then
    echo -e "${YELLOW}Tailing log file...${NC}"
    tail -f /tmp/oneshot_mcp_server.out | while IFS= read -r line; do
        echo -e "${YELLOW}[LOG]${NC} $line"
    done &
    TAIL_PID=$!
fi

echo -e "${GREEN}Server is running. Press Ctrl+C to stop.${NC}"
echo ""

# Keep the script running
wait $SERVER_PID