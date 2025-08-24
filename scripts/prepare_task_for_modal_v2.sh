#!/bin/bash
# Prepare a task directory for Modal execution by creating a tar of codex installation
set -euo pipefail

TASK_DIR="${1:-}"

if [ -z "$TASK_DIR" ]; then
    echo "Usage: $0 <task_directory>"
    echo "Prepares a task directory for Modal by archiving the local codex installation"
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
    echo "Removing old codex-files directory..."
    rm -rf "$TASK_DIR/codex-files"
fi

if [ -f "$TASK_DIR/codex-files.tar.gz" ]; then
    echo "Removing old codex-files.tar.gz..."
    rm -f "$TASK_DIR/codex-files.tar.gz"
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
        echo "Creating tar archive..."
        
        # Create tar archive directly
        cd "$(dirname "$CODEX_PACKAGE_PATH")"
        tar -czf "$TASK_DIR/codex-files.tar.gz" "$(basename "$CODEX_PACKAGE_PATH")"
        
        # Get size
        SIZE=$(du -h "$TASK_DIR/codex-files.tar.gz" | cut -f1)
        echo "✅ Created codex-files.tar.gz ($SIZE)"
        
        echo "Task is ready for Modal execution!"
        echo ""
        echo "To run with Modal:"
        echo "  cd $(dirname "$0")"
        echo "  modal run codex_modal_runner.py --task-dir $TASK_DIR"
        echo ""
        echo "Or using the unified runner:"
        echo "  SANDBOX_BACKEND=modal ./one_shot/run_sandbox.sh $TASK_DIR"
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