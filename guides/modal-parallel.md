### Running tasks in parallel with Modal

Use Modal to run multiple prepared tasks concurrently in a cloud sandbox.

## Prereqs
- Modal CLI installed and authenticated: `pip install modal` then `modal setup`
- Secret `openai-api-keys` created: `modal secret create openai-api-keys OPENAI_API_KEY=<your_key>`
- One-time: upload your local `codex` CLI into a persistent Modal volume

Upload codex once:
```bash
modal run scripts/codex_modal_runner.py::setup_codex
```

Prepare tasks (same as Docker):
```bash
uv run one_shot.prepare_task_for_eval --task-dir data/tasks/created/<task_id_timestamp>
```

Run a single task on Modal:
```bash
modal run scripts/codex_modal_runner.py --task-dir data/tasks/prepared/<task_id> --timeout 1800 --token-limit 100000 --model gpt-4o-mini
```

Artifacts are saved in Modal volume `codex-artifacts/<run_id>/` and also downloaded to `./data/runs/<run_id>/`.

Fetch artifacts later (optional):
```bash
python scripts/fetch_modal_artifacts.py list
python scripts/fetch_modal_artifacts.py fetch <run_id> -o ./data/runs/<run_id>
```

## Parallel runner

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
    # tasks: ["task_a", "task_b"]  # optional subset

output:
  results_dir: data/runs/parallel
  save_artifacts: true
```

Launch parallel runs:
```bash
cd scripts
python parallel_modal_runner.py \
  --config ../data/modal_parallel.yaml \
  --max-parallel 4 \
  --format markdown \
  --output ../data/runs/parallel_summary.md
```

Notes:
- Omit `--max-parallel` to use the YAML value. Tune to avoid rate limits
- The runner auto-discovers tasks when `datasets.*.tasks` is not set
- Detailed JSON results are saved under `data/runs/parallel/`; optional markdown/CSV summary via `--output`
- To fetch any missing artifacts later, use `python scripts/fetch_modal_artifacts.py list|fetch`


