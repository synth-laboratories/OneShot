#!/usr/bin/env python3
"""
Convert OneShot tasks into Harbor task bundles.

This script supports two input formats:
1. Prepared task directories created by OneShot.
2. JSONL exports generated via `one_shot.hf.export`.

Each converted task follows the Harbor layout:

task-root/
├── instruction.md
├── task.toml
├── environment/Dockerfile
├── solution/solve.sh (+ optional patch)
└── tests/test.sh (+ generated assets)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple


# Constants -------------------------------------------------------------------

INSTRUCTION_FILE_CANDIDATES = (
    "overlay_files/LM_INSTRUCTIONS.md",
    "overlay_files/lm_instructions.md",
)

DIFF_PATCH_CANDIDATES = (
    "overlay_files/diff.patch",
    "overlay_files/patch.diff",
    "overlay_files/overlay.patch",
)

DEFAULT_REWARD_PATH = "/logs/verifier/reward.txt"
DEFAULT_DATASET_VERSION = "1.0"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class ConversionResult:
    slug: str
    task_dir: Path
    oneshot_meta: dict


# Utility helpers -------------------------------------------------------------


def slugify(value: str) -> str:
    """Return a filesystem-friendly slug."""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-") or "task"


def load_json(path: Path) -> dict:
    """Load a JSON file, raising a useful error when missing."""
    if not path.exists():
        raise FileNotFoundError(f"Expected JSON file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e


def ensure_directory(path: Path, overwrite: bool = False) -> None:
    """Create directory, optionally removing it first."""
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"Target directory already exists: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def render_tags(tags: Sequence[str]) -> str:
    """Render list of tags as TOML array representation."""
    cleaned = [tag for tag in (tag.strip() for tag in tags) if tag]
    return "[" + ", ".join(json.dumps(tag) for tag in cleaned) + "]"


def infer_difficulty(tags: Sequence[str]) -> str:
    """Infer difficulty from known difficulty tags, defaulting to unknown."""
    difficulty_order = ("easy", "medium", "hard")
    lowered = {tag.lower() for tag in tags}
    for difficulty in difficulty_order:
        if difficulty in lowered:
            return difficulty
    return "unknown"


def find_first_existing(base_dir: Path, candidates: Iterable[str]) -> Optional[Path]:
    """Return the first candidate path that exists, relative to `base_dir`."""
    for relative in candidates:
        candidate = base_dir / relative
        if candidate.exists():
            return candidate
    return None


def normalize_oneshot_record(record: dict) -> dict:
    """
    Normalize JSONL export record into a structure similar to tb_meta.json.

    JSONL exports include flattened keys. We coerce them to a layout that
    matches prepared task metadata to reuse downstream logic.
    """
    metadata = {
        "title": record.get("title")
        or record.get("metadata.title")
        or record.get("task_title")
        or record.get("task_id"),
        "tags": record.get("tags")
        or record.get("metadata.tags")
        or record.get("task_tags")
        or [],
    }
    repo = {
        "git_url": record.get("repo_url")
        or record.get("metadata.repo_url")
        or record.get("repo.git_url")
        or record.get("git_url")
        or "",
        "branch": record.get("repo_branch")
        or record.get("repo.branch")
        or record.get("branch")
        or "main",
        "start_commit_sha": record.get("repo_commit")
        or record.get("repo.start_commit_sha")
        or record.get("commit_sha")
        or "HEAD",
        "subdir": record.get("repo_subdir") or record.get("repo.subdir") or "",
        "sparse_checkout": record.get("repo_sparse_checkout")
        or record.get("repo.sparse_checkout")
        or [],
    }

    evaluation = {
        "rubrics": record.get("evaluation.rubrics") or record.get("rubrics") or [],
        "test_scripts": record.get("evaluation.test_scripts")
        or record.get("test_scripts")
        or [],
    }

    lm_instructions = (
        record.get("instruction_markdown")
        or record.get("lm_instructions_markdown")
        or record.get("instructions_markdown")
        or record.get("lm_instructions")
        or record.get("instructions")
        or ""
    )

    diff_patch = (
        record.get("solution_patch")
        or record.get("diff_patch")
        or record.get("solution.diff")
    )

    return {
        "task_id": record.get("task_id") or slugify(str(metadata["title"] or "task")),
        "metadata": metadata,
        "repo": repo,
        "lm": {"instructions": lm_instructions},
        "evaluation": evaluation,
        "harbor": {"diff_patch": diff_patch},
    }


# Generation helpers ----------------------------------------------------------


def generate_instruction(
    oneshot_meta: dict, prepared_dir: Optional[Path] = None
) -> str:
    """Return markdown instructions sourced from files or metadata."""
    if prepared_dir:
        instruction_path = find_first_existing(
            prepared_dir, INSTRUCTION_FILE_CANDIDATES
        )
        if instruction_path:
            return instruction_path.read_text(encoding="utf-8").strip() + "\n"

    lm_data = oneshot_meta.get("lm", {})
    instructions = lm_data.get("instructions") or ""
    return textwrap.dedent(instructions).strip() + "\n"


def generate_task_toml(oneshot_meta: dict) -> str:
    """Render Harbor task.toml contents."""
    metadata = oneshot_meta.get("metadata", {})
    repo = oneshot_meta.get("repo", {})

    slug = oneshot_meta.get("task_id") or slugify(metadata.get("title", "task"))
    tags = metadata.get("tags") or []
    difficulty = infer_difficulty(tags)

    repo_url = repo.get("git_url", "")
    repo_commit = repo.get("start_commit_sha", "HEAD")

    content = textwrap.dedent(
        f"""
        [metadata]
        source = "oneshot"
        slug = {json.dumps(slug)}
        title = {json.dumps(metadata.get("title", slug))}
        difficulty = {json.dumps(difficulty)}
        tags = {render_tags(tags)}
        repo_url = {json.dumps(repo_url)}
        repo_commit = {json.dumps(repo_commit)}

        [verifier]
        timeout_sec = 300

        [agent]
        timeout_sec = 600

        [environment]
        build_timeout_sec = 900
        docker_image = ""
        cpus = 2
        memory_mb = 4096
        storage_mb = 10240
        """
    ).strip()
    return content + "\n"


def generate_dockerfile(oneshot_meta: dict) -> str:
    """Produce a simplified Harbor-friendly Dockerfile."""
    repo = oneshot_meta.get("repo", {})
    git_url = repo.get("git_url", "")
    git_branch = repo.get("branch") or "main"
    git_commit = repo.get("start_commit_sha") or "HEAD"

    return (
        textwrap.dedent(
            f"""
        FROM ubuntu:24.04

        ARG GIT_URL={json.dumps(git_url)}
        ARG GIT_BRANCH={json.dumps(git_branch)}
        ARG GIT_COMMIT={json.dumps(git_commit)}

        ENV DEBIAN_FRONTEND=noninteractive
        ENV TZ=UTC

        RUN apt-get update \\
            && apt-get install -y --no-install-recommends \\
                git \\
                python3 \\
                python3-venv \\
                python3-pip \\
                ca-certificates \\
                curl \\
                build-essential \\
            && rm -rf /var/lib/apt/lists/*

        RUN python3 -m pip install --no-cache-dir --break-system-packages pytest

        WORKDIR /workspace

        RUN set -eux; \\
            git clone --depth 1 --branch "$GIT_BRANCH" "$GIT_URL" repo; \\
            cd repo; \\
            if [ "$GIT_COMMIT" != "HEAD" ] && [ "$GIT_COMMIT" != "" ]; then \\
                git fetch --unshallow || true; \\
                git reset --hard "$GIT_COMMIT"; \\
            fi

        ENV REPO_DIR=/workspace/repo
        """
        ).strip()
        + "\n"
    )


def generate_solve_script(
    diff_patch_path: Optional[Path],
) -> Tuple[str, Optional[str]]:
    """
    Return solve.sh content and optional patch relative path.

    When a diff.patch is present we copy it and apply within solve.sh.
    """
    if diff_patch_path and diff_patch_path.exists():
        patch_filename = "patch.diff"
        script = textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail

            REPO_DIR="${{REPO_DIR:-/workspace/repo}}"
            PATCH_PATH="/oracle/{patch_filename}"

            if [ ! -d "$REPO_DIR" ]; then
                echo "Repository directory '$REPO_DIR' not found" >&2
                exit 1
            fi

            if [ ! -f "$PATCH_PATH" ]; then
                echo "Patch file '$PATCH_PATH' missing" >&2
                exit 1
            fi

            cd "$REPO_DIR"

            # Apply the patch; tolerate already-applied hunks
            git apply "$PATCH_PATH" || patch -p1 < "$PATCH_PATH"
            """
        ).strip()
        return script + "\n", patch_filename

    script = textwrap.dedent(
        """
        #!/usr/bin/env bash
        set -euo pipefail

        REPO_DIR="${REPO_DIR:-/workspace/repo}"
        echo "No oracle patch available for this task." >&2
        echo "Please implement the reference solution manually." >&2

        if [ -d "$REPO_DIR" ]; then
            ls "$REPO_DIR"
        fi
        """
    ).strip()
    return script + "\n", None


def write_test_artifacts(test_scripts: Sequence[dict], tests_root: Path) -> List[str]:
    """
    Materialize inline test scripts to the tests directory.

    Returns a list of relative paths (from the repo root) to executed scripts.
    """
    created_paths: List[str] = []
    for script in test_scripts:
        rel_path = script.get("path")
        content = script.get("content", "")
        if not rel_path:
            continue
        safe_path = Path(rel_path)
        if safe_path.is_absolute() or ".." in safe_path.parts:
            raise ValueError(f"Invalid test script path: {rel_path}")

        target_path = tests_root / safe_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        created_paths.append(safe_path.as_posix())
    return created_paths


def generate_test_script(
    created_paths: Sequence[str],
) -> str:
    """Combine generated assets into a Harbor verifier script."""
    lines: List[str] = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'REPO_DIR="${REPO_DIR:-/workspace/repo}"',
        f'REWARD_PATH="{DEFAULT_REWARD_PATH}"',
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        "",
        'mkdir -p "$(dirname "$REWARD_PATH")"',
        "",
        'if [ ! -d "$REPO_DIR" ]; then',
        "    echo \"Repository directory '$REPO_DIR' missing\" >&2",
        '    echo 0 > "$REWARD_PATH"',
        "    exit 0",
        "fi",
        "",
        "status=0",
    ]

    if not created_paths:
        lines.append('echo "No automated tests provided." >&2')
    else:
        for rel_path in created_paths:
            src_path = f"$SCRIPT_DIR/{rel_path}"
            dest_path = f"$REPO_DIR/{rel_path}"
            lines.extend(
                [
                    "",
                    f'SRC_PATH="{src_path}"',
                    f'DEST_PATH="{dest_path}"',
                    'mkdir -p "$(dirname "$DEST_PATH")"',
                    'cp "$SRC_PATH" "$DEST_PATH"',
                ]
            )

            if rel_path.endswith(".py"):
                lines.append('pytest -q "$DEST_PATH" || status=1')
            elif rel_path.endswith(".sh"):
                lines.append('bash "$DEST_PATH" || status=1')
            else:
                lines.append('python "$DEST_PATH" || status=1')

    lines.extend(
        [
            "",
            'if [ "$status" -eq 0 ]; then',
            '    echo 1 > "$REWARD_PATH"',
            "else",
            '    echo 0 > "$REWARD_PATH"',
            "fi",
            "",
            "exit 0",
            "",
        ]
    )

    return "\n".join(lines)


def write_registry(
    dataset_dir: Path,
    dataset_name: str,
    conversion_results: Sequence[ConversionResult],
    version: str = DEFAULT_DATASET_VERSION,
) -> None:
    """Create a Harbor registry.json under the dataset directory."""
    tasks_payload = []
    for result in conversion_results:
        repo = result.oneshot_meta.get("repo", {})
        tasks_payload.append(
            {
                "name": result.slug,
                "git_url": repo.get("git_url", ""),
                "git_commit_id": repo.get("start_commit_sha", "HEAD"),
                "path": f"tasks/{result.slug}",
            }
        )

    registry = {
        "name": dataset_name,
        "version": version,
        "tasks": tasks_payload,
    }

    registry_path = dataset_dir / "registry.json"
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


# Conversion entry points -----------------------------------------------------


def convert_prepared_task(
    prepared_dir: Path,
    out_dir: Path,
    *,
    overwrite: bool = False,
) -> ConversionResult:
    """Convert a prepared OneShot task directory."""
    logger.info(f"Converting prepared task: {prepared_dir}")
    tb_meta_path = prepared_dir / "tb_meta.json"
    if not tb_meta_path.exists():
        raise FileNotFoundError(
            f"tb_meta.json not found in prepared task directory: {prepared_dir}"
        )
    meta = load_json(tb_meta_path)

    task_id = meta.get("task_id") or slugify(
        meta.get("metadata", {}).get("title", "task")
    )
    logger.info(f"  Task ID: {task_id}")
    harbor_task_dir = out_dir / task_id
    ensure_directory(harbor_task_dir, overwrite=overwrite)

    # instruction.md
    logger.debug("  Generating instruction.md")
    instruction = generate_instruction(meta, prepared_dir)
    if not instruction.strip():
        logger.warning(f"  Warning: Empty instruction generated for {task_id}")
    (harbor_task_dir / "instruction.md").write_text(instruction, encoding="utf-8")

    # task.toml
    logger.debug("  Generating task.toml")
    task_toml = generate_task_toml(meta)
    (harbor_task_dir / "task.toml").write_text(task_toml, encoding="utf-8")

    # environment/Dockerfile
    logger.debug("  Generating Dockerfile")
    env_dir = harbor_task_dir / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)
    dockerfile = generate_dockerfile(meta)
    (env_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

    # solution/solve.sh (+ patch)
    logger.debug("  Generating solution")
    solution_dir = harbor_task_dir / "solution"
    solution_dir.mkdir(parents=True, exist_ok=True)
    diff_patch_path = find_first_existing(prepared_dir, DIFF_PATCH_CANDIDATES)
    if diff_patch_path:
        logger.info(f"  Found diff patch: {diff_patch_path}")
    else:
        logger.warning(f"  No diff patch found for {task_id}, oracle will be placeholder")
    solve_content, patch_filename = generate_solve_script(diff_patch_path)
    solve_path = solution_dir / "solve.sh"
    solve_path.write_text(solve_content, encoding="utf-8")
    os.chmod(solve_path, 0o755)

    if patch_filename and diff_patch_path:
        target_patch_path = solution_dir / patch_filename
        shutil.copyfile(diff_patch_path, target_patch_path)

    # tests/test.sh + generated assets
    logger.debug("  Generating tests")
    tests_dir = harbor_task_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_scripts = meta.get("evaluation", {}).get("test_scripts") or []
    created_paths = write_test_artifacts(test_scripts, tests_dir)
    if created_paths:
        logger.info(f"  Generated {len(created_paths)} test artifacts")
    else:
        logger.warning(f"  No test scripts found for {task_id}")
    test_script_content = generate_test_script(created_paths)
    test_sh_path = tests_dir / "test.sh"
    test_sh_path.write_text(test_script_content, encoding="utf-8")
    os.chmod(test_sh_path, 0o755)

    logger.info(f"  Successfully converted {task_id} to {harbor_task_dir}")
    return ConversionResult(slug=task_id, task_dir=harbor_task_dir, oneshot_meta=meta)


def convert_jsonl_record(
    record: dict, out_dir: Path, *, overwrite: bool = False
) -> ConversionResult:
    """Convert a JSONL record into a Harbor task."""
    meta = normalize_oneshot_record(record)
    task_id = meta.get("task_id")
    if not task_id:
        raise ValueError("task_id is required but was not found or generated")
    logger.info(f"Converting JSONL record: {task_id}")

    harbor_task_dir = out_dir / task_id
    ensure_directory(harbor_task_dir, overwrite=overwrite)

    # instruction
    (harbor_task_dir / "instruction.md").write_text(
        generate_instruction(meta), encoding="utf-8"
    )

    # task.toml
    (harbor_task_dir / "task.toml").write_text(
        generate_task_toml(meta), encoding="utf-8"
    )

    # environment
    env_dir = harbor_task_dir / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_dir.joinpath("Dockerfile").write_text(
        generate_dockerfile(meta), encoding="utf-8"
    )

    # solution
    solution_dir = harbor_task_dir / "solution"
    solution_dir.mkdir(parents=True, exist_ok=True)
    diff_patch = meta.get("harbor", {}).get("diff_patch")
    if diff_patch:
        patch_path = solution_dir / "patch.diff"
        patch_path.write_text(diff_patch, encoding="utf-8")
        solve_content, _ = generate_solve_script(patch_path)
    else:
        solve_content, _ = generate_solve_script(None)
    solve_path = solution_dir / "solve.sh"
    solve_path.write_text(solve_content, encoding="utf-8")
    os.chmod(solve_path, 0o755)

    # tests
    tests_dir = harbor_task_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    created_paths = write_test_artifacts(
        meta.get("evaluation", {}).get("test_scripts", []), tests_dir
    )
    test_script_content = generate_test_script(created_paths)
    test_sh_path = tests_dir / "test.sh"
    test_sh_path.write_text(test_script_content, encoding="utf-8")
    os.chmod(test_sh_path, 0o755)

    return ConversionResult(slug=task_id, task_dir=harbor_task_dir, oneshot_meta=meta)


# CLI -------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert OneShot tasks into Harbor-compatible tasks."
    )
    parser.add_argument(
        "--prepared-root",
        type=Path,
        action="append",
        default=[],
        help="Path to a directory containing prepared OneShot tasks.",
    )
    parser.add_argument(
        "--jsonl",
        type=Path,
        action="append",
        default=[],
        help="Path to a JSONL export file produced by one_shot.hf.export.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Destination directory for Harbor tasks (e.g., datasets/oneshot/tasks).",
    )
    parser.add_argument(
        "--dataset-name",
        type=str,
        help="Dataset name for optional registry.json generation.",
    )
    parser.add_argument(
        "--dataset-version",
        type=str,
        default=DEFAULT_DATASET_VERSION,
        help="Dataset version for registry.json (default: %(default)s).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing Harbor task directories if they already exist.",
    )
    parser.add_argument(
        "--emit-registry",
        action="store_true",
        help="Generate registry.json alongside the tasks directory.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level).",
    )
    return parser.parse_args()


def iter_prepared_tasks(root: Path) -> Iterator[Path]:
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            yield entry


def run_cli() -> None:
    args = parse_args()
    
    # Set log level based on verbosity
    if hasattr(args, 'verbose') and args.verbose:
        logger.setLevel(logging.DEBUG)
    
    out_dir: Path = args.out
    logger.info(f"Output directory: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    conversion_results: List[ConversionResult] = []
    errors: List[tuple[str, Exception]] = []

    # Convert prepared tasks
    for prepared_root in args.prepared_root:
        if not prepared_root.exists():
            error_msg = f"Prepared tasks root not found: {prepared_root}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        logger.info(f"Processing prepared tasks from: {prepared_root}")
        for task_dir in iter_prepared_tasks(prepared_root):
            try:
                result = convert_prepared_task(task_dir, out_dir, overwrite=args.overwrite)
                conversion_results.append(result)
            except Exception as e:
                error_msg = f"Failed to convert {task_dir}: {e}"
                logger.error(error_msg, exc_info=True)
                errors.append((str(task_dir), e))
                if not args.overwrite:
                    raise

    # Convert JSONL records
    for jsonl_path in args.jsonl:
        if not jsonl_path.exists():
            error_msg = f"JSONL file not found: {jsonl_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        logger.info(f"Processing JSONL file: {jsonl_path}")
        line_num = 0
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for line_num, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    result = convert_jsonl_record(record, out_dir, overwrite=args.overwrite)
                    conversion_results.append(result)
                except json.JSONDecodeError as e:
                    error_msg = f"Invalid JSON on line {line_num} of {jsonl_path}: {e}"
                    logger.error(error_msg)
                    errors.append((f"{jsonl_path}:{line_num}", e))
                except Exception as e:
                    error_msg = f"Failed to convert record on line {line_num} of {jsonl_path}: {e}"
                    logger.error(error_msg, exc_info=True)
                    errors.append((f"{jsonl_path}:{line_num}", e))

    # Generate registry if requested
    if args.emit_registry:
        if not args.dataset_name:
            raise ValueError("--emit-registry requires --dataset-name to be specified.")
        dataset_dir = out_dir.parent
        logger.info(f"Generating registry.json for dataset: {args.dataset_name}")
        write_registry(
            dataset_dir,
            args.dataset_name,
            conversion_results,
            version=args.dataset_version,
        )
        logger.info(f"Registry written to: {dataset_dir / 'registry.json'}")

    # Summary
    logger.info("\nConversion complete:")
    logger.info(f"  Successfully converted: {len(conversion_results)} tasks")
    if errors:
        logger.warning(f"  Errors encountered: {len(errors)}")
        for location, error in errors:
            logger.warning(f"    {location}: {error}")


def main() -> None:
    run_cli()


if __name__ == "__main__":
    main()
