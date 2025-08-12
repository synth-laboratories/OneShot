#!/bin/bash
# Simple MCP monitoring script - run in separate terminal

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

clear

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}CITB MCP Server Monitor${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Test if the server responds
echo -e "${BLUE}Testing MCP server...${NC}"
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | \
    python3 "$SCRIPT_DIR/mcp_citb_server.py" 2>/dev/null | \
    python3 -m json.tool > /dev/null 2>&1 && \
    echo -e "${GREEN}✓ MCP server responds correctly${NC}" || \
    echo -e "${RED}✗ MCP server not responding${NC}"

echo ""
echo -e "${YELLOW}Monitoring log file: /tmp/citb_mcp_server.out${NC}"
echo -e "${YELLOW}This will show all MCP server activity${NC}"
echo ""
echo -e "${GREEN}========================================${NC}"
echo ""

# Create log file if it doesn't exist
touch /tmp/citb_mcp_server.out

# Tail the log with timestamps
tail -f /tmp/citb_mcp_server.out | while IFS= read -r line; do
    echo "[$(date '+%H:%M:%S')] $line"
done