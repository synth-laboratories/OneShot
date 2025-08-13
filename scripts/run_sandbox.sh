#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Fallback to legacy script if still present
if [[ -x "$REPO_ROOT/src/one_shot_bench/run_sandbox.sh" ]]; then
    exec "$REPO_ROOT/src/one_shot_bench/run_sandbox.sh" "$@"
else
    echo "SANDBOX_BACKEND=docker not implemented here; use scripts/run_codex_box.sh directly." >&2
    exit 2
fi

#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../src/one_shot_bench/run_sandbox.sh" "$@"


