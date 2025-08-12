# HuggingFace Integration for Codex Coach

This folder contains scripts to:

1. Export prepared tasks to a HuggingFace-compatible JSONL dataset
2. Upload the dataset to the HuggingFace Hub
3. Download and run tasks from the Hub dataset

## Export Local Tasks to HF Format

```bash
python development/codex_coach/hf/export_hf_dataset.py --validate
```

Creates:

```
development/codex_coach/datasets/codex_coach_tasks/train.jsonl
```

## Upload to HuggingFace Hub

```bash
python development/codex_coach/hf/upload_to_huggingface.py --repo-id your-username/codex-coach-tasks
```

Requires authentication (set `HUGGINGFACE_HUB_TOKEN` env var or be logged in via `huggingface-cli login`).

## Run Tasks from a HuggingFace Dataset

Run all tasks from a dataset repository:

```bash
python development/codex_coach/hf/run_from_huggingface.py your-username/codex-coach-tasks
```

Run with options:

```bash
python development/codex_coach/hf/run_from_huggingface.py your-username/codex-coach-tasks \
  --max-parallel 10 \
  --max-tasks 5 \
  --model gpt-4 \
  --output development/codex_coach/hf/results.md
```

Notes:
- The runner clones repos at the specified commit, applies the optional `diff_patch`, writes tests from the dataset, and executes them.
- Pytest is executed locally. You can adapt the runner to use Modal or your preferred executor.


