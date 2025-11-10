"""
Integration tests for Codex with different model configurations.

These tests verify end-to-end Codex execution with various models and backends.
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


def get_test_task(task_name: str = "hello-world-example") -> Path:
    """Get a test task path."""
    task_path = TASKS_DIR / task_name
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
    script_env["SKIP_EVAL"] = "1"  # Skip evaluation to speed up tests
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
        
        # If script failed, print output for debugging
        if result.returncode != 0:
            print(f"\n=== Script failed with exit code {result.returncode} ===")
            print(f"STDOUT:\n{result.stdout}")
            print(f"STDERR:\n{result.stderr}")
            print(f"Run directory: {run_dir}")
            if run_dir.exists():
                print(f"Contents: {list(run_dir.iterdir())}")
        
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
    # Check for codex-run.log or codex.log
    codex_log = run_dir / "artifacts" / "codex.log"
    if not codex_log.exists():
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
                f"Agent may not have made any changes. Check logs at {run_dir / 'artifacts' / 'codex.log'}"
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
def test_codex_gpt5_nano_rebench_banking77() -> None:
    """
    Test Codex with gpt-5-nano on re-bench-banking77 task.
    
    This test verifies:
    1. Codex runs successfully with gpt-5-nano
    2. Task completes and produces a diff
    3. No critical errors occur
    
    Prerequisites:
    - OPENAI_API_KEY must be set
    - re-bench-banking77 task must exist in data/tasks/prepared/
    """
    # Check for required API key
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    task_path = get_test_task("re-bench-banking77")
    script_path = SCRIPTS_DIR / "run_codex_box.sh"
    
    # Run Codex with gpt-5-nano
    env: dict[str, str] = {
        "OPENAI_MODEL": "gpt-5-nano",
        "OPENAI_REASONING_EFFORT": "medium",
        "DOCKER_NO_CACHE": "1",  # Force rebuild
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env, timeout=900)
    
    # Verify run completed
    verify_agent_ran(run_dir)
    
    # Verify diff was submitted (if task requires changes)
    # Some tasks may not produce diffs, so we check if codex.log exists and has content
    codex_log = run_dir / "artifacts" / "codex.log"
    if not codex_log.exists():
        codex_log = run_dir / "artifacts" / "codex-run.log"
    
    assert codex_log.exists(), f"Codex log not found at {codex_log}"
    log_content = codex_log.read_text()
    
    # Check for critical errors
    if "ERROR:" in log_content:
        error_lines = [line for line in log_content.split("\n") if "ERROR" in line]
        critical_errors = [line for line in error_lines if "Missing environment variable" not in line]
        if critical_errors:
            error_msg = "\n".join(critical_errors[-5:])
            pytest.fail(
                f"Agent failed with critical errors. Check logs at {codex_log}.\n"
                f"Last errors:\n{error_msg}"
            )
    
    # Verify Codex initialized
    assert "codex.conversation_starts" in log_content or "Codex initialized" in log_content, \
        f"Codex doesn't appear to have started. Check logs at {codex_log}"
    
    # Check results.json if it exists
    results_path = run_dir / "results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text())
        assert "run_id" in results
        assert "exit_code" in results


@pytest.mark.integration
def test_codex_synth_experimental_oss_local_backend() -> None:
    """
    Test Codex with synth-experimental-oss via local synth backend.
    
    This test verifies:
    1. Codex connects to local backend at http://host.docker.internal:8000/api/synth-research
    2. synth-experimental-oss model is recognized and configured correctly
    3. Stream completes successfully (no "stream disconnected before completion" errors)
    4. Diff is submitted successfully
    
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
    
    # Run Codex with synth-experimental-oss against local backend
    env: dict[str, str] = {
        "OPENAI_MODEL": "synth-experimental-oss",
        "SYNTH_BASE_URL": local_backend_url,
        "OPENAI_BASE_URL": local_backend_url,
        "SYNTH_API_KEY": str(synth_api_key),  # Required for synth model detection
        "OPENAI_API_KEY": str(openai_api_key),  # Required for backend to call Groq
        "FORCE_OPENAI": "1",
        "DOCKER_NO_CACHE": "1",  # Force rebuild
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env, timeout=600)
    
    # Verify agent ran successfully (check for stream completion)
    codex_log = run_dir / "artifacts" / "codex.log"
    if not codex_log.exists():
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
    assert "http.response.status_code=200" in log_content or "status_code=200" in log_content, \
        f"Expected successful API response (200), but not found in logs. Check {codex_log}"
    
    # Verify SSE events were received (streaming worked)
    assert "codex.sse_event" in log_content or "sse_event" in log_content, \
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
    
    # Verify synth-experimental-oss model was configured
    if config_path.exists():
        config_content = config_path.read_text()
        assert "synth-experimental-oss" in config_content or "gpt-oss-120b" in config_content, \
            f"synth-experimental-oss model not configured correctly. Config: {config_content}"
    
    # Optionally check results.json
    results_path = run_dir / "results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text())
        assert "run_id" in results
        assert "exit_code" in results

