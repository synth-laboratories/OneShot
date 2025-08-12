# one-shot-bench
Quick host setup for codex-synth:

```bash
./scripts/install_codex_synth.sh
# then restart your shell or ensure ~/.local/bin is in PATH
```

### Start MITM workers and trust the proxy (full walkthrough)

1) Start the proxy and trace cleaner (runs in background)

```bash
uv tool install mitmproxy
./scripts/start_synth_workers.sh
```

This launches:
- a mitmproxy on `localhost:18080` writing logs to `/tmp/codex_mitm.out`
- a trace cleaner runs locally and copies raw traces to a clean DB under `data/traces/v3/clean_synth_ai.db/traces.sqlite3`

#### Tracing data model

- Raw DB: `data/traces/v3/raw_synth_ai.db/traces.sqlite3`, table `traces` (one row per HTTP transaction; includes `meta_json.session_id` from `RUN_ID`).
- Clean DB: `data/traces/v3/clean_synth_ai.db/traces.sqlite3`, table `cleaned_sessions` (one row per session; `formatted_json` aggregates events chronologically). Updated every 5 seconds by the cleaner.

Inspect a few cleaned sessions:
```bash
sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 \
  'SELECT session_id, substr(formatted_json,1,160) FROM cleaned_sessions LIMIT 3;'
```

Give sessions an ID when running codex-synth:
```bash
export RUN_ID="host_$(date +%s)"
codex-synth
```

2) Install and trust the MITM CA certificate (one-time)

- The certificate is generated at `~/.mitmproxy/mitmproxy-ca-cert.pem`.
- On macOS: open Keychain Access → import that file → set Trust to “Always Trust”.
- Or visit `http://mitm.it` while the proxy is running and follow the OS instructions.

3) Verify the proxy is reachable

```bash
curl -x http://localhost:18080 https://api.openai.com/v1/models | cat
```

4) Route Codex traffic through the proxy (host sessions)

Either set environment variables for the current shell:

```bash
export HTTP_PROXY=http://127.0.0.1:18080
export HTTPS_PROXY=http://127.0.0.1:18080
export ALL_PROXY=http://127.0.0.1:18080
codex-synth
```

…or configure a system-level HTTPS proxy to `127.0.0.1:18080` before running `codex-synth`.

5) Container runs (no extra setup)

- When using `src/one_shot_bench/run_codex_box.sh`, the script auto-detects the proxy and injects `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` into the container. It also copies the mitm CA into the build context if available, so you don’t need to repeat steps 2–4 for container runs.
Scalably converting pair programming cli trajectories into challenging digital agent tasks

## Running Evals (Docker vs Modal)

Use either a local Docker sandbox (Codex-in-the-Box) or Modal cloud to run and evaluate tasks. Both flows expect a prepared task directory that contains a `tb_meta.json` and optional `overlay_files/`.

### Docker: Codex-in-the-Box

- Prereqs: Docker installed; Node/npm installed; `@openai/codex` available (run `./scripts/install_codex_synth.sh` once to install `codex` and a `codex-synth` wrapper).
- Prepare a created task for evaluation:
  - `python src/one_shot_bench/prepare_task_for_eval.py data/tasks/created/<task_id_timestamp>`
  - Output goes to `data/tasks/prepared/<task_id>` with `tb_meta.json`, `overlay_files/`, and a generated `Dockerfile`.
- Run the eval in Docker:
  - `src/one_shot_bench/run_codex_box.sh data/tasks/prepared/<task_id> [timeout_sec] [token_limit]`
  - Example: `src/one_shot_bench/run_codex_box.sh data/tasks/prepared/add-lm-tracing-readme 900 50000`
- Results & scoring:
  - Run artifacts live under `data/runs/<run_id>/` (logs, `artifacts/`, diffs, traces).
  - After the container finishes, host-side scoring runs via `src/one_shot_bench/evaluate_run.py` and writes:
    - `data/runs/<run_id>/evaluation_results.json` (JSON summary)
    - `data/runs/<run_id>/scoring_results.md` (human-readable report)
  - If container produced `artifacts/tb_evaluation_results.json`, that is used; otherwise tests run locally against the agent diff.

Notes:
- The Docker script auto-detects and injects proxy variables when the local MITM proxy is running, and copies the CA cert into the image if available.
- The script copies your local `codex` installation into the image to run the agent reproducibly.

### Modal: Cloud Sandbox

- Prereqs: Modal CLI installed and authenticated (`pip install modal`, then `modal setup`). Ensure your OpenAI key is available to Modal as a secret named `openai-api-keys`.
  - Create once: `modal secret create openai-api-keys OPENAI_API_KEY=<your_key>`
- One-time setup: upload your local `codex` CLI into a persistent Modal volume:
  - `modal run scripts/codex_modal_runner.py::setup_codex`
- Prepare a task (same as Docker):
  - `python src/one_shot_bench/prepare_task_for_eval.py data/tasks/created/<task_id_timestamp>`
- Run the eval on Modal:
  - `modal run scripts/codex_modal_runner.py --task-dir data/tasks/prepared/<task_id> --timeout 1800 --token-limit 100000 --model gpt-4o-mini`
  - The runner stores artifacts in the Modal volume `codex-artifacts/<run_id>/` and also attempts to download them to `./data/runs/<run_id>/` automatically.
