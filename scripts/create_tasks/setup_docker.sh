#!/bin/bash
# Setup script for Docker-based OneShot task creation

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "Setting up Docker environment for OneShot..."

# Create Dockerfile if not exists
if [ ! -f "$REPO_ROOT/development/codex_coach/Dockerfile.oneshot" ]; then
    cat > "$REPO_ROOT/development/codex_coach/Dockerfile.oneshot" << 'EOF'
FROM node:18-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    python3 \
    python3-pip \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Codex CLI
RUN npm install -g @anthropic/codex

# Set up git defaults
RUN git config --global user.name "OneShot User" && \
    git config --global user.email "oneshot@localhost"

# Create workspace directory
WORKDIR /workspace

# Copy tool server into container
COPY scripts/create_tasks /tools/create_tasks

# Start tool server by default
CMD ["python3", "/tools/create_tasks/tool_server.py"]
EOF
    echo "Created Dockerfile.oneshot"
fi

# Build Docker image
echo "Building Docker image..."
docker build -f "$REPO_ROOT/development/codex_coach/Dockerfile.oneshot" \
    -t codex-oneshot \
    "$REPO_ROOT/development/codex_coach"

echo "Docker setup complete!"
echo ""
echo "To run a task in Docker:"
echo "  ./run_codex_create_task.sh -t 'Task' -n 'Instructions' -d"
echo ""
echo "Or use the Makefile:"
echo "  make create-task TITLE='Task' NOTES='Instructions' DOCKER=1"