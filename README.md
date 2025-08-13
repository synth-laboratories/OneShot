# one-shot-bench

Scalably converting pair-programming CLI trajectories into challenging digital agent tasks.

### Quick start

1) Install codex-synth wrapper
```bash
bash scripts/install_codex_synth.sh
```

2) Optional: start local tracing workers and trust CA
```bash
uv tool install mitmproxy
bash scripts/start_synth_workers.sh
```

2a) One-time: enable MCP tools for task creation
```bash
bash scripts/create_tasks/setup_codex_mcp.sh
```
Inside Codex, ask: "What tools do you have?" — you should see `repo.start_task.v1`, `repo.end_task.v1`, `repo.check_readiness.v1`, `repo.autofix_readiness.v1`.

2.5) Optional: create a task locally 
```bash
codex-synth
<Hi codex, please update the readme with "hello world". Use the start task tool to begin and end task tool to finish>
```

3) Hello world: run a prepared task locally (Docker)
```bash
scripts/run_codex_box.sh data/tasks/prepared/add-lm-tracing-readme 900 50000
```

Artifacts and results will appear under `data/runs/<run_id>/`.

### Get started guides

- Setup (install, workers, MCP): `guides/setup.md`
- Creating a task (Codex MCP, one-shot): `guides/creating-a-task.md`
- Running tasks sequentially with Docker: `guides/docker-sequential.md`
- Running tasks in parallel with Modal: `guides/modal-parallel.md`
- Using Hugging Face datasets: `guides/huggingface.md`

