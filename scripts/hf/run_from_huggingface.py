from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from datasets import load_dataset


@dataclass
class RunOptions:
    model: str
    max_parallel: int
    max_tasks: Optional[int]
    output: Optional[Path]


def run_single_task(example: Dict[str, Any], opts: RunOptions) -> Tuple[str, bool, str]:
    tid = example.get("task_instance_id") or example.get("task_id") or "unknown"

    # Clone repo at start commit
    repo = example["repo"]
    git_url = repo.get("git_url")
    branch = repo.get("branch") or "main"
    commit = repo.get("start_commit")

    workdir = Path("/tmp") / f"cc_{tid}"
    if workdir.exists():
        subprocess.run(["rm", "-rf", str(workdir)], check=False)
    workdir.mkdir(parents=True, exist_ok=True)

    repo_dir = workdir / "repo"
    cmd_clone = ["git", "clone", git_url, "-b", branch, str(repo_dir)]
    r = subprocess.run(cmd_clone, capture_output=True, text=True)
    if r.returncode != 0:
        return tid, False, f"clone failed: {r.stderr[:500]}"

    if commit:
        r = subprocess.run(["git", "checkout", commit], cwd=repo_dir, capture_output=True, text=True)
        if r.returncode != 0:
            return tid, False, f"checkout failed: {r.stderr[:500]}"

    # Apply diff if present
    diff_patch = (example.get("artifacts") or {}).get("diff_patch")
    if diff_patch and diff_patch.strip():
        patch_path = workdir / "agent.patch"
        patch_path.write_text(diff_patch)
        r = subprocess.run(["git", "apply", str(patch_path)], cwd=repo_dir, capture_output=True, text=True)
        if r.returncode != 0:
            # try 3-way
            r = subprocess.run(["git", "apply", "--3way", str(patch_path)], cwd=repo_dir, capture_output=True, text=True)
            if r.returncode != 0:
                return tid, False, f"patch failed: {r.stderr[:500]}"

    # Write tests from evaluation.test_scripts
    eval_obj = example.get("evaluation") or {}
    tests = eval_obj.get("test_scripts") or []
    for t in tests:
        test_path = repo_dir / t.get("path", "tests/test_cc.py")
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(t.get("content", ""))

    # Install pytest if needed
    subprocess.run([sys.executable, "-m", "pip", "install", "pytest", "-q"], capture_output=True)

    # Run tests
    r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=repo_dir, capture_output=True, text=True)
    success = r.returncode == 0
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    return tid, success, out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tasks from a HuggingFace dataset repo")
    parser.add_argument("repo_id", help="e.g. your-username/codex-coach-tasks")
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--model", default="gpt-4")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    ds = load_dataset(args.repo_id)
    train = ds["train"]

    examples: List[Dict[str, Any]] = []
    for ex in train:
        examples.append(ex)
        if args.max_tasks and len(examples) >= args.max_tasks:
            break

    opts = RunOptions(model=args.model, max_parallel=args.max_parallel, max_tasks=args.max_tasks, output=args.output)

    results: List[Tuple[str, bool, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=opts.max_parallel) as pool:
        futs = [pool.submit(run_single_task, ex, opts) for ex in examples]
        for fut in concurrent.futures.as_completed(futs):
            results.append(fut.result())

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    report_lines = [f"# Results\n\n", f"Passed {passed}/{total} tasks\n\n"]
    for tid, ok, out in results:
        report_lines.append(f"## {tid} - {'PASS' if ok else 'FAIL'}\n\n")
        report_lines.append("```\n")
        report_lines.append(out[:4000])
        report_lines.append("\n``" + "\n\n")

    if opts.output:
        opts.output.parent.mkdir(parents=True, exist_ok=True)
        opts.output.write_text("".join(report_lines))
        print(f"Wrote results to {opts.output}")
    else:
        print("".join(report_lines))

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Run Modal evaluation tasks in parallel using a HuggingFace dataset.
Downloads tasks from HF Hub and executes them via Modal.
"""

import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from datasets import load_dataset
import yaml


def reconstruct_task_files(record: Dict, temp_dir: Path) -> Path:
    """
    Reconstruct a prepared task directory from a HuggingFace dataset record.
    
    Returns the path to the reconstructed task directory.
    """
    task_dir = temp_dir / record["task_instance_id"]
    task_dir.mkdir(parents=True, exist_ok=True)
    
    # Create tb_meta.json
    tb_meta = {
        "id": record["task_id"],
        "title": record["title"],
        "tags": record["tags"],
        "created_at": record.get("created_at", datetime.now().isoformat()),
        "lm": {
            "instructions": record["instructions"]
        },
        "repo": json.loads(record["repo"]) if isinstance(record["repo"], str) else record["repo"],
    }
    
    # Add evaluation if present
    if record.get("evaluation"):
        eval_data = json.loads(record["evaluation"]) if isinstance(record["evaluation"], str) else record["evaluation"]
        tb_meta["evaluation"] = eval_data
    
    with open(task_dir / "tb_meta.json", "w") as f:
        json.dump(tb_meta, f, indent=2)
    
    # Create overlay_files directory
    overlay_dir = task_dir / "overlay_files"
    overlay_dir.mkdir(exist_ok=True)
    
    # Write LM_INSTRUCTIONS.md
    with open(overlay_dir / "LM_INSTRUCTIONS.md", "w") as f:
        f.write(record["instructions"])
    
    # Reconstruct artifacts if present
    if record.get("artifacts"):
        artifacts = json.loads(record["artifacts"]) if isinstance(record["artifacts"], str) else record["artifacts"]
        
        # Write diff patch
        if "diff_patch" in artifacts:
            with open(overlay_dir / "diff.patch", "w") as f:
                f.write(artifacts["diff_patch"])
        
        # Write notes
        if "notes" in artifacts:
            with open(overlay_dir / "notes.md", "w") as f:
                f.write(artifacts["notes"])
        
        # Write repo_info.json
        if "repo_info" in artifacts:
            repo_info = json.loads(artifacts["repo_info"]) if isinstance(artifacts["repo_info"], str) else artifacts["repo_info"]
            with open(overlay_dir / "repo_info.json", "w") as f:
                json.dump(repo_info, f, indent=2)
        
        # Write bootstrap script
        if "bootstrap_script" in artifacts:
            with open(overlay_dir / "box_bootstrap.sh", "w") as f:
                f.write(artifacts["bootstrap_script"])
            os.chmod(overlay_dir / "box_bootstrap.sh", 0o755)
    
    # Create a minimal .env file with API key
    api_key = os.environ.get("OPENAI_API_KEY", "")
    with open(task_dir / ".env", "w") as f:
        f.write(f"OPENAI_API_KEY={api_key}\n")
    
    return task_dir


def run_single_task_from_record(
    record: Dict,
    temp_dir: Path,
    model: str = "gpt-4o-mini",
    timeout_sec: int = 1800,
) -> Dict:
    """
    Run a single task from a HuggingFace dataset record.
    """
    start_time = time.time()
    
    try:
        # Reconstruct task files
        task_dir = reconstruct_task_files(record, temp_dir)
        
        print(f"🚀 Starting task: {record['task_id']}")
        
        # Run Modal
        cmd = [
            'modal', 'run', 'codex_modal_runner.py::main',
            '--task-dir', str(task_dir),
            '--timeout', str(timeout_sec),
            '--model', model,
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec + 60
        )
        
        # Parse output for scores
        output = result.stdout
        score = 0.0
        rubric_scores = {}
        test_results = {}
        
        if "Total Score:" in output:
            for line in output.split('\n'):
                if "Total Score:" in line:
                    try:
                        score = float(line.split(':')[1].strip().rstrip('%')) / 100
                    except:
                        pass
                elif "• " in line and "%" in line:
                    # Parse rubric scores
                    try:
                        if "weight:" in line:
                            parts = line.split('•')[1].split(':')
                            rubric_name = parts[0].strip()
                            score_part = parts[1].split('(')[0].strip().rstrip('%')
                            rubric_scores[rubric_name] = float(score_part) / 100
                    except:
                        pass
                elif "✅" in line or "❌" in line:
                    # Parse test results
                    try:
                        if "tests/" in line:
                            test_name = line.split("tests/")[1].split(":")[0].strip()
                            passed = "✅" in line
                            test_results[test_name] = passed
                    except:
                        pass
        
        duration = time.time() - start_time
        
        return {
            'task_id': record['task_id'],
            'task_instance_id': record['task_instance_id'],
            'status': 'success' if result.returncode == 0 else 'failed',
            'score': score,
            'rubric_scores': rubric_scores,
            'test_results': test_results,
            'duration': duration,
            'exit_code': result.returncode,
        }
        
    except subprocess.TimeoutExpired:
        return {
            'task_id': record['task_id'],
            'task_instance_id': record['task_instance_id'],
            'status': 'timeout',
            'score': 0,
            'duration': time.time() - start_time,
        }
    except Exception as e:
        return {
            'task_id': record['task_id'],
            'task_instance_id': record['task_instance_id'],
            'status': 'error',
            'score': 0,
            'duration': time.time() - start_time,
            'error': str(e),
        }


def run_parallel_from_dataset(
    dataset_name: str,
    split: str = "train",
    max_parallel: int = 5,
    max_tasks: Optional[int] = None,
    model: str = "gpt-4o-mini",
    timeout_sec: int = 1800,
    task_filter: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Run tasks in parallel from a HuggingFace dataset.
    """
    print(f"📥 Loading dataset: {dataset_name} (split: {split})")
    dataset = load_dataset(dataset_name, split=split)
    
    # Filter tasks if specified
    if task_filter:
        dataset = dataset.filter(lambda x: x["task_id"] in task_filter)
    
    # Limit tasks if specified
    if max_tasks:
        dataset = dataset.select(range(min(max_tasks, len(dataset))))
    
    print(f"📊 Running {len(dataset)} tasks with max {max_parallel} parallel workers")
    print("=" * 80)
    
    results = []
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            # Submit all tasks
            future_to_record = {
                executor.submit(
                    run_single_task_from_record,
                    record,
                    temp_path,
                    model,
                    timeout_sec
                ): record
                for record in dataset
            }
            
            # Process completed tasks
            completed = 0
            for future in as_completed(future_to_record):
                record = future_to_record[future]
                try:
                    result = future.result()
                    results.append(result)
                    completed += 1
                    
                    # Print progress
                    status_emoji = {
                        'success': '✅',
                        'failed': '❌',
                        'timeout': '⏱️',
                        'error': '🔥'
                    }.get(result['status'], '❓')
                    
                    score_str = f"{result['score']*100:.1f}%" if result['score'] > 0 else "N/A"
                    print(f"{status_emoji} [{completed}/{len(dataset)}] {result['task_id']}: {result['status']} (Score: {score_str}, Time: {result['duration']:.1f}s)")
                    
                except Exception as e:
                    print(f"❌ [{completed}/{len(dataset)}] {record['task_id']}: Exception - {e}")
                    results.append({
                        'task_id': record['task_id'],
                        'status': 'error',
                        'score': 0,
                        'duration': 0,
                        'error': str(e)
                    })
    
    return results


def generate_report(results: List[Dict]) -> str:
    """Generate a markdown report from results."""
    lines = []
    lines.append("\n## HuggingFace Dataset Evaluation Results\n")
    lines.append(f"**Run Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Total Tasks:** {len(results)}")
    
    # Calculate statistics
    successful = sum(1 for r in results if r['status'] == 'success')
    avg_score = sum(r['score'] for r in results) / len(results) if results else 0
    total_time = sum(r['duration'] for r in results)
    
    lines.append(f"**Success Rate:** {successful}/{len(results)} ({successful/len(results)*100:.1f}%)")
    lines.append(f"**Average Score:** {avg_score*100:.1f}%")
    lines.append(f"**Total Time:** {total_time:.1f}s")
    lines.append(f"**Average Time:** {total_time/len(results):.1f}s per task\n")
    
    # Create table
    lines.append("| Task ID | Status | Score | LLM Rubrics | Unit Tests | Time |")
    lines.append("|---------|--------|-------|-------------|------------|------|")
    
    for result in sorted(results, key=lambda x: x['task_id']):
        task_id = result['task_id'][:30]
        status = result['status']
        score = f"{result['score']*100:.0f}%" if result.get('score', 0) > 0 else "-"
        duration = f"{result['duration']:.1f}s"
        
        # Format rubrics
        rubric_str = "N/A"
        if result.get('rubric_scores'):
            parts = [f"{k}:{v*100:.0f}%" for k, v in result['rubric_scores'].items()]
            rubric_str = " / ".join(parts)
        
        # Format tests
        test_str = "N/A"
        if result.get('test_results'):
            passed = sum(1 for v in result['test_results'].values() if v)
            total = len(result['test_results'])
            test_str = f"{passed}/{total}"
        
        lines.append(f"| {task_id} | {status} | {score} | {rubric_str} | {test_str} | {duration} |")
    
    return "\n".join(lines)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Run tasks from HuggingFace dataset")
    parser.add_argument(
        "dataset",
        help="HuggingFace dataset name (e.g., 'username/dataset-name')"
    )
    parser.add_argument(
        "--split",
        default="train",
        help="Dataset split to use"
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=5,
        help="Maximum parallel Modal runs"
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        help="Maximum number of tasks to run"
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="Model to use for evaluation"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout per task in seconds"
    )
    parser.add_argument(
        "--task-filter",
        nargs="+",
        help="Only run specific task IDs"
    )
    parser.add_argument(
        "--output",
        help="Output file for results"
    )
    
    args = parser.parse_args()
    
    # Check for API key
    if not os.environ.get("OPENAI_API_KEY"):
        print("⚠️  Warning: OPENAI_API_KEY not set")
    
    # Run evaluation
    results = run_parallel_from_dataset(
        args.dataset,
        args.split,
        args.max_parallel,
        args.max_tasks,
        args.model,
        args.timeout,
        args.task_filter,
    )
    
    # Generate report
    report = generate_report(results)
    
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(report)
        print(f"\n📝 Results saved to: {output_path}")
        
        # Also save JSON
        json_path = output_path.with_suffix(".json")
        with open(json_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"📊 JSON results saved to: {json_path}")
    else:
        print(report)
    
    return 0


if __name__ == "__main__":
    exit(main())