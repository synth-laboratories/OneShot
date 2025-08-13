### Uploading a prepared task to Hugging Face (slim)

This guide shows how to publish a prepared task (Dockerfile + overlay_files + tb_meta.json) to the dataset `JoshPurtell/one-shot-bench` without heavy files like `codex-files/`.

#### 1) Verify the prepared task

```bash
ls -la data/tasks/prepared/<slug>
# Expect: Dockerfile, overlay_files/, tb_meta.json, (optional) evaluation/
```

#### 2) Dry-run the uploader

```bash
uv run python scripts/upload_prepared_task_hf.py \
  data/tasks/prepared/<slug> \
  JoshPurtell/one-shot-bench
```

You will see the allow/ignore patterns used. Nothing is uploaded yet.

#### 3) Upload (after review)

```bash
uv run python scripts/upload_prepared_task_hf.py \
  data/tasks/prepared/<slug> \
  JoshPurtell/one-shot-bench \
  tasks/<slug> \
  --yes
```

This uses a slim allow-list (tb_meta.json, Dockerfile, overlay_files/ essentials, evaluation/**) and ignores `codex-files/**`, `.env`, certificates, caches.

#### Alternative: CLI upload (new `hf upload`)

If you prefer CLI, either stage a slim copy first, or call the Python script above. If staging yourself, run:

```bash
uvx --from huggingface_hub hf upload \
  'data/tasks/prepared/<slug>' \
  'tasks/<slug>' \
  --repo-id 'JoshPurtell/one-shot-bench' --repo-type dataset \
  --commit-message 'Add prepared task: <slug>'
```

#### Notes

- Target dataset: `JoshPurtell/one-shot-bench` (`https://huggingface.co/datasets/JoshPurtell/one-shot-bench`).
- Do not upload `codex-files/` or secrets. The uploader script already excludes them.

