### Using Hugging Face datasets (export and evaluate)

This guide shows how to export prepared tasks to a HF-style JSONL dataset, and how to run evaluations from a HF dataset in parallel via Modal.

## Export prepared tasks to JSONL

The exporter writes JSONL records from `data/tasks/prepared/*` to `data/datasets/codex_coach_tasks/train.jsonl` by default.

```bash
uv run one_shot_bench.hf.export --out data/datasets/codex_coach_tasks/train.jsonl --split train --validate
```

Notes:
- Split bucketing uses a deterministic hash of `task_id` to map into train/validation/test partitions
- Each record includes `instructions`, `repo` metadata, `evaluation` rubrics/tests, and trimmed artifacts (diff, notes, repo_info)

## Evaluate a Hugging Face dataset via Modal (parallel)

Use the runner to read a dataset and execute tasks in parallel on Modal.

Example usage from a Python REPL or script:

```python
from one_shot_bench.hf.runner import run_parallel_from_dataset, generate_report

results = run_parallel_from_dataset(
    dataset_name="json",  # or a published HF dataset repo
    split="train",
    max_parallel=4,
    max_tasks=20,
    model="gpt-4o-mini",
    timeout_sec=1800,
)

report = generate_report(results)
print(report)
```

Dataset name options:
- Local JSON: first upload your JSONL to HF hub, or pass a local path when using `datasets.load_dataset("json", data_files=...)` inside a custom harness
- Published HF dataset: pass the dataset repo name (e.g., `"owner/oneshot-tasks"`) to `run_parallel_from_dataset`

Artifacts and scoring:
- Modal artifacts for each run are stored in the `codex-artifacts` volume under `<run_id>/`
- The runner parses completion logs to extract rubric/test scores when available


