#!/usr/bin/env python3
"""
Upload a slim prepared task to a Hugging Face dataset.

Usage:
  uv run python scripts/upload_prepared_task_hf.py <task_dir> <repo_id> [<path_in_repo>] [--yes]

Examples:
  uv run python scripts/upload_prepared_task_hf.py \
    data/tasks/prepared/update-readme-with-hello-world \
    JoshPurtell/one-shot-bench \
    tasks/update-readme-with-hello-world

Default path_in_repo is tasks/<basename(task_dir)>

By default this is a dry-run; pass --yes to actually upload.
"""

import sys
from pathlib import Path
from huggingface_hub import HfApi, login


def fail_usage() -> None:
    print("Usage: upload_prepared_task_hf.py <task_dir> <repo_id> [<path_in_repo>] [--yes]")
    sys.exit(1)


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-") or len(argv) < 2:
        fail_usage()

    task_dir = Path(argv[0]).resolve()
    repo_id = argv[1]
    path_in_repo = None
    yes = False

    for arg in argv[2:]:
        if arg == "--yes":
            yes = True
        elif not path_in_repo:
            path_in_repo = arg

    if not task_dir.exists():
        print(f"Error: task_dir not found: {task_dir}")
        sys.exit(1)

    if not path_in_repo:
        path_in_repo = f"tasks/{task_dir.name}"

    allow_patterns = [
        "tb_meta.json",
        "Dockerfile",
        "overlay_files/box_bootstrap.sh",
        "overlay_files/codex-synth",
        "overlay_files/LM_INSTRUCTIONS.md",
        "overlay_files/notes.md",
        "overlay_files/repo_info.json",
        "overlay_files/tb_meta.json",
        "evaluation/**",
    ]

    ignore_patterns = [
        "codex-files/**",
        "overlay_files/codex-files/**",
        ".env",
        "mitmproxy-ca-cert.pem",
        "**/__pycache__/**",
        "**/.DS_Store",
        "node_modules/**",
    ]

    print("[hf] Prepared task upload (dry-run)")
    print(f"  repo_id      : {repo_id}")
    print(f"  path_in_repo : {path_in_repo}")
    print(f"  task_dir     : {task_dir}")
    print("  allow_patterns:")
    for p in allow_patterns:
        print(f"    - {p}")
    print("  ignore_patterns:")
    for p in ignore_patterns:
        print(f"    - {p}")

    if not yes:
        print("\n[info] Pass --yes to actually upload.")
        # Helpful CLI alternative (new hf CLI)
        print("\nCLI alternative (stage a slim copy, then):")
        print("  uvx --from huggingface_hub hf upload '<slim_folder>' '%s' --repo-id '%s' --repo-type dataset" % (path_in_repo, repo_id))
        return

    # Ensure login (may open browser)
    try:
        HfApi().whoami()
    except Exception:
        login()

    api = HfApi()
    api.upload_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(task_dir),
        path_in_repo=path_in_repo,
        commit_message=f"Add prepared task: {path_in_repo}",
        allow_patterns=allow_patterns,
        ignore_patterns=ignore_patterns,
    )
    print("[hf] Uploaded prepared task:", path_in_repo)


if __name__ == "__main__":
    main()


