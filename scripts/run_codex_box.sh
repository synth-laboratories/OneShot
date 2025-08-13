#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/common.sh"

RUN_ID="${RUN_ID:-$(date +%Y%m%d__%H-%M-%S)}"
RUN_DIR="${REPO_ROOT}/data/runs/${RUN_ID}"
TRACE_DIR="${REPO_ROOT}/data/traces/v3"

TASK_PATH_INPUT="${1:-}"
if [[ -z "$TASK_PATH_INPUT" ]]; then
    echo "Usage: $0 <path-to-task> [extra docker args]"
    exit 1
fi

# Auto-prepare created tasks to prepared if needed
if [[ -f "$TASK_PATH_INPUT/tb_meta.json" && ! -f "$TASK_PATH_INPUT/Dockerfile" ]]; then
    echo "Detected created task. Preparing for evaluation..."
    uv run one_shot_bench.prepare_task_for_eval --task-dir "$TASK_PATH_INPUT"
    # Find prepared path by slug
    SLUG="$(basename "$TASK_PATH_INPUT")"
    PREPARED_DIR="${REPO_ROOT}/data/tasks/prepared/${SLUG}"
    if [[ -d "$PREPARED_DIR" ]]; then
        TASK_PATH_INPUT="$PREPARED_DIR"
        echo "Prepared task at: $TASK_PATH_INPUT"
    fi
fi

mkdir -p "$RUN_DIR"

if ! docker_is_running; then
    echo "Docker daemon is not running" >&2
    exit 1
fi

# Delegate to existing sandbox runner if present, else run a basic docker build/run
if [[ -x "${REPO_ROOT}/scripts/run_sandbox.sh" ]]; then
    "${REPO_ROOT}/scripts/run_sandbox.sh" "$TASK_PATH_INPUT" "$RUN_DIR" "$TRACE_DIR" "${@:2}"
else
    docker build -t oneshot-task "$TASK_PATH_INPUT"
    docker run --rm -v "$RUN_DIR:/runs" oneshot-task
fi

echo "Run artifacts in: $RUN_DIR"

