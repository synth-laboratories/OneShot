"""Integration tests covering the supported Codex flows.

OpenCode-specific coverage lives on the `add-open-code` feature branch.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DATA_DIR = REPO_ROOT / "data"
TASKS_DIR = DATA_DIR / "tasks" / "prepared"
RUNS_DIR = DATA_DIR / "runs"


def get_test_task() -> Path:
    """Return the prepared hello-world task or skip if unavailable."""
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
    """Launch the helper script and return its exit code and run directory."""
    run_id = f"test_{int(time.time())}"
    run_dir = RUNS_DIR / run_id

    script_env = os.environ.copy()
    script_env["RUN_ID"] = run_id
    if env:
        script_env.update(env)

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
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        pytest.fail(f"Script timed out after {timeout} seconds: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        pytest.fail(f"Failed to run script: {exc}")
    return 1, run_dir  # never reached, keeps type-checker happy


def verify_agent_ran(run_dir: Path) -> None:
    """Ensure Codex actually started and streamed tokens."""
    log_path = run_dir / "artifacts" / "codex-run.log"
    if not log_path.exists():
        pytest.fail(f"codex-run.log missing at {log_path}")

    log_content = log_path.read_text(encoding="utf-8", errors="ignore")
    if "ERROR:" in log_content or "unexpected status" in log_content:
        lines = [line for line in log_content.splitlines() if "ERROR" in line or "unexpected status" in line]
        pytest.fail(
            "Codex recorded errors. See log at "
            f"{log_path}\nLast entries:\n" + "\n".join(lines[-5:])
        )

    if "conversation_starts" not in log_content and "Codex initialized" not in log_content:
        pytest.fail(f"Codex does not appear to have started. Inspect {log_path}")


def verify_diff_submitted(run_dir: Path) -> None:
    """Confirm a diff was produced in the run artifacts."""
    verify_agent_ran(run_dir)

    diff_path = run_dir / "artifacts" / "diff.patch"
    if not diff_path.exists():
        alt_paths = [
            run_dir / "artifacts" / "container_git_diff_from_baseline.patch",
            run_dir / "artifacts" / "container_git_diff.patch",
        ]
        for candidate in alt_paths:
            if candidate.exists():
                diff_path = candidate
                break
        else:
            pytest.fail(f"diff.patch not found in {run_dir / 'artifacts'}")

    diff_content = diff_path.read_text(encoding="utf-8", errors="ignore")
    assert diff_content.strip(), f"diff.patch is empty at {diff_path}"
    assert diff_content.startswith("diff --git") or "+++" in diff_content, "diff patch is malformed"


@pytest.mark.integration
def test_codex_with_gpt5_nano() -> None:
    """Smoke test: Codex + gpt-5-nano should produce a diff."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_codex_box.sh"

    env = {
        "OPENAI_MODEL": "gpt-5-nano",
        "OPENAI_REASONING_EFFORT": "medium",
    }

    exit_code, run_dir = run_script(script_path, task_path, env=env)
    if exit_code != 0:
        pytest.skip(f"run_codex_box.sh exited with {exit_code}; inspect {run_dir}")

    verify_diff_submitted(run_dir)

    results_path = run_dir / "results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text())
        assert results.get("run_id")
        assert results.get("exit_code") == 0


@pytest.mark.integration
def test_codex_with_synth_small() -> None:
    """Ensure synth-small via the Synth backend still works when configured."""
    if not os.environ.get("SYNTH_API_KEY"):
        pytest.skip("SYNTH_API_KEY not set")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set (required for Synth backend)")

    task_path = get_test_task()
    script_path = SCRIPTS_DIR / "run_codex_box.sh"

    env = {
        "OPENAI_MODEL": "synth-small",
    }

    exit_code, run_dir = run_script(script_path, task_path, env=env)
    if exit_code != 0:
        pytest.skip(f"run_codex_box.sh exited with {exit_code}; inspect {run_dir}")

    verify_diff_submitted(run_dir)

    config_path = run_dir / "artifacts" / "codex-config.pre-run.toml"
    if config_path.exists():
        config = config_path.read_text(encoding="utf-8", errors="ignore")
        assert "synth" in config.lower(), "Synth backend not configured correctly"
