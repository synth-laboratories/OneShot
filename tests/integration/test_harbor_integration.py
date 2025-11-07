"""
Integration tests for Harbor conversion and execution.

These tests verify end-to-end conversion and optionally run Harbor tasks.
Set HARBOR_INTEGRATION_TEST=1 to enable actual Harbor execution tests.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import sys

import pytest  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.oneshot_to_harbor import convert_prepared_task, write_registry  # noqa: E402

# Set this environment variable to enable actual Harbor execution tests
RUN_HARBOR_TESTS = os.environ.get("HARBOR_INTEGRATION_TEST", "0") == "1"

DATA_ROOT = Path("data/tasks/prepared")


@pytest.mark.integration
def test_end_to_end_conversion_and_structure(tmp_path: Path) -> None:
    """Test complete conversion of a prepared task."""
    prepared_dir = DATA_ROOT / "hello-world-example"
    if not prepared_dir.exists():
        pytest.skip(f"Prepared task fixture not available: {prepared_dir}")

    # Convert task
    result = convert_prepared_task(prepared_dir, tmp_path, overwrite=True)

    # Verify all required files exist
    harbor_dir = tmp_path / result.slug
    required_files = [
        "instruction.md",
        "task.toml",
        "environment/Dockerfile",
        "solution/solve.sh",
        "tests/test.sh",
    ]
    for file_path in required_files:
        assert (harbor_dir / file_path).exists(), f"Missing required file: {file_path}"

    # Verify task.toml is valid TOML (basic check)
    task_toml_content = (harbor_dir / "task.toml").read_text()
    assert "[metadata]" in task_toml_content
    assert "[verifier]" in task_toml_content
    assert "[agent]" in task_toml_content
    assert "[environment]" in task_toml_content

    # Verify Dockerfile is valid
    dockerfile_content = (harbor_dir / "environment" / "Dockerfile").read_text()
    assert "FROM ubuntu:24.04" in dockerfile_content
    assert "WORKDIR" in dockerfile_content

    # Verify scripts are executable
    solve_sh = harbor_dir / "solution" / "solve.sh"
    test_sh = harbor_dir / "tests" / "test.sh"
    assert os.access(solve_sh, os.X_OK)
    assert os.access(test_sh, os.X_OK)


@pytest.mark.integration
def test_registry_generation(tmp_path: Path) -> None:
    """Test registry.json generation."""
    prepared_dir = DATA_ROOT / "hello-world-example"
    if not prepared_dir.exists():
        pytest.skip(f"Prepared task fixture not available: {prepared_dir}")

    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    # Convert task
    result = convert_prepared_task(prepared_dir, tasks_dir, overwrite=True)

    # Generate registry
    write_registry(tmp_path, "test-dataset", [result], version="1.0")

    # Verify registry
    registry_path = tmp_path / "registry.json"
    assert registry_path.exists()

    registry = json.loads(registry_path.read_text())
    assert registry["name"] == "test-dataset"
    assert registry["version"] == "1.0"
    assert len(registry["tasks"]) == 1
    assert registry["tasks"][0]["name"] == result.slug


@pytest.mark.integration
@pytest.mark.skipif(not RUN_HARBOR_TESTS, reason="HARBOR_INTEGRATION_TEST not set")
def test_harbor_oracle_execution(tmp_path: Path) -> None:
    """Test that converted task can be executed by Harbor oracle."""
    prepared_dir = DATA_ROOT / "hello-world-example"
    if not prepared_dir.exists():
        pytest.skip(f"Prepared task fixture not available: {prepared_dir}")

    # Convert task
    result = convert_prepared_task(prepared_dir, tmp_path, overwrite=True)
    task_path = tmp_path / result.slug

    # Check if Harbor CLI is available
    try:
        subprocess.run(
            ["harbor", "--version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("Harbor CLI not available")

    # Run Harbor oracle
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()

    try:
        result_proc = subprocess.run(
            [
                "harbor",
                "run",
                "-p",
                str(task_path),
                "-a",
                "oracle",
                "-e",
                "docker",
                "--jobs-dir",
                str(jobs_dir),
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        # Check that it completed
        assert result_proc.returncode == 0, f"Harbor execution failed: {result_proc.stderr}"

        # Verify results were written
        result_files = list(jobs_dir.rglob("result.json"))
        assert len(result_files) > 0, "No result.json files found"

        # Check result content
        result_json = json.loads(result_files[0].read_text())
        assert "stats" in result_json
        assert result_json["stats"]["n_trials"] > 0

    except subprocess.TimeoutExpired:
        pytest.fail("Harbor execution timed out")


@pytest.mark.integration
def test_batch_conversion(tmp_path: Path) -> None:
    """Test converting multiple tasks."""
    if not DATA_ROOT.exists():
        pytest.skip(f"Data root not available: {DATA_ROOT}")

    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    # Find available prepared tasks
    available_tasks = [d for d in DATA_ROOT.iterdir() if d.is_dir() and (d / "tb_meta.json").exists()]
    if not available_tasks:
        pytest.skip("No prepared tasks available")

    # Convert first few tasks
    results = []
    for task_dir in available_tasks[:3]:  # Limit to 3 for speed
        try:
            result = convert_prepared_task(task_dir, tasks_dir, overwrite=True)
            results.append(result)
        except Exception as e:
            pytest.fail(f"Failed to convert {task_dir}: {e}")

    assert len(results) > 0
    assert all((tasks_dir / r.slug).exists() for r in results)

