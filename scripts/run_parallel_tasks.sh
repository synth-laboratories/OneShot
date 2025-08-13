#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

TASKS_DIR="${REPO_ROOT}/data/tasks/prepared"
JOBS="${JOBS:-4}"

mapfile -t TASKS < <(find "$TASKS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)

run_one() {
    local task="$1"
    RUN_ID="$(date +%Y%m%d__%H-%M-%S)__$(basename "$task")" RUN_ID="$RUN_ID" "$SCRIPT_DIR/run_codex_box.sh" "$task" >/dev/null 2>&1 &
}

for t in "${TASKS[@]}"; do
    while (( $(jobs -rp | wc -l) >= JOBS )); do sleep 0.2; done
    run_one "$t"
done

wait
echo "Completed running $((${#TASKS[@]})) tasks in parallel."

