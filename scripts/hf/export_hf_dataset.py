from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src on path
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from one_shot.hf.export import DEFAULT_OUT, export_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Export prepared tasks to HF JSONL dataset")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--split",
        type=str,
        default=None,
        choices=[None, "train", "validation", "test"],
        nargs="?",
    )
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    return export_dataset(args.out, args.split, args.validate)


if __name__ == "__main__":
    sys.exit(main())
