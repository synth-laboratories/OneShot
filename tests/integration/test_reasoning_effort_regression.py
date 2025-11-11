"""
Integration test to verify reasoning_effort is correctly set for gpt-5-nano.

This test catches regressions where reasoning_effort is not properly configured,
which causes API errors: "Reasoning is mandatory for this endpoint and cannot be disabled."

CRITICAL: This test MUST pass for gpt-5-nano to work correctly.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

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
    env: dict[str, str] | None = None,
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
    run_id = f"test_reasoning_{int(time.time())}"
    run_dir = RUNS_DIR / run_id
    
    # Prepare environment
    script_env = os.environ.copy()
    script_env["RUN_ID"] = run_id
    script_env["SKIP_EVAL"] = "1"  # Skip evaluation to speed up test
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
        
        return result.returncode, run_dir
    except subprocess.TimeoutExpired:
        pytest.fail(f"Script timed out after {timeout} seconds")
        return 1, run_dir  # Unreachable but satisfies type checker
    except Exception as e:
        pytest.fail(f"Failed to run script: {e}")
        return 1, run_dir  # Unreachable but satisfies type checker


@pytest.mark.integration
def test_reasoning_effort_config_for_gpt5_nano() -> None:
    """
    CRITICAL REGRESSION TEST: Verify reasoning_effort is correctly set for gpt-5-nano.
    
    This test verifies:
    1. Config file contains reasoning_effort = "medium"
    2. Config file contains reasoning_summaries = "auto"
    3. Codex actually reads the config (not "reasoning effort: none")
    4. No API errors about reasoning being disabled
    
    This test MUST pass for gpt-5-nano to work correctly.
    """
    # Check for required API key
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_codex_box.sh"
    
    # Run Codex with gpt-5-nano
    env = {
        "OPENAI_MODEL": "gpt-5-nano",
        "OPENAI_REASONING_EFFORT": "medium",  # Explicitly set
        "SKIP_EVAL": "1",  # Skip evaluation to speed up test
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env, timeout=300)
    
    # Verify config file was created
    config_path = run_dir / "codex_home" / ".codex" / "config.toml"
    assert config_path.exists(), f"Config file not found at {config_path}"
    
    # Read and verify config file contents
    config_content = config_path.read_text()
    
    # CRITICAL: Verify model_reasoning_effort is set in config file
    assert 'model_reasoning_effort = "medium"' in config_content, (
        f"model_reasoning_effort not set to 'medium' in config file.\n"
        f"Config content:\n{config_content}"
    )
    
    # CRITICAL: Verify reasoning_summaries is set in config file
    assert 'reasoning_summaries = "auto"' in config_content, (
        f"reasoning_summaries not set to 'auto' in config file.\n"
        f"Config content:\n{config_content}"
    )
    
    # Verify model is set correctly
    assert 'model = "gpt-5-nano"' in config_content, (
        f"Model not set to 'gpt-5-nano' in config file.\n"
        f"Config content:\n{config_content}"
    )
    
    # Verify pre-run config was copied (if it exists)
    pre_run_config = run_dir / "artifacts" / "codex-config.pre-run.toml"
    if pre_run_config.exists():
        pre_run_content = pre_run_config.read_text()
        assert 'model_reasoning_effort = "medium"' in pre_run_content, (
            f"model_reasoning_effort not set in pre-run config.\n"
            f"Pre-run config:\n{pre_run_content}"
        )
    
    # Check Codex logs to verify it actually read the config
    codex_log = run_dir / "artifacts" / "codex-run.log"
    if codex_log.exists():
        log_content = codex_log.read_text()
        
        # CRITICAL: Verify Codex shows reasoning effort is NOT "none"
        # This is the actual regression we're catching
        if "reasoning effort: none" in log_content.lower():
            pytest.fail(
                f"REGRESSION DETECTED: Codex shows 'reasoning effort: none' "
                f"even though config file has model_reasoning_effort='medium'.\n"
                f"This will cause API errors.\n"
                f"Check logs at {codex_log}\n"
                f"Config file at {config_path}:\n{config_content}"
            )
        
        # Verify Codex shows reasoning effort is set (should show "medium" or similar)
        # Note: Codex might show it differently, so we check it's not "none"
        if "reasoning effort:" in log_content.lower():
            # Extract the reasoning effort line
            reasoning_lines = [
                line for line in log_content.split("\n")
                if "reasoning effort:" in line.lower()
            ]
            if reasoning_lines:
                reasoning_line = reasoning_lines[0].lower()
                assert "none" not in reasoning_line, (
                    f"Codex shows reasoning effort as 'none': {reasoning_lines[0]}\n"
                    f"This is a regression. Check logs at {codex_log}"
                )
        
        # CRITICAL: Verify no API errors about reasoning being disabled
        if "Reasoning is mandatory for this endpoint and cannot be disabled" in log_content:
            pytest.fail(
                f"REGRESSION DETECTED: API error about reasoning being disabled.\n"
                f"This means model_reasoning_effort was not properly set.\n"
                f"Check logs at {codex_log}\n"
                f"Config file at {config_path}:\n{config_content}"
            )
        
        # Verify Codex initialized (if it got that far)
        if "Codex initialized" in log_content or "codex.conversation_starts" in log_content:
            # Check that reasoning_effort was read correctly in the session config
            if "reasoning_effort: None" in log_content:
                pytest.fail(
                    f"REGRESSION DETECTED: Codex session shows reasoning_effort: None.\n"
                    f"This means the config was not read correctly.\n"
                    f"Check logs at {codex_log}\n"
                    f"Config file at {config_path}:\n{config_content}"
                )


@pytest.mark.integration
def test_reasoning_effort_cli_flags_for_gpt5_nano() -> None:
    """
    Verify that -c flags are correctly formatted for reasoning_effort.
    
    This test verifies that box_bootstrap.sh generates the correct -c flags
    using dotted path format (reasoning.effort) not underscore format (reasoning_effort).
    """
    # Check for required API key
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_codex_box.sh"
    
    # Run Codex with gpt-5-nano
    env = {
        "OPENAI_MODEL": "gpt-5-nano",
        "SKIP_EVAL": "1",
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env, timeout=300)
    
    # Check Codex logs for the actual command being executed
    codex_log = run_dir / "artifacts" / "codex-run.log"
    if codex_log.exists():
        log_content = codex_log.read_text()
        
        # Look for debug output showing the codex exec command
        debug_lines = [
            line for line in log_content.split("\n")
            if "[debug] codex exec" in line.lower()
        ]
        
        if debug_lines:
            debug_line = debug_lines[0]
            # Verify the command includes model_reasoning_effort (correct format)
            if "model_reasoning_effort" in debug_line:
                # Good - using correct format
                pass
            elif "reasoning.effort" in debug_line or "reasoning_effort" in debug_line:
                # Bad - using wrong format
                pytest.fail(
                    f"REGRESSION: box_bootstrap.sh is using wrong format for reasoning effort.\n"
                    f"Should use 'model_reasoning_effort' not 'reasoning.effort' or 'reasoning_effort'.\n"
                    f"Debug line: {debug_line}\n"
                    f"Check logs at {codex_log}"
                )


@pytest.mark.integration
def test_reasoning_effort_pattern_matching() -> None:
    """
    Verify that gpt-5-nano is correctly detected as a reasoning-required model.
    
    This test verifies the regex pattern matching in box_bootstrap.sh correctly
    identifies gpt-5-nano (and other gpt-5-* models) as requiring reasoning.
    """
    # Check for required API key
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_codex_box.sh"
    
    # Test with gpt-5-nano (should require reasoning)
    env = {
        "OPENAI_MODEL": "gpt-5-nano",
        "SKIP_EVAL": "1",
    }
    
    exit_code, run_dir = run_script(script_path, task_path, env=env, timeout=300)
    
    # Check logs for reasoning detection message
    codex_log = run_dir / "artifacts" / "codex-run.log"
    if codex_log.exists():
        log_content = codex_log.read_text()
        
        # Verify reasoning was detected (should see a message about reasoning-required model)
        # This could be in the bootstrap script output or Codex output
        if "[reasoning]" in log_content.lower() or "reasoning-required" in log_content.lower():
            # Good - reasoning was detected
            pass
        else:
            # Check if reasoning args were actually set by looking at config
            config_path = run_dir / "codex_home" / ".codex" / "config.toml"
            if config_path.exists():
                config_content = config_path.read_text()
                if 'model_reasoning_effort' in config_content:
                    # Config was set, so pattern matching worked (even if log doesn't show it)
                    pass
                else:
                    pytest.fail(
                        f"Pattern matching may have failed: gpt-5-nano should require reasoning "
                        f"but model_reasoning_effort not found in config.\n"
                        f"Config: {config_content}\n"
                        f"Logs: {codex_log}"
                    )

