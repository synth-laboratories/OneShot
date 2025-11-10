#!/usr/bin/env python3
"""
Measure Docker build performance for Codex task images.

This script runs `docker build` for the prepared Codex task and records how long
each scenario takes (warm cache, full rebuild without cache, etc.). It is meant
to make before/after comparisons when we tweak caching strategies.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_TASK_PATH = Path("data/tasks/prepared/hello-world-example")
DEFAULT_IMAGE_TAG = "oneshot-task-synth-ai:latest"


class CommandError(RuntimeError):
    """Raised when a child process exits with a non-zero status."""


def run_command(cmd: list[str], *, env: dict[str, str], description: str | None = None) -> None:
    """Run a command and stream its output to the terminal."""
    if description:
        print(description)
    print(f"+ {' '.join(shlex.quote(part) for part in cmd)}")
    result = subprocess.run(cmd, env=env, check=False)
    if result.returncode != 0:
        raise CommandError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")


def time_command(cmd: list[str], *, env: dict[str, str]) -> float:
    """Run a command, streaming output, and return the elapsed wall-clock time."""
    print(f"+ {' '.join(shlex.quote(part) for part in cmd)}")
    start = time.perf_counter()
    result = subprocess.run(cmd, env=env, check=False)
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise CommandError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")
    return elapsed


@dataclass
class Scenario:
    name: str
    description: str
    runner: Callable[["BenchmarkOptions"], float | None]


@dataclass
class BenchmarkOptions:
    task_path: Path
    image_tag: str
    extra_build_args: list[str]
    env: dict[str, str]
    allow_prune: bool


def _build_command(opts: BenchmarkOptions, additional_args: list[str]) -> list[str]:
    """Construct the docker build command."""
    cmd = ["docker", "build", "-t", opts.image_tag]
    if opts.extra_build_args:
        cmd.extend(opts.extra_build_args)
    if additional_args:
        cmd.extend(additional_args)
    cmd.append(str(opts.task_path))
    return cmd


def run_warm(opts: BenchmarkOptions) -> float:
    """Run docker build using existing caches."""
    print("=== Scenario: warm cache build ===")
    return time_command(_build_command(opts, []), env=opts.env)


def run_no_cache(opts: BenchmarkOptions) -> float:
    """Run docker build with --no-cache to simulate cache break."""
    print("=== Scenario: full rebuild (--no-cache) ===")
    return time_command(_build_command(opts, ["--no-cache"]), env=opts.env)


def run_after_prune(opts: BenchmarkOptions) -> float | None:
    """Prune builder cache (if allowed) and run docker build."""
    print("=== Scenario: build after cache prune ===")
    if not opts.allow_prune:
        print("Skipping: --allow-prune not specified (would run `docker builder prune --all`).")
        return None

    run_command(
        ["docker", "builder", "prune", "--all", "--force"],
        env=opts.env,
        description="Pruning BuildKit cache (this is destructive).",
    )
    return time_command(_build_command(opts, []), env=opts.env)


SCENARIOS: dict[str, Scenario] = {
    "warm": Scenario(
        name="warm",
        description="Build with an already-populated cache.",
        runner=run_warm,
    ),
    "no-cache": Scenario(
        name="no-cache",
        description="Build with --no-cache to simulate updating dependencies.",
        runner=run_no_cache,
    ),
    "pruned": Scenario(
        name="pruned",
        description="Prune BuildKit cache first (requires --allow-prune).",
        runner=run_after_prune,
    ),
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark docker build performance for the Codex task image.",
    )
    parser.add_argument(
        "scenarios",
        nargs="*",
        choices=SCENARIOS.keys(),
        default=["warm", "no-cache"],
        help="Scenarios to run (default: warm no-cache).",
    )
    parser.add_argument(
        "--task-path",
        type=Path,
        default=DEFAULT_TASK_PATH,
        help=f"Prepared task directory (default: {DEFAULT_TASK_PATH})",
    )
    parser.add_argument(
        "--image-tag",
        default=DEFAULT_IMAGE_TAG,
        help=f"Image tag to benchmark (default: {DEFAULT_IMAGE_TAG})",
    )
    parser.add_argument(
        "--build-arg",
        action="append",
        default=[],
        help="Additional --build-arg to pass to docker build (can be repeated).",
    )
    parser.add_argument(
        "--allow-prune",
        action="store_true",
        help="Allow destructive cache pruning (docker builder prune --all --force).",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional path to write benchmark results as JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands without executing them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    task_path = args.task_path.resolve()
    if not task_path.exists():
        print(f"Task directory not found: {task_path}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    opts = BenchmarkOptions(
        task_path=task_path,
        image_tag=args.image_tag,
        extra_build_args=[f"--build-arg={arg}" for arg in args.build_arg],
        env=env,
        allow_prune=args.allow_prune,
    )

    results: list[dict[str, object]] = []

    for scenario_name in args.scenarios:
        scenario = SCENARIOS[scenario_name]

        if args.dry_run:
            cmd = _build_command(opts, ["--no-cache"] if scenario_name == "no-cache" else [])
            print(f"[dry-run] Scenario '{scenario_name}' would run: {' '.join(cmd)}")
            if scenario_name == "pruned":
                print("[dry-run] (and run `docker builder prune --all --force` beforehand)")
            continue

        try:
            duration = scenario.runner(opts)
        except CommandError as exc:
            print(f"Scenario '{scenario_name}' failed: {exc}", file=sys.stderr)
            return 1

        results.append(
            {
                "scenario": scenario_name,
                "description": scenario.description,
                "duration_seconds": duration if duration is not None else None,
            }
        )
        if duration is not None:
            print(f"--> {scenario_name} completed in {duration:.2f} seconds\n")
        else:
            print(f"--> {scenario_name} skipped\n")

    if args.output_json and not args.dry_run:
        args.output_json.write_text(json.dumps(results, indent=2))
        print(f"Wrote results to {args.output_json}")

    if results:
        print("Summary:")
        for entry in results:
            duration = entry["duration_seconds"]
            if duration is None:
                print(f"  - {entry['scenario']}: skipped")
            else:
                print(f"  - {entry['scenario']}: {duration:.2f} s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
