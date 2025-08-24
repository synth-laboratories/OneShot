#!/bin/bash
# Run multiple tasks in parallel and aggregate scores
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TASKS_DIR="${REPO_ROOT}/data/tasks/prepared"
RUN_SCRIPT="${SCRIPT_DIR}/run_codex_box.sh"
TIMEOUT="${1:-1800}"
TOKEN_LIMIT="${2:-100000}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Find all available tasks
TASKS=()
for task_dir in "$TASKS_DIR"/*; do
    if [ -d "$task_dir" ] && [ -f "$task_dir/tb_meta.json" ]; then
        TASKS+=("$task_dir")
    fi
done

if [ ${#TASKS[@]} -eq 0 ]; then
    echo "No tasks found in $TASKS_DIR"
    exit 1
fi

echo -e "${BOLD}${BLUE}=====================================${NC}"
echo -e "${BOLD}Running ${#TASKS[@]} tasks in parallel${NC}"
echo -e "${BOLD}${BLUE}=====================================${NC}"
echo ""
echo "Tasks:"
for task in "${TASKS[@]}"; do
    echo "  - $(basename "$task")"
done
echo ""
echo "Timeout: ${TIMEOUT}s"
echo "Token limit: ${TOKEN_LIMIT}"
echo ""

# Create temp directory for outputs
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Start time
START_TIME=$(date +%s)

# Run tasks in parallel, capturing output
echo -e "${YELLOW}Starting parallel execution...${NC}"
echo ""

# Arrays to store parallel info (using indices instead of associative arrays)
PIDS=()
OUTPUT_FILES=()
TASK_NAMES=()

for task in "${TASKS[@]}"; do
    task_name=$(basename "$task")
    output_file="$TEMP_DIR/${task_name}.out"
    
    echo -e "  ${BLUE}►${NC} Starting: $task_name"
    
    # Run task in background, redirecting all output
    "$RUN_SCRIPT" "$task" "$TIMEOUT" "$TOKEN_LIMIT" > "$output_file" 2>&1 &
    
    PIDS+=($!)
    OUTPUT_FILES+=("$output_file")
    TASK_NAMES+=("$task_name")
done

echo ""
echo -e "${YELLOW}Waiting for tasks to complete...${NC}"
echo ""

# Wait for all tasks and collect results
TOTAL_SCORE=0
TOTAL_POSSIBLE=0

# Process results
for i in "${!PIDS[@]}"; do
    pid="${PIDS[$i]}"
    output_file="${OUTPUT_FILES[$i]}"
    task_name="${TASK_NAMES[$i]}"
    
    echo -e "${BOLD}${task_name}:${NC}"
    
    if wait $pid 2>/dev/null; then
        echo -e "  ${GREEN}✓ Success${NC}"
        
        # Try to extract score from output
        if grep -q "Score:" "$output_file" 2>/dev/null; then
            score=$(grep "Score:" "$output_file" | tail -1 | sed -E 's/.*Score: ([0-9.]+).*/\1/')
            echo -e "  Score: ${BOLD}$score${NC}"
            TOTAL_SCORE=$(echo "$TOTAL_SCORE + $score" | bc 2>/dev/null || echo $TOTAL_SCORE)
            TOTAL_POSSIBLE=$((TOTAL_POSSIBLE + 100))
        else
            echo "  Score: N/A"
        fi
    else
        echo -e "  ${RED}✗ Failed${NC}"
        TOTAL_POSSIBLE=$((TOTAL_POSSIBLE + 100))
    fi
    
    # Extract run directory
    if grep -q "Results in:" "$output_file" 2>/dev/null; then
        run_dir=$(grep "Results in:" "$output_file" | tail -1 | sed 's/.*Results in: //')
        echo "  Run: $run_dir"
    fi
    echo ""
done

# Calculate elapsed time
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
ELAPSED_MIN=$((ELAPSED / 60))
ELAPSED_SEC=$((ELAPSED % 60))

echo -e "${BOLD}${BLUE}=====================================${NC}"
echo -e "${BOLD}Summary${NC}"
echo -e "${BOLD}${BLUE}=====================================${NC}"
echo ""
echo -e "Total time: ${ELAPSED_MIN}m ${ELAPSED_SEC}s"

# Aggregate score
if [ $TOTAL_POSSIBLE -gt 0 ] && command -v bc >/dev/null 2>&1; then
    AVG_SCORE=$(echo "scale=1; $TOTAL_SCORE * 100 / $TOTAL_POSSIBLE" | bc 2>/dev/null || echo "N/A")
    echo -e "Aggregate Score: ${BOLD}${AVG_SCORE}%${NC}"
else
    echo -e "Aggregate Score: ${BOLD}N/A${NC}"
fi

echo ""
echo -e "${GREEN}✓ All tasks completed${NC}"