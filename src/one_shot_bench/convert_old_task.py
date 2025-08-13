#!/usr/bin/env python3
"""
Convert old synth_bench task format to new format.
Fixes common issues like SSH URLs, missing evaluation structure, etc.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Any

def convert_git_url(url: str, repo_info: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Convert SSH Git URLs to HTTPS and fix known problematic repos.
    Returns (fixed_url, updated_repo_info)
    """
    # Convert SSH to HTTPS
    if url.startswith("git@github.com:"):
        url = url.replace("git@github.com:", "https://github.com/")
    
    # Check for known problematic repositories and replace with working ones
    # No fallback mappings; enforce valid public URL instead
    if not url.startswith("https://github.com/"):
        raise ValueError(f"Unsupported git_url: {url}. Please use a public HTTPS GitHub URL.")
    
    return url, repo_info

def create_default_rubrics(task_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Create default rubrics if none exist."""
    return {
        "rubrics": [
            {
                "id": "task_completed",
                "criterion": "Task requirements were addressed",
                "weight": 0.5
            },
            {
                "id": "code_quality",
                "criterion": "Code is well-structured and follows best practices",
                "weight": 0.3
            },
            {
                "id": "documentation",
                "criterion": "Changes are properly documented",
                "weight": 0.2
            }
        ],
        "test_scripts": [
            {
                "path": "tests/test_basic.py",
                "rubric_id": "task_completed",
                "content": """import os

def test_changes_made():
    \"\"\"Basic test to check if changes were made\"\"\"
    # This is a placeholder test - should be customized per task
    assert True, "Replace with actual test"
"""
            }
        ]
    }

def create_dockerfile_content(task_meta: Dict[str, Any]) -> str:
    """Generate Dockerfile content."""
    repo = task_meta.get("repo", {})
    git_url = repo.get("git_url", "")
    if not git_url:
        raise ValueError("tb_meta.repo.git_url is required and must be a public HTTPS GitHub URL")
    branch = repo.get("branch", "main")
    commit = repo.get("start_commit_sha", "HEAD")
    task_id = task_meta.get("task_id", "unknown")
    
    return f'''FROM ubuntu:24.04

# Build arguments from tb_meta.json
ARG GIT_URL="{git_url}"
ARG GIT_BRANCH="{branch}"
ARG GIT_COMMIT="{commit}"
ARG TASK_ID="{task_id}"

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

# Copy mitmproxy CA certificate if it exists (will be added dynamically)
# This will be copied from host's ~/.mitmproxy/mitmproxy-ca-cert.pem
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

def convert_task(old_task_path: Path, new_tasks_dir: Path) -> None:
    """Convert an old task to the new format."""
    
    # Read old tb_meta.json
    old_meta_path = old_task_path / "tb_meta.json"
    if not old_meta_path.exists():
        print(f"Error: {old_meta_path} not found")
        return
    
    with open(old_meta_path) as f:
        task_meta = json.load(f)
    
    # Fix Git URL and repo info
    if "repo" in task_meta and "git_url" in task_meta["repo"]:
        fixed_url, updated_repo = convert_git_url(task_meta["repo"]["git_url"], task_meta["repo"])
        task_meta["repo"]["git_url"] = fixed_url
        task_meta["repo"].update(updated_repo)
    
    # Fix evaluation structure
    if "evaluation" in task_meta:
        old_eval = task_meta["evaluation"]
        # If using old format, convert to new
        if "content_rubric" in old_eval or "location_rubric" in old_eval or "clarity_rubric" in old_eval:
            task_meta["evaluation"] = create_default_rubrics(task_meta)
    
    # Extract task name from task_id
    task_name = task_meta["task_id"].rsplit("_", 2)[0]  # Remove timestamp
    
    # Create new task directory
    new_task_dir = new_tasks_dir / "generated" / task_name
    new_task_dir.mkdir(parents=True, exist_ok=True)
    
    # Create overlay_files directory
    overlay_dir = new_task_dir / "overlay_files"
    overlay_dir.mkdir(exist_ok=True)
    
    # Write updated tb_meta.json
    with open(new_task_dir / "tb_meta.json", "w") as f:
        json.dump(task_meta, f, indent=2)
    
    # Copy to overlay_files as well
    with open(overlay_dir / "tb_meta.json", "w") as f:
        json.dump(task_meta, f, indent=2)
    
    # Create Dockerfile
    with open(new_task_dir / "Dockerfile", "w") as f:
        f.write(create_dockerfile_content(task_meta))
    
    # Copy bootstrap script from working task
    template_bootstrap = Path(__file__).parent / "tasks" / "generated" / "add-lm-tracing-readme" / "overlay_files" / "box_bootstrap.sh"
    if template_bootstrap.exists():
        shutil.copy(template_bootstrap, overlay_dir / "box_bootstrap.sh")
    else:
        print(f"Warning: Template bootstrap script not found at {template_bootstrap}")
    
    # Copy codex-synth wrapper from working task
    template_codex = Path(__file__).parent / "tasks" / "generated" / "add-lm-tracing-readme" / "overlay_files" / "codex-synth"
    if template_codex.exists():
        shutil.copy(template_codex, overlay_dir / "codex-synth")
        os.chmod(overlay_dir / "codex-synth", 0o755)
    else:
        print(f"Warning: Template codex-synth not found at {template_codex}")
    
    # Copy other files if they exist
    for file_name in ["LM_INSTRUCTIONS.md", "repo_info.json", "diff.patch"]:
        old_file = old_task_path / file_name
        if old_file.exists():
            shutil.copy(old_file, overlay_dir / file_name)
    
    print(f"✅ Converted task to: {new_task_dir}")
    print(f"   - Fixed Git URL: {task_meta['repo']['git_url']}")
    print(f"   - Added {len(task_meta['evaluation'].get('rubrics', []))} rubrics")
    print(f"   - Added {len(task_meta['evaluation'].get('test_scripts', []))} test scripts")
    print(f"   - Created Dockerfile and overlay files")

def main():
    if len(sys.argv) < 2:
        print("Usage: python convert_old_task.py <old_task_path> [new_tasks_dir]")
        print("Example: python convert_old_task.py ../old/old_synth_bench/tasks/created/my-task_20250810_123456")
        sys.exit(1)
    
    old_task_path = Path(sys.argv[1])
    if not old_task_path.exists():
        print(f"Error: Task path {old_task_path} does not exist")
        sys.exit(1)
    
    if len(sys.argv) > 2:
        new_tasks_dir = Path(sys.argv[2])
    else:
        # Default to repository data/tasks directory
        new_tasks_dir = Path(__file__).parents[2] / "data" / "tasks"
    
    convert_task(old_task_path, new_tasks_dir)

if __name__ == "__main__":
    main()