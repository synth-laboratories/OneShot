#!/usr/bin/env python3
"""
Get agent performance score for a run.

Usage:
    python get_score.py <run_dir> [<task_dir>]
    
If task_dir is not provided, tries to infer from run_dir.
Outputs just the score as a float (0.0 to 1.0).
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _validate_path(path: Path, name: str, must_exist: bool = True) -> bool:
    """Validate a path before using it in subprocess calls.
    
    Args:
        path: Path to validate
        name: Name of the path for error messages
        must_exist: Whether the path must exist
        
    Returns:
        True if valid, False otherwise
    """
    try:
        resolved = path.resolve()
        # Check for path traversal attempts
        if ".." in str(resolved):
            logger.warning(f"{name} contains '..' - potential path traversal attempt: {path}")
            return False
        if must_exist and not resolved.exists():
            logger.warning(f"{name} does not exist: {path}")
            return False
        return True
    except (OSError, ValueError) as e:
        logger.warning(f"Invalid {name} path: {path} - {e}")
        return False


def get_score(run_dir: Path, task_dir: Optional[Path] = None) -> Optional[float]:
    """Get the evaluation score for a run."""
    # Validate run_dir
    if not _validate_path(run_dir, "run_dir", must_exist=True):
        return None
    
    # Check for evaluation_results.json first
    eval_file = run_dir / "evaluation_results.json"
    if eval_file.exists():
        try:
            with open(eval_file) as f:
                data = json.load(f)
            score = data.get("evaluation", {}).get("total_score")
            if score is not None:
                return float(score)
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
            logger.debug(f"Failed to read {eval_file}: {e}")
            pass
    
    # Check for tb_evaluation_results.json in artifacts
    tb_eval_file = run_dir / "artifacts" / "tb_evaluation_results.json"
    if tb_eval_file.exists():
        try:
            with open(tb_eval_file) as f:
                data = json.load(f)
            score = data.get("evaluation", {}).get("total_score")
            if score is not None:
                return float(score)
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
            logger.debug(f"Failed to read {tb_eval_file}: {e}")
            pass
    
    # If no evaluation file exists, run evaluation
    if task_dir is None:
        # Try to infer task_dir from run metadata
        results_file = run_dir / "results.json"
        if results_file.exists():
            try:
                with open(results_file) as f:
                    results = json.load(f)
                task_path = results.get("task_dir")
                if task_path:
                    task_dir = Path(task_path)
            except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
                logger.debug(f"Failed to read {results_file}: {e}")
                pass
    
    if task_dir:
        # Validate task_dir before using in subprocess
        if not _validate_path(task_dir, "task_dir", must_exist=True):
            return None
        
        # Run evaluation
        eval_script = Path(__file__).parent.parent / "src" / "one_shot" / "evaluate_run.py"
        if not _validate_path(eval_script, "eval_script", must_exist=True):
            logger.warning(f"Evaluation script not found: {eval_script}")
            return None
        
        try:
            result = subprocess.run(
                [sys.executable, str(eval_script), str(run_dir), str(task_dir)],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout to prevent hangs
            )
            if result.returncode == 0:
                # Try to read the score again
                return get_score(run_dir)
            else:
                logger.warning(
                    f"Evaluation script failed with return code {result.returncode}. "
                    f"stderr: {result.stderr[:200]}"
                )
        except subprocess.TimeoutExpired:
            logger.error("Evaluation script timed out after 5 minutes")
            return None
        except Exception as e:
            logger.error(f"Failed to run evaluation script: {e}")
            return None
    
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python get_score.py <run_dir> [<task_dir>]", file=sys.stderr)
        sys.exit(1)
    
    run_dir = Path(sys.argv[1])
    task_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    
    if not run_dir.exists():
        print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
        sys.exit(1)
    
    score = get_score(run_dir, task_dir)
    
    if score is None:
        print("Error: Could not determine score. Run evaluation first.", file=sys.stderr)
        sys.exit(1)
    
    # Output just the float score
    print(f"{score:.4f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)  # Only show warnings/errors
    main()

