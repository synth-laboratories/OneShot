### Running a prepared task from Hugging Face (Docker)

Use the helper script to fetch a prepared task from the dataset and run it locally with Docker.

#### 1) Fetch and run

```bash
cd /Users/joshuapurtell/Documents/GitHub/one-shot-bench
uv run python scripts/run_hf_task_docker.py \
  --repo-id JoshPurtell/one-shot-bench \
  --task-slug <slug> \
  --model gpt-5-mini
```

- If `data/tasks/prepared/<slug>` already exists, the script stages to `data/tasks/prepared/hf/<slug>` (or use `--force` to overwrite).
- The script then calls `scripts/run_codex_box.sh` to build and run the Docker image.

#### 2) Environment

- Requires `OPENAI_API_KEY` in your env for billing=api.
- Default model can be overridden with `--model` or `OPENAI_MODEL`.

#### 3) Results

- End-of-run summary prints the git diff, rubric scores, and unit test counts.
- Full artifacts are under `data/runs/<run_id>/`.

