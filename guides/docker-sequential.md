### Running tasks sequentially with Docker (Codex-in-the-Box)

Use a local Docker sandbox to run one or many prepared tasks one-by-one.

## Prereqs
- Docker installed and running
- Node/npm installed; `@openai/codex` available (install via `./scripts/install_codex_synth.sh`)
- Tasks prepared under `data/tasks/prepared/<task_id>`

Prepare a created task for evaluation:
```bash
uv run one_shot.prepare_task_for_eval --task-dir data/tasks/created/<task_id_timestamp>
```

Run the eval in Docker:
```bash
scripts/run_codex_box.sh data/tasks/prepared/<task_id> [timeout_sec] [token_limit]
# Example
scripts/run_codex_box.sh data/tasks/prepared/add-lm-tracing-readme 900 50000
```

Results & scoring:
- Run artifacts live under `data/runs/<run_id>/` (logs, `artifacts/`, diffs, traces)
- After the container finishes, host-side scoring runs via `src/one_shot/evaluate_run.py` and writes:
  - `data/runs/<run_id>/evaluation_results.json`
  - `data/runs/<run_id>/scoring_results.md`
- If the container produced `artifacts/tb_evaluation_results.json`, that is used; otherwise tests run locally against the agent diff

Notes:
- The Docker script auto-detects and injects proxy variables when the local MITM proxy is running, and copies the CA cert into the image if available
- The script copies your local `codex` installation into the image to run the agent reproducibly

## Run many tasks sequentially

All prepared tasks:
```bash
# From repo root
TIMEOUT=1800
TOKENS=100000
for task_dir in data/tasks/prepared/*; do
  [ -d "$task_dir" ] || continue
  if [ -f "$task_dir/tb_meta.json" ]; then
    echo "=== Running $(basename "$task_dir") ==="
    scripts/run_codex_box.sh "$task_dir" "$TIMEOUT" "$TOKENS"
  else
    echo "Skipping $task_dir (no tb_meta.json)"
  fi
done
```

Specific subset:
```bash
TASKS=( add-lm-tracing-readme another-task-id )
for t in "${TASKS[@]}"; do
  scripts/run_codex_box.sh "data/tasks/prepared/$t" 900 50000
done
```

Outputs and tips:
- Each run writes to `data/runs/<run_id>/` and generates `evaluation_results.json` and `scoring_results.md`
- Quick summary at `data/runs/<run_id>/summary.txt` (if present)
- Ensure an API key is available; proxy is auto-detected if running
- Tune `TIMEOUT`/`TOKENS` or add sleeps to avoid rate limits when iterating


