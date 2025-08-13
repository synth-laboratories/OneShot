#!/usr/bin/env python3
"""
Modal backend for running Codex Coach tasks in the cloud.
This provides an alternative to Docker for sandbox execution.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import modal

# Create Modal stub/app
stub = modal.App("codex-coach")

# Define the image with all required dependencies
# We'll handle codex installation at runtime since it needs to be copied from host
image = (
    modal.Image.debian_slim()
    .apt_install(
        "git",
        "curl",
        "build-essential",
        "python3",
        "python3-pip",
        "tmux",
        "vim",
        "jq",
        "ca-certificates",
        "wget",
    )
    .run_commands(
        # Install Node.js 20 via NodeSource
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
        "apt-get install -y nodejs",
        # Verify Node version
        "node --version",
        "npm --version",
        # Create working directories
        "mkdir -p /app/repo /app/artifacts /app/task",
        # Create directory for codex installation
        "mkdir -p /usr/local/lib/node_modules/@openai/",
        # Set up git config
        "git config --global user.email 'agent@codex-coach.ai'",
        "git config --global user.name 'Codex Agent'",
        "git config --global init.defaultBranch main",
    )
    .pip_install("pytest", "openai", "anthropic")
)

# Create volumes for persistent storage
artifacts_volume = modal.Volume.from_name("codex-artifacts", create_if_missing=True)
codex_volume = modal.Volume.from_name("codex-installation", create_if_missing=True)


@stub.function(
    image=image,
    volumes={"/codex": codex_volume},
    timeout=600,
)
def setup_codex_volume(codex_tar_data: bytes) -> Dict:
    """
    One-time setup to install codex into a persistent volume.
    This only needs to be run once, then all tasks can use the same codex installation.
    """
    import tarfile
    import io
    from pathlib import Path
    
    print("Setting up codex in persistent volume...")
    
    # Extract tar archive to volume
    tar_buffer = io.BytesIO(codex_tar_data)
    with tarfile.open(fileobj=tar_buffer, mode='r:gz') as tar:
        tar.extractall(path="/codex")
        print(f"Extracted codex to /codex")
    
    # List what we extracted
    codex_dir = Path("/codex")
    items = list(codex_dir.iterdir())
    print(f"Extracted {len(items)} top-level items:")
    for item in items[:10]:
        print(f"  - {item.name}")
    
    # Commit the volume
    codex_volume.commit()
    print("✅ Codex volume setup complete and committed")
    
    return {"status": "success", "message": "Codex installed to volume"}


@stub.function(
    image=image,
    secrets=[modal.Secret.from_name("openai-api-keys")],
    volumes={
        "/app/artifacts": artifacts_volume,
        "/codex": codex_volume,  # Mount the codex volume
    },
    timeout=3600,  # 1 hour default timeout
    cpu=2,
    memory=4096,
)
def run_task(
    task_files: Dict[str, bytes],
    timeout_sec: int = 1800,
    token_limit: int = 100000,
    model: str = "gpt-4o-mini",
    run_id: Optional[str] = None,
) -> Dict:
    """
    Run a single Codex Coach task in Modal.
    
    Args:
        task_files: Dictionary mapping file paths to file contents
        timeout_sec: Timeout in seconds for agent execution
        token_limit: Maximum tokens allowed for the agent
        model: OpenAI model to use
        run_id: Unique identifier for this run (generated if not provided)
    
    Returns:
        Dict with execution results and artifact paths
    """
    import shutil
    import tempfile
    from pathlib import Path
    
    print("=" * 60)
    print("STARTING MODAL TASK EXECUTION")
    print("=" * 60)
    
    # Log environment info
    print("\n=== ENVIRONMENT CHECK ===")
    
    # Check Node/npm
    node_version = subprocess.run(["node", "--version"], capture_output=True, text=True)
    print(f"Node version: {node_version.stdout.strip()}")
    
    npm_version = subprocess.run(["npm", "--version"], capture_output=True, text=True)
    print(f"NPM version: {npm_version.stdout.strip()}")
    
    # Setup codex from the persistent volume
    print("\n=== SETTING UP CODEX FROM VOLUME ===")
    codex_installed = False
    
    # Check if codex is in the volume
    codex_volume_path = Path("/codex")
    if codex_volume_path.exists():
        print(f"Found codex volume at {codex_volume_path}")
        
        # Find the codex installation
        codex_install_path = None
        if (codex_volume_path / "codex").exists():
            codex_install_path = codex_volume_path / "codex"
        elif (codex_volume_path / "@openai" / "codex").exists():
            codex_install_path = codex_volume_path / "@openai" / "codex"
        else:
            # List what's in the volume
            print("Searching for codex in volume...")
            for item in codex_volume_path.rglob("codex.js"):
                if "bin" in str(item):
                    codex_install_path = item.parent.parent
                    break
        
        if codex_install_path and codex_install_path.exists():
            print(f"Found codex installation at: {codex_install_path}")
            
            # Create symlink for codex command
            codex_bin = codex_install_path / "bin" / "codex.js"
            if codex_bin.exists():
                # Make sure it's executable
                os.chmod(codex_bin, 0o755)
                
                # Create symlink
                subprocess.run(["ln", "-sf", str(codex_bin), "/usr/local/bin/codex"], check=True)
                print(f"Created symlink: /usr/local/bin/codex -> {codex_bin}")
                
                # Verify installation
                which_result = subprocess.run(["which", "codex"], capture_output=True, text=True)
                if which_result.returncode == 0:
                    print(f"✅ Codex setup successfully from volume at: {which_result.stdout.strip()}")
                    codex_installed = True
                else:
                    print("❌ Failed to setup codex properly")
            else:
                print(f"❌ codex.js not found at: {codex_bin}")
        else:
            print("❌ Codex not found in volume - you may need to run setup_codex_volume first")
            print("Contents of volume:")
            for item in codex_volume_path.iterdir():
                print(f"  - {item.name}")
    else:
        print("❌ Codex volume not mounted or empty - run setup_codex_volume first")
    
    # Check for codex installation
    print("\n=== CODEX INSTALLATION CHECK ===")
    
    # Try to find codex
    which_codex = subprocess.run(["which", "codex"], capture_output=True, text=True)
    if which_codex.returncode == 0:
        print(f"Found codex at: {which_codex.stdout.strip()}")
        codex_found = True
    else:
        print("ERROR: 'which codex' failed - codex not in PATH")
        codex_found = False
    
    # Check npm global packages
    npm_list = subprocess.run(["npm", "list", "-g", "--depth=0"], capture_output=True, text=True)
    print(f"Global NPM packages:\n{npm_list.stdout}")
    
    # Check common locations
    for path in ["/usr/local/bin", "/usr/bin", "/opt/nodejs/bin", "/root/.npm-global/bin"]:
        if os.path.exists(path):
            files = os.listdir(path)
            codex_files = [f for f in files if "codex" in f.lower()]
            if codex_files:
                print(f"Found codex-related files in {path}: {codex_files}")
            else:
                print(f"No codex files in {path}")
    
    # Check PATH
    print(f"\nPATH environment: {os.environ.get('PATH', 'NOT SET')}")
    
    # Assert codex is available
    if not codex_found:
        # Try to find any codex executable in the filesystem
        find_result = subprocess.run(
            ["find", "/", "-name", "*codex*", "-type", "f", "-executable", "2>/dev/null"],
            capture_output=True,
            text=True,
            shell=True
        )
        if find_result.stdout:
            print(f"\nFound executable files with 'codex' in name:")
            print(find_result.stdout)
        
        return {
            "status": "error",
            "message": "FATAL: Codex is not installed or not in PATH. Cannot proceed.",
            "run_id": run_id if 'run_id' in locals() else "unknown",
        }
    
    # Generate run ID if not provided
    if not run_id:
        run_id = f"modal_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}"
    
    print(f"\nRun ID: {run_id}")
    
    # Create unique artifacts directory for this run
    artifacts_dir = Path(f"/app/artifacts/{run_id}")
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    print(f"Artifacts directory: {artifacts_dir}")
    
    # Create task directory and write files
    task_path = Path("/app/task")
    task_path.mkdir(parents=True, exist_ok=True)
    
    print(f"\n=== TASK FILES UPLOAD ===")
    print(f"Received {len(task_files)} files to process")
    
    for file_path, content in task_files.items():
        full_path = task_path / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write as text or binary based on content type
        if isinstance(content, bytes):
            with open(full_path, "wb") as f:
                f.write(content)
            print(f"Wrote binary file: {file_path} ({len(content)} bytes)")
        else:
            with open(full_path, "w") as f:
                f.write(content)
            print(f"Wrote text file: {file_path} ({len(content)} chars)")
        
        # Make scripts executable
        if file_path.endswith(".sh") or file_path.endswith("codex-synth"):
            os.chmod(full_path, 0o755)
            print(f"  Made executable: {file_path}")
    
    # Load task metadata
    tb_meta_path = task_path / "tb_meta.json"
    if not tb_meta_path.exists():
        return {
            "status": "error",
            "message": f"tb_meta.json not found in task files",
            "run_id": run_id,
        }
    
    with open(tb_meta_path) as f:
        tb_meta = json.load(f)
    
    # Extract repository info - handle both old and new formats
    if "repository" in tb_meta:
        # Old format
        repo_url = tb_meta.get("repository", {}).get("clone_url", "")
        repo_branch = tb_meta.get("repository", {}).get("branch", "main")
        repo_commit = tb_meta.get("repository", {}).get("commit", None)
    elif "repo" in tb_meta:
        # New format
        repo_url = tb_meta.get("repo", {}).get("git_url", "")
        repo_branch = tb_meta.get("repo", {}).get("branch", "main")
        repo_commit = tb_meta.get("repo", {}).get("start_commit_sha", None)
    else:
        return {
            "status": "error",
            "message": "No repository information found in tb_meta.json",
            "run_id": run_id,
        }
    
    # Clone repository
    repo_dir = Path("/app/repo")
    try:
        clone_result = subprocess.run(
            ["git", "clone", "--depth", "100", repo_url, str(repo_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Cloned repository from {repo_url}")
        
        # Checkout specific branch/commit if specified
        if repo_branch:
            subprocess.run(
                ["git", "checkout", repo_branch],
                cwd=repo_dir,
                check=True,
                capture_output=True,
            )
            print(f"Checked out branch: {repo_branch}")
        
        if repo_commit:
            subprocess.run(
                ["git", "checkout", repo_commit],
                cwd=repo_dir,
                check=True,
                capture_output=True,
            )
            print(f"Checked out commit: {repo_commit}")
    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to clone/checkout repository: {e}"
        if e.stdout:
            error_msg += f"\nSTDOUT: {e.stdout}"
        if e.stderr:
            error_msg += f"\nSTDERR: {e.stderr}"
        return {
            "status": "error",
            "message": error_msg,
            "run_id": run_id,
        }
    
    # Make repository writable
    subprocess.run(["chmod", "-R", "777", str(repo_dir)], check=True)
    
    # Create initial commit for diff tracking
    subprocess.run(
        ["git", "add", "-A"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit for diff tracking", "--allow-empty"],
        cwd=repo_dir,
        check=True,
    )
    
    # Set up environment variables
    env = os.environ.copy()
    env.update({
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        "OPENAI_MODEL": model,
        "TASK_ID": tb_meta.get("id", "unknown"),
        "AGENT_TIMEOUT_SEC": str(timeout_sec),
        "CODEX_DISABLE_SANDBOX": "1",  # Already in sandbox
        "ARTIFACTS_DIR": str(artifacts_dir),
    })
    
    # Get task instructions
    instructions = tb_meta.get("lm", {}).get("instructions", "")
    if not instructions:
        return {
            "status": "error",
            "message": "No instructions found in tb_meta.json",
            "run_id": run_id,
        }
    
    print(f"\n=== PREPARING BOOTSTRAP ENVIRONMENT ===")
    
    # Copy tb_meta.json to /app (bootstrap script expects it there)
    shutil.copy(tb_meta_path, "/app/tb_meta.json")
    print(f"Copied tb_meta.json to /app")
    
    # Copy .env file if it exists
    env_file = task_path / ".env"
    if env_file.exists():
        shutil.copy(env_file, "/app/.env")
        print(f"Copied .env to /app")
    else:
        print("No .env file found in task files")
    
    # Copy overlay files to /app for bootstrap script to access
    print(f"\n=== COPYING OVERLAY FILES ===")
    if (task_path / "overlay_files").exists():
        overlay_files = list((task_path / "overlay_files").iterdir())
        print(f"Found {len(overlay_files)} overlay files")
        for overlay_file in overlay_files:
            if overlay_file.is_file():
                target_path = Path("/app") / overlay_file.name
                shutil.copy(overlay_file, target_path)
                if overlay_file.name.endswith(".sh") or overlay_file.name == "codex-synth":
                    os.chmod(target_path, 0o755)
                    print(f"Copied and made executable: {overlay_file.name}")
                else:
                    print(f"Copied: {overlay_file.name}")
    else:
        print("No overlay_files directory found")
    
    # List /app directory contents
    print(f"\n=== /app DIRECTORY CONTENTS ===")
    app_files = os.listdir("/app")
    for f in sorted(app_files):
        path = Path("/app") / f
        if path.is_file():
            size = path.stat().st_size
            perms = oct(path.stat().st_mode)[-3:]
            print(f"  {f}: {size} bytes, perms={perms}")
        else:
            print(f"  {f}/ (directory)")
    
    # Check if custom bootstrap script exists
    bootstrap_script = Path("/app/box_bootstrap.sh")
    
    if bootstrap_script.exists():
        print(f"\n=== RUNNING BOOTSTRAP SCRIPT ===")
        print(f"Script path: {bootstrap_script}")
        print(f"Script size: {bootstrap_script.stat().st_size} bytes")
        print(f"Script permissions: {oct(bootstrap_script.stat().st_mode)[-3:]}")
        print(f"Working directory: {repo_dir}")
        print(f"Timeout: {timeout_sec} seconds")
        
        # Show first few lines of bootstrap script
        with open(bootstrap_script, "r") as f:
            lines = f.readlines()[:10]
            print(f"First {len(lines)} lines of bootstrap script:")
            for i, line in enumerate(lines, 1):
                print(f"  {i}: {line.rstrip()}")
        
        # Run bootstrap script with real-time output and completion detection
        try:
            print(f"\nExecuting bootstrap script...")
            print("=" * 60)
            print("BOOTSTRAP OUTPUT (REAL-TIME)")
            print("=" * 60)
            
            # Open log file for writing
            bootstrap_log = artifacts_dir / "bootstrap.log"
            completion_marker = Path("/app/artifacts/completion.json")
            
            # First, modify the bootstrap script to remove the sleep and signal completion
            with open(bootstrap_script, "r") as f:
                bootstrap_content = f.read()
            
            # Replace the sleep 120 with a completion signal
            if "sleep 120" in bootstrap_content:
                bootstrap_content = bootstrap_content.replace(
                    "sleep 120",
                    '# Completion signaled via completion.json instead of sleep'
                )
                # Write the modified script
                modified_script = Path("/tmp/modified_bootstrap.sh")
                with open(modified_script, "w") as f:
                    f.write(bootstrap_content)
                os.chmod(modified_script, 0o755)
                bootstrap_script = modified_script
                print("Modified bootstrap script to remove sleep 120")
            
            with open(bootstrap_log, "w") as log_file:
                # Use Popen for real-time output streaming
                process = subprocess.Popen(
                    ["/bin/bash", "-x", str(bootstrap_script)],  # Add -x for debug output
                    cwd=repo_dir,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # Combine stderr with stdout
                    text=True,
                    bufsize=1,  # Line buffered
                )
                
                # Track completion
                start_time = time.time()
                completion_detected = False
                
                # Stream output line by line while checking for completion
                while True:
                    # Check if process has output
                    line = process.stdout.readline()
                    if line:
                        # Write to log file
                        log_file.write(line)
                        log_file.flush()
                        # Also print to console for real-time viewing
                        print(line.rstrip())
                    
                    # Check if completion marker exists
                    if completion_marker.exists() and not completion_detected:
                        print(f"\n=== COMPLETION MARKER DETECTED ===")
                        completion_detected = True
                        # Give a few seconds for final artifacts to be written
                        time.sleep(3)
                        # Then terminate the process gracefully
                        process.terminate()
                        try:
                            agent_exit_code = process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            agent_exit_code = 0  # Consider it successful since completion marker exists
                        break
                    
                    # Check if process has finished naturally
                    if process.poll() is not None:
                        agent_exit_code = process.returncode
                        break
                    
                    # Check for timeout
                    if time.time() - start_time > timeout_sec:
                        process.kill()
                        agent_exit_code = 2  # Timeout exit code
                        print(f"\n=== TIMEOUT REACHED after {timeout_sec} seconds ===")
                        log_file.write(f"\n=== TIMEOUT REACHED after {timeout_sec} seconds ===\n")
                        break
                    
                    # Small sleep to avoid busy waiting
                    if not line:
                        time.sleep(0.1)
            
            print("=" * 60)
            print(f"Bootstrap script completed with exit code: {agent_exit_code}")
            
        except Exception as e:
            agent_exit_code = 1
            print(f"\n=== ERROR: {e} ===")
            bootstrap_log = artifacts_dir / "bootstrap.log"
            with open(bootstrap_log, "a") as f:
                f.write(f"\n=== ERROR: {e} ===\n")
    else:
        print(f"No bootstrap script found, running agent directly...")
        # Run agent directly with instructions
        agent_log_path = artifacts_dir / "agent.log"
        
        # Check if codex-synth exists in /app
        codex_synth_path = Path("/app/codex-synth")
        if codex_synth_path.exists():
            agent_cmd = str(codex_synth_path)
        else:
            agent_cmd = "codex"  # Use the codex from volume
        
        # Write instructions to temp file
        instructions_file = artifacts_dir / "instructions.txt"
        with open(instructions_file, "w") as f:
            f.write(instructions)
        
        print(f"\nRunning agent: {agent_cmd}")
        print("=" * 60)
        print("AGENT OUTPUT (REAL-TIME)")
        print("=" * 60)
        
        # Run codex-synth agent with streaming output
        with open(agent_log_path, "w") as log_file:
            try:
                process = subprocess.Popen(
                    [agent_cmd, "--model", model, "--file", str(instructions_file)],
                    cwd=repo_dir,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                
                # Stream output line by line
                for line in process.stdout:
                    log_file.write(line)
                    log_file.flush()
                    print(line.rstrip())
                
                # Wait for completion
                try:
                    agent_exit_code = process.wait(timeout=timeout_sec)
                except subprocess.TimeoutExpired:
                    process.kill()
                    agent_exit_code = 2  # Timeout exit code
                    print(f"\n=== TIMEOUT REACHED after {timeout_sec} seconds ===")
                    log_file.write(f"\n=== TIMEOUT REACHED after {timeout_sec} seconds ===\n")
                    
            except Exception as e:
                agent_exit_code = 1
                print(f"\n=== ERROR: {e} ===")
                log_file.write(f"\n=== ERROR: {e} ===\n")
        
        print("=" * 60)
        print(f"Agent completed with exit code: {agent_exit_code}")
    
    # Capture git diff
    diff_result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    
    diff_path = artifacts_dir / "diff.patch"
    with open(diff_path, "w") as f:
        f.write(diff_result.stdout)
    
    # Capture git status
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    
    status_path = artifacts_dir / "git_status.txt"
    with open(status_path, "w") as f:
        f.write(status_result.stdout)
    
    # Run evaluation if specified
    evaluation_results = {}
    if "evaluation" in tb_meta:
        eval_script = tb_meta["evaluation"].get("script")
        if eval_script:
            eval_log_path = artifacts_dir / "evaluation.log"
            
            # Write evaluation script
            eval_script_path = artifacts_dir / "eval.sh"
            with open(eval_script_path, "w") as f:
                f.write("#!/bin/bash\n")
                f.write(eval_script)
            
            subprocess.run(["chmod", "+x", str(eval_script_path)], check=True)
            
            # Run evaluation
            eval_result = subprocess.run(
                [str(eval_script_path)],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for evaluation
            )
            
            with open(eval_log_path, "w") as f:
                f.write(eval_result.stdout)
                if eval_result.stderr:
                    f.write("\n=== STDERR ===\n")
                    f.write(eval_result.stderr)
            
            evaluation_results = {
                "exit_code": eval_result.returncode,
                "passed": eval_result.returncode == 0,
            }
    
    # Copy ALL files from /app/artifacts to our run directory
    # The bootstrap script writes directly to /app/artifacts
    app_artifacts = Path("/app/artifacts")
    if app_artifacts.exists():
        print(f"\n=== COPYING ARTIFACTS FROM BOOTSTRAP ===")
        for item in app_artifacts.iterdir():
            if item.is_file():
                # Skip files already in our directory
                if item.name in ["bootstrap.log", "completion.json"]:
                    continue
                    
                target = artifacts_dir / item.name
                try:
                    shutil.copy(item, target)
                    print(f"Copied: {item.name} ({item.stat().st_size} bytes)")
                    
                    # If it's the evaluation results, parse it
                    if item.name == "tb_evaluation_results.json":
                        with open(item) as f:
                            evaluation_results = json.load(f)
                except Exception as e:
                    print(f"Failed to copy {item.name}: {e}")
    
    # Create completion marker
    completion_data = {
        "completed": True,
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "agent_exit_code": agent_exit_code if 'agent_exit_code' in locals() else 0,
        "evaluation_results": evaluation_results,
        "artifacts_dir": str(artifacts_dir),
    }
    
    completion_path = artifacts_dir / "completion.json"
    with open(completion_path, "w") as f:
        json.dump(completion_data, f, indent=2)
    
    # Commit artifacts to volume
    artifacts_volume.commit()
    
    # Print evaluation results summary
    print("\n" + "=" * 60)
    print("EVALUATION RESULTS SUMMARY")
    print("=" * 60)
    
    if evaluation_results:
        if "evaluation" in evaluation_results:
            eval_data = evaluation_results["evaluation"]
            print(f"Total Score: {eval_data.get('total_score', 0) * 100:.1f}%")
            print("\nRubric Scores:")
            for rubric_id, rubric_data in eval_data.get("rubrics", {}).items():
                score = rubric_data.get("score", 0)
                weight = rubric_data.get("weight", 0)
                reasoning = rubric_data.get("reasoning", "")
                print(f"  • {rubric_id}: {score * 100:.0f}% (weight: {weight})")
                if reasoning:
                    print(f"    {reasoning}")
        
        if "test_results" in evaluation_results:
            print("\nUnit Test Results:")
            for test_name, test_data in evaluation_results["test_results"].items():
                status = "✅ PASSED" if test_data.get("success") else "❌ FAILED"
                print(f"  • {test_name}: {status}")
    else:
        print("No evaluation results found")
    
    print("\n" + "=" * 60)
    
    return {
        "status": "success",
        "run_id": run_id,
        "artifacts_dir": str(artifacts_dir),
        "completion_data": completion_data,
        "evaluation_results": evaluation_results,
    }


@stub.function(schedule=modal.Cron("0 2 * * *"))
def cleanup_old_artifacts():
    """
    Clean up artifacts older than 7 days to manage storage.
    Runs daily at 2 AM UTC.
    """
    import shutil
    from datetime import datetime, timedelta
    
    artifacts_base = Path("/app/artifacts")
    if not artifacts_base.exists():
        return
    
    cutoff_time = datetime.now() - timedelta(days=7)
    
    for run_dir in artifacts_base.iterdir():
        if not run_dir.is_dir():
            continue
        
        # Check completion.json for timestamp
        completion_file = run_dir / "completion.json"
        if completion_file.exists():
            try:
                with open(completion_file) as f:
                    data = json.load(f)
                    timestamp = datetime.fromisoformat(data.get("timestamp", ""))
                    if timestamp < cutoff_time:
                        shutil.rmtree(run_dir)
                        print(f"Cleaned up old artifacts: {run_dir.name}")
            except Exception:
                pass  # Skip if can't parse


@stub.local_entrypoint()
def main(
    task_dir: str,
    timeout: int = 1800,
    token_limit: int = 100000,
    model: str = "gpt-4o-mini",
):
    """
    Local entrypoint for running a task via Modal.
    
    Usage:
        modal run codex_modal_runner.py --task-dir ./path/to/task
    """
    import json
    from pathlib import Path
    
    task_path = Path(task_dir)
    if not task_path.exists():
        print(f"Error: Task directory {task_dir} does not exist")
        return {"status": "error", "message": "Task directory not found"}
    
    # Read all task files into memory
    task_files = {}
    
    # Read tb_meta.json
    tb_meta_path = task_path / "tb_meta.json"
    if not tb_meta_path.exists():
        print(f"Error: tb_meta.json not found in {task_dir}")
        return {"status": "error", "message": "tb_meta.json not found"}
    
    with open(tb_meta_path, "rb") as f:
        task_files["tb_meta.json"] = f.read()
    
    # Read overlay files if they exist
    overlay_dir = task_path / "overlay_files"
    if overlay_dir.exists():
        for file_path in overlay_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(task_path)
                with open(file_path, "rb") as f:
                    task_files[str(relative_path)] = f.read()
    
    # Read .env file if it exists
    env_file = task_path / ".env"
    if env_file.exists():
        with open(env_file, "rb") as f:
            task_files[".env"] = f.read()
    
    # We no longer upload codex with each task - it's in the persistent volume
    print("Note: Codex will be loaded from persistent Modal volume")
    
    print(f"Running task from {task_dir} on Modal...")
    print(f"Model: {model}, Timeout: {timeout}s, Token limit: {token_limit}")
    print(f"Uploading {len(task_files)} task files...")
    
    # Call the remote function
    result = run_task.remote(
        task_files=task_files,
        timeout_sec=timeout,
        token_limit=token_limit,
        model=model,
    )
    
    print(f"\nTask completed with status: {result['status']}")
    print(f"Run ID: {result.get('run_id')}")
    print(f"Artifacts stored at: {result.get('artifacts_dir')}")
    
    if result["status"] == "error":
        print(f"Error message: {result.get('message', 'Unknown error')}")
        return result
    
    if result["status"] == "success":
        run_id = result['run_id']
        
        # Automatically fetch artifacts locally
        print(f"\n📥 Fetching artifacts to local filesystem...")
        local_run_dir = Path(f"./data/runs/{run_id}")
        local_run_dir.mkdir(parents=True, exist_ok=True)
        
        # Use Modal CLI to fetch artifacts
        import subprocess
        try:
            # Fetch the run directory from Modal volume
            subprocess.run(
                ["modal", "volume", "get", "codex-artifacts", 
                 f"{run_id}/", str(local_run_dir) + "/"],
                check=True,
                capture_output=True
            )
            print(f"✅ Artifacts saved to: {local_run_dir.absolute()}")
            
            # Also try to fetch evaluation results from root if they exist
            eval_files = ["tb_evaluation_results.json", "tb_evaluation.log"]
            for fname in eval_files:
                try:
                    subprocess.run(
                        ["modal", "volume", "get", "codex-artifacts",
                         fname, str(local_run_dir / fname)],
                        check=False,
                        capture_output=True
                    )
                except:
                    pass
            
            # Display artifacts summary
            print("\n📁 Downloaded artifacts:")
            for item in sorted(local_run_dir.rglob("*")):
                if item.is_file():
                    size = item.stat().st_size
                    rel_path = item.relative_to(local_run_dir)
                    if size > 0:
                        print(f"  • {rel_path} ({size:,} bytes)")
                    else:
                        print(f"  • {rel_path} (empty)")
            
            # Show diff if it exists and is non-empty
            diff_path = local_run_dir / run_id / "diff.patch"
            if not diff_path.exists():
                diff_path = local_run_dir / "diff.patch"
            
            if diff_path.exists() and diff_path.stat().st_size > 0:
                print("\n📝 Git diff:")
                print("-" * 40)
                with open(diff_path) as f:
                    diff_content = f.read()
                    if len(diff_content) > 2000:
                        print(diff_content[:2000])
                        print(f"\n... (truncated, {len(diff_content) - 2000} more bytes)")
                    else:
                        print(diff_content)
                print("-" * 40)
            else:
                print("\n📝 No changes made (diff.patch is empty)")
            
            # Show evaluation results if available
            eval_path = local_run_dir / "tb_evaluation_results.json"
            if eval_path.exists():
                with open(eval_path) as f:
                    eval_results = json.load(f)
                    
                print("\n📊 Evaluation Results:")
                print("-" * 40)
                if "evaluation" in eval_results:
                    eval_data = eval_results["evaluation"]
                    print(f"Total Score: {eval_data.get('total_score', 0) * 100:.1f}%")
                    
                    print("\nRubric Scores:")
                    for rubric_id, rubric_data in eval_data.get("rubrics", {}).items():
                        score = rubric_data.get("score", 0)
                        weight = rubric_data.get("weight", 0)
                        print(f"  • {rubric_id}: {score * 100:.0f}% (weight: {weight})")
                
                if "test_results" in eval_results:
                    print("\nUnit Tests:")
                    for test_name, test_data in eval_results["test_results"].items():
                        status = "✅" if test_data.get("success") else "❌"
                        print(f"  {status} {test_name}")
                print("-" * 40)
            
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Failed to fetch some artifacts: {e}")
            print(f"You can manually fetch with:")
            print(f"  modal volume get codex-artifacts {run_id}/ ./data/runs/{run_id}/")
        
        except Exception as e:
            print(f"⚠️  Error fetching artifacts: {e}")
    
    return result


@stub.local_entrypoint()
def setup_codex():
    """Setup codex in the persistent volume (one-time setup)."""
    import subprocess
    import tarfile
    import io
    from pathlib import Path
    
    print("Setting up codex in Modal volume...")
    
    # Find local codex installation
    codex_path = subprocess.run(["which", "codex"], capture_output=True, text=True)
    if codex_path.returncode != 0:
        print("Error: codex not found locally. Install with: npm install -g @openai/codex")
        return {"status": "error"}
    
    codex_real_path = subprocess.run(["realpath", codex_path.stdout.strip()], capture_output=True, text=True)
    codex_package_path = Path(codex_real_path.stdout.strip()).parent.parent
    
    # Check common locations
    if (codex_package_path / "lib/node_modules/@openai/codex").exists():
        codex_package_path = codex_package_path / "lib/node_modules/@openai/codex"
    elif not (codex_package_path / "package.json").exists():
        # Try to find it
        for parent in Path(codex_real_path.stdout.strip()).parents:
            if (parent / "package.json").exists() and "codex" in parent.name:
                codex_package_path = parent
                break
    
    print(f"Found codex at: {codex_package_path}")
    
    # Create tar archive
    print("Creating tar archive...")
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar:
        tar.add(codex_package_path, arcname="codex")
    
    tar_data = tar_buffer.getvalue()
    print(f"Archive size: {len(tar_data) / 1024 / 1024:.1f} MB")
    
    # Upload to Modal volume
    print("Uploading to Modal volume...")
    # Ensure secret exists using local .env if present (simple parser, no extra deps)
    dotenv_path = Path.cwd() / ".env"
    if dotenv_path.exists():
        try:
            key = None
            for line in dotenv_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
            if key:
                os.system(f"modal secret create openai-api-keys OPENAI_API_KEY={key} >/dev/null 2>&1 || true")
        except Exception:
            # Non-fatal; user can create the secret manually
            pass
    result = setup_codex_volume.remote(tar_data)
    
    print(f"Result: {result}")
    return result


# For Modal CLI compatibility, we don't use if __name__ == "__main__"
# Instead, Modal will call the functions directly