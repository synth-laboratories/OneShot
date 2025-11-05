#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== OneShot Bench Quick Example ==="
echo ""

# Load environment variables from .env if it exists
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    echo "Loading API keys from .env..."
    set -a  # automatically export all variables
    source "$SCRIPT_DIR/.env"
    set +a
else
    echo "Warning: No .env file found at $SCRIPT_DIR/.env"
fi

# Check if we have an API key
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo ""
    echo "ERROR: OPENAI_API_KEY not found!"
    echo ""
    echo "Please either:"
    echo "  1. Add OPENAI_API_KEY to .env file, OR"
    echo "  2. Export it: export OPENAI_API_KEY='your-key-here'"
    echo ""
    exit 1
fi

# Default to gpt-5-nano (fast and cheap)
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-5-nano}"
export SANDBOX_BACKEND="${SANDBOX_BACKEND:-docker}"

# Unset OPENAI_BASE_URL to use standard OpenAI endpoint (unless explicitly set after loading .env)
if [[ "${USE_CUSTOM_BASE_URL:-0}" != "1" ]]; then
    unset OPENAI_BASE_URL
fi

echo "Configuration:"
echo "  Model: $OPENAI_MODEL"
echo "  Backend: $SANDBOX_BACKEND"
echo "  Base URL: ${OPENAI_BASE_URL:-https://api.openai.com/v1 (default)}"
echo "  API Key: ${OPENAI_API_KEY:0:10}..."
echo ""

# Run the prepared task
TASK_PATH="$SCRIPT_DIR/data/tasks/prepared/quick-hello-world"

if [[ ! -d "$TASK_PATH" ]]; then
    echo "Task not yet prepared. Preparing now..."
    TASK_PATH="$SCRIPT_DIR/data/tasks/created/quick-hello-world"
fi

echo "Running task from: $TASK_PATH"
echo ""

bash "$SCRIPT_DIR/scripts/run_codex_box.sh" "$TASK_PATH"

echo ""
echo "=== Task completed! ==="
echo ""
echo "Check results in: data/runs/<run_id>/"

