#!/usr/bin/env python3
"""
Evaluate a Codex-in-the-Box run against Terminal-Bench rubrics.

Usage: python evaluate_run.py <run_dir> <task_dir>
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
import shutil
import asyncio
import os

# Try to load .env file for API keys
env_candidates = [
    Path.cwd() / ".env",
    Path(__file__).parent.parent.parent / ".env",
    Path(__file__).parent.parent.parent.parent / ".env",
    Path(__file__).parent.parent.parent.parent.parent / "synth-ai" / ".env",
]

try:
    from dotenv import load_dotenv
    
    # Try multiple .env file locations
    for env_file in env_candidates:
        if env_file.exists():
            load_dotenv(env_file, override=False)
            break
except ImportError:
    # python-dotenv not installed, skip
    pass

# Optional import of structured LM scorer (gpt-5-nano)
LM_SCORER_AVAILABLE = False
LM_SCORER_ERROR = None
try:
    scorer_module_path = (Path(__file__).resolve().parent.parent / "synth_bench" / "evaluation")
    sys.path.append(str(scorer_module_path))
    from lm_rubric_scorer_structured import LMRubricScorerStructured  # type: ignore
    LM_SCORER_AVAILABLE = True
except Exception as import_err:
    # Will proceed without LM scoring if import fails
    LM_SCORER_AVAILABLE = False
    LM_SCORER_ERROR = str(import_err)


def load_task_metadata(task_dir: Path) -> Dict[str, Any]:
    """Load the tb_meta.json file."""
    meta_path = task_dir / "tb_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"No tb_meta.json found at {meta_path}")
    
    with open(meta_path) as f:
        return json.load(f)


def load_run_artifacts(run_dir: Path) -> Dict[str, Any]:
    """Load artifacts from a run directory."""
    artifacts = {}
    
    # Load diff if present, fallback to container baseline diff
    diff_candidates = [
        run_dir / "artifacts" / "diff.patch",
        run_dir / "artifacts" / "container_git_diff_from_baseline.patch",
        run_dir / "artifacts" / "container_git_diff.patch",
    ]
    for p in diff_candidates:
        if p.exists():
            with open(p) as f:
                artifacts["diff"] = f.read()
            break
    
    # Load clean trace if present
    trace_path = run_dir / "artifacts" / "clean_session_trace.json"
    if trace_path.exists():
        try:
            with open(trace_path) as f:
                content = f.read()
                if content.strip() and not content.startswith("Error:"):
                    artifacts["trace"] = json.loads(content)
                else:
                    print(f"Warning: Invalid or empty trace file at {trace_path}")
        except (json.JSONDecodeError, Exception) as e:
            print(f"Warning: Could not load trace file: {e}")
    
    # Load container logs if present
    log_path = run_dir / "logs" / "container_full.log"
    if log_path.exists():
        with open(log_path) as f:
            artifacts["logs"] = f.read()
    
    # Load codex run log if present
    codex_log = run_dir / "artifacts" / "codex-run.log"
    if codex_log.exists():
        with open(codex_log) as f:
            artifacts["codex_run_log"] = f.read()
    
    # Load codex session JSONLs if present
    sessions_dir = run_dir / "artifacts" / "codex-sessions"
    if sessions_dir.exists() and sessions_dir.is_dir():
        session_files: List[Path] = list(sessions_dir.rglob("*.jsonl"))
        sessions: List[str] = []
        for sf in session_files:
            try:
                sessions.append(sf.read_text())
            except Exception:
                pass
        artifacts["codex_sessions_jsonl"] = sessions
    
    return artifacts


def compute_agent_metrics(run_dir: Path, artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """Compute agent metrics from codex logs: tokens used, tool calls, time, and cost estimates."""
    import time
    import os
    
    tokens_total = 0
    tokens_events = 0
    tool_calls_codex_log = 0
    tool_calls_sessions = 0

    # Parse tokens and tool calls from codex-run.log
    codex_log_text = artifacts.get("codex_run_log")
    if isinstance(codex_log_text, str):
        for line in codex_log_text.splitlines():
            m = re.search(r"tokens used:\s*(\d+)", line)
            if m:
                tokens_total += int(m.group(1))
                tokens_events += 1
            if "FunctionCall:" in line:
                tool_calls_codex_log += 1

    # Parse tool calls from codex session JSONL
    sessions_texts = artifacts.get("codex_sessions_jsonl")
    if isinstance(sessions_texts, list):
        for text in sessions_texts:
            for raw in text.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    if obj.get("type") == "function_call":
                        tool_calls_sessions += 1
                except Exception:
                    continue

    # Calculate time duration from run directory timestamps
    time_taken_seconds = 0.0
    try:
        # Try to get start/end times from run directory
        start_time = run_dir.stat().st_ctime
        end_time = run_dir.stat().st_mtime
        time_taken_seconds = max(0, end_time - start_time)
    except Exception:
        pass
    
    # Estimate money spent (rough estimate based on tokens)
    # Assuming gpt-5-nano pricing: ~$0.10 per 1M tokens input, $0.30 per 1M tokens output
    # Rough estimate: assume 50/50 input/output split
    money_spent_usd = 0.0
    if tokens_total > 0:
        # Very rough estimate: $0.20 per 1M tokens average
        money_spent_usd = (tokens_total / 1_000_000) * 0.20
    
    return {
        "tokens_total": tokens_total,
        "tokens_events": tokens_events,
        "tool_calls_codex_log": tool_calls_codex_log,
        "tool_calls_sessions": tool_calls_sessions,
        "tool_calls_total": tool_calls_codex_log + tool_calls_sessions,
        "time_taken_seconds": time_taken_seconds,
        "money_spent_usd": money_spent_usd,
    }


def collect_repo_artifacts_for_lm(repo_dir: Path) -> Dict[str, Any]:
    """Collect a small set of relevant files from the repo for LM evaluation."""
    artifacts: Dict[str, Any] = {"files": {}}
    relevant_files = [
        "README.md",
        "readme.md",
        "README.rst",
        "CONTRIBUTING.md",
    ]
    for file_name in relevant_files:
        file_path = repo_dir / file_name
        if file_path.exists():
            try:
                artifacts["files"][file_name] = file_path.read_text()
            except Exception:
                pass
    return artifacts


def setup_test_environment(task_meta: Dict, diff_content: str) -> Path:
    """Set up a temporary test environment with the repo and applied diff."""
    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="tb_eval_"))
    
    print(f"Setting up test environment in {temp_dir}")
    
    # Clone the repository
    repo_config = task_meta["repo"]
    git_url = repo_config["git_url"]
    branch = repo_config.get("branch", "main")
    commit = repo_config.get("start_commit_sha")
    
    # Clone
    result = subprocess.run(
        ["git", "clone", git_url, "-b", branch, str(temp_dir / "repo")],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone repo: {result.stderr}")
    
    repo_dir = temp_dir / "repo"
    
    # Checkout specific commit if provided
    if commit:
        result = subprocess.run(
            ["git", "checkout", commit],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"Warning: Could not checkout commit {commit}: {result.stderr}")
    
    # Install test dependencies
    print("Installing test dependencies...")
    result = subprocess.run(
        ["pip3", "install", "pytest", "--quiet"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"Warning: Failed to install pytest: {result.stderr}")
    
    # Apply the diff
    if diff_content and diff_content.strip():
        diff_file = temp_dir / "agent.patch"
        with open(diff_file, "w") as f:
            f.write(diff_content)
        
        result = subprocess.run(
            ["git", "apply", str(diff_file)],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"Warning: Failed to apply diff cleanly: {result.stderr}")
            # Try with --3way
            result = subprocess.run(
                ["git", "apply", "--3way", str(diff_file)],
                cwd=repo_dir,
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                print(f"Error: Could not apply diff at all: {result.stderr}")
    
    return repo_dir


def run_test_script(repo_dir: Path, test_script: Dict) -> Tuple[bool, str]:
    """Run a single test script and return success status and output."""
    # Write test file
    test_path = repo_dir / test_script["path"]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(test_path, "w") as f:
        f.write(test_script["content"])
    
    # Run pytest on the specific test
    result = subprocess.run(
        ["python3", "-m", "pytest", str(test_path), "-v", "--tb=short"],
        cwd=repo_dir,
        capture_output=True,
        text=True
    )
    
    output = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
    success = result.returncode == 0
    
    return success, output


def generate_markdown_report(
    run_dir: Path, 
    task_meta: Dict,
    evaluation: Dict[str, Any],
    test_results: Dict[str, Tuple[bool, str]],
    diff_content: str,
    lm_evaluation: Optional[Dict[str, Any]] = None,
    agent_metrics: Optional[Dict[str, Any]] = None,
    score_delta: Optional[float] = None,
    baseline_before: Optional[float] = None,
    baseline_after: Optional[float] = None,
    relative_lift: Optional[float] = None,
) -> None:
    """Generate a markdown report of the evaluation results."""
    report_path = run_dir / "scoring_results.md"
    
    with open(report_path, "w") as f:
        # Header
        f.write("# 📊 Scoring Results\n\n")
        f.write(f"**Run ID:** `{run_dir.name}`  \n")
        f.write(f"**Task:** `{task_meta['task_id']}`  \n")
        f.write(f"**Task Title:** {task_meta['metadata']['title']}  \n")
        f.write(f"**Generated:** {Path.cwd().name} evaluation  \n\n")
        
        # Overall Score
        score = evaluation["total_score"]
        score_pct = score * 100
        
        # Score with visual indicator
        if score >= 1.0:
            score_emoji = "🏆"
        elif score >= 0.8:
            score_emoji = "✅"
        elif score >= 0.5:
            score_emoji = "⚠️"
        else:
            score_emoji = "❌"

        f.write(f"## {score_emoji} Overall Score: **{score_pct:.0f}%**\n\n")

        # Score bar visualization
        filled = int(score * 20)
        empty = 20 - filled
        bar = "█" * filled + "░" * empty
        f.write(f"```\n[{bar}] {score_pct:.1f}%\n```\n\n")
        
        # Scoring Table: Qualitative and Quantitative
        f.write("## 📊 Scoring Breakdown\n\n")
        f.write("### Qualitative Scoring (LLM-Based Rubrics)\n\n")
        f.write("| Rubric | Weight | Score | Status | Criterion |\n")
        f.write("|--------|--------|-------|--------|----------|\n")
        
        # Use LM evaluation if available, otherwise fall back to test-based evaluation
        if lm_evaluation is not None and lm_evaluation.get("rubric_scores"):
            for rs in lm_evaluation["rubric_scores"]:
                rid = rs.get("rubric_id", "?")
                rscore = rs.get("score", 0.0)
                rweight = next(
                    (r["weight"] for r in task_meta["evaluation"]["rubrics"] if r["id"] == rid),
                    0.0
                )
                rcriterion = next(
                    (r["criterion"] for r in task_meta["evaluation"]["rubrics"] if r["id"] == rid),
                    "Unknown"
                )
                
                if rscore >= 1.0:
                    status = "✅ PASS"
                elif rscore >= 0.5:
                    status = "⚠️ PARTIAL"
                else:
                    status = "❌ FAIL"
                
                f.write(f"| `{rid}` | {rweight:.0%} | **{rscore:.0%}** | {status} | {rcriterion} |\n")
        else:
            # Fallback to test-based rubrics
            for rubric_id, rubric_data in evaluation["rubrics"].items():
                score_val = rubric_data["score"]
                weight = rubric_data["weight"]
                criterion = rubric_data["criterion"]
                
                if score_val is not None:
                    if score_val >= 1.0:
                        status = "✅ PASS"
                    elif score_val >= 0.5:
                        status = "⚠️ PARTIAL"
                    else:
                        status = "❌ FAIL"
                    score_str = f"{score_val:.0%}"
                else:
                    status = "❓ N/A"
                    score_str = "N/A"
                
                f.write(f"| `{rubric_id}` | {weight:.0%} | **{score_str}** | {status} | {criterion} |\n")
        
        # Add baseline score delta row if available
        if baseline_before is not None and baseline_after is not None:
            delta_pct = score_delta * 100 if score_delta is not None else (baseline_after - baseline_before) * 100
            delta_sign = "+" if delta_pct >= 0 else ""
            lift_str = f" ({relative_lift:.2f}% lift)" if relative_lift is not None else ""
            
            if delta_pct > 0:
                status = "✅ IMPROVED"
            elif delta_pct < 0:
                status = "❌ REGRESSED"
            else:
                status = "⚖️ NO CHANGE"
            
            f.write(f"| `baseline_performance` | - | **{baseline_before:.0%} → {baseline_after:.0%}** ({delta_sign}{delta_pct:.2f}pp{lift_str}) | {status} | Baseline score before vs after patch |\n")
        
        # Quantitative Metrics
        f.write("\n### Quantitative Metrics\n\n")
        f.write("| Metric | Value |\n")
        f.write("|--------|-------|\n")
        
        time_seconds = agent_metrics.get("time_taken_seconds", 0.0) if agent_metrics else 0.0
        time_minutes = time_seconds / 60.0
        time_hours = time_seconds / 3600.0
        if time_hours >= 1:
            time_str = f"{time_hours:.2f} hours"
        elif time_minutes >= 1:
            time_str = f"{time_minutes:.2f} minutes"
        else:
            time_str = f"{time_seconds:.2f} seconds"
        
        tokens = agent_metrics.get("tokens_total", 0) if agent_metrics else 0
        money = agent_metrics.get("money_spent_usd", 0.0) if agent_metrics else 0.0
        tool_calls = agent_metrics.get("tool_calls_total", 0) if agent_metrics else 0
        
        f.write(f"| Time Taken | {time_str} |\n")
        f.write(f"| Tokens Spent | {tokens:,} |\n")
        f.write(f"| Money Spent | ${money:.4f} USD |\n")
        f.write(f"| Tool Calls | {tool_calls} |\n")
        
        if score_delta is not None:
            delta_pct = score_delta * 100
            delta_sign = "+" if score_delta >= 0 else ""
            f.write(f"| Score Delta (Before → After) | {delta_sign}{delta_pct:.2f} percentage points |\n")
        else:
            f.write(f"| Score Delta (Before → After) | N/A (re_bench_comparison.json not found) |\n")
        
        # LM Rubric Details (if available)
        if lm_evaluation is not None and lm_evaluation.get("rubric_scores"):
            f.write("\n## 🤖 LLM Rubric Details\n\n")
            for rs in lm_evaluation["rubric_scores"]:
                rid = rs.get("rubric_id", "?")
                rscore = rs.get("score", 0.0)
                reasoning = rs.get("reasoning", "")
                evidence = rs.get("evidence", "")
                
                f.write(f"### `{rid}` - Score: {rscore:.0%}\n\n")
                if reasoning:
                    f.write(f"**Reasoning:** {reasoning[:500]}{'...' if len(reasoning) > 500 else ''}\n\n")
                if evidence:
                    f.write(f"**Evidence:** {evidence[:500]}{'...' if len(evidence) > 500 else ''}\n\n")
        
        # Test Results (only if test scripts exist)
        test_scripts = task_meta.get("evaluation", {}).get("test_scripts", [])
        if test_scripts:
            f.write("\n## 🧪 Test Results\n\n")
            
            # Group tests by rubric
            tests_by_rubric = {}
            for test_script in test_scripts:
                rubric_id = test_script.get("rubric_id", "unknown")
                if rubric_id not in tests_by_rubric:
                    tests_by_rubric[rubric_id] = []
                tests_by_rubric[rubric_id].append(test_script)
            
            for rubric_id in evaluation["rubrics"].keys():
                if rubric_id in tests_by_rubric:
                    f.write(f"### Rubric: `{rubric_id}`\n\n")
                    
                    for test_script in tests_by_rubric[rubric_id]:
                        test_path = test_script["path"]
                        success, output = test_results.get(test_path, (False, ""))
                        
                        if success:
                            f.write(f"✅ **{test_path}** - PASSED\n\n")
                        else:
                            f.write(f"❌ **{test_path}** - FAILED\n\n")
                        
                        # Extract failure reason
                        lines = output.split('\n')
                        failure_lines = []
                        capture = False
                        
                        for line in lines:
                            if 'FAILED' in line or 'FAILURES' in line:
                                capture = True
                            if capture:
                                failure_lines.append(line)
                                if 'AssertionError' in line or 'assert' in line.lower():
                                    # Get a few more lines for context
                                    failure_lines.extend(lines[lines.index(line)+1:lines.index(line)+3])
                                    break
                        
                        if failure_lines:
                            f.write("```\n")
                            for line in failure_lines[:10]:  # Limit output
                                if line.strip():
                                    f.write(f"{line}\n")
                            f.write("```\n\n")
        
        # Test Functions
        f.write("## 📝 Test Definitions\n\n")
        f.write("<details>\n<summary>Click to expand test code</summary>\n\n")
        
        if test_scripts:
            for test_script in test_scripts:
                test_path = test_script["path"]
                rubric_id = test_script.get("rubric_id", "unknown")
                
                f.write(f"### {test_path} (rubric: `{rubric_id}`)\n\n")
                f.write("```python\n")
                f.write(test_script["content"])
                f.write("\n```\n\n")
        
        f.write("</details>\n\n")
        
        # Diff Preview
        f.write("## 🔧 Agent's Changes\n\n")
        
        if diff_content and diff_content.strip():
            f.write("<details>\n<summary>Click to see diff</summary>\n\n")
            f.write("```diff\n")
            f.write(diff_content)
            f.write("\n```\n\n")
            f.write("</details>\n\n")
            
            # Diff stats
            lines = diff_content.split('\n')
            additions = sum(1 for line in lines if line.startswith('+') and not line.startswith('+++'))
            deletions = sum(1 for line in lines if line.startswith('-') and not line.startswith('---'))
            f.write(f"**Changes:** +{additions} / -{deletions} lines\n\n")
        else:
            f.write("*No changes made*\n\n")
        
        # Task Instructions
        f.write("## 📄 Task Instructions\n\n")
        f.write("<details>\n<summary>Original task instructions</summary>\n\n")
        f.write(f"```\n{task_meta['lm']['instructions']}\n```\n\n")
        f.write("</details>\n\n")
        
        # Final unified summary table
        total_tests = len(test_results)
        tests_passed = sum(1 for _p, (_ok, _out) in test_results.items() if _ok)
        tests_failed = total_tests - tests_passed
        lm_score_val = None
        if lm_evaluation is not None and isinstance(lm_evaluation.get("weighted_score"), (int, float)):
            lm_score_val = float(lm_evaluation["weighted_score"]) * 100
        tokens_total = agent_metrics.get("tokens_total", 0) if agent_metrics else 0
        tool_calls_total = agent_metrics.get("tool_calls_total", 0) if agent_metrics else 0
        diff_lines_count = len(diff_content.splitlines()) if diff_content else 0

        f.write("\n## ✅ Final Summary\n\n")
        f.write("| Metric | Value |\n")
        f.write("|---|---:|\n")
        f.write(f"| Unit tests score | {score_pct:.0f}% |\n")
        f.write(f"| Unit tests (pass/fail) | {tests_passed}/{tests_failed} |\n")
        f.write(f"| LM rubric score | {lm_score_val:.0f}% |\n" if lm_score_val is not None else "| LM rubric score | N/A |\n")
        f.write(f"| Diff lines | {diff_lines_count} |\n")
        f.write(f"| Agent tokens (sum) | {tokens_total} |\n")
        f.write(f"| Agent tool calls (total) | {tool_calls_total} |\n")

        # Footer
        f.write("---\n")
        f.write("*Report generated by evaluation system*\n")
    
    print(f"Markdown report saved to: {report_path}")


def evaluate_rubrics(task_meta: Dict, test_results: Dict[str, Tuple[bool, str]]) -> Dict[str, Any]:
    """Evaluate rubrics based on test results."""
    rubrics = task_meta["evaluation"]["rubrics"]
    rubric_scores = {}
    
    for rubric in rubrics:
        rubric_id = rubric["id"]
        criterion = rubric["criterion"]
        weight = rubric["weight"]
        
        # Find tests for this rubric
        rubric_tests = [
            test for test in task_meta["evaluation"]["test_scripts"]
            if test.get("rubric_id") == rubric_id
        ]
        
        if rubric_tests:
            # Score based on test results
            passed = sum(1 for test in rubric_tests if test_results.get(test["path"], (False, ""))[0])
            total = len(rubric_tests)
            score = passed / total if total > 0 else 0
        else:
            # No tests for this rubric - manual review needed
            score = None
        
        rubric_scores[rubric_id] = {
            "criterion": criterion,
            "weight": weight,
            "score": score,
            "max_score": 1.0,
            "weighted_score": score * weight if score is not None else None,
            "test_count": len(rubric_tests),
            "tests_passed": sum(1 for test in rubric_tests if test_results.get(test["path"], (False, ""))[0])
        }
    
    # Calculate total score
    total_weight = sum(r["weight"] for r in rubrics)
    earned_weight = sum(
        r["weighted_score"] for r in rubric_scores.values() 
        if r["weighted_score"] is not None
    )
    
    return {
        "rubrics": rubric_scores,
        "total_score": earned_weight / total_weight if total_weight > 0 else 0,
        "max_score": 1.0
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: python evaluate_run.py <run_dir> <task_dir>")
        sys.exit(1)
    
    run_dir = Path(sys.argv[1])
    task_dir = Path(sys.argv[2])
    
    if not run_dir.exists():
        print(f"Error: Run directory {run_dir} does not exist")
        sys.exit(1)
    
    if not task_dir.exists():
        print(f"Error: Task directory {task_dir} does not exist")
        sys.exit(1)
    
    print(f"Evaluating run: {run_dir.name}")
    print(f"Against task: {task_dir.name}")
    print("=" * 60)
    
    # Load task metadata
    task_meta = load_task_metadata(task_dir)
    
    # Load run artifacts
    artifacts = load_run_artifacts(run_dir)
    
    if "diff" not in artifacts:
        print("Warning: No diff.patch found in artifacts")
        diff_content = ""
    else:
        diff_content = artifacts["diff"]
        print(f"Found diff with {len(diff_content.splitlines())} lines")
    
    # Compute agent metrics early
    agent_metrics = compute_agent_metrics(run_dir, artifacts)

    # Check if evaluation was already done in container
    container_eval_path = run_dir / "artifacts" / "tb_evaluation_results.json"
    if container_eval_path.exists():
        print("\n✅ Found container evaluation results - using those instead of re-running tests")
        print("=" * 60)
        
        with open(container_eval_path) as f:
            container_eval = json.load(f)
        
        # Use container's evaluation results
        evaluation = container_eval.get("evaluation", {})
        test_results = {
            path: (data["success"], data.get("output", ""))
            for path, data in container_eval.get("test_results", {}).items()
        }
        
        # Display results from container
        print("Tests run in container:")
        print("-" * 40)
        for path, (success, _) in test_results.items():
            if success:
                print(f"  ✅ PASSED: {path}")
            else:
                print(f"  ❌ FAILED: {path}")
        
        print("\nRubric scores from container:")
        print("-" * 40)
        
        for rubric_id, rubric_data in evaluation.get("rubrics", {}).items():
            score = rubric_data.get("score", 0)
            weight = rubric_data.get("weight", 0)
            # Get criterion from task metadata if not in container results
            criterion = rubric_data.get("criterion", "")
            if not criterion:
                for r in task_meta.get("evaluation", {}).get("rubrics", []):
                    if r["id"] == rubric_id:
                        criterion = r["criterion"]
                        break
            tests_passed = rubric_data.get("tests_passed", 0)
            test_count = rubric_data.get("test_count", 0)
            
            if score >= 1.0:
                symbol = "✅"
            elif score >= 0.5:
                symbol = "⚠️"
            else:
                symbol = "❌"
            
            print(f"{symbol} {rubric_id}: {score:.0%} ({tests_passed}/{test_count} tests) [weight: {weight:.0%}]")
            print(f"   {criterion}")
        
        print("\n" + "=" * 60)
        print(f"FINAL SCORE: {evaluation.get('total_score', 0):.0%}")
        print("=" * 60)
        
        # Print baseline delta if available
        re_bench_comparison = run_dir / "re_bench_comparison.json"
        if re_bench_comparison.exists():
            try:
                with open(re_bench_comparison) as f:
                    comp_data = json.load(f)
                    baseline_score = comp_data.get("baseline", {}).get("mean_outcome_reward") or comp_data.get("comparison", {}).get("baseline_score")
                    patched_score = comp_data.get("patched", {}).get("mean_outcome_reward") or comp_data.get("comparison", {}).get("patched_score")
                    relative_lift = comp_data.get("comparison", {}).get("relative_lift_percent")
                    
                    if baseline_score is not None and patched_score is not None:
                        delta_pct = (patched_score - baseline_score) * 100
                        delta_sign = "+" if delta_pct >= 0 else ""
                        lift_str = f" ({relative_lift:.2f}% lift)" if relative_lift is not None else ""
                        
                        if delta_pct > 0:
                            status_emoji = "✅"
                        elif delta_pct < 0:
                            status_emoji = "❌"
                        else:
                            status_emoji = "⚖️"
                        
                        print(f"\n{status_emoji} Baseline Performance: {baseline_score:.0%} → {patched_score:.0%} ({delta_sign}{delta_pct:.2f}pp{lift_str})")
            except Exception:
                pass

        # Print agent metrics summary
        if agent_metrics:
            print("\nAgent Metrics:")
            print("- Token events parsed:", agent_metrics.get("tokens_events", 0))
            print("- Tokens (sum across events):", agent_metrics.get("tokens_total", 0))
            print("- Tool calls (codex-run.log):", agent_metrics.get("tool_calls_codex_log", 0))
            print("- Tool calls (codex-sessions):", agent_metrics.get("tool_calls_sessions", 0))
            print("- Tool calls (total):", agent_metrics.get("tool_calls_total", 0))
        
        # Enhance evaluation with missing criterion fields
        enhanced_evaluation = evaluation.copy()
        if "rubrics" in enhanced_evaluation:
            for rubric_id, rubric_data in enhanced_evaluation["rubrics"].items():
                if "criterion" not in rubric_data:
                    for r in task_meta.get("evaluation", {}).get("rubrics", []):
                        if r["id"] == rubric_id:
                            rubric_data["criterion"] = r["criterion"]
                            break
        
        # Prepare results payload
        results_path = run_dir / "evaluation_results.json"
        results_payload: Dict[str, Any] = {
            "task_id": task_meta["task_id"],
            "run_id": run_dir.name,
            "evaluation": enhanced_evaluation,
            "test_results": {
                path: {"success": success, "output": output}
                for path, (success, output) in test_results.items()
            },
            "diff_lines": len(diff_content.splitlines()) if diff_content else 0,
            "agent_metrics": agent_metrics,
        }

        # Optionally run LM-based rubric scoring using structured outputs
        lm_evaluation_dict: Optional[Dict[str, Any]] = None
        if LM_SCORER_AVAILABLE:
            try:
                print("\nRunning LM-based rubric scorer (gpt-5-nano, structured outputs)...")
                # Create a temp repo with diff applied for LM evaluation only
                repo_dir: Optional[Path] = None
                try:
                    repo_dir = setup_test_environment(task_meta, diff_content)
                    # Collect artifacts for LM
                    lm_artifacts = collect_repo_artifacts_for_lm(repo_dir)
                    lm_artifacts["test_results"] = {
                        path: {"success": success}
                        for path, (success, _output) in test_results.items()
                    }

                    async def _run_lm_eval():
                        scorer = LMRubricScorerStructured(model="gpt-5-nano", temperature=0.1)
                        return await scorer.evaluate_task(task_meta, lm_artifacts)

                    lm_result = asyncio.run(_run_lm_eval())
                    lm_evaluation_dict = {
                        "weighted_score": lm_result.weighted_score,
                        "rubric_scores": [
                            {
                                "rubric_id": s.rubric_id,
                                "score": s.score,
                                "reasoning": s.reasoning,
                                "evidence": s.evidence,
                                "suggestions": getattr(s, "suggestions", None),
                            }
                            for s in lm_result.rubric_scores
                        ],
                        "summary": lm_result.summary,
                        "metadata": lm_result.metadata,
                    }
                finally:
                    if repo_dir is not None:
                        temp_dir = repo_dir.parent
                        if temp_dir.exists() and "tb_eval_" in str(temp_dir):
                            shutil.rmtree(temp_dir)
            except Exception as lm_err:
                print(f"Warning: LM rubric scoring failed: {lm_err}")

        if lm_evaluation_dict is not None:
            results_payload["lm_evaluation"] = lm_evaluation_dict

        # Save the results in our format
        with open(results_path, "w") as f:
            json.dump(results_payload, f, indent=2)
        
        print(f"\nDetailed results saved to: {results_path}")
        
        # Get score delta from re_bench comparison if available
        score_delta = None
        baseline_before = None
        baseline_after = None
        relative_lift = None
        re_bench_comparison = run_dir / "re_bench_comparison.json"
        if re_bench_comparison.exists():
            try:
                with open(re_bench_comparison) as f:
                    comp_data = json.load(f)
                    baseline_score = comp_data.get("baseline", {}).get("mean_outcome_reward") or comp_data.get("comparison", {}).get("baseline_score")
                    patched_score = comp_data.get("patched", {}).get("mean_outcome_reward") or comp_data.get("comparison", {}).get("patched_score")
                    score_delta = patched_score - baseline_score if (baseline_score is not None and patched_score is not None) else None
                    baseline_before = baseline_score
                    baseline_after = patched_score
                    relative_lift = comp_data.get("comparison", {}).get("relative_lift_percent")
            except Exception:
                pass
        
        # Generate markdown report (includes LM results if present)
        generate_markdown_report(
            run_dir, 
            task_meta, 
            evaluation, 
            test_results,
            diff_content,
            lm_evaluation=lm_evaluation_dict,
            agent_metrics=agent_metrics,
            score_delta=score_delta,
            baseline_before=baseline_before,
            baseline_after=baseline_after,
            relative_lift=relative_lift,
        )
        
        print(f"Markdown report saved to: {run_dir / 'scoring_results.md'}")
        return
    
    # If no container evaluation, check if we should run tests or use LLM-only evaluation
    test_scripts = task_meta.get("evaluation", {}).get("test_scripts", [])
    
    if not test_scripts:
        print("\nNo deterministic test scripts found - using LLM-based evaluation only")
        print("=" * 60)
        
        # Set up test environment for LLM evaluation
        repo_dir = None
        try:
            repo_dir = setup_test_environment(task_meta, diff_content)
            print(f"Test environment ready at {repo_dir}")
            
            # Run LLM-based rubric scoring (required)
            print("\nRunning LLM-based rubric scorer (required)...")
            if not LM_SCORER_AVAILABLE:
                print("ERROR: LLM scorer not available but required for this task!")
                if LM_SCORER_ERROR:
                    print(f"Import error: {LM_SCORER_ERROR}")
                print("Install the LM scorer module to evaluate this task.")
                print("Required: pip install openai python-dotenv")
                sys.exit(1)
            
            # Check for API key
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                print("ERROR: OPENAI_API_KEY not found in environment!")
                print("Please set OPENAI_API_KEY environment variable or add it to a .env file.")
                print(f"Checked .env files in: {', '.join(str(p) for p in env_candidates if p.exists())}")
                sys.exit(1)
            
            lm_artifacts = collect_repo_artifacts_for_lm(repo_dir)
            
            async def _run_lm_eval():
                scorer = LMRubricScorerStructured(model="gpt-5-nano", temperature=0.1)
                return await scorer.evaluate_task(task_meta, lm_artifacts)
            
            lm_result = asyncio.run(_run_lm_eval())
            lm_evaluation_dict = {
                "weighted_score": lm_result.weighted_score,
                "rubric_scores": [
                    {
                        "rubric_id": s.rubric_id,
                        "score": s.score,
                        "reasoning": s.reasoning,
                        "evidence": s.evidence,
                        "suggestions": getattr(s, "suggestions", None),
                    }
                    for s in lm_result.rubric_scores
                ],
                "summary": lm_result.summary,
                "metadata": lm_result.metadata,
            }
            
            # Create evaluation structure from LLM scores
            evaluation = {
                "rubrics": {
                    rs["rubric_id"]: {
                        "criterion": next(
                            (r["criterion"] for r in task_meta["evaluation"]["rubrics"] 
                             if r["id"] == rs["rubric_id"]), 
                            "Unknown"
                        ),
                        "weight": next(
                            (r["weight"] for r in task_meta["evaluation"]["rubrics"] 
                             if r["id"] == rs["rubric_id"]), 
                            0.0
                        ),
                        "score": rs["score"],
                        "max_score": 1.0,
                        "weighted_score": rs["score"] * next(
                            (r["weight"] for r in task_meta["evaluation"]["rubrics"] 
                             if r["id"] == rs["rubric_id"]), 
                            0.0
                        ),
                    }
                    for rs in lm_evaluation_dict["rubric_scores"]
                },
                "total_score": lm_result.weighted_score,
                "max_score": 1.0,
            }
            
            test_results = {}
            
        finally:
            if repo_dir is not None:
                temp_dir = repo_dir.parent
                if temp_dir.exists() and "tb_eval_" in str(temp_dir):
                    shutil.rmtree(temp_dir)
        
        # Get score delta from re_bench comparison if available (before LLM eval)
        score_delta = None
        baseline_before = None
        baseline_after = None
        relative_lift = None
        re_bench_comparison = run_dir / "re_bench_comparison.json"
        if re_bench_comparison.exists():
            try:
                with open(re_bench_comparison) as f:
                    comp_data = json.load(f)
                    baseline_score = comp_data.get("baseline", {}).get("mean_outcome_reward") or comp_data.get("comparison", {}).get("baseline_score")
                    patched_score = comp_data.get("patched", {}).get("mean_outcome_reward") or comp_data.get("comparison", {}).get("patched_score")
                    score_delta = patched_score - baseline_score if (baseline_score is not None and patched_score is not None) else None
                    baseline_before = baseline_score
                    baseline_after = patched_score
                    relative_lift = comp_data.get("comparison", {}).get("relative_lift_percent")
            except Exception:
                pass
        
        # Save results
        results_path = run_dir / "evaluation_results.json"
        results_payload: Dict[str, Any] = {
            "task_id": task_meta["task_id"],
            "run_id": run_dir.name,
            "evaluation": evaluation,
            "test_results": {},
            "diff_lines": len(diff_content.splitlines()) if diff_content else 0,
            "agent_metrics": agent_metrics,
            "lm_evaluation": lm_evaluation_dict,
            "quantitative_metrics": {
                "time_taken_seconds": agent_metrics.get("time_taken_seconds", 0.0),
                "tokens_spent": agent_metrics.get("tokens_total", 0),
                "money_spent_usd": agent_metrics.get("money_spent_usd", 0.0),
                "tool_calls": agent_metrics.get("tool_calls_total", 0),
                "score_delta": score_delta,
            },
        }
        
        with open(results_path, "w") as f:
            json.dump(results_payload, f, indent=2)
        
        print("\n" + "=" * 60)
        print(f"FINAL SCORE (LLM-based): {evaluation['total_score']:.1%}")
        print("=" * 60)
        
        # Print baseline delta if available
        if baseline_before is not None and baseline_after is not None:
            delta_pct = score_delta * 100 if score_delta is not None else (baseline_after - baseline_before) * 100
            delta_sign = "+" if delta_pct >= 0 else ""
            lift_str = f" ({relative_lift:.2f}% lift)" if relative_lift is not None else ""
            
            if delta_pct > 0:
                status_emoji = "✅"
            elif delta_pct < 0:
                status_emoji = "❌"
            else:
                status_emoji = "⚖️"
            
            print(f"\n{status_emoji} Baseline Performance: {baseline_before:.0%} → {baseline_after:.0%} ({delta_sign}{delta_pct:.2f}pp{lift_str})")
        
        # Generate markdown report
        generate_markdown_report(
            run_dir,
            task_meta,
            evaluation,
            test_results,
            diff_content,
            lm_evaluation=lm_evaluation_dict,
            agent_metrics=agent_metrics,
            score_delta=score_delta,
            baseline_before=baseline_before,
            baseline_after=baseline_after,
            relative_lift=relative_lift,
        )
        
        print(f"\nDetailed results saved to: {results_path}")
        print(f"Markdown report saved to: {run_dir / 'scoring_results.md'}")
        return
    
    # If no container evaluation, run tests locally
    print("\nNo container evaluation found - running tests locally...")
    
    # Set up test environment
    try:
        repo_dir = setup_test_environment(task_meta, diff_content)
        print(f"Test environment ready at {repo_dir}")
        
        # Run all test scripts
        test_results = {}
        print("\nRunning tests:")
        print("-" * 40)
        
        for test_script in task_meta["evaluation"]["test_scripts"]:
            test_path = test_script["path"]
            rubric_id = test_script.get("rubric_id", "unknown")
            
            print(f"Running {test_path} (rubric: {rubric_id})...")
            success, output = run_test_script(repo_dir, test_script)
            test_results[test_path] = (success, output)
            
            if success:
                print("  ✅ PASSED")
            else:
                print("  ❌ FAILED")
                # Print first few lines of failure
                error_lines = [line for line in output.split('\n') if 'FAILED' in line or 'AssertionError' in line]
                if error_lines:
                    print(f"     {error_lines[0][:100]}")
        
        # Evaluate rubrics
        print("\nEvaluating rubrics:")
        print("-" * 40)
        
        evaluation = evaluate_rubrics(task_meta, test_results)
        
        for rubric_id, rubric_result in evaluation["rubrics"].items():
            score = rubric_result["score"]
            weight = rubric_result["weight"]
            criterion = rubric_result["criterion"]
            tests_passed = rubric_result["tests_passed"]
            test_count = rubric_result["test_count"]
            
            if score is not None:
                score_str = f"{score:.1%} ({tests_passed}/{test_count} tests)"
                symbol = "✅" if score >= 1.0 else "⚠️" if score >= 0.5 else "❌"
            else:
                score_str = "N/A (no tests)"
                symbol = "❓"
            
            print(f"{symbol} {rubric_id}: {score_str} (weight: {weight:.0%})")
            print(f"   {criterion}")
        
        print("\n" + "=" * 60)
        print(f"FINAL SCORE: {evaluation['total_score']:.1%}")
        print("=" * 60)
        
        # Print baseline delta if available
        re_bench_comparison = run_dir / "re_bench_comparison.json"
        if re_bench_comparison.exists():
            try:
                with open(re_bench_comparison) as f:
                    comp_data = json.load(f)
                    baseline_score = comp_data.get("baseline", {}).get("mean_outcome_reward") or comp_data.get("comparison", {}).get("baseline_score")
                    patched_score = comp_data.get("patched", {}).get("mean_outcome_reward") or comp_data.get("comparison", {}).get("patched_score")
                    relative_lift = comp_data.get("comparison", {}).get("relative_lift_percent")
                    
                    if baseline_score is not None and patched_score is not None:
                        delta_pct = (patched_score - baseline_score) * 100
                        delta_sign = "+" if delta_pct >= 0 else ""
                        lift_str = f" ({relative_lift:.2f}% lift)" if relative_lift is not None else ""
                        
                        if delta_pct > 0:
                            status_emoji = "✅"
                        elif delta_pct < 0:
                            status_emoji = "❌"
                        else:
                            status_emoji = "⚖️"
                        
                        print(f"\n{status_emoji} Baseline Performance: {baseline_score:.0%} → {patched_score:.0%} ({delta_sign}{delta_pct:.2f}pp{lift_str})")
            except Exception:
                pass

        # Print agent metrics summary
        if agent_metrics:
            print("\nAgent Metrics:")
            print("- Token events parsed:", agent_metrics.get("tokens_events", 0))
            print("- Tokens (sum across events):", agent_metrics.get("tokens_total", 0))
            print("- Tool calls (codex-run.log):", agent_metrics.get("tool_calls_codex_log", 0))
            print("- Tool calls (codex-sessions):", agent_metrics.get("tool_calls_sessions", 0))
            print("- Tool calls (total):", agent_metrics.get("tool_calls_total", 0))
        
        # Save detailed results
        results_path = run_dir / "evaluation_results.json"
        results_payload: Dict[str, Any] = {
            "task_id": task_meta["task_id"],
            "run_id": run_dir.name,
            "evaluation": evaluation,
            "test_results": {
                path: {"success": success, "output": output}
                for path, (success, output) in test_results.items()
            },
            "diff_lines": len(diff_content.splitlines()) if diff_content else 0,
            "agent_metrics": agent_metrics,
        }

        # Optionally run LM-based rubric scoring (gpt-5-nano structured outputs)
        lm_evaluation_dict: Optional[Dict[str, Any]] = None
        if LM_SCORER_AVAILABLE:
            try:
                print("\nRunning LM-based rubric scorer (gpt-5-nano, structured outputs)...")
                # Prepare artifacts for LM
                lm_artifacts = collect_repo_artifacts_for_lm(repo_dir)
                lm_artifacts["test_results"] = {
                    path: {"success": success}
                    for path, (success, _output) in test_results.items()
                }

                # Evaluate with structured scorer
                async def _run_lm_eval():
                    scorer = LMRubricScorerStructured(model="gpt-5-nano", temperature=0.1)
                    # The structured scorer expects the full task spec (same as task_meta JSON)
                    return await scorer.evaluate_task(task_meta, lm_artifacts)

                lm_result = asyncio.run(_run_lm_eval())

                # Convert to serializable dict
                lm_evaluation_dict = {
                    "weighted_score": lm_result.weighted_score,
                    "rubric_scores": [
                        {
                            "rubric_id": s.rubric_id,
                            "score": s.score,
                            "reasoning": s.reasoning,
                            "evidence": s.evidence,
                            "suggestions": getattr(s, "suggestions", None),
                        }
                        for s in lm_result.rubric_scores
                    ],
                    "summary": lm_result.summary,
                    "metadata": lm_result.metadata,
                }

                # Attach to results
                results_payload["lm_evaluation"] = lm_evaluation_dict
            except Exception as lm_err:
                print(f"Warning: LM rubric scoring failed: {lm_err}")

        with open(results_path, "w") as f:
            json.dump(results_payload, f, indent=2)
        
        print(f"\nDetailed results saved to: {results_path}")
        
        # Save test output
        test_output_path = run_dir / "test_output.txt"
        with open(test_output_path, "w") as f:
            for path, (success, output) in test_results.items():
                f.write(f"{'='*60}\n")
                f.write(f"TEST: {path}\n")
                f.write(f"STATUS: {'PASSED' if success else 'FAILED'}\n")
                f.write(f"{'='*60}\n")
                f.write(output)
                f.write("\n\n")
        
        print(f"Test output saved to: {test_output_path}")
        
        # Generate markdown report (includes LM evaluation if present)
        generate_markdown_report(
            run_dir, 
            task_meta, 
            evaluation, 
            test_results,
            diff_content,
            lm_evaluation=lm_evaluation_dict,
            agent_metrics=agent_metrics,
            score_delta=None
        )
        
    finally:
        # Clean up temp directory
        if 'repo_dir' in locals():
            temp_dir = repo_dir.parent
            if temp_dir.exists() and "tb_eval_" in str(temp_dir):
                shutil.rmtree(temp_dir)
                print("\nCleaned up temp directory")


if __name__ == "__main__":
    main()
