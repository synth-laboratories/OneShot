from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure src on path
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from one_shot.hf.runner import generate_report, run_parallel_from_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run evaluations from a Hugging Face dataset")
    parser.add_argument("--dataset", required=True, help="HuggingFace dataset id (e.g. user/dataset)")
    parser.add_argument("--split", default="train")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--run-report", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("data/hf/runs"))
    parser.add_argument("--token", default=os.getenv("HUGGINGFACE_HUB_TOKEN"))
    args = parser.parse_args()

    results = run_parallel_from_dataset(
        dataset_id=args.dataset,
        split=args.split,
        repo_root=args.repo_root,
        max_workers=args.max_workers,
        local_run_dir=args.out,
        token=args.token,
    )

    if args.run_report:
        generate_report(results, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
