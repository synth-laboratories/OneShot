from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.oneshot_to_harbor import (  # noqa: E402
    convert_jsonl_record,
    convert_prepared_task,
    generate_dockerfile,
    generate_instruction,
    generate_solve_script,
    generate_task_toml,
    generate_test_script,
    infer_difficulty,
    normalize_oneshot_record,
    slugify,
    write_registry,
    write_test_artifacts,
)


DATA_ROOT = Path("data/tasks/prepared")


@pytest.mark.parametrize(
    "slug",
    [
        "hello-world-example",
        "synth-ai-cuvier-cli",
    ],
)
def test_convert_prepared_task_creates_expected_structure(
    tmp_path: Path, slug: str
) -> None:
    prepared_dir = DATA_ROOT / slug
    if not prepared_dir.exists():
        pytest.skip(f"Prepared task fixture not available: {prepared_dir}")

    result = convert_prepared_task(prepared_dir, tmp_path, overwrite=True)

    harbor_dir = tmp_path / result.slug
    assert harbor_dir.exists()

    instruction_path = harbor_dir / "instruction.md"
    assert instruction_path.exists()
    assert instruction_path.read_text(encoding="utf-8").strip()

    task_toml = (harbor_dir / "task.toml").read_text(encoding="utf-8")
    assert f'slug = "{result.slug}"' in task_toml
    assert 'source = "oneshot"' in task_toml

    dockerfile = (harbor_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM ubuntu:24.04" in dockerfile
    assert "git clone" in dockerfile

    solve_sh = harbor_dir / "solution" / "solve.sh"
    assert solve_sh.exists()
    assert os.access(solve_sh, os.X_OK), "solve.sh should be executable"

    test_sh = harbor_dir / "tests" / "test.sh"
    assert test_sh.exists()
    assert os.access(test_sh, os.X_OK), "test.sh should be executable"

    # Tasks with diff patches should ship the oracle patch alongside solve.sh.
    patch_path = harbor_dir / "solution" / "patch.diff"
    if slug == "synth-ai-cuvier-cli":
        assert patch_path.exists()
        assert "pytest -q" in test_sh.read_text(encoding="utf-8")
        generated_test = (
            harbor_dir / "tests" / "tests" / "unit" / "cli" / "test_opencode_command.py"
        )
        assert generated_test.exists()
    else:
        assert not patch_path.exists()


# Unit tests for utility functions


def test_slugify() -> None:
    assert slugify("Hello World") == "hello-world"
    assert slugify("Test 123") == "test-123"
    assert slugify("Multiple---Dashes") == "multiple-dashes"
    assert slugify("") == "task"
    assert slugify("   ") == "task"
    assert slugify("Special!@#$%Chars") == "special-chars"  # Special chars become dashes


def test_render_tags() -> None:
    from scripts.oneshot_to_harbor import render_tags  # noqa: F811
    
    assert render_tags(["tag1", "tag2"]) == '["tag1", "tag2"]'
    assert render_tags([]) == "[]"
    assert render_tags(["  tag1  ", "tag2"]) == '["tag1", "tag2"]'
    assert render_tags(["tag with spaces"]) == '["tag with spaces"]'


def test_infer_difficulty() -> None:
    assert infer_difficulty(["easy"]) == "easy"
    assert infer_difficulty(["medium"]) == "medium"
    assert infer_difficulty(["hard"]) == "hard"
    assert infer_difficulty(["Easy", "MEDIUM"]) == "easy"  # First match
    assert infer_difficulty(["other", "tags"]) == "unknown"
    assert infer_difficulty([]) == "unknown"


def test_generate_instruction_from_meta() -> None:
    meta = {
        "lm": {
            "instructions": "Test instructions\nWith multiple lines"
        }
    }
    result = generate_instruction(meta)
    assert "Test instructions" in result
    assert "With multiple lines" in result


def test_generate_instruction_empty() -> None:
    meta = {"lm": {}}
    result = generate_instruction(meta)
    assert isinstance(result, str)
    assert result.strip() == ""


def test_generate_task_toml() -> None:
    meta = {
        "task_id": "test-task",
        "metadata": {
            "title": "Test Task",
            "tags": ["easy", "test"]
        },
        "repo": {
            "git_url": "https://github.com/test/repo",
            "start_commit_sha": "abc123"
        }
    }
    result = generate_task_toml(meta)
    assert 'slug = "test-task"' in result
    assert 'source = "oneshot"' in result
    assert 'difficulty = "easy"' in result
    assert 'repo_url = "https://github.com/test/repo"' in result
    assert 'repo_commit = "abc123"' in result


def test_generate_dockerfile() -> None:
    meta = {
        "repo": {
            "git_url": "https://github.com/test/repo",
            "branch": "main",
            "start_commit_sha": "abc123"
        }
    }
    result = generate_dockerfile(meta)
    assert "FROM ubuntu:24.04" in result
    assert "git clone" in result
    assert "python3" in result
    assert "pytest" in result
    assert "REPO_DIR=/workspace/repo" in result


def test_generate_solve_script_with_patch() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        patch_path = Path(tmpdir) / "test.patch"
        patch_path.write_text("test patch content")
        script, patch_filename = generate_solve_script(patch_path)
        assert patch_filename == "patch.diff"
        assert "#!/usr/bin/env bash" in script
        assert "/oracle/patch.diff" in script
        assert "git apply" in script


def test_generate_solve_script_without_patch() -> None:
    script, patch_filename = generate_solve_script(None)
    assert patch_filename is None
    assert "No oracle patch available" in script
    assert "ls" in script


def test_generate_test_script_empty() -> None:
    result = generate_test_script([])
    assert "#!/usr/bin/env bash" in result
    assert "No automated tests provided" in result
    assert "/logs/verifier/reward.txt" in result


def test_generate_test_script_with_tests() -> None:
    paths = ["test.py", "test.sh", "test.js"]
    result = generate_test_script(paths)
    assert "pytest -q" in result
    assert "bash" in result
    assert "python" in result


def test_write_test_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tests_root = Path(tmpdir)
        test_scripts = [
            {"path": "test.py", "content": "def test(): pass"},
            {"path": "subdir/test.sh", "content": "#!/bin/bash\necho test"},
        ]
        created = write_test_artifacts(test_scripts, tests_root)
        assert len(created) == 2
        assert "test.py" in created
        assert "subdir/test.sh" in created
        assert (tests_root / "test.py").exists()
        assert (tests_root / "subdir" / "test.sh").exists()


def test_write_test_artifacts_invalid_path() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tests_root = Path(tmpdir)
        test_scripts = [{"path": "/absolute/path", "content": "test"}]
        with pytest.raises(ValueError, match="Invalid test script path"):
            write_test_artifacts(test_scripts, tests_root)


def test_normalize_oneshot_record() -> None:
    record = {
        "title": "Test Task",
        "tags": ["test"],
        "repo_url": "https://github.com/test/repo",
        "repo_commit": "abc123",
        "instruction_markdown": "Test instructions",
        "diff_patch": "test patch"
    }
    result = normalize_oneshot_record(record)
    assert result["metadata"]["title"] == "Test Task"
    assert result["repo"]["git_url"] == "https://github.com/test/repo"
    assert result["repo"]["start_commit_sha"] == "abc123"
    assert result["lm"]["instructions"] == "Test instructions"
    assert result["harbor"]["diff_patch"] == "test patch"


def test_normalize_oneshot_record_alternate_keys() -> None:
    record = {
        "task_title": "Alt Task",
        "metadata.tags": ["alt"],
        "repo.git_url": "https://github.com/alt/repo",
        "repo.start_commit_sha": "def456",
        "lm_instructions": "Alt instructions"
    }
    result = normalize_oneshot_record(record)
    assert result["metadata"]["title"] == "Alt Task"
    assert result["repo"]["git_url"] == "https://github.com/alt/repo"
    assert result["repo"]["start_commit_sha"] == "def456"
    assert result["lm"]["instructions"] == "Alt instructions"


def test_convert_jsonl_record() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        record = {
            "task_id": "jsonl-test",
            "title": "JSONL Test Task",
            "tags": ["test"],
            "repo_url": "https://github.com/test/repo",
            "repo_commit": "abc123",
            "instruction_markdown": "Test instructions",
            "diff_patch": "test patch",
            "test_scripts": [
                {"path": "test.py", "content": "def test(): assert True"}
            ]
        }
        result = convert_jsonl_record(record, out_dir, overwrite=True)
        assert result.slug == "jsonl-test"
        assert (out_dir / "jsonl-test").exists()
        assert (out_dir / "jsonl-test" / "instruction.md").exists()
        assert (out_dir / "jsonl-test" / "task.toml").exists()
        assert (out_dir / "jsonl-test" / "solution" / "solve.sh").exists()
        assert (out_dir / "jsonl-test" / "solution" / "patch.diff").exists()
        assert (out_dir / "jsonl-test" / "tests" / "test.sh").exists()


def test_write_registry() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        dataset_dir = Path(tmpdir)
        tasks_dir = dataset_dir / "tasks"
        tasks_dir.mkdir()
        
        results = [
            type("Result", (), {
                "slug": "task1",
                "oneshot_meta": {
                    "repo": {
                        "git_url": "https://github.com/test/repo1",
                        "start_commit_sha": "abc123"
                    }
                }
            })(),
            type("Result", (), {
                "slug": "task2",
                "oneshot_meta": {
                    "repo": {
                        "git_url": "https://github.com/test/repo2",
                        "start_commit_sha": "def456"
                    }
                }
            })(),
        ]
        
        write_registry(dataset_dir, "test-dataset", results, version="1.0")
        registry_path = dataset_dir / "registry.json"
        assert registry_path.exists()
        
        registry = json.loads(registry_path.read_text())
        assert registry["name"] == "test-dataset"
        assert registry["version"] == "1.0"
        assert len(registry["tasks"]) == 2
        assert registry["tasks"][0]["name"] == "task1"
        assert registry["tasks"][0]["path"] == "tasks/task1"
