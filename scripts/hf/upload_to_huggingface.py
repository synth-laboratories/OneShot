from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure src on path
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / 'src'
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from one_shot.hf.upload import upload_dataset as upload_dataset_mod


DEFAULT_DATA = Path("data/datasets/codex_coach_tasks/train.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload JSONL dataset to HuggingFace Hub")
    parser.add_argument("--repo-id", required=True, help="e.g. your-username/codex-coach-tasks")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--message", default="Add dataset")
    args = parser.parse_args()

    if not args.data.exists():
        print(f"Dataset file not found: {args.data}", file=sys.stderr)
        return 1

    token = os.getenv("HUGGINGFACE_HUB_TOKEN") or os.getenv("HF_TOKEN")
    try:
        upload_dataset_mod(args.data.parent, args.repo_id, token, private=False, create_pr=False)
    except Exception as e:
        print(f"Upload failed: {e}", file=sys.stderr)
        return 1
    print(f"Uploaded to https://huggingface.co/datasets/{args.repo_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Deprecated inline implementation kept for reference; logic moved to one_shot.hf.upload.
"""

import json
from pathlib import Path
from typing import Optional
from datasets import Dataset, DatasetDict, Features, Value, Sequence
from huggingface_hub import HfApi, create_repo


def load_jsonl_dataset(jsonl_path: Path) -> Dataset:
    """Load JSONL file as HuggingFace Dataset."""
    records = []
    with open(jsonl_path) as f:
        for line in f:
            records.append(json.loads(line))
    
    # Define features schema
    features = Features({
        "task_instance_id": Value("string"),
        "task_id": Value("string"),
        "title": Value("string"),
        "tags": Sequence(Value("string")),
        "instructions": Value("string"),
        "repo": {
            "git_url": Value("string"),
            "branch": Value("string"),
            "start_commit_sha": Value("string"),
        },
        "evaluation": Value("string"),  # Store as JSON string for flexibility
        "artifacts": Value("string"),    # Store as JSON string
        "metadata": Value("string"),     # Store as JSON string
        "created_at": Value("string"),
    })
    
    # Convert complex fields to JSON strings
    for record in records:
        if "evaluation" in record:
            record["evaluation"] = json.dumps(record["evaluation"])
        if "artifacts" in record:
            record["artifacts"] = json.dumps(record["artifacts"])
        if "metadata" in record:
            record["metadata"] = json.dumps(record["metadata"])
    
    return Dataset.from_list(records, features=features)


def create_dataset_card(dataset_name: str, stats: dict) -> str:
    """Generate README.md dataset card."""
    return f"""---
license: apache-2.0
task_categories:
- text-generation
- question-answering
language:
- en
tags:
- code
- agent
- evaluation
- benchmark
size_categories:
- n<1K
---

# {dataset_name}

## Dataset Description

This dataset contains prepared benchmark tasks for evaluating code generation agents.
Each task includes instructions, repository context, and evaluation criteria.

### Dataset Summary

- **Total tasks**: {stats.get('exported', 0)}
- **Task types**: Code modification, test generation, documentation
- **Evaluation methods**: LLM rubrics + unit tests
- **Source**: Codex Coach benchmark suite

## Dataset Structure

### Data Fields

- `task_instance_id`: Unique identifier for this task instance
- `task_id`: Stable task identifier (without timestamps)
- `title`: Human-readable task title
- `tags`: List of task categories/tags
- `instructions`: Task instructions for the agent
- `repo`: Repository information
  - `git_url`: Git repository URL
  - `branch`: Target branch
  - `start_commit_sha`: Starting commit
- `evaluation`: Evaluation configuration (JSON string)
- `artifacts`: Optional artifacts like diffs, notes (JSON string)
- `metadata`: Export metadata (JSON string)

### Data Splits

| Split | # Examples |
|-------|------------|
| train | {stats.get('exported', 0)} |

## Usage

```python
from datasets import load_dataset

# Load from HuggingFace Hub
dataset = load_dataset("{dataset_name}")

# Parse JSON fields
import json
for example in dataset["train"]:
    evaluation = json.loads(example["evaluation"])
    if example["artifacts"]:
        artifacts = json.loads(example["artifacts"])
```

## Evaluation

Tasks include two types of evaluation:
1. **LLM Rubrics**: Criteria evaluated by language models
2. **Unit Tests**: Python tests that validate outputs

## Citation

```bibtex
@misc{{codex_coach_2024,
  title={{Codex Coach: Benchmark Tasks for Code Agents}},
  year={{2024}},
  publisher={{HuggingFace}}
}}
```
"""


def upload_dataset(
    dataset_dir: Path,
    repo_id: str,
    token: Optional[str] = None,
    private: bool = False,
    create_pr: bool = False,
) -> str:
    """
    Upload dataset to HuggingFace Hub.
    
    Args:
        dataset_dir: Directory containing train.jsonl and dataset_info.json
        repo_id: HuggingFace repo ID (e.g., "username/dataset-name")
        token: HuggingFace API token
        private: Whether to create a private repository
        create_pr: Whether to create a pull request instead of direct push
    
    Returns:
        URL of the uploaded dataset
    """
    # Load dataset info
    info_path = dataset_dir / "dataset_info.json"
    if info_path.exists():
        with open(info_path) as f:
            info = json.load(f)
    else:
        info = {"statistics": {"exported": 0}}
    
    # Create repository if needed
    api = HfApi(token=token)
    try:
        create_repo(
            repo_id=repo_id,
            repo_type="dataset",
            private=private,
            token=token,
        )
        print(f"✅ Created repository: {repo_id}")
    except Exception as e:
        if "already exists" in str(e):
            print(f"ℹ️  Repository already exists: {repo_id}")
        else:
            raise
    
    # Load all splits
    dataset_dict = {}
    for split_file in dataset_dir.glob("*.jsonl"):
        split_name = split_file.stem
        print(f"Loading split: {split_name}")
        dataset_dict[split_name] = load_jsonl_dataset(split_file)
    
    if not dataset_dict:
        raise ValueError(f"No JSONL files found in {dataset_dir}")
    
    # Create DatasetDict
    dataset = DatasetDict(dataset_dict)
    
    # Push to hub
    print(f"📤 Uploading to {repo_id}...")
    dataset.push_to_hub(
        repo_id,
        token=token,
        create_pr=create_pr,
    )
    
    # Create and upload dataset card
    dataset_name = repo_id.split("/")[-1]
    readme_content = create_dataset_card(dataset_name, info.get("statistics", {}))
    
    api.upload_file(
        path_or_fileobj=readme_content.encode(),
        path_in_repo="README.md",
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
        create_pr=create_pr,
    )
    
    url = f"https://huggingface.co/datasets/{repo_id}"
    print(f"✅ Dataset uploaded: {url}")
    
    return url


def main():
    import argparse
    import os
    
    parser = argparse.ArgumentParser(description="Upload dataset to HuggingFace Hub")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("datasets/codex_coach_tasks"),
        help="Directory containing dataset files"
    )
    parser.add_argument(
        "--repo-id",
        required=True,
        help="HuggingFace repo ID (e.g., 'username/dataset-name')"
    )
    parser.add_argument(
        "--token",
        help="HuggingFace API token (or set HF_TOKEN env var)"
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Create private repository"
    )
    parser.add_argument(
        "--create-pr",
        action="store_true",
        help="Create pull request instead of direct push"
    )
    
    args = parser.parse_args()
    
    # Get token from args or environment
    token = args.token or os.environ.get("HF_TOKEN")
    if not token:
        print("⚠️  No token provided. You may need to login with: huggingface-cli login")
    
    # Upload dataset
    url = upload_dataset(
        args.dataset_dir,
        args.repo_id,
        token,
        args.private,
        args.create_pr,
    )
    
    print(f"\n🎉 Success! View your dataset at: {url}")
    
    return 0


if __name__ == "__main__":
    exit(main())