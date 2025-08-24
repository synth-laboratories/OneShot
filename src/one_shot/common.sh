#!/bin/bash
# Shared utilities for Codex-in-the-Box

set -euo pipefail

# Generate run ID
generate_run_id() {
    date '+%Y-%m-%d__%H-%M-%S'
}

# Logging with timestamp
log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

# Retry with exponential backoff
retry_with_backoff() {
    local max_attempts="${1:-5}"
    local delay="${2:-1}"
    local factor="${3:-2}"
    shift 3
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if "$@"; then
            return 0
        fi
        log "Attempt $attempt failed, retrying in ${delay}s..."
        sleep $delay
        delay=$((delay * factor))
        attempt=$((attempt + 1))
    done
    return 1
}

# Safe JSON extraction using Python (no eval)
json_extract() {
    local file="$1"
    local path="$2"
    
    # Prefer jq if available, otherwise use safe Python
    if command -v jq >/dev/null 2>&1; then
        jq -r "$path" "$file" 2>/dev/null || echo ""
    else
        python3 -c "
import json, sys
try:
    with open('$file', 'r') as f:
        data = json.load(f)
    # Handle dotted paths like 'git.url'
    parts = '$path'.replace('[', '.').replace(']', '').split('.')
    result = data
    for part in parts:
        if part.startswith('.'):
            part = part[1:]
        if part:
            if isinstance(result, dict):
                result = result.get(part, '')
            elif isinstance(result, list):
                try:
                    result = result[int(part)]
                except (ValueError, IndexError):
                    result = ''
                    break
            else:
                result = ''
                break
    print(result if result is not None else '')
except Exception:
    print('')
" 2>/dev/null || echo ""
    fi
}

# Check for required commands
check_prerequisites() {
    # First try to load .env file if OPENAI_API_KEY not set
    if [ -z "${OPENAI_API_KEY:-}" ]; then
        local env_candidates=(
            "$PWD/.env"
            "$(dirname "${BASH_SOURCE[0]}")/.env"
            "$(dirname "${BASH_SOURCE[0]}")/../../.env"
            "$(dirname "${BASH_SOURCE[0]}")/../../../.env"
        )
        
        for env_file in "${env_candidates[@]}"; do
            if [ -f "$env_file" ]; then
                log "Loading environment from $env_file"
                # Only load OPENAI_API_KEY to avoid other script errors
                export OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" "$env_file" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
                if [ -n "$OPENAI_API_KEY" ]; then
                    break
                fi
            fi
        done
    fi
    
    local missing=()
    
    for cmd in docker python3; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        log "ERROR: Missing required commands: ${missing[*]}"
        return 1
    fi
    
    # Check Docker is running (portable, no GNU timeout dependency)
    log "Checking Docker daemon..."
    if ! docker ps >/dev/null 2>&1; then
        log "ERROR: Docker daemon is not running or not responding"
        log "Please start Docker Desktop and try again"
        log "On macOS: open -a Docker"
        return 1
    fi
    log "✅ Docker is running"
    
    # Check for OpenAI API key
    if [ -z "${OPENAI_API_KEY:-}" ]; then
        log "ERROR: OPENAI_API_KEY environment variable not set"
        return 1
    fi
    
    return 0
}

# Determine proxy host based on platform
get_proxy_host() {
    local platform="$(uname -s)"
    case "$platform" in
        Linux)
            # On Linux, use the bridge gateway IP
            docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || echo "172.17.0.1"
            ;;
        Darwin|MINGW*|MSYS*|CYGWIN*)
            # On Mac/Windows, use host.docker.internal
            echo "host.docker.internal"
            ;;
        *)
            log "WARNING: Unknown platform $platform, defaulting to host.docker.internal"
            echo "host.docker.internal"
            ;;
    esac
}

# Export common functions
export -f generate_run_id
export -f log
export -f retry_with_backoff
export -f json_extract
export -f check_prerequisites
export -f get_proxy_host