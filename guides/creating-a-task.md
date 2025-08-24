### Creating a task (Codex MCP and one-shot helper)

Two paths: interactive inside Codex with MCP tools, or a non-interactive helper script.

## A) Interactive (MCP in Codex)

Prereq: MCP configured per Setup.

```bash
export RUN_ID="host_$(date +%s)"
codex-synth
```

In the Codex session, use the tools:
- Call `repo.start_task.v1` with a title and optional notes/labels
- Do the work
- Call `repo.end_task.v1` with a brief summary

Artifacts are written to `data/tasks/created/<task_slug>/`:
- `tb_meta.json`
- `overlay_files/` (e.g., `LM_INSTRUCTIONS.md`, `diff.patch`, `repo_info.json`, `notes.md`)
- optional `evaluation/` scaffold
- `trace/`

Convert to prepared when ready:
```bash
uv run one_shot.prepare_task_for_eval --task-dir data/tasks/created/<task_id_timestamp>
```

## B) One‑shot (non-interactive prompt)

Use the helper to construct a prompt and run Codex once with start/end instructions:

```bash
./scripts/create_tasks/create_task.sh "Add README section about sequential Docker evals"
```

This writes `data/tasks/created/<task_slug>/`. Convert it as above, or pass the created dir directly to the Docker runner (it will auto-prepare if needed).


