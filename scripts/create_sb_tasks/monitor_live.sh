#!/bin/bash
# Live monitoring of MCP server activity
# Run this in a separate terminal while using codex

clear
echo "========================================"
echo "CITB MCP Server Live Monitor"
echo "========================================"
echo ""
echo "Monitoring: /tmp/citb_mcp_server.out"
echo ""

# Watch for tool calls and results
tail -f /tmp/citb_mcp_server.out | while IFS= read -r line; do
    # Highlight important lines
    if echo "$line" | grep -q "Tool call:"; then
        echo -e "\033[1;32m>>> $line\033[0m"  # Green for tool calls
    elif echo "$line" | grep -q '"ok": true'; then
        echo -e "\033[1;34m✓ SUCCESS: $line\033[0m"  # Blue for success
    elif echo "$line" | grep -q '"ok": false'; then
        echo -e "\033[1;31m✗ FAILURE: $line\033[0m"  # Red for actual failures
    elif echo "$line" | grep -q "ERROR"; then
        echo -e "\033[1;31m$line\033[0m"  # Red for errors
    elif echo "$line" | grep -q "Started task:\|Ended task:"; then
        echo -e "\033[1;33m$line\033[0m"  # Yellow for task events
    else
        echo "$line"
    fi
done