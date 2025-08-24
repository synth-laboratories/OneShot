# Hugging Face - Dataset Management

Push prepared tasks to Hugging Face datasets and run evaluations from published datasets.

## Export Tasks to Hugging Face Format

Convert prepared tasks into JSONL format for Hugging Face:

```bash
# Export all prepared tasks to JSONL
uv run one_shot.hf.export --out data/datasets/codex_coach_tasks/train.jsonl --split train --validate

# Export specific tasks only
uv run one_shot.hf.export --out custom_dataset.jsonl --tasks task1 task2 task3

# Export with validation split
uv run one_shot.hf.export --out data/datasets/codex_coach_tasks/validation.jsonl --split validation
```

**What gets exported:**
- Task instructions and metadata
- Repository configuration
- Evaluation rubrics and unit tests
- Trimmed artifacts (diffs, notes)
- Deterministic train/validation/test splits

## Upload Dataset to Hugging Face

```bash
# Upload to your Hugging Face account
uv run python scripts/hf/upload_to_huggingface.py \
  --dataset-path data/datasets/codex_coach_tasks/train.jsonl \
  --repo-id your-username/your-dataset-name \
  --yes
```

**Alternative method:**
```bash
# Upload a prepared task directly
uv run python scripts/upload_prepared_task_hf.py \
  data/tasks/prepared/your-task-slug \
  your-username/your-dataset-name \
  tasks/your-task-slug \
  --yes
```

## Download and Run from Hugging Face

### Method 1: Run Individual Tasks (Docker)

```bash
# Run a specific task from HF dataset
uv run python scripts/run_hf_task_docker.py \
  --repo-id your-username/your-dataset-name \
  --task-slug your-task-slug \
  --model gpt-4o-mini
```

### Method 2: Run Dataset in Parallel (Modal)

```python
from one_shot.hf.runner import run_parallel_from_dataset, generate_report

# Run multiple tasks from HF dataset
results = run_parallel_from_dataset(
    dataset_name="your-username/your-dataset-name",
    split="train",
    max_parallel=4,
    max_tasks=20,
    model="gpt-4o-mini",
    timeout_sec=1800,
    token_limit=100000
)

# Generate summary report
report = generate_report(results)
print(report)
```

## Dataset Structure

Each record in the JSONL dataset contains:

```json
{
  "task_id": "add-hello-world-section_20250101_120000",
  "instructions": "Add a Hello World section to the README...",
  "repo": {
    "git_url": "https://github.com/your-org/your-repo",
    "branch": "main",
    "start_commit_sha": "abc123",
    "end_commit_sha": "def456"
  },
  "evaluation": {
    "rubrics": [
      {
        "id": "task_completion",
        "criterion": "Task requirements completed",
        "weight": 0.4
      }
    ],
    "test_scripts": [...]
  },
  "artifacts": {
    "diff_patch": "...",
    "notes": "User notes...",
    "repo_info": {...}
  },
  "meta": {
    "prepared_version": "one_shot",
    "created_from": "data/tasks/prepared/add-hello-world-section"
  }
}
```

## Local Dataset Testing

Before uploading to Hugging Face, test with local JSONL files:

```python
from one_shot.hf.runner import run_parallel_from_dataset

# Test with local JSONL file
results = run_parallel_from_dataset(
    dataset_name="json",
    data_files="data/datasets/codex_coach_tasks/train.jsonl",
    split="train",
    max_parallel=2,
    max_tasks=5
)
```

## Dataset Management

### List Available Datasets

```bash
# List your datasets on Hugging Face
huggingface-cli repo list
```

### Update Existing Dataset

```bash
# Update an existing dataset with new tasks
uv run one_shot.hf.export --out updated_dataset.jsonl --split train
uv run python scripts/hf/upload_to_huggingface.py \
  --dataset-path updated_dataset.jsonl \
  --repo-id your-username/existing-dataset \
  --yes
```

### Download Dataset Locally

```python
from datasets import load_dataset

# Load from Hugging Face
dataset = load_dataset("your-username/your-dataset-name", split="train")

# Convert to local JSONL
import json
with open("local_copy.jsonl", "w") as f:
    for record in dataset:
        f.write(json.dumps(record) + "\n")
```

## Parallel Execution on Modal

### Configuration File

Create `data/modal_parallel.yaml`:

```yaml
agents:
  model: gpt-4o-mini
  timeout_sec: 1800
  token_limit: 100000
  max_parallel: 4

datasets:
  prepared_tasks:
    enabled: true
    path: data/tasks/prepared
    tasks: ["task_a", "task_b"]  # optional subset

  huggingface:
    enabled: true
    repo_id: "your-username/your-dataset-name"
    split: "train"
    max_tasks: 10

output:
  results_dir: data/runs/parallel
  save_artifacts: true
```

### Run Parallel Evaluation

```bash
# Run from config file
modal run scripts/codex_modal_runner.py::run_parallel_from_config \
  --config data/modal_parallel.yaml
```

### Monitor Parallel Runs

```bash
# Check running tasks
python scripts/fetch_modal_artifacts.py list

# Fetch results when complete
python scripts/fetch_modal_artifacts.py fetch <run_id> -o ./data/runs/
```

## Best Practices

### Dataset Organization

1. **Naming Convention**: Use descriptive, consistent naming
   - `your-project-cli-tasks`
   - `your-project-web-tasks`
   - `your-project-algorithm-tasks`

2. **Splitting Strategy**:
   - Use `--split train/validation/test` for proper evaluation
   - Keep test set separate and only evaluate on it once
   - Use deterministic hashing for consistent splits

3. **Versioning**:
   - Tag important versions: `v1.0`, `v2.0`
   - Include dataset statistics in description
   - Document any breaking changes

### Performance Optimization

1. **Task Selection**:
   - Start with smaller, focused datasets
   - Gradually increase complexity
   - Remove duplicate or very similar tasks

2. **Parallel Execution**:
   - Use appropriate `max_parallel` based on your Modal quota
   - Start with shorter timeouts for testing
   - Monitor costs on Modal dashboard

3. **Artifact Management**:
   - Enable artifact saving for debugging
   - Clean up old runs periodically
   - Archive important results

## Troubleshooting

### Upload Issues

**Dataset too large:**
- Reduce number of tasks per upload
- Split into multiple datasets
- Remove large artifacts from tasks

**Authentication error:**
```bash
# Ensure you're logged in to Hugging Face
huggingface-cli login
```

### Download Issues

**Dataset not found:**
- Check repository name and permissions
- Verify dataset exists: `huggingface-cli repo info your-username/dataset-name`

**Import errors:**
```python
# Install datasets library
pip install datasets
```

### Modal Execution Issues

**Timeout errors:**
- Increase `timeout_sec` in configuration
- Break complex tasks into smaller steps
- Check if tasks are hanging

**Resource limits:**
- Monitor Modal usage dashboard
- Reduce `max_parallel` if hitting limits
- Use smaller models for testing
