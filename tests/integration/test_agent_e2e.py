"""
Integration tests for Codex and OpenCode agents.

These tests verify end-to-end agent execution and diff submission.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DATA_DIR = REPO_ROOT / "data"
TASKS_DIR = DATA_DIR / "tasks" / "prepared"
RUNS_DIR = DATA_DIR / "runs"


def get_test_task() -> Path:
    """Get a test task path (hello-world-example)."""
    task_path = TASKS_DIR / "hello-world-example"
    if not task_path.exists():
        pytest.skip(f"Test task not found: {task_path}")
    return task_path


def run_script(
    script_path: Path,
    task_path: Path,
    env: Optional[dict[str, str]] = None,
    timeout: int = 600,
) -> tuple[int, Path]:
    """
    Run a script and return exit code and run directory.
    
    Args:
        script_path: Path to the script to run
        task_path: Path to the task directory
        env: Additional environment variables
        timeout: Timeout in seconds
        
    Returns:
        Tuple of (exit_code, run_dir)
    """
    run_id = f"test_{int(time.time())}"
    run_dir = RUNS_DIR / run_id
    
    # Prepare environment
    script_env = os.environ.copy()
    script_env["RUN_ID"] = run_id
    if env:
        script_env.update(env)
    
    # Run the script
    try:
        result = subprocess.run(
            ["bash", str(script_path), str(task_path)],
            cwd=str(REPO_ROOT),
            env=script_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, run_dir
    except subprocess.TimeoutExpired:
        pytest.fail(f"Script timed out after {timeout} seconds")
        return 1, run_dir  # Unreachable but satisfies type checker
    except Exception as e:
        pytest.fail(f"Failed to run script: {e}")
        return 1, run_dir  # Unreachable but satisfies type checker


def verify_agent_ran(run_dir: Path) -> None:
    """
    Verify that the agent actually ran (not just that the script executed).
    
    Args:
        run_dir: Path to the run directory
        
    Raises:
        AssertionError: If agent didn't run successfully
    """
    # Check for codex-run.log (Codex) or opencode logs
    codex_log = run_dir / "artifacts" / "codex-run.log"
    if codex_log.exists():
        log_content = codex_log.read_text()
        # Check for errors that indicate the agent didn't run
        if "ERROR:" in log_content or "unexpected status" in log_content:
            # Extract error details
            error_lines = [line for line in log_content.split("\n") if "ERROR" in line or "unexpected status" in line]
            error_msg = "\n".join(error_lines[-5:])  # Last 5 error lines
            pytest.fail(
                f"Agent failed to run successfully. Check logs at {codex_log}.\n"
                f"Last errors:\n{error_msg}"
            )
        # Check if agent actually started
        if "conversation_starts" not in log_content and "Codex initialized" not in log_content:
            pytest.fail(f"Agent doesn't appear to have started. Check logs at {codex_log}")


def verify_diff_submitted(run_dir: Path) -> None:
    """
    Verify that a diff was submitted by checking for diff.patch file.
    
    Args:
        run_dir: Path to the run directory
        
    Raises:
        AssertionError: If diff.patch is missing or empty
    """
    # First verify agent ran
    verify_agent_ran(run_dir)
    
    diff_path = run_dir / "artifacts" / "diff.patch"
    
    # Check if diff file exists
    if not diff_path.exists():
        # Check alternative locations
        alt_paths = [
            run_dir / "artifacts" / "container_git_diff_from_baseline.patch",
            run_dir / "artifacts" / "container_git_diff.patch",
        ]
        for alt_path in alt_paths:
            if alt_path.exists():
                diff_path = alt_path
                break
        else:
            pytest.fail(
                f"diff.patch not found in {run_dir / 'artifacts'}. "
                f"Agent may not have made any changes. Check logs at {run_dir / 'artifacts' / 'codex-run.log'}"
            )
    
    # Check if diff file is non-empty
    diff_content = diff_path.read_text()
    assert len(diff_content.strip()) > 0, f"diff.patch is empty at {diff_path}"
    
    # Verify it looks like a valid diff (starts with diff --git or similar)
    assert (
        diff_content.startswith("diff --git")
        or diff_content.startswith("---")
        or "+++" in diff_content
    ), f"diff.patch doesn't look like a valid diff: {diff_content[:200]}"


@pytest.mark.integration
def test_codex_with_gpt5_nano() -> None:
    """Test Codex with gpt-5-nano (direct OpenAI)."""
    # Check for required API key
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_codex_box.sh"
    
    # Run Codex with gpt-5-nano
    env = {
        "OPENAI_MODEL": "gpt-5-nano",
        "FORCE_OPENAI": "1",  # Force direct OpenAI usage
        "OPENAI_REASONING_EFFORT": "medium",
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env)
    
    # Verify run completed and diff was submitted
    verify_diff_submitted(run_dir)
    
    # Optionally check results.json
    results_path = run_dir / "results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text())
        assert "run_id" in results
        assert "exit_code" in results


@pytest.mark.integration
def test_codex_with_synth_small_local_backend() -> None:
    """
    Test Codex with synth-small via local synth backend.
    
    This test verifies:
    1. Codex connects to local backend at http://host.docker.internal:8000/api/synth-research
    2. Stream completes successfully (no "stream disconnected before completion" errors)
    3. Diff is submitted successfully
    
    Prerequisites:
    - Local backend must be running at http://127.0.0.1:8000
    - SYNTH_API_KEY and OPENAI_API_KEY must be set
    """
    # Check for required API keys
    synth_api_key = os.environ.get("SYNTH_API_KEY")
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    
    if not synth_api_key:
        pytest.skip("SYNTH_API_KEY not set")
    if not openai_api_key:
        pytest.skip("OPENAI_API_KEY not set (needed for synth backend)")
    
    # Verify local backend is running
    try:
        resp = httpx.get("http://127.0.0.1:8000/health", timeout=5.0)
        if resp.status_code != 200:
            pytest.skip(f"Local backend not healthy (status: {resp.status_code})")
    except Exception as e:
        pytest.skip(f"Local backend not reachable: {e}")
    
    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_codex_box.sh"
    
    # Configure for local backend
    local_backend_url = "http://host.docker.internal:8000/api/synth-research"
    
    # Run Codex with synth-small against local backend
    # Note: The script will set OPENAI_API_KEY=SYNTH_API_KEY when IS_SYNTH_MODEL=true
    # Force Docker rebuild to ensure updated LM_INSTRUCTIONS.md is included
    env = {
        "OPENAI_MODEL": "synth-small",
        "SYNTH_BASE_URL": local_backend_url,
        "OPENAI_BASE_URL": local_backend_url,
        "SYNTH_API_KEY": synth_api_key,  # Required for synth model detection
        "OPENAI_API_KEY": openai_api_key,  # Required for backend to call OpenAI
        "FORCE_OPENAI": "1",
        "DOCKER_NO_CACHE": "1",  # Force rebuild to include updated LM_INSTRUCTIONS.md
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env, timeout=600)
    
    # Verify agent ran successfully (check for stream completion)
    codex_log = run_dir / "artifacts" / "codex-run.log"
    assert codex_log.exists(), f"Codex log not found at {codex_log}"
    
    log_content = codex_log.read_text()
    
    # Primary goal: Verify streaming works without disconnection errors
    if "stream disconnected before completion" in log_content:
        pytest.fail(
            f"Stream disconnected before completion. Check logs at {codex_log}.\n"
            f"This indicates the Responses API format or connection handling needs fixing."
        )
    
    # Verify successful API connection (200 status code)
    assert "http.response.status_code=200" in log_content, \
        f"Expected successful API response (200), but not found in logs. Check {codex_log}"
    
    # Verify SSE events were received (streaming worked)
    assert "codex.sse_event" in log_content, \
        f"Expected SSE events but none found. Check {codex_log}"
    
    # Check for critical API errors (but allow non-critical ones)
    if "ERROR:" in log_content:
        error_lines = [line for line in log_content.split("\n") if "ERROR" in line]
        # Filter out non-critical errors (like missing env vars that we handle)
        critical_errors = [line for line in error_lines if "Missing environment variable" not in line]
        if critical_errors:
            error_msg = "\n".join(critical_errors[-5:])
            pytest.fail(
                f"Agent failed with critical API errors. Check logs at {codex_log}.\n"
                f"Last errors:\n{error_msg}"
            )
    
    # Verify Codex initialized and started conversation
    assert "codex.conversation_starts" in log_content or "Codex initialized" in log_content, \
        f"Codex doesn't appear to have started. Check logs at {codex_log}"
    
    # CRITICAL: Verify a diff was actually submitted (this is required for the test)
    verify_diff_submitted(run_dir)
    
    # Verify local backend was used
    config_path = run_dir / "artifacts" / "codex-config.pre-run.toml"
    if config_path.exists():
        config_content = config_path.read_text()
        # Verify local backend URL is configured
        assert "host.docker.internal:8000" in config_content or "synth-research" in config_content.lower(), \
            f"Local backend not configured correctly. Config: {config_content}"
    
    # Optionally check results.json
    results_path = run_dir / "results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text())
        assert "run_id" in results
        assert "exit_code" in results


@pytest.mark.integration
def test_codex_with_synth_small() -> None:
    """Test Codex with synth-small via synth backend (uses default remote URL)."""
    # Check for required API keys
    if not os.environ.get("SYNTH_API_KEY"):
        pytest.skip("SYNTH_API_KEY not set")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set (needed for synth backend)")
    
    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_codex_box.sh"
    
    # Run Codex with synth-small
    env = {
        "OPENAI_MODEL": "synth-small",
        "FORCE_OPENAI": "1",  # This will be overridden by synth model detection
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env)
    
    # Verify run completed and diff was submitted
    verify_diff_submitted(run_dir)
    
    # For synth-small, also verify it used the synth backend
    # Check config to ensure synth backend was used
    config_path = run_dir / "artifacts" / "codex-config.pre-run.toml"
    if config_path.exists():
        config_content = config_path.read_text()
        # Verify synth backend URL is configured
        assert "synth-backend" in config_content or "synth_research" in config_content.lower(), \
            "Synth backend not configured correctly"
    
    # Optionally check results.json
    results_path = run_dir / "results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text())
        assert "run_id" in results
        assert "exit_code" in results


@pytest.mark.integration
def test_opencode_with_gpt5_nano() -> None:
    """Test OpenCode with gpt-5-nano."""
    # Check for required API key
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_opencode_box.sh"
    
    # Run OpenCode with gpt-5-nano
    env = {
        "OPENAI_MODEL": "gpt-5-nano",
        "OPencode_MODE": "docker",  # Use Docker mode for consistency
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env, timeout=900)
    
    # OpenCode in Docker mode creates artifacts similar to Codex
    # Check for diff.patch in artifacts directory
    diff_path = run_dir / "artifacts" / "diff.patch"
    
    # Also check alternative locations that might be used
    alt_diff_paths = [
        run_dir / "diff.patch",
        task_path / "repo" / ".git" / "diff.patch",  # Unlikely but check anyway
    ]
    
    # Try to find diff in any of the expected locations
    found_diff = False
    if diff_path.exists() and diff_path.stat().st_size > 0:
        found_diff = True
        verify_diff_submitted(run_dir)
    else:
        # Check alternative paths
        for alt_path in alt_diff_paths:
            if alt_path.exists() and alt_path.stat().st_size > 0:
                found_diff = True
                diff_content = alt_path.read_text()
                assert len(diff_content.strip()) > 0, f"diff.patch is empty at {alt_path}"
                assert (
                    diff_content.startswith("diff --git")
                    or diff_content.startswith("---")
                    or "+++" in diff_content
                ), f"diff.patch doesn't look like a valid diff: {diff_content[:200]}"
                break
    
    # If no diff file found, check git diff in repo directory (OpenCode might write directly)
    if not found_diff:
        task_repo = task_path / "repo"
        if task_repo.exists():
            try:
                result = subprocess.run(
                    ["git", "diff", "HEAD"],
                    cwd=str(task_repo),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Diff exists in repo, verify it's valid
                    diff_content = result.stdout
                    assert len(diff_content.strip()) > 0, "git diff is empty"
                    assert (
                        diff_content.startswith("diff --git")
                        or diff_content.startswith("---")
                        or "+++" in diff_content
                    ), f"git diff doesn't look valid: {diff_content[:200]}"
                    found_diff = True
            except Exception as e:
                pytest.fail(f"Failed to check git diff in repo: {e}")
    
    # If still no diff found, fail the test
    assert found_diff, f"No diff found in any expected location. Checked: {diff_path}, {alt_diff_paths}, and git diff in {task_repo if task_repo.exists() else 'N/A'}"

