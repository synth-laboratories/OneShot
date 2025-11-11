"""
Integration tests for Research Bench evaluation pipeline.

These tests verify the end-to-end flow:
1. Running Codex on a re-bench task
2. Evaluating the run against rubrics
3. Comparing baseline performance (optional, slow)

These tests ensure the evaluation pipeline continues to work after changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DATA_DIR = REPO_ROOT / "data"
TASKS_DIR = DATA_DIR / "tasks" / "prepared"
RUNS_DIR = DATA_DIR / "runs"


def get_rebench_task() -> Optional[Path]:
    """Get a re-bench task path (any available re-bench task).
    
    Returns the first available re-bench task found in the tasks directory.
    Does not prefer any specific task - works with any re-bench task.
    """
    # Try any re-bench task (no preference for banking77)
    for task_dir in sorted(TASKS_DIR.iterdir()):
        if task_dir.is_dir() and task_dir.name.startswith("re-bench-"):
            return task_dir
    
    return None


def run_codex_on_task(
    task_path: Path,
    model: str = "gpt-5-nano",
    timeout: int = 600,
) -> tuple[int, Path]:
    """
    Run Codex on a task and return exit code and run directory.
    
    Args:
        task_path: Path to the task directory
        model: Model to use
        timeout: Timeout in seconds
        
    Returns:
        Tuple of (exit_code, run_dir)
    """
    run_id = f"test_rebench_{int(time.time())}"
    run_dir = RUNS_DIR / run_id
    
    # Prepare environment
    script_env = os.environ.copy()
    script_env["RUN_ID"] = run_id
    script_env["OPENAI_MODEL"] = model
    script_env["SKIP_EVAL"] = "1"  # Skip evaluation in run_codex_box.sh, we'll do it separately
    
    script_path = SCRIPTS_DIR / "run_codex_box.sh"
    
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
        
        if result.returncode != 0:
            print(f"\n=== Codex run failed with exit code {result.returncode} ===")
            print(f"STDOUT:\n{result.stdout[-1000:]}")
            print(f"STDERR:\n{result.stderr[-1000:]}")
        
        return result.returncode, run_dir
    except subprocess.TimeoutExpired:
        pytest.fail(f"Codex run timed out after {timeout} seconds")
        return 1, run_dir  # Unreachable
    except Exception as e:
        pytest.fail(f"Failed to run Codex: {e}")
        return 1, run_dir  # Unreachable


def evaluate_run(
    run_dir: Path,
    task_dir: Path,
    timeout: int = 300,
) -> tuple[int, dict]:
    """
    Evaluate a run directory.
    
    Args:
        run_dir: Path to run directory
        task_dir: Path to task directory
        timeout: Timeout in seconds
        
    Returns:
        Tuple of (exit_code, results_dict)
    """
    script_env = os.environ.copy()
    script_env["PYTHONPATH"] = str(REPO_ROOT / "src")
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "one_shot.evaluate_run", str(run_dir), str(task_dir)],
            cwd=str(REPO_ROOT),
            env=script_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        # Load results if available
        results = {}
        eval_file = run_dir / "evaluation_results.json"
        if eval_file.exists():
            results = json.loads(eval_file.read_text())
        
        return result.returncode, results
    except subprocess.TimeoutExpired:
        pytest.fail(f"Evaluation timed out after {timeout} seconds")
        return 1, {}
    except Exception as e:
        pytest.fail(f"Failed to evaluate run: {e}")
        return 1, {}


def verify_run_artifacts(run_dir: Path) -> None:
    """Verify that run directory has required artifacts."""
    assert run_dir.exists(), f"Run directory does not exist: {run_dir}"
    
    artifacts_dir = run_dir / "artifacts"
    assert artifacts_dir.exists(), f"Artifacts directory does not exist: {artifacts_dir}"
    
    # Check for diff.patch (agent should have made changes)
    diff_path = artifacts_dir / "diff.patch"
    if not diff_path.exists():
        # Check alternative locations
        alt_paths = [
            artifacts_dir / "container_git_diff_from_baseline.patch",
            artifacts_dir / "container_git_diff.patch",
        ]
        found = False
        for alt_path in alt_paths:
            if alt_path.exists():
                found = True
                break
        if not found:
            pytest.skip("No diff.patch found - agent may not have made changes (this is OK for some tasks)")
    
    # Check for baseline SHA
    baseline_sha = artifacts_dir / "baseline_sha.txt"
    assert baseline_sha.exists(), f"baseline_sha.txt not found in {artifacts_dir}"
    
    # Check for metadata
    metadata = run_dir / "metadata.json"
    assert metadata.exists(), f"metadata.json not found in {run_dir}"


def verify_evaluation_results(run_dir: Path, results: dict) -> None:
    """Verify that evaluation results are valid."""
    assert "evaluation" in results or "lm_evaluation" in results, \
        "Evaluation results should contain 'evaluation' or 'lm_evaluation'"
    
    # Check for scoring results markdown
    scoring_md = run_dir / "scoring_results.md"
    assert scoring_md.exists(), f"scoring_results.md not found in {run_dir}"


@pytest.mark.integration
def test_rebench_codex_run() -> None:
    """
    Test running Codex on a re-bench task.
    
    This test verifies:
    1. Codex can run on a re-bench task
    2. Required artifacts are generated (diff.patch, baseline_sha.txt, metadata.json)
    3. Run directory structure is correct
    """
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    task_path = get_rebench_task()
    if not task_path:
        pytest.skip("No re-bench task found in data/tasks/prepared/")
    
    # Run Codex
    exit_code, run_dir = run_codex_on_task(task_path, model="gpt-5-nano", timeout=600)
    
    # Verify run completed
    assert exit_code == 0, f"Codex run failed with exit code {exit_code}"
    
    # Verify artifacts
    verify_run_artifacts(run_dir)


@pytest.mark.integration
def test_rebench_evaluation() -> None:
    """
    Test evaluating a re-bench run.
    
    This test verifies:
    1. Evaluation can run on a completed Codex run
    2. Evaluation results are generated correctly
    3. Results include LLM rubric scores and quantitative metrics
    """
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set (needed for LLM evaluation)")
    
    task_path = get_rebench_task()
    if not task_path:
        pytest.skip("No re-bench task found in data/tasks/prepared/")
    
    # First run Codex
    exit_code, run_dir = run_codex_on_task(task_path, model="gpt-5-nano", timeout=600)
    assert exit_code == 0, f"Codex run failed with exit code {exit_code}"
    
    # Verify artifacts exist
    verify_run_artifacts(run_dir)
    
    # Run evaluation
    eval_exit_code, results = evaluate_run(run_dir, task_path, timeout=300)
    
    # Verify evaluation completed
    assert eval_exit_code == 0, f"Evaluation failed with exit code {eval_exit_code}"
    
    # Verify results
    verify_evaluation_results(run_dir, results)


@pytest.mark.integration
@pytest.mark.slow
def test_rebench_baseline_comparison() -> None:
    """
    Test baseline comparison for a re-bench run.
    
    This test verifies:
    1. Baseline comparison can run on a completed Codex run
    2. Comparison results are generated correctly
    3. Results include baseline delta, rubric scores, and code quality checks
    
    Note: This test is slow (runs baseline evaluation with multiple seeds) and is marked as 'slow'.
    Run with: pytest -m "integration and slow"
    """
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    if not os.environ.get("GROQ_API_KEY"):
        pytest.skip("GROQ_API_KEY not set (needed for baseline evaluation)")
    
    task_path = get_rebench_task()
    if not task_path:
        pytest.skip("No re-bench task found in data/tasks/prepared/")
    
    # First run Codex
    exit_code, run_dir = run_codex_on_task(task_path, model="gpt-5-nano", timeout=600)
    assert exit_code == 0, f"Codex run failed with exit code {exit_code}"
    
    # Verify artifacts exist
    verify_run_artifacts(run_dir)
    
    # Check for diff.patch (baseline comparison requires changes)
    diff_path = run_dir / "artifacts" / "diff.patch"
    if not diff_path.exists():
        alt_paths = [
            run_dir / "artifacts" / "container_git_diff_from_baseline.patch",
            run_dir / "artifacts" / "container_git_diff.patch",
        ]
        found = False
        for alt_path in alt_paths:
            if alt_path.exists():
                diff_path = alt_path
                found = True
                break
        if not found:
            pytest.skip("No diff.patch found - baseline comparison requires agent changes")
    
    # Run baseline comparison (with reduced seeds for faster testing)
    script_env = os.environ.copy()
    script_env["PYTHONPATH"] = str(REPO_ROOT / "src")
    
    compare_script = SCRIPTS_DIR / "re_bench_compare.py"
    result = subprocess.run(
        [sys.executable, str(compare_script), str(run_dir), "--num-seeds", "2"],
        cwd=str(REPO_ROOT),
        env=script_env,
        capture_output=True,
        text=True,
        timeout=1800,  # 30 minutes for baseline comparison
    )
    
    # Verify comparison completed
    assert result.returncode == 0, \
        f"Baseline comparison failed with exit code {result.returncode}\n" \
        f"STDOUT:\n{result.stdout[-1000:]}\n" \
        f"STDERR:\n{result.stderr[-1000:]}"
    
    # Verify comparison results exist
    comparison_json = run_dir / "re_bench_comparison.json"
    assert comparison_json.exists(), f"re_bench_comparison.json not found in {run_dir}"
    
    # Verify results structure
    with open(comparison_json) as f:
        comparison = json.load(f)
    
    assert "baseline_score" in comparison or "patched_score" in comparison, \
        "Comparison results should include baseline or patched scores"
    
    assert "reward_types" in comparison, \
        "Comparison results should include reward_types"
    
    # Verify reward types structure
    rewards = comparison["reward_types"]
    assert isinstance(rewards, list), "reward_types should be a list"
    
    # Check for expected reward types
    reward_type_names = [r.get("type") for r in rewards]
    assert "baseline_delta" in reward_type_names or "qualitative_rubric" in reward_type_names, \
        "Comparison should include baseline_delta or qualitative_rubric rewards"


@pytest.mark.integration
def test_rebench_batch_runner() -> None:
    """
    Test the batch runner (run_re_bench.py) with a simple config.
    
    This test verifies:
    1. Batch runner can load config files
    2. Batch runner can execute runs
    3. Batch results are generated correctly
    """
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    task_path = get_rebench_task()
    if not task_path:
        pytest.skip("No re-bench task found in data/tasks/prepared/")
    
    # Create a temporary config file for testing
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
        config_content = f"""# Test config