- Fetch artifacts later (optional):
  - List runs: `python scripts/fetch_modal_artifacts.py list`
  - Fetch a run: `python scripts/fetch_modal_artifacts.py fetch <run_id> -o ./data/runs/<run_id>`
- Scoring:
  - If the task/overlay produced `tb_evaluation_results.json`, scores are included in `completion.json` and the fetched files.
  - You can also run host-side scoring after fetching artifacts: `python src/one_shot_bench/evaluate_run.py data/runs/<run_id> data/tasks/prepared/<task_id>` to generate `evaluation_results.json` and `scoring_results.md` locally.

Tips:
- Modal runs require the `openai-api-keys` secret to be present in your Modal account. Rotate keys via `modal secret update` if needed.
- The Modal flow does not rely on the local MITM proxy; use Docker if you need on-box traffic capture.

## MCP: repo_start_task / repo_end_task

Enable Codex MCP tools that save tasks into this repo under `data/tasks/created`:

```bash
./scripts/create_sb_tasks/setup_codex_mcp.sh
```

This writes `~/.codex/config.toml` to point Codex at `scripts/create_sb_tasks/mcp_citb_server.py`.
Verify inside Codex: ask “What tools do you have?” and ensure you see
`repo.start_task.v1`, `repo.end_task.v1`, `repo.check_readiness.v1`, `repo.autofix_readiness.v1`.

If tasks still save under `development/...`, remove stale aliases/functions and reinstall our wrapper and MCP config:

```bash
./scripts/install_codex_synth.sh
./scripts/create_sb_tasks/setup_codex_mcp.sh
exec $SHELL -l
type -a codex-synth  # should show ~/.local/bin/codex-synth
```

## Modal: Parallel Evaluations

Run multiple prepared tasks concurrently on Modal using the parallel runner.

Prereqs:
- Modal CLI set up (`pip install modal`, `modal setup`) and secret `openai-api-keys` created.
- One-time Codex volume uploaded as in the Modal section above.
- Tasks prepared under `data/tasks/prepared/<task_id>` (one dir per task).

1) Create a config file (example: `data/modal_parallel.yaml`)

```yaml
agents:
  model: gpt-4o-mini
  timeout_sec: 1800
  token_limit: 100000
  max_parallel: 4   # how many Modal runs at once

datasets:
  prepared_tasks:
    enabled: true
    path: data/tasks/prepared
    # tasks: ["task_a", "task_b"]  # optional; if omitted, auto-discovers subdirs

output:
  results_dir: data/runs/parallel
  save_artifacts: true
```

2) Launch parallel runs

From the repo root (recommended):

```bash
# Run from scripts/ so the internal Modal call can locate codex_modal_runner
cd scripts
python parallel_modal_runner.py \
  --config ../data/modal_parallel.yaml \
  --max-parallel 4 \
  --format markdown \
  --output ../data/runs/parallel_summary.md
```

Notes:
- Omit `--max-parallel` to use the YAML value. Tune to avoid rate limits.
- The runner auto-discovers tasks when `datasets.*.tasks` is not set.
- Detailed JSON results are saved under `data/runs/parallel/` with timestamps; a markdown or CSV summary is optionally written via `--output`.
- To fetch any missing artifacts later, use `python scripts/fetch_modal_artifacts.py list|fetch` as described above.

## Docker: Sequential Evaluations

Run multiple prepared tasks one-by-one locally using Codex-in-the-Box. This is useful to avoid resource contention or API rate limits when you don’t need parallelism.

Prereqs:
- Tasks prepared under `data/tasks/prepared/<task_id>` (each contains a `tb_meta.json`).
- Docker installed and running; follow the “Docker: Codex-in-the-Box” section above for one-time setup.

1) Run all prepared tasks sequentially

```bash
# From repo root
TIMEOUT=1800   # seconds
TOKENS=100000  # token budget

for task_dir in data/tasks/prepared/*; do
  [ -d "$task_dir" ] || continue
  if [ -f "$task_dir/tb_meta.json" ]; then
    echo "=== Running $(basename "$task_dir") ==="
    src/one_shot_bench/run_codex_box.sh "$task_dir" "$TIMEOUT" "$TOKENS"
  else
    echo "Skipping $task_dir (no tb_meta.json)"
  fi
done
```

2) Run a specific subset sequentially

```bash
# List task IDs (directory names under data/tasks/prepared)
TASKS=( add-lm-tracing-readme another-task-id )
for t in "${TASKS[@]}"; do
  src/one_shot_bench/run_codex_box.sh "data/tasks/prepared/$t" 900 50000
done
```

Outputs and scoring:
- Each run writes artifacts and logs under `data/runs/<run_id>/` and generates `evaluation_results.json` and `scoring_results.md` after the container completes.
- You can inspect a quick summary at `data/runs/<run_id>/summary.txt`.

Tips:
- Ensure an API key is available (see `run_codex_box.sh` usage). If a local MITM proxy is running, it’s auto-detected and injected.
- Sequential runs can still hit provider rate limits; tune `TIMEOUT`/`TOKENS` or add `sleep` between iterations if needed.
