#!/usr/bin/env python3
"""
Collect key artifacts from a Codex Box run into an easy-to-browse folder.

Usage:
    uv run python scripts/collect_codex_results.py \
        --run-dir data/runs/20251105__01-00-45 \
        --task-dir data/tasks/created/synth-ai-cuvier-cli \
        --out-root results

This copies:
    - The agent-produced diff (artifacts/diff.patch)
    - The reference diff from the task directory (diff.patch)
    - Codex execution log and pytest output (codex-run.log)
into results/<run_id>/. Existing files are overwritten.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def locate(source: Path, relative: str) -> Path:
    path = source / relative
    if not path.exists():
        raise FileNotFoundError(f"Expected file missing: {path}")
    return path


def copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"Copied {src} -> {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Codex run artifacts")
    parser.add_argument("--run-dir", required=True, help="Path to data/runs/<run_id>")
    parser.add_argument(
        "--task-dir",
        required=True,
        help="Task directory (created or prepared) containing diff.patch",
    )
    parser.add_argument(
        "--out-root",
        default="results",
        help="Directory under which results/<run_id>/ is created (default: results/)",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    task_dir = Path(args.task_dir).resolve()
    out_root = Path(args.out_root).resolve()

    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")
    if not task_dir.exists():
        raise FileNotFoundError(f"Task directory not found: {task_dir}")

    run_id = run_dir.name
    out_dir = out_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Agent diff
    agent_diff = locate(run_dir, "artifacts/diff.patch")
    copy(agent_diff, out_dir / "agent_diff.patch")

    # Target diff (from task)
    target_diff = locate(task_dir, "diff.patch")
    copy(target_diff, out_dir / "target_diff.patch")

    # Codex run log (pytest results appear here)
    codex_log = locate(run_dir, "artifacts/codex-run.log")
    copy(codex_log, out_dir / "codex-run.log")

    print(f"Done. Inspect results in {out_dir}")


if __name__ == "__main__":
    main()