[defaults]
codex_config = "~/.codex"
run_baseline_comparison = false
skip_eval_if_exists = true
num_runs = 1
verbose = false

[[runs]]
task = "{task_path.name if task_path else 'unknown'}"
model = "gpt-5-nano"
run_baseline_comparison = false
"""
        f.write(config_content)
        config_path = Path(f.name)
    
    try:
        # Run batch runner
        script_env = os.environ.copy()
        script_env["PYTHONPATH"] = str(REPO_ROOT / "src")
        script_env["SKIP_EVAL"] = "1"  # Skip evaluation in run_codex_box.sh
        
        batch_script = SCRIPTS_DIR / "run_re_bench.py"
        result = subprocess.run(
            [sys.executable, str(batch_script), "--config", str(config_path)],
            cwd=str(REPO_ROOT),
            env=script_env,
            capture_output=True,
            text=True,
            timeout=900,  # 15 minutes
        )
        
        # Verify batch runner completed
        assert result.returncode == 0, \
            f"Batch runner failed with exit code {result.returncode}\n" \
            f"STDOUT:\n{result.stdout[-2000:]}\n" \
            f"STDERR:\n{result.stderr[-2000:]}"
        
        # Check for batch results (output directory should be created)
        # The batch runner creates output in data/runs/<batch_id>/
        # We can't easily predict the batch_id, but we can check stdout for it
        if "Results saved to:" in result.stdout:
            # Extract output directory from stdout
            for line in result.stdout.split("\n"):
                if "Results saved to:" in line:
                    # Try to extract path
                    parts = line.split("Results saved to:")
                    if len(parts) > 1:
                        output_path = Path(parts[1].strip())
                        if output_path.exists():
                            batch_results = output_path / "batch_results.json"
                            if batch_results.exists():
                                with open(batch_results) as bf:
                                    batch_data = json.load(bf)
                                assert "summary" in batch_data, "Batch results should include summary"
                                assert "runs" in batch_data, "Batch results should include runs"
    finally:
        # Clean up temp config file
        config_path.unlink(missing_ok=True)

