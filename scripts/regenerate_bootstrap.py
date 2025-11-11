#!/usr/bin/env python3
"""
Helper script to regenerate box_bootstrap.sh from template.
This ensures we always use the latest bootstrap script without breaking Docker cache.
"""

import sys
from pathlib import Path

# Extract the bootstrap_content template from prepare_task_for_eval.py
bootstrap_content = '''#!/bin/bash
set -euo pipefail

echo "🚀 Starting OneShot task evaluation (headless exec)..."

# Ensure common install locations are on PATH
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Env
export TASK_ID="${TASK_ID}"
export PYTHONUNBUFFERED=1
export CODEX_NONINTERACTIVE=1
export RUST_LOG=${RUST_LOG:-info}
export CODEX_TUI_RECORD_SESSION=1
export CODEX_TUI_SESSION_LOG_PATH=/app/artifacts/codex-session.jsonl
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-5-mini}"

# Load .env if it exists (for OPENAI_BASE_URL, etc.)
if [ -f "/app/.env" ]; then
  set -a
  source /app/.env
  set +a
fi

ARTIFACTS_DIR=/app/artifacts
mkdir -p "$ARTIFACTS_DIR"

# Perform pre-baseline removals if specified
if [ -f "/app/remove_repo_paths.txt" ]; then
  echo "[pre] removing files listed in /app/remove_repo_paths.txt" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    echo "[pre] rm -rf /app/repo/$p" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null
    rm -rf "/app/repo/$p" 2>/dev/null || true
  done < "/app/remove_repo_paths.txt"
fi

# Log chosen model (config is provided via bind mount and CLI -m)
echo "[model] OPENAI_MODEL=${OPENAI_MODEL:-}" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null

# Pre-run: show config locations and contents for verification
echo "[check] whoami=$(whoami), home=$HOME" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null
echo "[check] listing /root/.codex" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null
ls -la /root/.codex 2>&1 | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null || true
for p in \
  /root/.codex/config.toml \
  /root/.config/codex/config.toml \
  /app/.codex/config.toml \
  /app/config.toml; do
  if [ -f "$p" ]; then
    echo "[check] found $p:" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null
    sed -n '1,200p' "$p" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null
  else
    echo "[check] missing $p" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null
  fi
done
if [ -f "/root/.codex/config.toml" ]; then
  cp -f "/root/.codex/config.toml" "$ARTIFACTS_DIR/codex-config.pre-run.toml" 2>/dev/null || true
fi

# Snapshot files before run (in /app and $HOME)
BEFORE_SNAPSHOT=$(mktemp)
{ find /app -type f 2>/dev/null; find "$HOME" -type f 2>/dev/null; } | sort > "$BEFORE_SNAPSHOT"

# Prepare repo baseline commit (capture current working tree)
if [ -d "/app/repo/.git" ]; then
  (
    cd /app/repo
    BASELINE_HEAD="$(git rev-parse --verify -q HEAD || true)"
    echo -n "${BASELINE_HEAD:-}" > /app/artifacts/baseline_head.txt
    git add -A || true
    if ! git diff --cached --quiet; then
      git config user.email codex@local
      git config user.name Codex
      git commit -m "baseline: pre-codex state" >/dev/null 2>&1 || true
    fi
    git rev-parse --verify -q HEAD > /app/artifacts/baseline_sha.txt || true
  )
fi

# Build prompt
PROMPT=""
if [ -f "/app/LM_INSTRUCTIONS.md" ]; then
  PROMPT="$(cat /app/LM_INSTRUCTIONS.md)"
elif [ -f "/app/tb_meta.json" ]; then
  PROMPT="$(jq -r '.lm.instructions // empty' /app/tb_meta.json)"
fi

if [ -z "$PROMPT" ]; then
  echo "❌ No LM instructions found; cannot run headlessly." >&2
  exit 1
fi

echo "Running Codex exec (non-interactive) in /app/repo..."
# Assemble Codex arguments (reasoning is mandatory on GPT‑5 models).
# Codex -c flags use dotted paths: model_reasoning_effort (not reasoning.effort or reasoning_effort)
REASONING_ARGS=()
# Check if model requires reasoning (gpt-5-*, o1, o1-mini, o1-preview)
if [[ "${OPENAI_MODEL}" =~ ^gpt-5- ]] || [[ "${OPENAI_MODEL}" =~ ^(o1|o1-mini|o1-preview)$ ]]; then
  REASONING_VALUE="${OPENAI_REASONING_EFFORT:-medium}"
  # Codex -c flags: use model_reasoning_effort (verified working format)
  REASONING_ARGS+=(-c)
  REASONING_ARGS+=("model_reasoning_effort=\\"${REASONING_VALUE}\\"")
  REASONING_ARGS+=(-c)
  REASONING_ARGS+=("reasoning.summaries=\\"auto\\"")
  echo "[reasoning] Detected reasoning-required model: ${OPENAI_MODEL}, setting model_reasoning_effort=${REASONING_VALUE}"
  echo "[reasoning] Passing -c flags: ${REASONING_ARGS[*]}"
fi
# Always pass model via Codex -m/--model flag; OPENAI_MODEL defaults to gpt-5-mini
( cd /app/repo && \
  echo "[debug] model: ${OPENAI_MODEL}" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null && \
  echo "[debug] REASONING_ARGS count: ${#REASONING_ARGS[@]}" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null && \
  echo "[debug] REASONING_ARGS: ${REASONING_ARGS[*]}" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null && \
  echo "[debug] codex exec ${REASONING_ARGS[*]} -m '${OPENAI_MODEL}'" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null && \
  echo "[debug] config file at /root/.codex/config.toml:" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null && \
  cat /root/.codex/config.toml | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null && \
  codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
    "${REASONING_ARGS[@]}" \
    -m "$OPENAI_MODEL" \
    "$PROMPT" \
  2>&1 | tee "$ARTIFACTS_DIR/codex-run.log" )

# Persist final codex config for debugging
if [ -f "/root/.codex/config.toml" ]; then
  cp -f "/root/.codex/config.toml" "$ARTIFACTS_DIR/codex-config.toml" 2>/dev/null || true
fi
STATUS=${PIPESTATUS[0]}

# Copy logs if any
LOG_DIR="$HOME/.codex/log"
if [ -d "$LOG_DIR" ]; then
  cp -f "$LOG_DIR"/codex-tui.log "$ARTIFACTS_DIR"/ 2>/dev/null || true
  cp -f "$LOG_DIR"/session-*.jsonl "$ARTIFACTS_DIR"/ 2>/dev/null || true
fi

# Copy session logs if any (Codex may write to ~/.codex/sessions/YYYY/...)
SESS_DIR="$HOME/.codex/sessions"
if [ -d "$SESS_DIR" ]; then
  mkdir -p "$ARTIFACTS_DIR/codex-sessions"
  find "$SESS_DIR" -type f -name '*.jsonl' -print0 2>/dev/null | \
    xargs -0 -I{} cp -f "{}" "$ARTIFACTS_DIR/codex-sessions/" 2>/dev/null || true
fi

# Summarize artifact sizes and session counts
RUN_LOG_BYTES=0
TUI_LOG_BYTES=0
if [ -f "$ARTIFACTS_DIR/codex-run.log" ]; then RUN_LOG_BYTES=$(wc -c < "$ARTIFACTS_DIR/codex-run.log" | awk '{print $1}'); fi
if [ -f "$ARTIFACTS_DIR/codex-tui.log" ]; then TUI_LOG_BYTES=$(wc -c < "$ARTIFACTS_DIR/codex-tui.log" | awk '{print $1}'); fi
SESSION_COUNT=$(find "$ARTIFACTS_DIR" -maxdepth 2 -type f -name 'session-*.jsonl' 2>/dev/null | wc -l | awk '{print $1}')
if [ "$SESSION_COUNT" -gt 0 ]; then
  SESSION_BYTES=$(find "$ARTIFACTS_DIR" -maxdepth 2 -type f -name 'session-*.jsonl' -print0 2>/dev/null | xargs -0 wc -c | tail -n1 | awk '{print $1}')
else
  SESSION_BYTES=0
fi
echo "[collect] artifacts: run_bytes=$RUN_LOG_BYTES, tui_bytes=$TUI_LOG_BYTES, sessions_count=$SESSION_COUNT, sessions_bytes=$SESSION_BYTES"

# Snapshot files after run and summarize new files
AFTER_SNAPSHOT=$(mktemp)
{ find /app -type f 2>/dev/null; find "$HOME" -type f 2>/dev/null; } | sort > "$AFTER_SNAPSHOT"
if command -v comm >/dev/null 2>&1; then
  NEW_FILES=$(comm -13 "$BEFORE_SNAPSHOT" "$AFTER_SNAPSHOT")
else
  NEW_FILES=$(grep -F -x -v -f "$BEFORE_SNAPSHOT" "$AFTER_SNAPSHOT" || true)
fi
NEW_FILES_COUNT=$(printf "%s\n" "$NEW_FILES" | sed '/^$/d' | wc -l | awk '{print $1}')
echo "[collect] new_files_created=$NEW_FILES_COUNT"

# Capture git status and diffs from /app/repo
if [ -d "/app/repo/.git" ]; then
  (
    cd /app/repo
    git status --porcelain=v1 | tee /app/artifacts/container_git_status.txt >/dev/null
    git diff > /app/artifacts/container_git_diff.patch
    # Stage and capture cached diff
    git add -A || true
    git diff --cached > /app/artifacts/container_git_diff_cached.patch
    # Commit if there are staged changes
    if ! git diff --cached --quiet; then
      git config user.email codex@local
      git config user.name Codex
      git commit -m "Codex changes in container" >/dev/null 2>&1 || true
    fi
    # Diff relative to baseline
    BASELINE_SHA="$(cat /app/artifacts/baseline_sha.txt 2>/dev/null || true)"
    if [ -n "$BASELINE_SHA" ]; then
      git diff --stat "$BASELINE_SHA"..HEAD | tee /app/artifacts/container_git_diff_from_baseline.stat >/dev/null
      git diff "$BASELINE_SHA"..HEAD > /app/artifacts/container_git_diff_from_baseline.patch
      git format-patch "$BASELINE_SHA"..HEAD --stdout > /app/artifacts/container_git_commits_from_baseline.patch || true
      CHANGED_FILES=$(git diff --name-only "$BASELINE_SHA"..HEAD | wc -l | awk '{print $1}')
      ADD_DEL=$(git diff --numstat "$BASELINE_SHA"..HEAD | awk '{adds+=$1; dels+=$2} END {if (NR==0) print "0 0"; else print adds+0 " " dels+0}')
      ADDED_LINES=$(echo "$ADD_DEL" | awk '{print $1}')
      DELETED_LINES=$(echo "$ADD_DEL" | awk '{print $2}')
    else
      CHANGED_FILES=$(git status --porcelain=v1 | wc -l | awk '{print $1}')
      ADD_DEL=$(git diff --numstat | awk '{adds+=$1; dels+=$2} END {if (NR==0) print "0 0"; else print adds+0 " " dels+0}')
      ADDED_LINES=$(echo "$ADD_DEL" | awk '{print $1}')
      DELETED_LINES=$(echo "$ADD_DEL" | awk '{print $2}')
    fi
    # Produce canonical diff.patch for host evaluators
    if [ -f /app/artifacts/container_git_diff_from_baseline.patch ]; then
      cp -f /app/artifacts/container_git_diff_from_baseline.patch /app/artifacts/diff.patch || true
    elif [ -f /app/artifacts/container_git_diff.patch ]; then
      cp -f /app/artifacts/container_git_diff.patch /app/artifacts/diff.patch || true
    else
      git diff HEAD > /app/artifacts/diff.patch || true
    fi
    COMMIT_SHA=$(git rev-parse --verify -q HEAD || true)
    echo "[collect] git: changed_files=$CHANGED_FILES, additions=$ADDED_LINES, deletions=$DELETED_LINES, head=${COMMIT_SHA:-none}"
  )
else
  echo "[collect] git: no repo at /app/repo"
fi

exit $STATUS
'''

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: regenerate_bootstrap.py <task_path>", file=sys.stderr)
        sys.exit(1)
    
    task_path = Path(sys.argv[1])
    overlay_dir = task_path / "overlay_files"
    
    if not overlay_dir.exists():
        overlay_dir.mkdir(parents=True, exist_ok=True)
    
    bootstrap_path = overlay_dir / "box_bootstrap.sh"
    bootstrap_path.write_text(bootstrap_content)
    bootstrap_path.chmod(0o755)
    print(f"✓ Regenerated {bootstrap_path}")

if __name__ == "__main__":
    main()

