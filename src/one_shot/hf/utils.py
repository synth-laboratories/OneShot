from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Any, Optional


def read_text_safe(path: Path, cap_bytes: Optional[int] = None) -> Optional[str]:
    if not path.exists():
        return None
    data = path.read_bytes()
    if cap_bytes is not None and len(data) > cap_bytes:
        truncated = data[:cap_bytes]
        return truncated.decode("utf-8", errors="ignore") + "\n\n[TRUNCATED]"
    return data.decode("utf-8", errors="ignore")


def read_json_safe(path: Path, as_text: bool = False) -> Optional[str | Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text())
        return json.dumps(obj, ensure_ascii=False) if as_text else obj
    except Exception:
        return None


def stable_task_id(task_instance_id: str) -> str:
    parts = task_instance_id.rsplit("_", 2)
    if len(parts) >= 3 and parts[-2].isdigit() and parts[-1].isdigit():
        return "_".join(parts[:-2])
    return task_instance_id


def deterministic_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


