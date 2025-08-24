#!/bin/bash
# Run multiple tasks sequentially and show detailed results
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
echo -e "${BOLD}Running ${#TASKS[@]} tasks sequentially${NC}"
echo -e "${BOLD}${BLUE}=====================================${NC}"
echo ""

# Start time
START_TIME=$(date +%s)

# Track results
TOTAL_SCORE=0
TOTAL_POSSIBLE=0
TASK_RESULTS=()

# Run each task
for i in "${!TASKS[@]}"; do
    task="${TASKS[$i]}"
    task_name=$(basename "$task")
    task_num=$((i + 1))
    
    echo -e "${BOLD}[$task_num/${#TASKS[@]}] Running: $task_name${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    # Create temp file for output
    TEMP_OUT=$(mktemp)
    
    # Run the task
    if "$RUN_SCRIPT" "$task" "$TIMEOUT" "$TOKEN_LIMIT" 2>&1 | tee "$TEMP_OUT"; then
        status="${GREEN}✓ Success${NC}"
        
        # Extract score and run directory
        if grep -q "Results in:" "$TEMP_OUT" 2>/dev/null; then
            run_dir=$(grep "Results in:" "$TEMP_OUT" | tail -1 | sed 's/.*Results in: //')
            
            # Try to get evaluation results
            eval_file="$run_dir/artifacts/tb_evaluation_results.json"
            if [ -f "$eval_file" ]; then
                # Extract total score using python for JSON parsing
                if command -v python3 >/dev/null 2>&1; then
                    score=$(python3 -c "import json; data=json.load(open('$eval_file')); print(data['evaluation']['total_score']*100)" 2>/dev/null || echo "N/A")
                    
                    # Get rubric scores
                    rubrics=$(python3 -c "
import json
data = json.load(open('$eval_file'))
rubrics = data['evaluation']['rubrics']
for name, info in rubrics.items():
    print(f\"    {name}: {info['score']*100:.0f}% (weight: {info['weight']})\")
" 2>/dev/null || echo "")
                    
                    # Get test results
                    tests=$(python3 -c "
import json
data = json.load(open('$eval_file'))
tests = data.get('test_results', {})
passed = sum(1 for t in tests.values() if t.get('success', False))
total = len(tests)
print(f\"    Tests: {passed}/{total} passed\")
" 2>/dev/null || echo "")
                else
                    score="N/A"
                    rubrics=""
                    tests=""
                fi
            else
                score="N/A"
                rubrics=""
                tests=""
            fi
            
            if [ "$score" != "N/A" ]; then
                TOTAL_SCORE=$(echo "$TOTAL_SCORE + $score" | bc 2>/dev/null || echo $TOTAL_SCORE)
                TOTAL_POSSIBLE=$((TOTAL_POSSIBLE + 100))
            fi
        else
            run_dir="N/A"
            score="N/A"
            rubrics=""
            tests=""
        fi
    else
        status="${RED}✗ Failed${NC}"
        run_dir="N/A"
        score="0"
        rubrics=""
        tests=""
        TOTAL_POSSIBLE=$((TOTAL_POSSIBLE + 100))
        
        # Try to capture error from output
        error_msg=$(tail -20 "$TEMP_OUT" | grep -E "Error:|error:|Failed:|failed:" | head -1 || echo "Unknown error")
    fi
    
    # Clean up temp file
    rm -f "$TEMP_OUT"
    
    # Store result
    TASK_RESULTS+=("${task_name}|${status}|${score}|${run_dir}")
    
    echo ""
    echo -e "${BOLD}Task Result:${NC}"
    echo -e "  Status: $status"
    if [ "$score" != "N/A" ] && [ "$score" != "0" ]; then
        echo -e "  Score: ${BOLD}${score}%${NC}"
        if [ -n "$rubrics" ]; then
            echo -e "  Rubrics:"
            echo "$rubrics"
        fi
        if [ -n "$tests" ]; then
            echo "$tests"
        fi
    elif [ "$score" == "0" ]; then
        echo -e "  Score: ${RED}0%${NC}"
        if [ -n "${error_msg:-}" ]; then
            echo -e "  Error: ${RED}$error_msg${NC}"
        fi
    fi
    if [ "$run_dir" != "N/A" ]; then
        echo -e "  Run: $run_dir"
    fi
    echo ""
done

# Calculate elapsed time
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
ELAPSED_MIN=$((ELAPSED / 60))
ELAPSED_SEC=$((ELAPSED % 60))

# Print summary
echo -e "${BOLD}${BLUE}=====================================${NC}"
echo -e "${BOLD}Final Summary${NC}"
echo -e "${BOLD}${BLUE}=====================================${NC}"
echo ""

# Show all results
for result in "${TASK_RESULTS[@]}"; do
    IFS='|' read -r name status score run_dir <<< "$result"
    echo -e "${BOLD}$name:${NC}"
    echo -e "  $status"
    if [ "$score" != "N/A" ]; then
        if [ "$score" == "0" ]; then
            echo -e "  Score: ${RED}${score}%${NC}"
        else
            echo -e "  Score: ${BOLD}${score}%${NC}"
        fi
    fi
    echo ""
done

echo -e "Total time: ${BOLD}${ELAPSED_MIN}m ${ELAPSED_SEC}s${NC}"

# Aggregate score
if [ $TOTAL_POSSIBLE -gt 0 ] && command -v bc >/dev/null 2>&1; then
    AVG_SCORE=$(echo "scale=1; $TOTAL_SCORE / ${#TASKS[@]}" | bc 2>/dev/null || echo "N/A")
    echo -e "Average Score: ${BOLD}${AVG_SCORE}%${NC}"
else
    echo -e "Average Score: ${BOLD}N/A${NC}"
fi

echo ""
echo -e "${GREEN}✓ All tasks completed${NC}"