#!/usr/bin/env python3
"""
Fetch a prepared task from a Hugging Face dataset and run it with Docker using
scripts/run_codex_box.sh.

Example:
  uv run python scripts/run_hf_task_docker.py \
    --repo-id JoshPurtell/one-shot-bench \
    --task-slug update-readme-with-hello-world \
    --model gpt-5-mini
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from huggingface_hub import snapshot_download


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            s = Path(root) / f
            d = dst / rel / f
            shutil.copy2(s, d)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", default="JoshPurtell/one-shot-bench")
    parser.add_argument("--task-slug", required=True, help="Prepared task slug under tasks/<slug>")
    parser.add_argument("--out-root", default="data/tasks/prepared", help="Local prepared tasks root")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5-mini"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-run", action="store_true", help="Only download/stage; don't run")
    args = parser.parse_args()

    repo_id = args.repo_id
    path_in_repo = f"tasks/{args.task_slug}"
    base_out = Path(args.out_root).resolve()
    out_dir = (base_out / args.task_slug).resolve()

    # Download just the task subtree to a temp dir via allow_patterns
    with tempfile.TemporaryDirectory() as tmp:
        print(f"[hf] Downloading {repo_id}:{path_in_repo} …")
        cache_dir = snapshot_download(
            repo_id=repo_id,
            repo_type="dataset",
            allow_patterns=[f"{path_in_repo}/**"],
            local_dir=tmp,
            local_dir_use_symlinks=False,
        )
        src = Path(cache_dir) / path_in_repo
        if not (src / "tb_meta.json").exists() and not (src / "Dockerfile").exists():
            print(f"Error: downloaded path does not look like a prepared task: {src}")
            sys.exit(1)

        if out_dir.exists():
            if not args.force:
                # Stage under prepared/hf/<slug> instead of overwriting
                alt = (base_out / "hf" / args.task_slug).resolve()
                print(f"[stage] {out_dir} exists; staging under {alt} instead (use --force to overwrite).")
                out_dir = alt
            else:
                shutil.rmtree(out_dir)

        print(f"[stage] Staging to {out_dir}")
        copy_tree(src, out_dir)

    if args.no_run:
        print("[done] Task staged. Skipping run due to --no-run.")
        return

    # Run with Docker runner
    env = os.environ.copy()
    env.setdefault("OPENAI_MODEL", args.model)
    run_script = Path(__file__).parents[0] / "run_codex_box.sh"
    cmd = ["bash", str(run_script), str(out_dir)]
    print("[run] ", " ".join(cmd))
    proc = subprocess.run(cmd, env=env)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()


