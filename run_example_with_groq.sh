#!/usr/bin/env bash

set -euo pipefail

# Set up environment for groq
echo "=== Setting up OneShot Bench example with Groq ==="
echo ""

# Check for GROQ_API_KEY or use OPENAI_API_KEY as fallback
if [[ -z "${GROQ_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
    echo "Error: Please set GROQ_API_KEY or OPENAI_API_KEY environment variable"
    echo "Example: export GROQ_API_KEY=your-api-key-here"
    exit 1
fi

# Use GROQ_API_KEY if available, otherwise fall back to OPENAI_API_KEY
export OPENAI_API_KEY="${GROQ_API_KEY:-${OPENAI_API_KEY}}"

# Configure groq settings
# Groq uses OpenAI-compatible API, so we set the base URL
export OPENAI_BASE_URL="https://api.groq.com/openai/v1"

# Set the model to groq's gpt-oss-120b as requested
# This is Groq's high-performance OSS model (500 tokens/sec)
export OPENAI_MODEL="openai/gpt-oss-120b"

echo "Configuration:"
echo "  Model: $OPENAI_MODEL"
echo "  Base URL: $OPENAI_BASE_URL"
echo "  API Key: ${OPENAI_API_KEY:0:10}..."
echo ""

# Run the example task
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_PATH="$SCRIPT_DIR/data/tasks/created/hello-world-example"

echo "Running task from: $TASK_PATH"
echo ""

# Use Modal sandbox by default (set SANDBOX_BACKEND=docker to use Docker instead)
export SANDBOX_BACKEND="${SANDBOX_BACKEND:-modal}"
echo "Sandbox backend: $SANDBOX_BACKEND"
echo ""

# Execute the task
bash "$SCRIPT_DIR/scripts/run_codex_box.sh" "$TASK_PATH"

echo ""
echo "=== Task completed! ==="
echo ""
echo "Check the results in: data/runs/<run_id>/"

