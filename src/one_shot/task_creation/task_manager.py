import os
import json
import logging
import tempfile
import platform
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from .git import GitHelpers
from .traces import TraceExporter
from .readiness import WorktreeReadiness
from one_shot.sensitivity import ensure_task_sensitivity, SensitivityLevel

logger = logging.getLogger(__name__)


def _get_state_file_path() -> Path:
    """Get user-specific state file path.
    
    Uses ~/.oneshot/state.json if writable, otherwise falls back to temp directory.
    """
    state_dir = Path.home() / ".oneshot"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir / "state.json"
    except (OSError, PermissionError):
        # Fall back to temp directory if home directory is not writable
        return Path(tempfile.gettempdir()) / "oneshot_state.json"


class OneShotTaskManager:
    """Manages OneShot task creation"""

    def __init__(self, base_dir: Path | None = None, tasks_dir: Path | None = None):
        self.state_file = _get_state_file_path()
        # Allow custom base_dir (for pair programming mode where cwd is temp workspace)
        if base_dir is None:
            # Check environment variable for custom base directory
            env_base_dir = os.environ.get('ONESHOT_BASE_DIR')
            if env_base_dir:
                self.base_dir = Path(env_base_dir).resolve()
            else:
                self.base_dir = Path.cwd()
        else:
            self.base_dir = Path(base_dir).resolve()
        
        # Allow custom tasks_dir (for specifying exact save location)
        if tasks_dir is None:
            # Check environment variable for custom tasks directory
            env_tasks_dir = os.environ.get('ONESHOT_TASKS_DIR')
            if env_tasks_dir:
                self.tasks_dir = Path(env_tasks_dir).resolve()
            else:
                self.tasks_dir = self.base_dir / 'data' / 'tasks' / 'created'
        else:
            self.tasks_dir = Path(tasks_dir).resolve()

    def generate_task_slug(self, title: str) -> str:
        import re
        slug = re.sub(r'[^\w\s-]', '', title.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        slug = slug.strip('-')[:50]
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"{slug}_{timestamp}"

    def start_task(self, task_title: str, notes: str = "", labels: List[str] = None) -> Dict[str, Any]:
        readiness = WorktreeReadiness.check_readiness()
        if not readiness['ok']:
            return {"ok": False, "code": "NOT_READY", "message": "Worktree not ready", "details": readiness}

        GitHelpers.stage_all()
        start_commit = GitHelpers.commit(f"OneShot start: {task_title}")
        if not start_commit:
            start_commit = GitHelpers.get_head_sha()

        branch = GitHelpers.get_current_branch()
        remote_urls = GitHelpers.get_remote_urls()

        task_slug = self.generate_task_slug(task_title)

        state = {
            "task_slug": task_slug,
            "task_title": task_title,
            "notes": notes,
            "labels": labels or [],
            "start_commit": start_commit,
            "branch": branch,
            "remote_urls": remote_urls,
            "started_at": datetime.now().isoformat(),
            "cwd": str(self.base_dir),
            "run_id": os.environ.get('RUN_ID', task_slug),
        }

        # Write state file with file locking to prevent concurrent access issues
        try:
            with open(self.state_file, 'w') as f:
                # Try to acquire lock (non-blocking)
                try:
                    if platform.system() != "Windows":
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    # On Windows, file locking is handled automatically by open()
                except (ImportError, OSError):
                    # If locking fails, continue anyway (better than crashing)
                    pass
                json.dump(state, f, indent=2)
        except (OSError, IOError) as e:
            logger.error(f"Failed to write state file: {e}")
            raise

        logger.info(f"Started task: {task_slug}")
        return {"ok": True, "task_slug": task_slug, "start_commit": start_commit, "started_at": state['started_at']}

    def end_task(self, summary: str, labels: List[str] = None) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {"ok": False, "code": "NO_STATE", "message": "No task in progress (state file not found)"}

        # Read state file with file locking to prevent concurrent access issues
        try:
            with open(self.state_file, 'r') as f:
                # Try to acquire lock (non-blocking)
                try:
                    if platform.system() != "Windows":
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                    # On Windows, file locking is handled automatically by open()
                except (ImportError, OSError):
                    # If locking fails, continue anyway (better than crashing)
                    pass
                state = json.load(f)
        except (OSError, IOError) as e:
            logger.error(f"Failed to read state file: {e}")
            return {"ok": False, "code": "STATE_READ_ERROR", "message": f"Could not read state file: {e}"}

        GitHelpers.stage_all()
        touched_files = GitHelpers.get_touched_files(state['start_commit'])
        diff = GitHelpers.get_diff(state['start_commit']) or GitHelpers.get_staged_diff()
        end_commit = GitHelpers.commit(f"OneShot end: {summary}") or GitHelpers.get_head_sha()

        trace_data = TraceExporter.export_session(
            run_id=state.get('run_id'),
            start_time=datetime.fromisoformat(state['started_at']),
            end_time=datetime.now(),
        )

        task_dir = self.tasks_dir / state['task_slug']
        task_dir.mkdir(parents=True, exist_ok=True)
        trace_dir = task_dir / 'trace'
        trace_dir.mkdir(exist_ok=True)
        eval_dir = task_dir / 'evaluation'
        eval_dir.mkdir(exist_ok=True)
        tests_dir = eval_dir / 'tests_skeleton'
        tests_dir.mkdir(exist_ok=True)

        tb_meta = {
            "task_id": state['task_slug'],
            "metadata": {
                "title": state['task_title'],
                "tags": list(set((state.get('labels', []) or []) + (labels or [])))
            },
            "repo": {
                "git_url": state['remote_urls'][0] if state['remote_urls'] else "",
                "branch": state['branch'],
                "start_commit_sha": state['start_commit'],
                "end_commit_sha": end_commit,
                "subdir": "",
                "sparse_checkout": []
            },
            "lm": {"instructions": state.get('notes', '')},
            "evaluation": {"content_rubric": [], "location_rubric": [], "clarity_rubric": []}
        }

        gh_pat = os.environ.get("PRIVATE_GITHUB_PAT") or os.environ.get("GH_PAT")
        sensitivity_level = ensure_task_sensitivity(tb_meta, token=gh_pat)
        if sensitivity_level == SensitivityLevel.SENSITIVE:
            meta_tags = tb_meta.setdefault("metadata", {}).setdefault("tags", [])
            if "sensitive" not in meta_tags:
                meta_tags.append("sensitive")

        with open(task_dir / 'tb_meta.json', 'w') as f:
            json.dump(tb_meta, f, indent=2)

        with open(task_dir / 'LM_INSTRUCTIONS.md', 'w') as f:
            f.write(f"# Task: {state['task_title']}\n\n")
            f.write(state.get('notes', ''))

        repo_info = {
            "remote_urls": state['remote_urls'],
            "branch": state['branch'],
            "start_commit": state['start_commit'],
            "end_commit": end_commit,
            "touched_files": touched_files,
        }
        with open(task_dir / 'repo_info.json', 'w') as f:
            json.dump(repo_info, f, indent=2)

        with open(task_dir / 'diff.patch', 'w') as f:
            f.write(diff)

        with open(trace_dir / 'session_id.txt', 'w') as f:
            f.write(trace_data.get('session_id', state.get('run_id', 'unknown')))

        with open(trace_dir / 'session_clean.json', 'w') as f:
            json.dump(trace_data, f, indent=2)

        with open(eval_dir / 'rubric_template.md', 'w') as f:
            f.write("# Evaluation Rubric\n\n")
            f.write("## Content\n- [ ] TODO: Assess content correctness\n\n")
            f.write("## Location\n- [ ] TODO: Assess file location appropriateness\n\n")
            f.write("## Clarity\n- [ ] TODO: Assess code clarity and style\n\n")

        with open(tests_dir / 'test_skeleton.py', 'w') as f:
            f.write("import pytest\n\n")
            safe_name = state['task_slug'].replace('-', '_')
            f.write(f"def test_{safe_name}():\n")
            f.write("    pass\n")

        with open(task_dir / 'notes.md', 'w') as f:
            f.write(f"# Task Notes: {state['task_title']}\n\n")
            f.write(f"Created: {state['started_at']}\n")
            f.write(f"Completed: {datetime.now().isoformat()}\n\n")
            f.write(f"## Summary\n{summary}\n\n")
            f.write("## Files Changed\n")
            for file in touched_files:
                f.write(f"- {file}\n")
            f.write("\n## TODO\n")
            f.write("- [ ] Review diff.patch\n")
            f.write("- [ ] Fill out evaluation rubric\n")
            f.write("- [ ] Implement tests\n")
            f.write("- [ ] Validate trace data\n")

        self.state_file.unlink()
        logger.info(f"Ended task: {state['task_slug']}")
        return {
            "ok": True,
            "task_dir": str(task_dir),
            "diff_bytes": len(diff),
            "touched_files": touched_files,
            "clean_trace_path": str(trace_dir / 'session_clean.json'),
        }
