#!/bin/bash
# Setup codex in Modal volume using Modal CLI directly
set -euo pipefail

echo "Setting up codex in Modal volume..."

# Find local codex installation
CODEX_PATH=$(which codex)
if [ -z "$CODEX_PATH" ]; then
    echo "Error: codex not found. Install with: npm install -g @openai/codex"
    exit 1
fi

# Resolve the actual codex package location
CODEX_REAL_PATH=$(realpath "$CODEX_PATH")
CODEX_PACKAGE_PATH=$(dirname $(dirname "$CODEX_REAL_PATH"))

# Check common locations for the @openai/codex package
if [ -d "$CODEX_PACKAGE_PATH/lib/node_modules/@openai/codex" ]; then
    CODEX_PACKAGE_PATH="$CODEX_PACKAGE_PATH/lib/node_modules/@openai/codex"
elif [ -d "$CODEX_PACKAGE_PATH/@openai/codex" ]; then
    CODEX_PACKAGE_PATH="$CODEX_PACKAGE_PATH/@openai/codex"
fi

if [ ! -d "$CODEX_PACKAGE_PATH" ]; then
    echo "Error: Could not find codex package directory at $CODEX_PACKAGE_PATH"
    exit 1
fi

echo "Found codex at: $CODEX_PACKAGE_PATH"

# Create temporary directory for the archive
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Copy codex to temp directory (to avoid symlink issues)
echo "Copying codex files..."
cp -r "$CODEX_PACKAGE_PATH" "$TEMP_DIR/codex"

# Create the volume if it doesn't exist
echo "Creating Modal volume 'codex-installation' if needed..."
modal volume create codex-installation 2>/dev/null || true

# Upload the directory to Modal volume
echo "Uploading codex to Modal volume..."
modal volume put codex-installation "$TEMP_DIR/codex" codex

echo "✅ Codex has been uploaded to Modal volume 'codex-installation'"
echo ""
echo "You can now run tasks with:"
echo "  modal run codex_modal_runner.py::main --task-dir ./path/to/task"