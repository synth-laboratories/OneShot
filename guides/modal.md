### Using Modal

Run OneShot Bench tasks on Modal as a cloud alternative to Docker. This guide covers setup and single-task runs; see `guides/modal-parallel.md` for parallel execution.

## Prerequisites
- Modal CLI installed and authenticated:
  ```bash
  uv tool install modal  # or: pip install modal
  modal setup            # login and save token
  ```
- OpenAI credentials available to the run:
  - Easiest: export locally and let the runner pass them through
    ```bash
    export OPENAI_API_KEY=sk-...
    export OPENAI_MODEL=gpt-5-mini
    ```
  - For the advanced runner in `scripts/codex_modal_runner.py`, create a Modal secret once (optional if `.env` contains `OPENAI_API_KEY`, the setup step will try to create it for you):
    ```bash
    modal secret create openai-api-keys OPENAI_API_KEY=$OPENAI_API_KEY
    ```

## Option A — Quick single run (uses scripts/modal_runner.py)
This mirrors the Docker path but runs the task in a Modal sandbox.

```bash
# From repo root
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-5-mini

# Prepared task
SANDBOX_BACKEND=modal bash scripts/run_codex_box.sh data/tasks/prepared/<slug>

# Or a newly created raw task (auto-prepares it first)
SANDBOX_BACKEND=modal bash scripts/run_codex_box.sh data/tasks/created/<task_id_timestamp>
```

Artifacts are saved under `data/runs/<run_id>/`. This path uses `scripts/modal_runner.py` under the hood, packaging the task and running it inside a Modal function.

## Option B — Advanced runner + volumes (scripts/codex_modal_runner.py)
This approach stores a Codex install and run artifacts in persistent Modal volumes for faster subsequent runs and easier artifact retrieval.

1) One-time: upload your local Codex install to a Modal volume
```bash
# If you have .env with OPENAI_API_KEY, the step will also ensure the secret exists
modal run scripts/codex_modal_runner.py::setup_codex
```

2) Prepare a task (same command used for Docker)
```bash
uv run one_shot.prepare_task_for_eval --task-dir data/tasks/created/<task_id_timestamp>
```

3) Run a single prepared task on Modal
```bash
modal run scripts/codex_modal_runner.py \
  --task-dir data/tasks/prepared/<slug> \
  --timeout 1800 \
  --token-limit 100000 \
  --model ${OPENAI_MODEL:-gpt-4o-mini}
```

4) Fetch artifacts later (if you didn’t run locally or want to re-download)
```bash
python scripts/fetch_modal_artifacts.py list
python scripts/fetch_modal_artifacts.py fetch <run_id> -o ./data/runs/<run_id>
```

Notes
- Modal volumes used: `codex-installation` (Codex), `codex-artifacts` (run outputs).
- The single-task command auto-downloads artifacts to `./data/runs/<run_id>/` when possible.

## Parallel runs
See `guides/modal-parallel.md` to launch many tasks concurrently with configurable limits and summaries.

## Troubleshooting
- modal not found: install CLI via `uv tool install modal` or `pip install modal`, then `modal setup`.
- Not authenticated: `modal token ls` should show a token; run `modal setup` if empty.
- Missing OpenAI key: export `OPENAI_API_KEY` locally, or create Modal secret `openai-api-keys`.
- Codex not found in volume: re-run `modal run scripts/codex_modal_runner.py::setup_codex`.
- Where are my artifacts?: check Modal volume `codex-artifacts` and/or local `data/runs/<run_id>/`.

