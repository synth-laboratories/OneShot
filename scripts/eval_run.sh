#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RUN_DIR_INPUT="${1:-}"
TASK_DIR_INPUT="${2:-}"

if [[ -z "$RUN_DIR_INPUT" ]]; then
  echo "Usage: $0 <run_dir> [prepared_task_dir]" >&2
  echo "Example: $0 $REPO_ROOT/data/runs/20250823__14-54-33" >&2
  exit 1
fi

# Normalize to absolute path
if [[ "$RUN_DIR_INPUT" != /* ]]; then RUN_DIR_INPUT="$REPO_ROOT/$RUN_DIR_INPUT"; fi
RUN_DIR="$RUN_DIR_INPUT"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "Error: run_dir does not exist: $RUN_DIR" >&2
  exit 1
fi

# Resolve prepared task directory either from arg or metadata.json
if [[ -z "$TASK_DIR_INPUT" ]]; then
  META_JSON="$RUN_DIR/metadata.json"
  if [[ -f "$META_JSON" ]]; then
    TASK_DIR_INPUT=$(jq -r '.task_path // .task_dir // empty' "$META_JSON" 2>/dev/null || true)
  fi
fi

if [[ -z "$TASK_DIR_INPUT" ]]; then
  echo "Error: could not infer prepared task dir from run metadata; pass it as the second argument." >&2
  exit 1
fi

if [[ "$TASK_DIR_INPUT" != /* ]]; then TASK_DIR_INPUT="$REPO_ROOT/$TASK_DIR_INPUT"; fi
TASK_DIR="$TASK_DIR_INPUT"

if [[ ! -d "$TASK_DIR" ]]; then
  echo "Error: prepared task dir does not exist: $TASK_DIR" >&2
  exit 1
fi

echo "[eval] run_dir=$RUN_DIR"
echo "[eval] task_dir=$TASK_DIR"

# Ensure src is importable for -m one_shot
export PYTHONPATH="$REPO_ROOT/src:${PYTHONPATH:-}"

# Run evaluation module
uv run python -m one_shot.evaluate_run "$RUN_DIR" "$TASK_DIR" | cat

# Print brief summary, including duration if available
SUMMARY_JSON="$RUN_DIR/evaluation_results.json"
RUN_RESULTS_JSON="$RUN_DIR/results.json"

if [[ -f "$SUMMARY_JSON" ]]; then
  echo "\n[evaluate_run] Summary:"
  uv run python - << 'PY'
import json, sys
from pathlib import Path
run_dir = Path(sys.argv[1])
eval_path = run_dir / 'evaluation_results.json'
data = json.load(open(eval_path))
lm = data.get('lm_evaluation') or {}
lm_score = lm.get('weighted_score')
total = data.get('evaluation', {}).get('total_score')
print(f"  Unit tests score: {total:.0%}" if isinstance(total, (int, float)) else "  Unit tests score: N/A")
print(f"  LM (gpt-5-nano) score: {lm_score:.0%}" if isinstance(lm_score, (int, float)) else "  LM (gpt-5-nano) score: N/A")
PY
  "$RUN_DIR"
fi

if [[ -f "$RUN_RESULTS_JSON" ]]; then
  uv run python - << 'PY'
import json, sys
from datetime import datetime, timezone
from dateutil import parser as dtp
run = json.load(open(sys.argv[1]))
start = run.get('start_time')
end = run.get('end_time')
if start and end:
    try:
        # Try ISO parsing (dateutil is available via uv-installed deps if present)
        s = dtp.parse(start)
        e = dtp.parse(end)
        dur = (e - s).total_seconds()
        print(f"  Duration: {dur:.1f}s")
    except Exception:
        print("  Duration: N/A")
else:
    print("  Duration: N/A")
PY
  "$RUN_RESULTS_JSON" || true
fi

exit 0


