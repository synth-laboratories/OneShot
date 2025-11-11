"""Unit tests for evaluate_run.py - no external dependencies required."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from one_shot.evaluate_run import (
    evaluate_rubrics,
    compute_agent_metrics,
    load_task_metadata,
    run_test_script,
)


class TestEvaluateRubrics:
    """Test rubric evaluation logic."""

    def test_evaluate_rubrics_with_tests(self):
        """Test rubric evaluation when tests exist."""
        task_meta = {
            "evaluation": {
                "rubrics": [
                    {"id": "r1", "criterion": "Criterion 1", "weight": 0.5},
                    {"id": "r2", "criterion": "Criterion 2", "weight": 0.5},
                ],
                "test_scripts": [
                    {"path": "test_r1.py", "rubric_id": "r1"},
                    {"path": "test_r1_2.py", "rubric_id": "r1"},
                    {"path": "test_r2.py", "rubric_id": "r2"},
                ],
            }
        }
        test_results = {
            "test_r1.py": (True, ""),
            "test_r1_2.py": (True, ""),
            "test_r2.py": (False, ""),
        }
        
        result = evaluate_rubrics(task_meta, test_results)
        
        assert "rubrics" in result
        assert "total_score" in result
        assert result["rubrics"]["r1"]["score"] == 1.0  # 2/2 passed
        assert result["rubrics"]["r1"]["tests_passed"] == 2
        assert result["rubrics"]["r1"]["test_count"] == 2
        assert result["rubrics"]["r2"]["score"] == 0.0  # 0/1 passed
        assert result["rubrics"]["r2"]["tests_passed"] == 0
        assert result["rubrics"]["r2"]["test_count"] == 1
        assert result["total_score"] == 0.5  # (1.0 * 0.5 + 0.0 * 0.5) / 1.0

    def test_evaluate_rubrics_no_tests(self):
        """Test rubric evaluation when no tests exist."""
        task_meta = {
            "evaluation": {
                "rubrics": [
                    {"id": "r1", "criterion": "Criterion 1", "weight": 1.0},
                ],
                "test_scripts": [],
            }
        }
        test_results = {}
        
        result = evaluate_rubrics(task_meta, test_results)
        
        assert result["rubrics"]["r1"]["score"] is None
        assert result["rubrics"]["r1"]["test_count"] == 0
        assert result["total_score"] == 0.0

    def test_evaluate_rubrics_partial_pass(self):
        """Test rubric evaluation with partial test passes."""
        task_meta = {
            "evaluation": {
                "rubrics": [
                    {"id": "r1", "criterion": "Criterion 1", "weight": 1.0},
                ],
                "test_scripts": [
                    {"path": "test1.py", "rubric_id": "r1"},
                    {"path": "test2.py", "rubric_id": "r1"},
                    {"path": "test3.py", "rubric_id": "r1"},
                ],
            }
        }
        test_results = {
            "test1.py": (True, ""),
            "test2.py": (True, ""),
            "test3.py": (False, ""),
        }
        
        result = evaluate_rubrics(task_meta, test_results)
        
        assert result["rubrics"]["r1"]["score"] == pytest.approx(0.6667, abs=0.0001)  # 2/3
        assert result["rubrics"]["r1"]["tests_passed"] == 2
        assert result["rubrics"]["r1"]["test_count"] == 3

    def test_evaluate_rubrics_missing_evaluation(self):
        """Test rubric evaluation with missing evaluation section."""
        task_meta = {}
        test_results = {}
        
        result = evaluate_rubrics(task_meta, test_results)
        
        assert result["rubrics"] == {}
        assert result["total_score"] == 0

    def test_evaluate_rubrics_missing_rubric_id(self):
        """Test rubric evaluation with rubric missing id."""
        task_meta = {
            "evaluation": {
                "rubrics": [
                    {"criterion": "Criterion 1", "weight": 1.0},  # Missing id
                ],
                "test_scripts": [],
            }
        }
        test_results = {}
        
        result = evaluate_rubrics(task_meta, test_results)
        
        # Rubric without id should be skipped
        assert len(result["rubrics"]) == 0

    def test_evaluate_rubrics_path_traversal_protection(self):
        """Test that test paths are validated (path traversal handled in run_test_script)."""
        task_meta = {
            "evaluation": {
                "rubrics": [
                    {"id": "r1", "criterion": "Criterion 1", "weight": 1.0},
                ],
                "test_scripts": [
                    {"path": "../../etc/passwd", "rubric_id": "r1"},  # Path traversal attempt
                    {"path": "test1.py", "rubric_id": "r1"},
                ],
            }
        }
        test_results = {
            "../../etc/passwd": (False, "Error: Test path attempts to escape"),  # Would be rejected by run_test_script
            "test1.py": (True, ""),
        }
        
        result = evaluate_rubrics(task_meta, test_results)
        
        # Both tests are counted (path traversal filtering happens in run_test_script, not evaluate_rubrics)
        assert result["rubrics"]["r1"]["test_count"] == 2
        # But only test1.py passed (the path traversal one failed)
        assert result["rubrics"]["r1"]["tests_passed"] == 1


class TestComputeAgentMetrics:
    """Test agent metrics computation."""

    def test_compute_metrics_from_log(self):
        """Test metrics computation from codex log."""
        artifacts = {
            "codex_run_log": "tokens used: 1000\nFunctionCall: test\ntokens used: 500\nFunctionCall: test2",
        }
        
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            result = compute_agent_metrics(run_dir, artifacts)
            
            assert result["tokens_total"] == 1500
            assert result["tokens_events"] == 2
            assert result["tool_calls_codex_log"] == 2
            assert result["tool_calls_total"] == 2

    def test_compute_metrics_from_sessions(self):
        """Test metrics computation from session JSONL."""
        artifacts = {
            "codex_sessions_jsonl": [
                '{"type": "function_call", "name": "test"}\n{"type": "message"}',
                '{"type": "function_call", "name": "test2"}',
            ],
        }
        
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            result = compute_agent_metrics(run_dir, artifacts)
            
            # Each line is parsed separately: 2 function_call lines = 2 tool calls
            assert result["tool_calls_sessions"] == 2
            assert result["tool_calls_total"] == 2

    def test_compute_metrics_empty_artifacts(self):
        """Test metrics computation with empty artifacts."""
        artifacts = {}
        
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            result = compute_agent_metrics(run_dir, artifacts)
            
            assert result["tokens_total"] == 0
            assert result["tool_calls_total"] == 0
            assert result["money_spent_usd"] == 0.0

    def test_compute_metrics_invalid_jsonl(self):
        """Test metrics computation with invalid JSONL."""
        artifacts = {
            "codex_sessions_jsonl": [
                "invalid json\n{invalid}",
                '{"type": "function_call"}',
            ],
        }
        
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            result = compute_agent_metrics(run_dir, artifacts)
            
            # Should handle invalid JSON gracefully
            assert result["tool_calls_sessions"] == 1  # Only valid one counted


class TestLoadTaskMetadata:
    """Test task metadata loading."""

    def test_load_valid_metadata(self):
        """Test loading valid task metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            meta_path = task_dir / "tb_meta.json"
            meta_data = {"task_id": "test", "metadata": {"title": "Test"}}
            
            with open(meta_path, "w") as f:
                json.dump(meta_data, f)
            
            result = load_task_metadata(task_dir)
            assert result == meta_data

    def test_load_missing_metadata(self):
        """Test loading missing metadata file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            
            with pytest.raises(FileNotFoundError):
                load_task_metadata(task_dir)

    def test_load_invalid_json(self):
        """Test loading invalid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            meta_path = task_dir / "tb_meta.json"
            
            with open(meta_path, "w") as f:
                f.write("invalid json {")
            
            with pytest.raises(ValueError, match="Invalid JSON"):
                load_task_metadata(task_dir)


