#!/usr/bin/env python3
"""
Prepare a task from tasks/created/ for evaluation in tasks/prepared/.
This script takes the raw output from task creation and adds all necessary
files and structure for Docker-based evaluation.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Any

def fix_git_url(url: str, repo_info: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Fix Git URLs and known problematic repositories."""
    # Convert SSH to HTTPS
    if url.startswith("git@github.com:"):
        url = url.replace("git@github.com:", "https://github.com/")
    
    # Map of problematic repos to working alternatives
    # No fallback mappings; enforce public HTTPS URL
    if not url.startswith("https://github.com/"):
        raise ValueError(f"Unsupported git_url: {url}. Please use a public HTTPS GitHub URL.")
    
    return url, repo_info

def convert_evaluation_format(old_eval: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    """Convert old evaluation format to new format with rubrics and test scripts."""
    # Check if already in new format
    if "rubrics" in old_eval and "test_scripts" in old_eval:
        return old_eval
    
    # Create default rubrics based on old format or defaults
    rubrics = []
    test_scripts = []
    
    # Add default rubrics
    rubrics.append({
        "id": "task_completion",
        "criterion": "Task requirements were successfully completed",
        "weight": 0.4
    })
    
    rubrics.append({
        "id": "code_quality", 
        "criterion": "Code follows best practices and is well-structured",
        "weight": 0.3
    })
    
    rubrics.append({
        "id": "testing",
        "criterion": "Appropriate tests were added and pass",
        "weight": 0.3
    })
    
    # Add basic test scripts
    test_scripts.append({
        "path": "tests/test_task_completion.py",
        "rubric_id": "task_completion",
        "content": """import os
import subprocess

def test_changes_were_made():
    \"\"\"Verify that meaningful changes were made to the repository.\"\"\"
    # Check if any Python files were modified or created
    result = subprocess.run(['git', 'diff', '--name-only', '--cached'], 
                          capture_output=True, text=True)
    changed_files = result.stdout.strip().split('\\n') if result.stdout.strip() else []
    
    # At least one file should have been changed
    assert len(changed_files) > 0, "No files were changed by the agent"
    
    # Check for test files
    test_files = [f for f in changed_files if 'test' in f.lower()]
    assert len(test_files) > 0, "No test files were created or modified"
"""
    })
    
    test_scripts.append({
        "path": "tests/test_code_runs.py",
        "rubric_id": "testing",
        "content": """import subprocess
import os

def test_tests_can_run():
    \"\"\"Verify that any added tests can actually run without errors.\"\"\"
    # Find test files
    test_files = []
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.startswith('test_') and file.endswith('.py'):
                test_files.append(os.path.join(root, file))
    
    if not test_files:
        # No test files found, which might be okay for some tasks
        return
    
    # Try to run the first test file found
    for test_file in test_files[:1]:  # Just test one to avoid long runs
        result = subprocess.run(['python3', '-m', 'pytest', test_file, '-v', '--tb=short'],
                              capture_output=True, text=True)
        # We don't require tests to pass, just that they can run
        assert 'SyntaxError' not in result.stderr, f"Syntax error in {test_file}"
        assert 'ImportError' not in result.stderr, f"Import error in {test_file}"
"""
    })
    
    return {
        "rubrics": rubrics,
        "test_scripts": test_scripts
    }

def create_dockerfile(task_meta: Dict[str, Any]) -> str:
    """Generate Dockerfile content for the task."""
    repo = task_meta.get("repo", {})
    
    return f'''FROM ubuntu:24.04

# Build arguments from tb_meta.json
ARG GIT_URL="{repo.get('git_url', '')}"
ARG GIT_BRANCH="{repo.get('branch', 'main')}"
ARG GIT_COMMIT="{repo.get('start_commit_sha', 'HEAD')}"
ARG TASK_ID="{task_meta.get('task_id', 'unknown')}"

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Configure apt for robustness (keep ports.ubuntu.com for arm64)
RUN set -eux; \
  cat >/etc/apt/apt.conf.d/99-robust <<'EOF' 
Acquire::Retries "5";
Acquire::By-Hash "yes";
Acquire::CompressionTypes::Order "gz";
Acquire::http::No-Cache "true";
Acquire::https::No-Cache "true";
Acquire::http::Pipeline-Depth "0";
EOF

# Install system dependencies (robust against mirror hash mismatches)
RUN set -eux; \
  rm -rf /var/lib/apt/lists/*; \
  for i in 1 2 3; do \
    apt-get clean; \
    apt-get update --fix-missing || true; \
    apt-get update -o Acquire::CompressionTypes::Order::=gz -o Acquire::http::No-Cache=true -o Acquire::https::No-Cache=true && break || sleep 2; \
  done; \
  apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    python3 \
    python3-venv \
    python3-pip \
    tmux \
    vim \
    less \
    jq \
    ca-certificates \
    util-linux \
    expect; \
  apt-get clean; rm -rf /var/lib/apt/lists/*

# Install Node.js and npm
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \\
    apt-get install -y nodejs

# Copy the entire codex installation from host
COPY codex-files/ /usr/local/lib/node_modules/@openai/codex/
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex && \\
    chmod +x /usr/local/bin/codex

# Create working directory
WORKDIR /app

# Clone the repository and ensure it's writable
RUN git clone ${{GIT_URL}} repo \\
    && cd repo \\
    && git checkout ${{GIT_BRANCH}} \\
    && git reset --hard ${{GIT_COMMIT}} \\
    && chmod -R 777 /app/repo

# Copy overlay files into /app
COPY overlay_files/ /app/

# Copy overlay files intended for the cloned repository (after clone)
COPY overlay_repo_files/ /app/repo/
RUN chmod -R 777 /app/repo

# Copy .env file if it exists (will be copied from task directory)
COPY .env /app/.env

# Copy mitmproxy CA certificate if it exists
COPY mitmproxy-ca-cert.pem* /usr/local/share/ca-certificates/mitmproxy-ca.crt

# Update CA certificates
RUN if [ -f /usr/local/share/ca-certificates/mitmproxy-ca.crt ]; then \\
        update-ca-certificates; \\
    fi

# Set Node to use the system CA bundle
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

# Ensure uv default install locations are on PATH
ENV PATH="/root/.local/bin:/root/.cargo/bin:${{PATH}}"

# Make scripts executable
RUN chmod +x /app/*.sh

# Create artifacts directory
RUN mkdir -p /app/artifacts

# Install Python test dependencies
RUN pip3 install --break-system-packages pytest

# Install mitmproxy for container-side tracing
RUN pip3 install --break-system-packages mitmproxy

# Create directories for container-side tracing
RUN mkdir -p /app/traces /app/src

# Copy tracing scripts from cloned repo (after git clone)
RUN cp -r /app/repo/src/local_tracing/ /app/src/local_tracing/ && chmod +x /app/src/local_tracing/*.py

# Expose proxy port internally
EXPOSE 18080

# Set up environment
ENV TASK_ID=${{TASK_ID}}
ENV PYTHONUNBUFFERED=1

# Default command
CMD ["/app/box_bootstrap.sh"]
'''

def prepare_task(created_task_path: Path, prepared_dir: Path) -> None:
    """Convert a task from created/ to prepared/ format."""
    
    # Read the task metadata
    tb_meta_path = created_task_path / "tb_meta.json"
    if not tb_meta_path.exists():
        print(f"Error: No tb_meta.json found at {tb_meta_path}")
        return
    
    with open(tb_meta_path) as f:
        task_meta = json.load(f)
    
    print(f"\n📦 Preparing task: {task_meta['task_id']}")
    print(f"   Title: {task_meta['metadata']['title']}")
    
    # Print git information
    if "repo" in task_meta:
        repo_info = task_meta["repo"]
        print(f"   Branch: {repo_info.get('branch', 'unknown')}")
        if "start_commit" in task_meta:
            print(f"   Start Commit: {task_meta['start_commit']}")
        if "end_commit" in task_meta:
            print(f"   End Commit: {task_meta['end_commit']}")
    
    # Fix repository URL and info
    if "repo" in task_meta:
        original_url = task_meta["repo"].get("git_url", "")
        fixed_url, updated_repo = fix_git_url(original_url, task_meta["repo"])
        task_meta["repo"] = updated_repo
        task_meta["repo"]["git_url"] = fixed_url
        if original_url != fixed_url:
            print(f"   Fixed Git URL: {fixed_url}")
        
        # Update repository URL to current remote and commit SHA to current branch tip
        try:
            import subprocess
            # Get current remote URL
            current_remote = subprocess.run(
                ["git", "remote", "get-url", "origin"], 
                capture_output=True, text=True, cwd=Path.cwd()
            ).stdout.strip()
            
            # Convert SSH to HTTPS if needed
            if current_remote.startswith("git@github.com:"):
                current_remote = current_remote.replace("git@github.com:", "https://github.com/")
            
            # Update the repository URL
            if current_remote and current_remote != fixed_url:
                task_meta["repo"]["git_url"] = current_remote
                print(f"   Updated Git URL to current remote: {current_remote}")
            
            # Get current commit SHA for the branch
            branch = task_meta["repo"].get("branch", "main")
            current_commit = subprocess.run(
                ["git", "ls-remote", "--heads", current_remote, branch], 
                capture_output=True, text=True, cwd=Path.cwd()
            ).stdout.strip()
            
            if current_commit:
                current_sha = current_commit.split()[0]
                # Update both start and end commit SHA to current tip
                task_meta["repo"]["start_commit_sha"] = current_sha
                task_meta["repo"]["end_commit_sha"] = current_sha
                print(f"   Updated commit SHA to current tip: {current_sha[:8]}...")
            
        except Exception as e:
            print(f"   ⚠️  Warning: Could not update repository info: {e}")
            print("   Using original repository information")
    
    # Convert evaluation format
    if "evaluation" in task_meta:
        task_meta["evaluation"] = convert_evaluation_format(
            task_meta["evaluation"], 
            task_meta["task_id"]
        )
        print(f"   Added {len(task_meta['evaluation']['rubrics'])} rubrics")
        print(f"   Added {len(task_meta['evaluation']['test_scripts'])} test scripts")
    
    # Extract base task name (remove timestamp)
    task_name = task_meta["task_id"].rsplit("_", 2)[0]
    
    # Create output directory
    output_dir = prepared_dir / task_name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create overlay_files directory
    overlay_dir = output_dir / "overlay_files"
    overlay_dir.mkdir(exist_ok=True)
    # Create overlay_repo_files directory (files to inject into cloned repo)
    overlay_repo_dir = output_dir / "overlay_repo_files"
    overlay_repo_dir.mkdir(exist_ok=True)
    
    # Write updated tb_meta.json to both locations
    with open(output_dir / "tb_meta.json", "w") as f:
        json.dump(task_meta, f, indent=2)
    
    with open(overlay_dir / "tb_meta.json", "w") as f:
        json.dump(task_meta, f, indent=2)
    
    # Create Dockerfile
    with open(output_dir / "Dockerfile", "w") as f:
        f.write(create_dockerfile(task_meta))
    print("   Created Dockerfile")
    
    # Check if this is a uv-managed repository
    repo_root = Path(__file__).parents[2]
    uv_lock_file = repo_root / "uv.lock"
    pyproject_file = repo_root / "pyproject.toml"
    
    if not (uv_lock_file.exists() and pyproject_file.exists()):
        print("\n❌ Critical Error: This repository does not use uv for dependency management.")
        print("Non-uv repositories are not yet supported.")
        print("Please ensure your repository has uv.lock and pyproject.toml files.")
        
        # Clean up the partially created output directory
        if output_dir.exists():
            shutil.rmtree(output_dir)
            print(f"Cleaned up incomplete output directory: {output_dir}")
        
        return
    
    print("   ✓ Detected uv-managed repository")
    
    # Generate box_bootstrap.sh script
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

ARTIFACTS_DIR=/app/artifacts
mkdir -p "$ARTIFACTS_DIR"

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
# Always pass model via Codex -m/--model flag; OPENAI_MODEL defaults to gpt-5-mini
( cd /app/repo && \
  echo "[debug] model: ${OPENAI_MODEL}" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null && \
  echo "[debug] codex exec -m '${OPENAI_MODEL}'" | tee -a "$ARTIFACTS_DIR/codex-run.log" >/dev/null && \
  codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
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
      read ADD_DEL <<< "$(git diff --numstat "$BASELINE_SHA"..HEAD | awk '{adds+=$1; dels+=$2} END {print (adds+0)"""" """"(dels+0)}')"
      ADDED_LINES=$(echo "$ADD_DEL" | awk '{print $1}')
      DELETED_LINES=$(echo "$ADD_DEL" | awk '{print $2}')
    else
      CHANGED_FILES=$(git status --porcelain=v1 | wc -l | awk '{print $1}')
      read ADD_DEL <<< "$(git diff --numstat | awk '{adds+=$1; dels+=$2} END {print (adds+0)"""" """"(dels+0)}')"
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
    
    bootstrap_path = overlay_dir / "box_bootstrap.sh"
    with open(bootstrap_path, 'w') as f:
        f.write(bootstrap_content)
    os.chmod(bootstrap_path, 0o755)
    print("   Generated box_bootstrap.sh")
    
    # Create codex-synth wrapper script (compat): delegate to codex
    codex_wrapper_content = '''#!/bin/bash
# Compatibility wrapper: map codex-synth -> codex
export TASK_ID="${{TASK_ID}}"
export PYTHONUNBUFFERED=1
exec codex "$@"
'''
    
    codex_wrapper_path = overlay_dir / "codex-synth"
    with open(codex_wrapper_path, 'w') as f:
        f.write(codex_wrapper_content)
    os.chmod(codex_wrapper_path, 0o755)
    print("   Generated codex-synth wrapper")

    # Copy container tracing scripts
    scripts_dir = Path(__file__).parents[2] / "scripts"
    for script_name in ["container_start_proxy.sh", "export_container_traces.sh", "container_trace_health_check.sh"]:
        script_src = scripts_dir / script_name
        if script_src.exists():
            shutil.copy(script_src, overlay_dir / script_name)
            os.chmod(overlay_dir / script_name, 0o755)
            print(f"   Copied {script_name}")
        else:
            print(f"   ⚠️  Warning: Script {script_name} not found at {script_src}")

    # Copy other files from created task
    for file_name in ["LM_INSTRUCTIONS.md", "repo_info.json", "diff.patch", "notes.md"]:
        src_file = created_task_path / file_name
        if src_file.exists():
            shutil.copy(src_file, overlay_dir / file_name)
    
    # Copy evaluation directory if it exists
    eval_src = created_task_path / "evaluation"
    if eval_src.exists() and eval_src.is_dir():
        eval_dst = output_dir / "evaluation"
        if eval_dst.exists():
            shutil.rmtree(eval_dst)
        shutil.copytree(eval_src, eval_dst)
        print("   Copied evaluation directory")
    
    print(f"\n✅ Task prepared at: {output_dir}")
    print(f"   Ready for evaluation with: ./run_codex_box.sh {output_dir}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python prepare_task_for_eval.py <created_task_path>")
        print("\nExample:")
        print("  python prepare_task_for_eval.py tasks/created/my-task_20250810_123456")
        print("\nThis will prepare the task in tasks/prepared/my-task/")
        sys.exit(1)
    
    created_task_path = Path(sys.argv[1])
    if not created_task_path.exists():
        print(f"Error: Task path {created_task_path} does not exist")
        sys.exit(1)
    
    # Determine output directory under data/tasks
    repo_root = Path(__file__).parents[2]
    prepared_dir = repo_root / "data" / "tasks" / "prepared"

    prepare_task(created_task_path, prepared_dir)

if __name__ == "__main__":
    main()