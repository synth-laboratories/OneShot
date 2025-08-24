#!/bin/bash
# Simpler version that just runs tasks and shows basic results
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TASKS_DIR="${REPO_ROOT}/data/tasks/prepared"
RUN_SCRIPT="${SCRIPT_DIR}/run_codex_box.sh"
TIMEOUT="${1:-1800}"
TOKEN_LIMIT="${2:-100000}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "======================================"
echo "Running tasks in parallel"
echo "======================================"
echo ""

# Find tasks
TASKS=($(find "$TASKS_DIR" -maxdepth 1 -type d -name "*" | grep -v "^$TASKS_DIR$"))

echo "Found ${#TASKS[@]} tasks:"
for task in "${TASKS[@]}"; do
    echo "  - $(basename "$task")"
done
echo ""

# Run in parallel
PIDS=()
for task in "${TASKS[@]}"; do
    echo "Starting: $(basename "$task")"
    "$RUN_SCRIPT" "$task" "$TIMEOUT" "$TOKEN_LIMIT" > /tmp/$(basename "$task").log 2>&1 &
    PIDS+=($!)
done

echo ""
echo "Waiting for completion..."
echo ""

# Wait and collect results
SUCCESS=0
FAILED=0

for i in "${!PIDS[@]}"; do
    pid=${PIDS[$i]}
    task=${TASKS[$i]}
    task_name=$(basename "$task")
    
    if wait $pid; then
        echo -e "${GREEN}✓${NC} $task_name: Success"
        SUCCESS=$((SUCCESS + 1))
        
        # Show last run directory
        if grep -q "Results in:" /tmp/${task_name}.log 2>/dev/null; then
            run_dir=$(grep "Results in:" /tmp/${task_name}.log | tail -1 | awk '{print $NF}')
            echo "    → $run_dir"
        fi
    else
        echo -e "${RED}✗${NC} $task_name: Failed"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "======================================"
echo "Summary: ${GREEN}$SUCCESS passed${NC}, ${RED}$FAILED failed${NC}"
echo "======================================"

# Cleanup
rm -f /tmp/*.log 2>/dev/null || true