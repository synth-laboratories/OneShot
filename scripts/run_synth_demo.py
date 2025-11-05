#!/usr/bin/env python3
"""Run synthetic demo tasks concurrently using the local synth-ai dataset."""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Tuple

from one_shot.datasets import list_task_records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate synthetic demo tasks concurrently via the synth-ai dataset registry."
    )
    parser.add_argument(
        "--split",
        default="created",
        help="Dataset split to load (default: created).",
    )
    parser.add_argument(
        "--tag",
        default="synth-demo",
        help="Tag to filter tasks by (default: synth-demo).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum concurrent workers (defaults to number of tasks).",
    )
    return parser.parse_args()


def load_cases(record: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Path]:
    """Read the cases.json declared in tb_meta."""
    meta = record["tb_meta"]
    evaluation = meta.get("evaluation") or {}
    cases_file = evaluation.get("cases_file")
    if not cases_file:
        raise ValueError(f"{record['task_id']} is missing evaluation.cases_file")

    base_dir = Path(record["path"])
    cases_path = base_dir / cases_file
    if not cases_path.exists():
        raise FileNotFoundError(f"Cases file not found for {record['task_id']}: {cases_path}")

    with cases_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"{record['task_id']} cases file does not contain a 'cases' list")
    return cases, cases_path


def solve_case(task_id: str, case: Dict[str, Any]) -> Any:
    """Deterministic solver used for the synthetic demo tasks."""
    case_type = case.get("type")
    if task_id == "synth-demo-math":
        numbers: Iterable[float] = case.get("numbers") or []
        if case_type == "sum":
            return sum(numbers)
        if case_type == "product":
            result = 1
            for value in numbers:
                result *= value
            return result
        if case_type == "mean":
            numbers = list(numbers)
            if not numbers:
                return 0
            return round(sum(numbers) / len(numbers), 2)
    elif task_id == "synth-demo-text":
        if case_type == "format":
            template = case.get("template", "")
            data = case.get("data") or {}
            return template.format(**data)
        if case_type == "slug":
            text = str(case.get("text", "")).strip().lower()
            slug = []
            previous_dash = False
            for char in text:
                if char.isalnum():
                    slug.append(char)
                    previous_dash = False
                elif char in {" ", "-", "_"}:
                    if not previous_dash:
                        slug.append("-")
                    previous_dash = True
            return "".join(slug).strip("-")
        if case_type == "word_count":
            text = str(case.get("text", "")).strip()
            return 0 if not text else len(text.split())

    raise ValueError(f"No solver available for {task_id}:{case_type}")


def values_match(expected: Any, actual: Any) -> bool:
    """Compare expected and actual values with tolerance for floats."""
    if isinstance(expected, float):
        return abs(expected - float(actual)) <= 0.01
    return expected == actual


def evaluate_task(record: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate a single task and return a summary dictionary."""
    task_id = record["task_id"]
    thread_name = threading.current_thread().name
    print(f"→ [{thread_name}] starting {task_id}")
    start = time.perf_counter()

    cases, cases_path = load_cases(record)
    case_results = []
    passed = 0

    for case in cases:
        actual = solve_case(task_id, case)
        expected = case.get("expected")
        success = values_match(expected, actual)
        if success:
            passed += 1
        case_results.append(
            {
                "id": case.get("id"),
                "type": case.get("type"),
                "expected": expected,
                "actual": actual,
                "success": success,
            }
        )

    duration = time.perf_counter() - start
    score = passed / len(cases) if cases else 0.0

    summary = {
        "task_id": task_id,
        "cases_path": str(cases_path),
        "score": score,
        "passed": passed,
        "total": len(cases),
        "duration": duration,
        "cases": case_results,
    }
    print(
        f"✓ [{thread_name}] finished {task_id}: "
        f"{passed}/{len(cases)} cases ({score * 100:.1f}%) in {duration:.2f}s"
    )
    return summary


def main() -> int:
    args = parse_args()

    records = list_task_records(args.split)
    selected = [
        record for record in records if args.tag in (record["tb_meta"].get("metadata") or {}).get("tags", [])
    ]

    if not selected:
        print(f"No tasks tagged with '{args.tag}' available in split '{args.split}'.")
        return 1

    max_workers = args.max_workers or len(selected)
    print(
        f"Running {len(selected)} task(s) tagged '{args.tag}' from split '{args.split}' "
        f"with up to {max_workers} worker(s)."
    )

    start = time.perf_counter()
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(evaluate_task, record): record for record in selected}
        for future in as_completed(future_map):
            results.append(future.result())

    total_duration = time.perf_counter() - start
    aggregate_score = mean(result["score"] for result in results)

    print("\nIndividual Task Scores:")
    for result in sorted(results, key=lambda entry: entry["task_id"]):
        print(
            f"  - {result['task_id']}: {result['passed']}/{result['total']} "
            f"({result['score'] * 100:.1f}%) in {result['duration']:.2f}s"
        )
        for case in result["cases"]:
            status = "PASS" if case["success"] else "FAIL"
            print(
                f"      • {case['id']} ({case['type']}): {status} "
                f"(expected={case['expected']}, actual={case['actual']})"
            )

    print(
        f"\nAggregate score across {len(results)} tasks: {aggregate_score * 100:.1f}% "
        f"(total wall-clock {total_duration:.2f}s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
