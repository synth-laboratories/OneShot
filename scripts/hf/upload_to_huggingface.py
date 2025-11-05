from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure src/ is importable when running as a script
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from one_shot.hf.upload import upload_dataset as upload_dataset_mod  # noqa: E402

DEFAULT_DATA = Path("data/datasets/codex_coach_tasks/train.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload JSONL dataset to HuggingFace Hub",
    )
    parser.add_argument("--repo-id", required=True, help="e.g. your-username/codex-coach-tasks")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--private", action="store_true", help="Upload to a private dataset repo")
    args = parser.parse_args()

    if not args.data.exists():
        print(f"Dataset file not found: {args.data}", file=sys.stderr)
        return 1

    token = os.getenv("HUGGINGFACE_HUB_TOKEN") or os.getenv("HF_TOKEN")
    try:
        upload_dataset_mod(args.data.parent, args.repo_id, token, private=args.private, create_pr=False)
    except Exception as exc:  # pragma: no cover - surfacing message is enough
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 1

    visibility = "private" if args.private else "public"
    print(f"Uploaded ({visibility}) to https://huggingface.co/datasets/{args.repo_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
