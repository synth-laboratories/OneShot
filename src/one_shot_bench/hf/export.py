from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, List

from .utils import read_text_safe, read_json_safe, stable_task_id, deterministic_hash


PREPARED_ROOT = Path("data/tasks/prepared")
DEFAULT_OUT = Path("data/datasets/codex_coach_tasks/train.jsonl")


def build_record(prepared_dir: Path, diff_cap: int = 256_000, notes_cap: int = 256_000) -> Dict[str, Any]:
    tb_meta_path = prepared_dir / "tb_meta.json"
    if not tb_meta_path.exists():
        raise FileNotFoundError(f"Missing tb_meta.json in {prepared_dir}")
    tb_meta = json.loads(tb_meta_path.read_text())

    overlay = prepared_dir / "overlay_files"
    instructions = read_text_safe(overlay / "LM_INSTRUCTIONS.md") or tb_meta.get("lm", {}).get("instructions", "")
    diff_patch = read_text_safe(overlay / "diff.patch", cap_bytes=diff_cap)
    notes = read_text_safe(overlay / "notes.md", cap_bytes=notes_cap)
    repo_info_text = read_json_safe(overlay / "repo_info.json", as_text=True)

    repo_meta = tb_meta.get("repo", {})
    repo_obj = {
        "git_url": repo_meta.get("git_url", ""),
        "branch": repo_meta.get("branch", ""),
        "start_commit": repo_meta.get("start_commit_sha", ""),
        "end_commit": repo_meta.get("end_commit_sha", ""),
        "subdir": repo_meta.get("subdir", ""),
    }

    evaluation_meta = tb_meta.get("evaluation", {})
    rubrics = evaluation_meta.get("rubrics", [])
    test_scripts = evaluation_meta.get("test_scripts", [])

    paths = {
        "prepared_dir": str(prepared_dir),
        "overlay_dir": str(overlay),
        "diff_patch": str(overlay / "diff.patch"),
        "notes": str(overlay / "notes.md"),
        "repo_info": str(overlay / "repo_info.json"),
    }

    task_instance_id = tb_meta.get("task_id", prepared_dir.name)
    record: Dict[str, Any] = {
        "task_instance_id": task_instance_id,
        "task_id": stable_task_id(task_instance_id),
        "title": tb_meta.get("metadata", {}).get("title", ""),
        "tags": tb_meta.get("metadata", {}).get("tags", []),
        "instructions": instructions,
        "repo": repo_obj,
        "evaluation": {"rubrics": rubrics, "test_scripts": test_scripts},
        "artifacts": {"diff_patch": diff_patch, "notes": notes, "repo_info": repo_info_text, "paths": paths},
        "meta": {"prepared_version": "one_shot_bench", "created_from": str(prepared_dir)},
    }
    return record


def export_dataset(out_path: Path, split: Optional[str] = None, validate: bool = False) -> int:
    prepared_dirs = sorted([p for p in PREPARED_ROOT.glob("*") if p.is_dir()])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for p in prepared_dirs:
            rec = build_record(p)
            if split:
                h = deterministic_hash(rec["task_id"])  # 64 hex chars
                bucket = int(h[:2], 16)  # 0..255
                if split == "train" and bucket >= 32:
                    pass
                elif split == "validation" and 16 <= bucket < 32:
                    pass
                elif split == "test" and bucket < 16:
                    pass
                else:
                    continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1

    if validate:
        try:
            from datasets import load_dataset

            _ = load_dataset("json", data_files={"train": str(out_path)})
        except Exception as _:
            return 2
    return 0


