#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

TASKS_DIR="${REPO_ROOT}/data/tasks/prepared"

for task in $(find "$TASKS_DIR" -mindepth 1 -maxdepth 1 -type d | sort); do
    "$SCRIPT_DIR/run_codex_box.sh" "$task"
done

echo "Completed sequential execution."

