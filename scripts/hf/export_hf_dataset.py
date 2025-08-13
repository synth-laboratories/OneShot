from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src on path
import os
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / 'src'
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from one_shot_bench.hf.export import export_dataset, DEFAULT_OUT


def main() -> int:
    parser = argparse.ArgumentParser(description="Export prepared tasks to HF JSONL dataset")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--split", type=str, default=None, choices=[None, "train", "validation", "test"], nargs="?")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    return export_dataset(args.out, args.split, args.validate)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Deprecated inline implementation retained below for reference; logic moved to one_shot_bench.hf.export.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any
import re
from datetime import datetime


def normalize_task_id(task_id: str) -> str:
    """
    Remove timestamp suffixes from task IDs to create stable identifiers.
    Example: 'add-lm-tracing-readme-20240812' -> 'add-lm-tracing-readme'
    """
    # Remove timestamp patterns like -20240812 or _20240812_143022
    task_id = re.sub(r'[-_]\d{8}(_\d{6})?$', '', task_id)
    return task_id


def load_task_data(task_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Load all relevant data from a prepared task directory.
    
    Returns None if the task is invalid or missing required files.
    """
    # Required files
    tb_meta_path = task_dir / "tb_meta.json"
    if not tb_meta_path.exists():
        print(f"⚠️  Skipping {task_dir.name}: missing tb_meta.json")
        return None
    
    # Load main metadata
    with open(tb_meta_path) as f:
        tb_meta = json.load(f)
    
    # Create record with core fields
    record = {
        "task_instance_id": task_dir.name,  # Keep original with timestamp
        "task_id": normalize_task_id(tb_meta.get("id", task_dir.name)),
        "title": tb_meta.get("title", ""),
        "tags": tb_meta.get("tags", []),
        "created_at": tb_meta.get("created_at", datetime.now().isoformat()),
    }
    
    # Get instructions (prefer overlay_files/LM_INSTRUCTIONS.md)
    instructions = ""
    lm_instructions_path = task_dir / "overlay_files" / "LM_INSTRUCTIONS.md"
    if lm_instructions_path.exists():
        instructions = lm_instructions_path.read_text(encoding="utf-8")
    elif "lm" in tb_meta and "instructions" in tb_meta["lm"]:
        instructions = tb_meta["lm"]["instructions"]
    record["instructions"] = instructions
    
    # Repository information
    repo_info = {}
    if "repo" in tb_meta:
        repo_info = tb_meta["repo"]
    elif "repository" in tb_meta:
        # Handle old format
        repo_info = {
            "git_url": tb_meta["repository"].get("url", ""),
            "branch": tb_meta["repository"].get("branch", "main"),
            "start_commit_sha": tb_meta["repository"].get("commit", ""),
        }
    record["repo"] = repo_info
    
    # Evaluation configuration
    evaluation = tb_meta.get("evaluation", {})
    
    # Load evaluation tests if directory exists
    eval_dir = task_dir / "evaluation"
    if eval_dir.exists():
        test_files = []
        for test_file in eval_dir.glob("tests/*.py"):
            test_files.append({
                "name": test_file.name,
                "content": test_file.read_text(encoding="utf-8")[:5000],  # Cap at 5KB
            })
        if test_files:
            evaluation["test_files"] = test_files
    
    record["evaluation"] = evaluation
    
    # Optional artifacts
    artifacts = {}
    
    # Diff patch
    diff_path = task_dir / "overlay_files" / "diff.patch"
    if diff_path.exists():
        diff_content = diff_path.read_text(encoding="utf-8")
        artifacts["diff_patch"] = diff_content[:10000]  # Cap at 10KB
    
    # Notes
    notes_path = task_dir / "overlay_files" / "notes.md"
    if notes_path.exists():
        artifacts["notes"] = notes_path.read_text(encoding="utf-8")[:5000]
    
    # Repo info JSON
    repo_info_path = task_dir / "overlay_files" / "repo_info.json"
    if repo_info_path.exists():
        with open(repo_info_path) as f:
            artifacts["repo_info"] = json.dumps(json.load(f))  # Store as string
    
    # Bootstrap script (for reference)
    bootstrap_path = task_dir / "overlay_files" / "box_bootstrap.sh"
    if bootstrap_path.exists():
        artifacts["bootstrap_script"] = bootstrap_path.read_text(encoding="utf-8")[:10000]
    
    # Only include artifacts if non-empty
    if artifacts:
        record["artifacts"] = artifacts
    
    # Add metadata
    record["metadata"] = {
        "source": "codex_coach",
        "version": "1.0.0",
        "prepared_path": str(task_dir.relative_to(Path.cwd())),
    }
    
    return record


def export_dataset(
    prepared_dir: Path,
    output_dir: Path,
    dataset_name: str = "codex_coach_tasks",
    split: str = "train",
    max_tasks: Optional[int] = None
) -> Dict[str, Any]:
    """
    Export all prepared tasks to HuggingFace dataset format.
    
    Returns statistics about the export.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{split}.jsonl"
    
    stats = {
        "total_tasks": 0,
        "exported": 0,
        "skipped": 0,
        "errors": [],
    }
    
    # Collect all task directories
    task_dirs = sorted([d for d in prepared_dir.iterdir() if d.is_dir()])
    
    if max_tasks:
        task_dirs = task_dirs[:max_tasks]
    
    records = []
    
    for task_dir in task_dirs:
        stats["total_tasks"] += 1
        
        try:
            record = load_task_data(task_dir)
            if record:
                records.append(record)
                stats["exported"] += 1
                print(f"✅ Exported: {task_dir.name}")
            else:
                stats["skipped"] += 1
        except Exception as e:
            stats["errors"].append(f"{task_dir.name}: {str(e)}")
            print(f"❌ Error processing {task_dir.name}: {e}")
    
    # Write JSONL file
    with open(output_file, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    print(f"\n📊 Export complete: {output_file}")
    print(f"   Exported: {stats['exported']} tasks")
    print(f"   Skipped: {stats['skipped']} tasks")
    if stats["errors"]:
        print(f"   Errors: {len(stats['errors'])} tasks")
    
    # Also create a dataset info file
    info_file = output_dir / "dataset_info.json"
    with open(info_file, "w") as f:
        json.dump({
            "dataset_name": dataset_name,
            "version": "1.0.0",
            "description": "Codex Coach benchmark tasks for code agent evaluation",
            "splits": {
                split: {
                    "num_examples": stats["exported"],
                    "num_bytes": output_file.stat().st_size,
                }
            },
            "features": {
                "task_instance_id": {"dtype": "string"},
                "task_id": {"dtype": "string"},
                "title": {"dtype": "string"},
                "tags": {"dtype": "list", "feature": {"dtype": "string"}},
                "instructions": {"dtype": "string"},
                "repo": {
                    "git_url": {"dtype": "string"},
                    "branch": {"dtype": "string"},
                    "start_commit_sha": {"dtype": "string"},
                },
                "evaluation": {
                    "rubrics": {"dtype": "list"},
                    "test_scripts": {"dtype": "list"},
                },
                "artifacts": {"dtype": "dict"},
                "metadata": {"dtype": "dict"},
            },
            "export_timestamp": datetime.now().isoformat(),
            "statistics": stats,
        }, f, indent=2)
    
    return stats


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Export prepared tasks to HuggingFace format")
    parser.add_argument(
        "--prepared-dir",
        type=Path,
        default=Path("data/tasks/prepared"),
        help="Path to prepared tasks directory"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("datasets/codex_coach_tasks"),
        help="Output directory for dataset files"
    )
    parser.add_argument(
        "--dataset-name",
        default="codex_coach_tasks",
        help="Name for the dataset"
    )
    parser.add_argument(
        "--split",
        default="train",
        choices=["train", "validation", "test"],
        help="Dataset split name"
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        help="Maximum number of tasks to export (for testing)"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the exported dataset"
    )
    
    args = parser.parse_args()
    
    # Export dataset
    stats = export_dataset(
        args.prepared_dir,
        args.output_dir,
        args.dataset_name,
        args.split,
        args.max_tasks
    )
    
    # Optionally validate
    if args.validate:
        print("\n🔍 Validating exported dataset...")
        output_file = args.output_dir / f"{args.split}.jsonl"
        
        with open(output_file) as f:
            for i, line in enumerate(f):
                try:
                    record = json.loads(line)
                    assert "task_id" in record
                    assert "instructions" in record
                    assert "repo" in record
                except Exception as e:
                    print(f"❌ Invalid record at line {i+1}: {e}")
                    return 1
        
        print(f"✅ All {i+1} records validated successfully!")
    
    return 0


if __name__ == "__main__":
    exit(main())