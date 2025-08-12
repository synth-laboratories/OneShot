#!/bin/bash
# Prepare a task directory for Modal execution by copying codex installation
set -euo pipefail

TASK_DIR="${1:-}"

if [ -z "$TASK_DIR" ]; then
    echo "Usage: $0 <task_directory>"
    echo "Prepares a task directory for Modal by copying the local codex installation"
    exit 1
fi

# Convert to absolute path
TASK_DIR="$(cd "$TASK_DIR" && pwd)"

if [ ! -f "$TASK_DIR/tb_meta.json" ]; then
    echo "Error: No tb_meta.json found in $TASK_DIR"
    exit 1
fi

echo "Preparing task for Modal: $TASK_DIR"

# Check if codex-files already exists
if [ -d "$TASK_DIR/codex-files" ]; then
    echo "codex-files already exists, removing old version..."
    rm -rf "$TASK_DIR/codex-files"
fi

# Find and copy local codex installation
echo "Looking for local codex installation..."
CODEX_PATH=$(which codex)

if [ -n "$CODEX_PATH" ]; then
    # Resolve the actual codex package location
    CODEX_REAL_PATH=$(realpath "$CODEX_PATH")
    CODEX_PACKAGE_PATH=$(dirname $(dirname "$CODEX_REAL_PATH"))
    
    # Check common locations for the @openai/codex package
    if [ -d "$CODEX_PACKAGE_PATH/lib/node_modules/@openai/codex" ]; then
        CODEX_PACKAGE_PATH="$CODEX_PACKAGE_PATH/lib/node_modules/@openai/codex"
    elif [ -d "$CODEX_PACKAGE_PATH/@openai/codex" ]; then
        CODEX_PACKAGE_PATH="$CODEX_PACKAGE_PATH/@openai/codex"
    fi
    
    if [ -d "$CODEX_PACKAGE_PATH" ]; then
        echo "Found codex at: $CODEX_PACKAGE_PATH"
        echo "Copying to $TASK_DIR/codex-files..."
        
        # Copy entire directory structure
        cp -r "$CODEX_PACKAGE_PATH" "$TASK_DIR/codex-files"
        
        # Count files
        FILE_COUNT=$(find "$TASK_DIR/codex-files" -type f | wc -l)
        echo "✅ Copied $FILE_COUNT files to codex-files"
        
        # Make scripts executable
        find "$TASK_DIR/codex-files" -name "*.js" -o -name "*.sh" | xargs chmod +x 2>/dev/null || true
        
        echo "Task is ready for Modal execution!"
        echo ""
        echo "To run with Modal:"
        echo "  cd $(dirname "$0")"
        echo "  modal run codex_modal_runner.py --task-dir $TASK_DIR"
        echo ""
        echo "Or using the unified runner:"
        echo "  SANDBOX_BACKEND=modal ./one_shot_bench/run_sandbox.sh $TASK_DIR"
    else
        echo "ERROR: Could not find codex package directory!"
        echo "Checked: $CODEX_PACKAGE_PATH"
        exit 1
    fi
else
    echo "ERROR: codex command not found!"
    echo "Please ensure codex is installed: npm install -g @openai/codex"
    exit 1
fi