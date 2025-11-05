#!/usr/bin/env python3
"""
Export a single run's artifacts to a Hugging Face dataset datum (JSONL + README).

Usage:
  uv run python scripts/export_hf.py \
    --run-dir data/runs/20250812__19-46-08 \
    --task-dir data/tasks/prepared/update-readme-with-hello-world \
    --out-root data/hf/one-shot-bench

This will create:
  data/hf/one-shot-bench/<task_id>__<timestamp>/
    - data.jsonl   (one record)
    - README.md    (dataset card for this datum)
"""

import argparse
import json
from pathlib import Path
from datetime import datetime


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path):
    return json.loads(read_text(path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--out-root", default="data/hf/one-shot-bench")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    task_dir = Path(args.task_dir).resolve()
    out_root = Path(args.out_root).resolve()

    artifacts_dir = run_dir / "artifacts"
    tb_meta_file = artifacts_dir / "tb_meta.json"
    if not tb_meta_file.exists():
        tb_meta_file = task_dir / "tb_meta.json"
    tb_meta = load_json(tb_meta_file)

    task_id = tb_meta["task_id"]
    repo = tb_meta.get("repo", {})
    lm = tb_meta.get("lm", {})

    # Required artifacts
    diff = read_text(artifacts_dir / "diff.patch")
    eval_json = load_json(artifacts_dir / "tb_evaluation_results.json")

    # Optional prompt (from file captured during bootstrap if present)
    prompt_file = artifacts_dir / "instructions.txt"
    prompt = read_text(prompt_file) if prompt_file.exists() else lm.get("instructions", "")

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    slug = f"{task_id}__{ts}"
    out_dir = out_root / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "task_id": task_id,
        "repo": {
            "git_url": repo.get("git_url"),
            "branch": repo.get("branch"),
            "start_commit_sha": repo.get("start_commit_sha"),
        },
        "prompt": prompt,
        "diff": diff,
        "evaluation": eval_json.get("evaluation", {}),
        "test_results": eval_json.get("test_results", {}),
        "sensitivity": (tb_meta.get("sensitivity") or {}).get("level", "unknown"),
        "raw": eval_json,
    }

    # Write JSONL (single datum)
    with (out_dir / "data.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Write a minimal dataset card
    card = f"""# one-shot-bench datum: {task_id}

Generated at: {ts} (UTC)

## Fields
- task_id: string
- repo: {{ git_url, branch, start_commit_sha }}
- prompt: string
- diff: string (git diff)
- evaluation: object (rubric scores)
- test_results: object (per-test booleans)
- sensitivity: safe/sensitive/unknown label

## Notes
This is a single-run datum exported from OneShot Bench. Aggregate multiple
data.jsonl rows across subfolders for a larger dataset.
"""
    (out_dir / "README.md").write_text(card, encoding="utf-8")

    print("[export] Wrote:")
    print(f"  - {out_dir / 'data.jsonl'}")
    print(f"  - {out_dir / 'README.md'}")
    print("\nNext: upload to Hugging Face (manual confirmation)")
    print("Option A (Python API):")
    print("  uv run python - <<'PY'\nfrom huggingface_hub import HfApi\nimport os\napi = HfApi()\napi.upload_folder(\n    repo_id='JoshPurtell/one-shot-bench', repo_type='dataset',\n    folder_path=r'%s', path_in_repo='%s',\n    commit_message='Add %s'\n)\nPY" % (str(out_dir), slug, slug))
    print("\nOption B (CLI, huggingface-cli >= 0.23):")
    print("  huggingface-cli upload datasets/JoshPurtell/one-shot-bench '%s' '.' --repo-type dataset" % str(out_dir))


if __name__ == "__main__":
    main()
