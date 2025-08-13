#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"

docker_is_running() {
    if docker ps >/dev/null 2>&1; then
        return 0
    fi
    return 1
}


