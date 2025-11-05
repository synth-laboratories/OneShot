from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Iterable, List, Dict, Any

from datasets import Dataset, DatasetDict, Features, Value, Sequence
from huggingface_hub import HfApi, create_repo
from one_shot.sensitivity import detect_repo_sensitivity, SensitivityLevel


def _read_records(dataset_dir: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for split_file in dataset_dir.glob("*.jsonl"):
        if not split_file.is_file():
            continue
        with split_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record.setdefault("sensitivity", "unknown")
                records.append(record)
    return records


def _enforce_sensitivity(records: Iterable[Dict[str, Any]], private: bool, token: Optional[str]) -> None:
    if private:
        return

    sensitive_ids: List[str] = []
    unknown_ids: List[str] = []

    for record in records:
        sensitivity = (record.get("sensitivity") or "unknown").lower()
        repo_url = (record.get("repo") or {}).get("git_url", "")

        detected = SensitivityLevel.UNKNOWN
        if repo_url:
            detected = detect_repo_sensitivity(repo_url, token)
            if detected == SensitivityLevel.SENSITIVE and sensitivity != "sensitive":
                sensitivity = "sensitive"

        if sensitivity == "sensitive":
            sensitive_ids.append(record.get("task_id") or record.get("task_instance_id", "<unknown-task>"))
        elif sensitivity == "unknown" and detected != SensitivityLevel.SAFE:
            unknown_ids.append(record.get("task_id") or record.get("task_instance_id", "<unknown-task>"))

    if sensitive_ids:
        raise ValueError(
            "Refusing to upload sensitive tasks to a public Hugging Face dataset: "
            + ", ".join(sorted(set(sensitive_ids)))
        )
    if unknown_ids:
        raise ValueError(
            "Unable to confirm sensitivity for tasks: "
            + ", ".join(sorted(set(unknown_ids)))
            + ". Mark these tasks explicitly as safe or publish privately."
        )


def load_jsonl_dataset(jsonl_path: Path) -> Dataset:
    records = []
    with open(jsonl_path) as f:
        for line in f:
            records.append(json.loads(line))
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
        "evaluation": Value("string"),
        "artifacts": Value("string"),
        "metadata": Value("string"),
        "created_at": Value("string"),
        "sensitivity": Value("string"),
    })
    for record in records:
        if "evaluation" in record:
            record["evaluation"] = json.dumps(record["evaluation"])
        if "artifacts" in record:
            record["artifacts"] = json.dumps(record["artifacts"])
        if "metadata" in record:
            record["metadata"] = json.dumps(record["metadata"])
        record.setdefault("sensitivity", "unknown")
    return Dataset.from_list(records, features=features)


def create_dataset_card(dataset_name: str, stats: dict) -> str:
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
- `sensitivity`: Sensitivity label (`safe`, `sensitive`, `unknown`)

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
    info_path = dataset_dir / "dataset_info.json"
    if info_path.exists():
        with open(info_path) as f:
            info = json.load(f)
    else:
        info = {"statistics": {"exported": 0}}

    gh_pat = os.environ.get("PRIVATE_GITHUB_PAT") or os.environ.get("GH_PAT")
    records = _read_records(dataset_dir)
    if records:
        _enforce_sensitivity(records, private, gh_pat)

    api = HfApi(token=token)
    try:
        create_repo(repo_id=repo_id, repo_type="dataset", private=private, token=token)
    except Exception as e:
        if "already exists" not in str(e):
            raise

    dataset_dict = {}
    for split_file in dataset_dir.glob("*.jsonl"):
        split_name = split_file.stem
        dataset_dict[split_name] = load_jsonl_dataset(split_file)
    if not dataset_dict:
        raise ValueError(f"No JSONL files found in {dataset_dir}")
    dataset = DatasetDict(dataset_dict)
    dataset.push_to_hub(repo_id, token=token, create_pr=create_pr)

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
    return f"https://huggingface.co/datasets/{repo_id}"
