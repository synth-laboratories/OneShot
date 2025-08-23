# OneShot Bench

Scalably converting pair-programming CLI trajectories into challenging digital agent tasks.

Key idea:
- You pair progress with Codex in the terminal until you're happy with the result.
- Codex uses mcp tools to save the diff, initial validation criteria like a rubric and fail->pass unit tests, and a record of its successful trajectory to local storage.
- You can then (upon optional tweaking of the rubric/unit tests) load that scenario into modal or docker and evaluate another instance of codex using a different model to see if it can headlessly "one-shot" the problem.
- Such data can be shared to and fetched from huggingface. See  https://huggingface.co/datasets/JoshPurtell/one-shot-bench

Motivations:
- SWEBench is over a year old, and we're doing work with agents. This is a simple-ish (please contribute!) approach to create and curate an evaluation dataset that actually matters for you.
- CLI agents are really powerful and have lots of applications. OSS scaffolding for facilitating the creation of high-quality data using skilled practitioners that already love using them means more data in the ecosystem.
- Selfishly, I hate evaluating agents on SWEBench and other legacy SWE agent benchmarks. I'm hoping to curate a super high-quality long-horizon agent dataset to really put Synth's algos to the test!!

```
+hello world
[results] ----------------------------------------
[results] Rubric total score: 54%
[results]  - task_completion: 0% (weight=0.4)
[results]  - code_quality: 80% (weight=0.3)
[results]  - testing: 100% (weight=0.3)
[results] Unit tests: 1 passed, 1 failed
[results] ========================================
[cleanup] Removing container
```

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

2.5) Optional: create a task locally - NOTE, there's a known bug where the MCP tools say failure after successful execution. Ignore it or push a fix :-)
```bash
codex-synth
<Hi codex, please update the readme with "hello world". Use the start task tool to begin and end task tool to finish>
```

3) Hello world: run a prepared task locally (Docker)
```bash
scripts/run_codex_box.sh data/tasks/prepared/add-lm-tracing-readme 900 50000
```
or run a newly created raw task (will automatically be prepared)
```bash
bash scripts/run_codex_box.sh data/tasks/created/update-readme-with-hello-world_20250812_181007 
```

Artifacts and results will appear under `data/runs/<run_id>/`.

### Get started guides

- Setup (install, workers, MCP): `guides/setup.md`
- Creating a task (Codex MCP, one-shot): `guides/creating-a-task.md`
- Running tasks sequentially with Docker: `guides/docker-sequential.md`
- Running tasks in parallel with Modal: `guides/modal-parallel.md`
- Running on Modal (setup + single-run): `guides/modal.md`
- Using Hugging Face datasets: `guides/huggingface.md`

### Hugging Face integration

- Upload a prepared task (slim, excludes heavy files):
  - Guide: `guides/huggingface_upload.md`
  - Command:
    ```bash
    uv run python scripts/upload_prepared_task_hf.py \
      data/tasks/prepared/<slug> \
      JoshPurtell/one-shot-bench \
      tasks/<slug> \
      --yes
    ```

- Run a prepared task fetched from Hugging Face (Docker):
  - Guide: `guides/huggingface_run.md`
  - Command:
    ```bash
    uv run python scripts/run_hf_task_docker.py \
      --repo-id JoshPurtell/one-shot-bench \
      --task-slug <slug> \
      --model gpt-5-mini
    ```

### Modal (optional)

- To run Codex in Modal instead of Docker:
  ```bash
  export OPENAI_API_KEY=sk-...
  export OPENAI_MODEL=gpt-5-mini
  uv tool install modal && modal setup
  SANDBOX_BACKEND=modal bash scripts/run_codex_box.sh data/tasks/prepared/<slug>
  ```

hello world
