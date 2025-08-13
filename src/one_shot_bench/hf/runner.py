from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from datasets import load_dataset


def reconstruct_task_files(record: Dict, temp_dir: Path) -> Path:
    task_dir = temp_dir / record["task_instance_id"]
    task_dir.mkdir(parents=True, exist_ok=True)

    tb_meta = {
        "id": record["task_id"],
        "title": record.get("title", ""),
        "tags": record.get("tags", []),
        "created_at": record.get("created_at", datetime.now().isoformat()),
        "lm": {"instructions": record.get("instructions", "")},
        "repo": json.loads(record["repo"]) if isinstance(record.get("repo"), str) else record.get("repo", {}),
    }

    if record.get("evaluation"):
        eval_data = json.loads(record["evaluation"]) if isinstance(record["evaluation"], str) else record["evaluation"]
        tb_meta["evaluation"] = eval_data

    with open(task_dir / "tb_meta.json", "w") as f:
        json.dump(tb_meta, f, indent=2)

    overlay_dir = task_dir / "overlay_files"
    overlay_dir.mkdir(exist_ok=True)

    with open(overlay_dir / "LM_INSTRUCTIONS.md", "w") as f:
        f.write(record.get("instructions", ""))

    if record.get("artifacts"):
        artifacts = json.loads(record["artifacts"]) if isinstance(record["artifacts"], str) else record["artifacts"]
        if "diff_patch" in artifacts:
            with open(overlay_dir / "diff.patch", "w") as f:
                f.write(artifacts["diff_patch"])
        if "notes" in artifacts:
            with open(overlay_dir / "notes.md", "w") as f:
                f.write(artifacts["notes"])
        if "repo_info" in artifacts:
            repo_info = json.loads(artifacts["repo_info"]) if isinstance(artifacts["repo_info"], str) else artifacts["repo_info"]
            with open(overlay_dir / "repo_info.json", "w") as f:
                json.dump(repo_info, f, indent=2)
        if "bootstrap_script" in artifacts:
            with open(overlay_dir / "box_bootstrap.sh", "w") as f:
                f.write(artifacts["bootstrap_script"])
            os.chmod(overlay_dir / "box_bootstrap.sh", 0o755)

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
    start_time = time.time()
    try:
        task_dir = reconstruct_task_files(record, temp_dir)
        cmd = [
            'modal', 'run', 'codex_modal_runner.py::main',
            '--task-dir', str(task_dir),
            '--timeout', str(timeout_sec),
            '--model', model,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec + 60)
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
                    try:
                        if "weight:" in line:
                            parts = line.split('•')[1].split(':')
                            rubric_name = parts[0].strip()
                            score_part = parts[1].split('(')[0].strip().rstrip('%')
                            rubric_scores[rubric_name] = float(score_part) / 100
                    except:
                        pass
                elif "✅" in line or "❌" in line:
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
    dataset = load_dataset(dataset_name, split=split)
    if task_filter:
        dataset = dataset.filter(lambda x: x["task_id"] in task_filter)
    if max_tasks:
        dataset = dataset.select(range(min(max_tasks, len(dataset))))
    results = []
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            future_to_record = {
                executor.submit(
                    run_single_task_from_record,
                    record,
                    temp_path,
                    model,
                    timeout_sec,
                ): record
                for record in dataset
            }
            for future in as_completed(future_to_record):
                try:
                    results.append(future.result())
                except Exception as e:
                    record = future_to_record[future]
                    results.append({'task_id': record['task_id'], 'status': 'error', 'score': 0, 'duration': 0, 'error': str(e)})
    return results


def generate_report(results: List[Dict]) -> str:
    lines: List[str] = []
    lines.append("\n## HuggingFace Dataset Evaluation Results\n")
    lines.append(f"**Run Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Total Tasks:** {len(results)}")
    successful = sum(1 for r in results if r['status'] == 'success')
    avg_score = sum(r['score'] for r in results) / len(results) if results else 0
    total_time = sum(r['duration'] for r in results)
    lines.append(f"**Success Rate:** {successful}/{len(results)} ({successful/len(results)*100:.1f}%)")
    lines.append(f"**Average Score:** {avg_score*100:.1f}%")
    lines.append(f"**Total Time:** {total_time:.1f}s")
    lines.append(f"**Average Time:** {total_time/len(results):.1f}s per task\n")
    lines.append("| Task ID | Status | Score | LLM Rubrics | Unit Tests | Time |")
    lines.append("|---------|--------|-------|-------------|------------|------|")
    for result in sorted(results, key=lambda x: x['task_id']):
        task_id = result['task_id'][:30]
        status = result['status']
        score = f"{result['score']*100:.0f}%" if result.get('score', 0) > 0 else "-"
        duration = f"{result['duration']:.1f}s"
        rubric_str = "N/A"
        if result.get('rubric_scores'):
            parts = [f"{k}:{v*100:.0f}%" for k, v in result['rubric_scores'].items()]
            rubric_str = " / ".join(parts)
        test_str = "N/A"
        if result.get('test_results'):
            passed = sum(1 for v in result['test_results'].values() if v)
            total = len(result['test_results'])
            test_str = f"{passed}/{total}"
        lines.append(f"| {task_id} | {status} | {score} | {rubric_str} | {test_str} | {duration} |")
    return "\n".join(lines)


