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
from typing import Dict, Any, List

def fix_git_url(url: str, repo_info: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Fix Git URLs and known problematic repositories."""
    # Convert SSH to HTTPS
    if url.startswith("git@github.com:"):
        url = url.replace("git@github.com:", "https://github.com/")
    
    # Map of problematic repos to working alternatives
    repo_replacements = {
        "https://github.com/synth-laboratories/research.git": {
            "url": "https://github.com/synth-laboratories/synth-ai.git",
            "branch": "main",
            "commit": "75339c2",
            "reason": "Private repository - using public synth-ai instead"
        }
    }
    
    if url in repo_replacements:
        replacement = repo_replacements[url]
        print(f"  ⚠️  Replacing problematic repo: {replacement['reason']}")
        url = replacement["url"]
        repo_info["branch"] = replacement["branch"]
        repo_info["start_commit_sha"] = replacement["commit"]
        repo_info["end_commit_sha"] = replacement["commit"]
    
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
ARG GIT_URL="{repo.get('git_url', 'https://github.com/synth-laboratories/synth-ai.git')}"
ARG GIT_BRANCH="{repo.get('branch', 'main')}"
ARG GIT_COMMIT="{repo.get('start_commit_sha', 'HEAD')}"
ARG TASK_ID="{task_meta.get('task_id', 'unknown')}"

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    git \\
    curl \\
    build-essential \\
    python3 \\
    python3-venv \\
    python3-pip \\
    tmux \\
    vim \\
    less \\
    jq \\
    ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

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

# Copy overlay files
COPY overlay_files/ /app/

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

# Make scripts executable
RUN chmod +x /app/*.sh

# Create artifacts directory
RUN mkdir -p /app/artifacts

# Install Python test dependencies
RUN pip3 install --break-system-packages pytest

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
    
    # Fix repository URL and info
    if "repo" in task_meta:
        original_url = task_meta["repo"].get("git_url", "")
        fixed_url, updated_repo = fix_git_url(original_url, task_meta["repo"])
        task_meta["repo"] = updated_repo
        task_meta["repo"]["git_url"] = fixed_url
        if original_url != fixed_url:
            print(f"   Fixed Git URL: {fixed_url}")
    
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
    
    # Write updated tb_meta.json to both locations
    with open(output_dir / "tb_meta.json", "w") as f:
        json.dump(task_meta, f, indent=2)
    
    with open(overlay_dir / "tb_meta.json", "w") as f:
        json.dump(task_meta, f, indent=2)
    
    # Create Dockerfile
    with open(output_dir / "Dockerfile", "w") as f:
        f.write(create_dockerfile(task_meta))
    print("   Created Dockerfile")
    
    # Copy bootstrap script from template
    template_dir = Path(__file__).parents[2] / "data" / "tasks" / "prepared" / "add-lm-tracing-readme" / "overlay_files"
    
    bootstrap_src = template_dir / "box_bootstrap.sh"
    if bootstrap_src.exists():
        shutil.copy(bootstrap_src, overlay_dir / "box_bootstrap.sh")
        print("   Copied box_bootstrap.sh")
    else:
        print(f"   ⚠️  Warning: Template bootstrap not found at {bootstrap_src}")
    
    # Copy codex-synth wrapper
    codex_src = template_dir / "codex-synth"
    if codex_src.exists():
        shutil.copy(codex_src, overlay_dir / "codex-synth")
        os.chmod(overlay_dir / "codex-synth", 0o755)
        print("   Copied codex-synth wrapper")
    else:
        print(f"   ⚠️  Warning: Template codex-synth not found at {codex_src}")
    
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
        print(f"   Copied evaluation directory")
    
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