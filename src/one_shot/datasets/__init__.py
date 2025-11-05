"""Integration helpers bridging OneShot tasks with synth-ai task datasets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, List

from synth_ai.task.datasets import TaskDatasetRegistry, TaskDatasetSpec

from one_shot.sensitivity import SensitivityLevel

DATA_ROOT_ENV = "ONESHOT_DATA_ROOT"
DATASET_ID = "oneshot.local.tasks"


class TaskRecord(dict):
    """Dictionary-like representation of a single task entry."""

    path: Path

    def __init__(self, *, task_id: str, split: str, path: Path, tb_meta: Dict):
        super().__init__(
            task_id=task_id,
            split=split,
            path=str(path),
            sensitivity=(tb_meta.get("sensitivity") or {}).get("level", "unknown"),
            tb_meta=tb_meta,
        )
        self.path = path


class LocalTaskDataset(dict):
    """Return value from the dataset loader (matches synth-ai expectations)."""

    spec: TaskDatasetSpec

    def __init__(self, spec: TaskDatasetSpec, records: Iterable[TaskRecord]):
        records = list(records)
        by_split: Dict[str, List[TaskRecord]] = {}
        for record in records:
            by_split.setdefault(record["split"], []).append(record)
        super().__init__(records=records, by_split=by_split, spec=spec)
        self.spec = spec

    def split(self, name: str) -> List[TaskRecord]:
        return list(self["by_split"].get(name, []))


registry = TaskDatasetRegistry()


def data_root() -> Path:
    override = os.getenv(DATA_ROOT_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


def _iter_tasks(root: Path, split: str) -> Iterable[TaskRecord]:
    base = root / "tasks" / split
    if not base.exists():
        return []
    for candidate in sorted(base.iterdir()):
        tb_meta_path = candidate / "tb_meta.json"
        if not tb_meta_path.exists():
            continue
        try:
            tb_meta = json.loads(tb_meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        task_id = tb_meta.get("task_id") or candidate.name
        yield TaskRecord(task_id=task_id, split=split, path=candidate, tb_meta=tb_meta)


def _load_local_dataset(spec: TaskDatasetSpec) -> LocalTaskDataset:
    root = data_root()
    records = list(_iter_tasks(root, "created")) + list(_iter_tasks(root, "prepared"))
    # Ensure sensitivity defaults are present for downstream consumers.
    for record in records:
        meta = record["tb_meta"]
        sensitivity = (meta.get("sensitivity") or {}).get("level")
        if not sensitivity:
            meta.setdefault("sensitivity", {"level": SensitivityLevel.UNKNOWN.value})
            record["tb_meta"] = meta
    return LocalTaskDataset(spec, records)


def register_local_dataset() -> TaskDatasetSpec:
    spec = TaskDatasetSpec(
        id=DATASET_ID,
        name="OneShot Local Tasks",
        splits=["created", "prepared"],
        default_split="created",
        description="Tasks discovered from data/tasks/{created,prepared}",
    )
    registry.register(spec, _load_local_dataset, cache=False)
    return spec


def get_local_dataset(split: str | None = None) -> LocalTaskDataset:
    spec = registry.describe(DATASET_ID)
    effective_split = registry.ensure_split(spec, split)
    dataset = registry.get(spec)
    dataset["active_split"] = effective_split
    return dataset


def list_task_records(split: str | None = None) -> List[TaskRecord]:
    dataset = get_local_dataset(split)
    active_split = dataset["active_split"]
    return dataset.split(active_split)


# Register dataset when module is imported (idempotent)
try:
    register_local_dataset()
except ValueError:
    pass