class TestRunTestScript:
    """Test test script execution."""

    def test_run_test_script_success(self):
        """Test running a successful test script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_script = {
                "path": "test_example.py",
                "content": "def test_example():\n    assert True\n",
            }
            
            success, output = run_test_script(repo_dir, test_script)
            
            assert success is True
            assert "PASSED" in output or "passed" in output.lower()

    def test_run_test_script_failure(self):
        """Test running a failing test script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_script = {
                "path": "test_example.py",
                "content": "def test_example():\n    assert False\n",
            }
            
            success, output = run_test_script(repo_dir, test_script)
            
            assert success is False
            assert "FAILED" in output or "failed" in output.lower()

    def test_run_test_script_missing_path(self):
        """Test running test script without path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_script = {
                "content": "def test_example():\n    assert True\n",
            }
            
            success, output = run_test_script(repo_dir, test_script)
            
            assert success is False
            assert "missing 'path'" in output.lower()

    def test_run_test_script_path_traversal(self):
        """Test path traversal protection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_script = {
                "path": "../../etc/passwd",
                "content": "def test_example():\n    assert True\n",
            }
            
            success, output = run_test_script(repo_dir, test_script)
            
            assert success is False
            assert "escape" in output.lower() or "invalid" in output.lower()

    def test_run_test_script_nested_path(self):
        """Test running test script in nested directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_script = {
                "path": "tests/unit/test_example.py",
                "content": "def test_example():\n    assert True\n",
            }
            
            success, output = run_test_script(repo_dir, test_script)
            
            assert success is True
            # Verify nested directory was created
            assert (repo_dir / "tests" / "unit" / "test_example.py").exists()

