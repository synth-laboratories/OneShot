#!/bin/bash
#
# Unified sandbox runner that dispatches to Docker or Modal backend
# based on SANDBOX_BACKEND environment variable.
#
# Usage:
#   SANDBOX_BACKEND=docker ./run_sandbox.sh <task_dir> [options]
#   SANDBOX_BACKEND=modal ./run_sandbox.sh <task_dir> [options]
#
# Default: docker

set -e

# Set default backend if not specified
SANDBOX_BACKEND="${SANDBOX_BACKEND:-docker}"

# Get task directory (first argument)
TASK_DIR="${1}"
if [ -z "$TASK_DIR" ]; then
    echo "Error: Task directory not specified"
    echo "Usage: $0 <task_dir> [options]"
    exit 1
fi

# Shift to get remaining arguments
shift

echo "Running with sandbox backend: $SANDBOX_BACKEND"
echo "Task directory: $TASK_DIR"

case "$SANDBOX_BACKEND" in
    docker)
        echo "Dispatching to Docker backend..."
        # Call existing Docker runner
        exec "$(dirname "$0")/run_codex_box.sh" "$TASK_DIR" "$@"
        ;;
        
    modal)
        echo "Dispatching to Modal backend..."
        
        # Check if Modal is installed
        if ! command -v modal &> /dev/null; then
            echo "Error: Modal CLI not found. Please install with: pip install modal"
            exit 1
        fi
        
        # Check Modal authentication
        if ! modal token ls &> /dev/null; then
            echo "Error: Not authenticated with Modal. Please run: modal setup"
            exit 1
        fi
        
        # Parse arguments for Modal
        TIMEOUT="${AGENT_TIMEOUT_SEC:-1800}"
        TOKEN_LIMIT="${AGENT_MAX_TOKENS:-100000}"
        MODEL="${OPENAI_MODEL:-gpt-4o-mini}"
        
        # Convert to absolute path
        TASK_DIR_ABS="$(cd "$TASK_DIR" && pwd)"
        
        # Prepare task for Modal (copy codex-files)
        echo "Preparing task for Modal execution..."
        PREPARE_SCRIPT="$(dirname "$0")/../prepare_task_for_modal_v2.sh"
        if [ -f "$PREPARE_SCRIPT" ]; then
            "$PREPARE_SCRIPT" "$TASK_DIR_ABS"
            if [ $? -ne 0 ]; then
                echo "Error: Failed to prepare task for Modal"
                exit 1
            fi
        else
            echo "Warning: prepare_task_for_modal_v2.sh not found"
            echo "Continuing anyway, but task may fail without codex-files"
        fi
        
        # Run via Modal
        echo "Starting Modal execution..."
        echo "  Timeout: ${TIMEOUT}s"
        echo "  Token limit: ${TOKEN_LIMIT}"
        echo "  Model: ${MODEL}"
        
        # Change to parent directory to find the modal runner
        cd "$(dirname "$0")/.."
        
        # Execute Modal runner
        modal run codex_modal_runner.py \
            --task-dir "$TASK_DIR_ABS" \
            --timeout "$TIMEOUT" \
            --token-limit "$TOKEN_LIMIT" \
            --model "$MODEL"
        
        MODAL_EXIT_CODE=$?
        
        if [ $MODAL_EXIT_CODE -eq 0 ]; then
            echo "Modal execution completed successfully"
        else
            echo "Modal execution failed with exit code: $MODAL_EXIT_CODE"
        fi
        
        exit $MODAL_EXIT_CODE
        ;;
        
    *)
        echo "Error: Unknown sandbox backend: $SANDBOX_BACKEND"
        echo "Supported backends: docker, modal"
        exit 1
        ;;
esac