#!/usr/bin/env python3
"""
Run Codex on re-bench tasks with multiple seeds and evaluate results.

This script orchestrates running Codex on re-bench tasks (like banking77) across
multiple seeds, evaluates each run, and optionally runs baseline comparisons.

Usage:
    python scripts/run_re_bench.py \
        --task re-bench-banking77 \
        --num-seeds 10 \
        --model gpt-5-nano \
        [--run-baseline-comparison] \
        [--skip-eval] \
        [--output-dir data/runs/banking77_batch_20250101]

Example:
    # Run 10 seeds of banking77 with baseline comparison
    python scripts/run_re_bench.py \
        --task re-bench-banking77 \
        --num-seeds 10 \
        --run-baseline-comparison
"""

import argparse
import json
import os
import subprocess
import sys
import time
import shutil
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

# Force unbuffered output
os.environ['PYTHONUNBUFFERED'] = '1'

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except AttributeError:
    # Python < 3.7, use flush=True everywhere
    pass

# Print immediately to verify script is running
print("=" * 60, flush=True)
print("ONESHOT REBENCH SCRIPT STARTING", flush=True)
print("=" * 60, flush=True)
print(f"Python: {sys.executable}", flush=True)
print(f"Script: {__file__}", flush=True)
print(f"Args: {sys.argv}", flush=True)
print(f"PYTHONUNBUFFERED: {os.environ.get('PYTHONUNBUFFERED', 'not set')}", flush=True)
print("=" * 60, flush=True)

# Try to import tomllib (Python 3.11+) or tomli
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# Add src to path for imports
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def load_toml_file(path: Path) -> Dict[str, Any]:
    """Load a TOML file and return its contents."""
    if not tomllib:
        raise RuntimeError("tomllib or tomli is required to load TOML files")
    with open(path, "rb") as file:
        return tomllib.load(file)


def ensure_list(value: Optional[Any]) -> List[str]:
    """Ensure a configuration value is a list of strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def run_shell_command(command: str, *, cwd: Path, env: Optional[Dict[str, str]] = None, timeout: Optional[int] = None) -> None:
    """Run a shell command with pretty logging."""
    print(f"  → Executing: {command}")
    print(f"  → Working directory: {cwd}")
    if timeout:
        print(f"  ⏱️  Timeout: {timeout}s")
    sys.stdout.flush()
    try:
        # Don't capture output for interactive commands - let them print directly
        result = subprocess.run(
            command, 
            shell=True, 
            check=True, 
            cwd=str(cwd), 
            env=env,
            timeout=timeout,
        )
        return result
    except subprocess.TimeoutExpired:
        print(f"  ❌ Command timed out after {timeout}s")
        sys.stdout.flush()
        raise
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Command failed with exit code {e.returncode}")
        sys.stdout.flush()
        raise


def run_codex_on_task(
    task_dir: Path,
    seed: int,
    model: Optional[str] = None,
    codex_config: Optional[Path] = None,
    verbose: bool = False,
) -> Path:
    """Run codex on a single task instance, return run_dir.
    
    Args:
        task_dir: Path to prepared task directory
        seed: Seed number for this run
        model: Model to use (optional, can be set via codex config)
        codex_config: Path to codex config file (optional)
        verbose: Whether to show verbose output
        
    Returns:
        Path to run directory created
    """
    # Generate unique run ID with seed
    timestamp = time.strftime("%Y%m%d__%H-%M-%S")
    run_id = f"{timestamp}_seed{seed:03d}"
    run_dir = REPO_ROOT / "data" / "runs" / run_id
    
    print(f"\n{'='*60}")
    print(f"Running Codex: Seed {seed}")
    print(f"Run ID: {run_id}")
    print(f"{'='*60}")
    
    # Prepare environment
    env = os.environ.copy()
    env["RUN_ID"] = run_id
    
    if model:
        env["OPENAI_MODEL"] = model
    
    # Set codex config if provided
    if codex_config and codex_config.exists():
        codex_home = codex_config.parent
        env["CODEX_HOME_DIR"] = str(codex_home)
        print(f"Using Codex config: {codex_config}")
    
    # Run codex box script
    run_script = REPO_ROOT / "scripts" / "run_codex_box.sh"
    if not run_script.exists():
        raise FileNotFoundError(f"Codex run script not found: {run_script}")
    
    cmd = [str(run_script), str(task_dir)]
    
    if verbose:
        print(f"Command: {' '.join(cmd)}")
        print(f"Environment: RUN_ID={run_id}, OPENAI_MODEL={model or 'default'}")
    
    result = subprocess.run(
        cmd,
        env=env,
        cwd=REPO_ROOT,
        capture_output=not verbose,
        text=True,
    )
    
    if result.returncode != 0:
        print(f"❌ Codex run failed for seed {seed}")
        if not verbose and result.stderr:
            print("Error output:")
            print(result.stderr[-500:])  # Last 500 chars
        raise RuntimeError(f"Codex run failed for seed {seed}: {result.returncode}")
    
    if not run_dir.exists():
        raise RuntimeError(f"Run directory not created: {run_dir}")
    
    print(f"✅ Codex run completed: {run_dir}")
    return run_dir


def evaluate_run_directory(
    run_dir: Path,
    task_dir: Path,
    skip_if_exists: bool = True,
) -> Optional[Dict[str, Any]]:
    """Evaluate a run directory.
    
    Args:
        run_dir: Path to run directory
        task_dir: Path to prepared task directory
        skip_if_exists: Skip evaluation if results already exist
        
    Returns:
        Evaluation results dict or None if skipped/failed
    """
    eval_file = run_dir / "evaluation_results.json"
    
    if skip_if_exists and eval_file.exists():
        print("  ⏭️  Evaluation already exists, skipping")
        try:
            with open(eval_file) as f:
                return json.load(f)
        except Exception as e:
            print(f"  ⚠️  Could not load existing evaluation: {e}")
    
    print("  📊 Running evaluation...")
    
    # Call evaluate_run module directly (works even if shell script isn't executable)
    result = subprocess.run(
        [sys.executable, "-m", "one_shot.evaluate_run", str(run_dir), str(task_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    
    if result.returncode != 0:
        print(f"  ❌ Evaluation failed (exit code: {result.returncode})")
        if result.stderr:
            print(f"  Error: {result.stderr[-300:]}")
        return None
    
    # Load results
    if eval_file.exists():
        try:
            with open(eval_file) as f:
                return json.load(f)
        except Exception as e:
            print(f"  ⚠️  Could not load evaluation results: {e}")
            return None
    else:
        print("  ⚠️  Evaluation completed but no results file found")
        return None


def run_baseline_comparison(
    run_dir: Path,
    verbose: bool = False,
    num_seeds: int = 10,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Run baseline comparison for a re-bench run.
    
    Args:
        run_dir: Path to run directory
        verbose: Whether to show verbose output
        num_seeds: Number of seeds for baseline evaluation
        model: Model to use for baseline (None = use task default)
        
    Returns:
        Comparison results dict or None if failed
    """
    print("  🔄 Running baseline comparison...")
    
    compare_script = REPO_ROOT / "scripts" / "re_bench_compare.py"
    if not compare_script.exists():
        print(f"  ⚠️  Baseline comparison script not found: {compare_script}")
        return None
    
    cmd = [sys.executable, str(compare_script), str(run_dir)]
    if verbose:
        cmd.append("--verbose")
    if num_seeds != 10:
        cmd.extend(["--num-seeds", str(num_seeds)])
    if model:
        cmd.extend(["--model", model])
    
    result = subprocess.run(
        cmd,
        capture_output=not verbose,
        text=True,
    )
    
    if result.returncode != 0:
        print("  ❌ Baseline comparison failed")
        if not verbose and result.stderr:
            print(f"  Error: {result.stderr[-200:]}")
        return None
    
    # Load comparison results
    comparison_file = run_dir / "re_bench_comparison.json"
    if comparison_file.exists():
        try:
            with open(comparison_file) as f:
                return json.load(f)
        except Exception as e:
            print(f"  ⚠️  Could not load comparison results: {e}")
            return None
    else:
        print("  ⚠️  Comparison completed but no results file found")
        return None


def run_single_seed(
    task_dir: Path,
    seed: int,
    model: Optional[str],
    codex_config: Optional[Path],
    run_baseline_comparison: bool,
    skip_eval: bool,
    verbose: bool,
    baseline_num_seeds: int,
    baseline_model: Optional[str],
) -> Dict[str, Any]:
    """Run a single seed (Codex + evaluation + optional baseline comparison).
    
    Returns:
        Run info dict
    """
    seed_start = time.time()
    try:
        # Run codex
        run_dir = run_codex_on_task(
            task_dir,
            seed,
            model=model,
            codex_config=codex_config,
            verbose=verbose,
        )
        
        # Evaluate
        eval_results = evaluate_run_directory(
            run_dir,
            task_dir,
            skip_if_exists=skip_eval,
        )
        
        # Baseline comparison (optional)
        comparison_results = None
        if run_baseline_comparison:
            comparison_results = run_baseline_comparison(
                run_dir,
                verbose=verbose,
                num_seeds=baseline_num_seeds,
                model=baseline_model,
            )
        
        seed_elapsed = time.time() - seed_start
        
        run_info = {
            "run_dir": str(run_dir.relative_to(REPO_ROOT)),
            "run_id": run_dir.name,
            "seed": seed,
            "status": "completed",
            "elapsed_seconds": seed_elapsed,
            "evaluation": eval_results,
            "baseline_comparison": comparison_results,
        }
        
        # Extract scores
        if eval_results:
            eval_data = eval_results.get("evaluation", {})
            run_info["evaluation_score"] = eval_data.get("total_score")
            run_info["lm_score"] = eval_results.get("lm_evaluation", {}).get("weighted_score")
        
        if comparison_results:
            combined = comparison_results.get("combined_scores", {})
            run_info["combined_score"] = combined.get("combined_score")
            run_info["baseline_delta"] = combined.get("baseline_delta")
        
        print(f"✅ Seed {seed} completed in {seed_elapsed:.1f}s")
        return run_info
        
    except Exception as e:
        seed_elapsed = time.time() - seed_start
        print(f"❌ Seed {seed} failed: {e}")
        return {
            "seed": seed,
            "status": "failed",
            "error": str(e),
            "elapsed_seconds": seed_elapsed,
        }


def run_single_config(
    task: str,
    num_seeds: int = 1,
    model: Optional[str] = None,
    codex_config: Optional[Path] = None,
    run_baseline_comparison: bool = False,
    skip_eval: bool = False,
    output_dir: Optional[Path] = None,
    verbose: bool = False,
    baseline_num_seeds: int = 10,
    baseline_model: Optional[str] = None,
    max_concurrent: int = 2,
) -> Dict[str, Any]:
    """Run a single configuration (one task with multiple seeds).
    
    Returns:
        Dict with 'summary' and 'runs' keys
    """
    # Resolve task directory
    task_dir = Path(task)
    if not task_dir.is_absolute():
        # Try as task name first
        task_dir = REPO_ROOT / "data" / "tasks" / "prepared" / task
        if not task_dir.exists():
            # Try as relative path
            task_dir = REPO_ROOT / task
    
    if not task_dir.exists():
        raise FileNotFoundError(f"Task directory not found: {task}")
    
    print(f"Task directory: {task_dir}")
    
    # Generate batch ID
    batch_id = f"{task_dir.name}_{time.strftime('%Y%m%d_%H%M%S')}"
    
    # Determine output directory
    if output_dir:
        output_dir = Path(output_dir)
    else:
        output_dir = REPO_ROOT / "data" / "runs" / batch_id
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Batch ID: {batch_id}")
    print(f"Output directory: {output_dir}")
    print(f"Number of seeds: {num_seeds}")
    print(f"Max concurrent: {max_concurrent}")
    if run_baseline_comparison:
        print("Baseline comparison: ENABLED")
        print(f"  Baseline seeds: {baseline_num_seeds}")
        if baseline_model:
            print(f"  Baseline model: {baseline_model}")
    print()
    
    # Run seeds concurrently
    runs = []
    start_time = time.time()
    
    if max_concurrent > 1 and num_seeds > 1:
        # Use ThreadPoolExecutor for concurrent execution
        print(f"Running {num_seeds} seeds with max {max_concurrent} concurrent...")
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(
                    run_single_seed,
                    task_dir,
                    seed,
                    model,
                    codex_config,
                    run_baseline_comparison,
                    skip_eval,
                    verbose,
                    baseline_num_seeds,
                    baseline_model,
                ): seed
                for seed in range(num_seeds)
            }
            
            for future in as_completed(futures):
                seed = futures[future]
                try:
                    run_info = future.result()
                    runs.append(run_info)
                except Exception as e:
                    print(f"❌ Seed {seed} failed with exception: {e}")
                    runs.append({
                        "seed": seed,
                        "status": "failed",
                        "error": str(e),
                    })
    else:
        # Sequential execution
        for seed in range(num_seeds):
            run_info = run_single_seed(
                task_dir,
                seed,
                model,
                codex_config,
                run_baseline_comparison,
                skip_eval,
                verbose,
                baseline_num_seeds,
                baseline_model,
            )
            runs.append(run_info)
    
    total_elapsed = time.time() - start_time
    
    # Calculate summary
    completed = [r for r in runs if r.get("status") == "completed"]
    failed = [r for r in runs if r.get("status") == "failed"]
    
    eval_scores = [r.get("evaluation_score") for r in completed if r.get("evaluation_score") is not None]
    combined_scores = [r.get("combined_score") for r in completed if r.get("combined_score") is not None]
    
    summary = {
        "batch_id": batch_id,
        "task": str(task_dir.relative_to(REPO_ROOT)),
        "model": model,
        "num_seeds": num_seeds,
        "completed": len(completed),
        "failed": len(failed),
        "mean_evaluation_score": sum(eval_scores) / len(eval_scores) if eval_scores else None,
        "mean_combined_score": sum(combined_scores) / len(combined_scores) if combined_scores else None,
        "total_elapsed_seconds": total_elapsed,
    }
    
    # Save results
    results = {
        "summary": summary,
        "runs": runs,
    }
    
    results_file = output_dir / "batch_results.json"
    results_file.write_text(json.dumps(results, indent=2))
    
    runs_file = output_dir / "runs.txt"
    run_dirs = [r["run_dir"] for r in completed if "run_dir" in r]
    runs_file.write_text("\n".join(run_dirs) + "\n")
    
    # Print summary
    print(f"\n{'='*60}")
    print("BATCH SUMMARY")
    print(f"{'='*60}")
    print(f"Completed: {len(completed)}/{num_seeds}")
    print(f"Failed: {len(failed)}/{num_seeds}")
    if eval_scores:
        print(f"Mean Evaluation Score: {summary['mean_evaluation_score']:.3f}")
    if combined_scores:
        print(f"Mean Combined Score: {summary['mean_combined_score']:.3f}")
    print(f"Total Time: {total_elapsed/60:.1f} minutes")
    print(f"\nResults saved to: {results_file}")
    print(f"Run directories saved to: {runs_file}")
    print(f"{'='*60}")
    
    return results


def run_pair_programming(args: argparse.Namespace) -> None:
    """Run a pair programming session to create a new research bench datum."""
    print("=" * 60)
    print("PAIR PROGRAMMING MODE")
    print("=" * 60)
    sys.stdout.flush()
    
    if not args.config:
        print("Error: --config is required for pair programming")
        sys.exit(1)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = REPO_ROOT / config_path

    print(f"Loading config from: {config_path}")
    sys.stdout.flush()

    if not config_path.exists():
        print(f"Error: Pair programming config file not found: {args.config}")
        print(f"  Tried: {config_path}")
        sys.exit(1)

    print("Parsing TOML config...")
    sys.stdout.flush()
    try:
        config = load_toml_file(config_path)
        print("✓ Config loaded successfully")
    except Exception as exc:  # pragma: no cover - best effort parsing
        print(f"Error: Could not load pair programming config: {exc}")
        sys.exit(1)
    sys.stdout.flush()

    print("Extracting pair_programming section...")
    sys.stdout.flush()
    pair_cfg = config.get("pair_programming")
    if not isinstance(pair_cfg, dict):
        print("Error: Config must have a [pair_programming] section")
        sys.exit(1)
    print("✓ Found pair_programming section")
    sys.stdout.flush()

    def require_field(key: str) -> Any:
        value = pair_cfg.get(key)
        if not value:
            print(f"Error: [pair_programming] missing required field '{key}'")
            sys.exit(1)
        return value

    print("Extracting configuration fields...")
    sys.stdout.flush()
    repo_url = require_field("repo_url")
    repo_branch = pair_cfg.get("repo_branch")
    repo_commit = pair_cfg.get("repo_commit")
    repo_subdir = pair_cfg.get("repo_subdir", "")
    print(f"✓ Repo URL: {repo_url}")
    if repo_branch:
        print(f"✓ Branch: {repo_branch}")
    sys.stdout.flush()

    print("Creating workspace directory...")
    sys.stdout.flush()
    workspace_dir: Path
    base_created = False
    if getattr(args, "workspace_dir", None):
        workspace_dir = Path(args.workspace_dir).expanduser().resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ Using existing workspace: {workspace_dir}")
    else:
        workspace_base = pair_cfg.get("workspace_base_dir")
        if workspace_base:
            workspace_base = Path(workspace_base).expanduser()
            workspace_base.mkdir(parents=True, exist_ok=True)
        workspace_dir = Path(
            tempfile.mkdtemp(prefix="oneshot_pair_", dir=str(workspace_base) if workspace_base else None)
        )
        base_created = True
        print(f"✓ Created temporary workspace: {workspace_dir}")
    sys.stdout.flush()

    print(f"\n{'='*60}")
    print("PAIR PROGRAMMING SETUP")
    print(f"{'='*60}")
    print(f"Workspace directory: {workspace_dir}")
    start_time = time.time()
    sys.stdout.flush()
    
    repo_dir = workspace_dir / "repo"
    if repo_dir.exists() and any(repo_dir.iterdir()):
        print(f"Error: Repository directory already exists and is not empty: {repo_dir}")
        sys.exit(1)

    # Clone repository with shallow clone for speed
    clone_start = time.time()
    print(f"\n📦 Cloning repository (shallow clone): {repo_url}")
    sys.stdout.flush()
    clone_cmd = ["git", "clone", "--depth", "1", repo_url, str(repo_dir)]
    if repo_branch:
        clone_cmd.extend(["--branch", repo_branch])
    print(f"  → {' '.join(clone_cmd)}")
    print("  ⏱️  Timeout: 30s")
    sys.stdout.flush()
    try:
        subprocess.run(clone_cmd, check=True, timeout=30)
        clone_time = time.time() - clone_start
        print(f"  ✓ Clone complete ({clone_time:.1f}s)")
    except subprocess.TimeoutExpired:
        print("  ❌ Git clone timed out after 30s")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Git clone failed: {e}")
        sys.exit(1)
    sys.stdout.flush()

    # Checkout commit if provided (after shallow clone)
    if repo_commit:
        print(f"📌 Checking out commit: {repo_commit}")
        print("  ⏱️  Timeout: 30s")
        sys.stdout.flush()
        try:
            subprocess.run(
                ["git", "fetch", "--depth", "1", "origin", repo_commit],
                check=True,
                cwd=repo_dir,
                timeout=30
            )
            subprocess.run(
                ["git", "checkout", repo_commit],
                check=True,
                cwd=repo_dir,
                timeout=30
            )
        except subprocess.TimeoutExpired:
            print("  ❌ Git fetch/checkout timed out after 30s")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Git fetch/checkout failed: {e}")
            sys.exit(1)

    # Run installation commands
    install_commands = ensure_list(pair_cfg.get("install_commands"))
    if not install_commands:
        install_commands = ["uv pip install -e ."]

    env = os.environ.copy()
    install_start = time.time()
    print("\n🔧 Running installation commands...")
    print("  ⚠️  Each command has a 30s timeout")
    print("  💡 Tip: Installation can be slow (30-60s) - this is normal!")
    sys.stdout.flush()
    for i, command in enumerate(install_commands, 1):
        cmd_start = time.time()
        print(f"\n  [{i}/{len(install_commands)}] {command}")
        sys.stdout.flush()
        try:
            run_shell_command(command, cwd=repo_dir, env=env, timeout=30)
            cmd_time = time.time() - cmd_start
            print(f"  ✓ Command {i} complete ({cmd_time:.1f}s)")
        except subprocess.TimeoutExpired:
            cmd_time = time.time() - cmd_start
            print(f"  ❌ Command {i} timed out after 30s (elapsed: {cmd_time:.1f}s)")
            print(f"  💡 Tip: Try running manually: cd {repo_dir} && {command}")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            cmd_time = time.time() - cmd_start
            print(f"  ❌ Command {i} failed ({cmd_time:.1f}s): {e}")
            sys.exit(1)
        sys.stdout.flush()
    install_time = time.time() - install_start
    print(f"\n  ✓ All installation commands complete ({install_time:.1f}s total)")

    # Optional pre-commands (e.g., uv sync, data preparation)
    pre_commands = ensure_list(pair_cfg.get("pre_commands"))
    if pre_commands:
        print("\n⚙️  Running pre-commands...")
        sys.stdout.flush()
        for i, command in enumerate(pre_commands, 1):
            print(f"  [{i}/{len(pre_commands)}] {command}")
            sys.stdout.flush()
            try:
                run_shell_command(command, cwd=repo_dir, env=env, timeout=30)
            except subprocess.TimeoutExpired:
                print(f"  ⚠️  Pre-command {i} timed out (continuing anyway)")
            except subprocess.CalledProcessError as e:
                print(f"  ⚠️  Pre-command {i} failed (continuing anyway): {e}")
            sys.stdout.flush()
    
    total_setup_time = time.time() - start_time
    print(f"\n✓ Setup complete! Total time: {total_setup_time:.1f}s")
    sys.stdout.flush()

    working_dir = repo_dir / repo_subdir if repo_subdir else repo_dir
    if not working_dir.exists():
        print(f"Error: Working directory not found: {working_dir}")
        sys.exit(1)
    
    # Create .env file in temp workspace so synth-ai commands can find it
    # This allows baseline scripts and other synth-ai commands to access API keys
    env_file_in_workspace = working_dir / ".env"
    if env_file_in_workspace.exists():
        print("⚠️  Warning: .env already exists in workspace, not overwriting")
    else:
        # Write API keys to .env file in workspace
        env_vars_to_write = {}
        for key in ["SYNTH_API_KEY", "ENVIRONMENT_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
                    "BACKEND_BASE_URL", "SYNTH_BASE_URL"]:
            if key in env:
                env_vars_to_write[key] = env[key]
        
        if env_vars_to_write:
            with open(env_file_in_workspace, 'w') as f:
                f.write("# Environment variables for synth-ai commands\n")
                f.write("# Auto-generated by run_re_bench.py\n")
                f.write("# Copied from OneShot/.env or environment\n\n")
                for key, value in env_vars_to_write.items():
                    f.write(f"{key}={value}\n")
            print(f"✓ Created .env file in workspace: {env_file_in_workspace}")
            print(f"  Contains: {', '.join(env_vars_to_write.keys())}")
        else:
            print("⚠️  Warning: No API keys found to write to .env file")
            print("  Set SYNTH_API_KEY, ENVIRONMENT_API_KEY, GROQ_API_KEY in your environment")
            print("  Or specify env_file in TOML config to load from a file")
    
    # Verify .env file is accessible and contains required variables
    print("\n🔍 Verifying .env file in workspace...")
    if env_file_in_workspace.exists():
        print(f"  ✓ .env file exists: {env_file_in_workspace}")
        # Read and verify contents
        with open(env_file_in_workspace) as f:
            env_file_contents = f.read()
        required_vars = ["SYNTH_API_KEY", "ENVIRONMENT_API_KEY", "GROQ_API_KEY"]
        missing_in_file = []
        for var in required_vars:
            if f"{var}=" not in env_file_contents:
                missing_in_file.append(var)
        if missing_in_file:
            print(f"  ⚠️  Missing in .env file: {', '.join(missing_in_file)}")
        else:
            print("  ✓ .env file contains all required variables")
    else:
        print("  ❌ .env file not found in workspace!")
    
    # Run a verification check in the workspace to confirm env vars are accessible
    print("\n🔍 Running environment verification check in workspace...")
    verify_script = working_dir / ".verify_env.sh"
    with open(verify_script, 'w') as f:
        f.write("#!/bin/bash\n")
        f.write("# Verification script to check environment variables\n")
        f.write("set -e\n\n")
        f.write("# Load .env if it exists\n")
        f.write("if [ -f .env ]; then\n")
        f.write("    set -a\n")
        f.write("    source .env\n")
        f.write("    set +a\n")
        f.write("fi\n\n")
        f.write("# Check required variables\n")
        f.write("MISSING=0\n")
        f.write("for var in SYNTH_API_KEY ENVIRONMENT_API_KEY GROQ_API_KEY; do\n")
        f.write("    if [ -z \"${!var}\" ]; then\n")
        f.write("        echo \"❌ $var not set\"\n")
        f.write("        MISSING=1\n")
        f.write("    else\n")
        f.write("        echo \"✓ $var is set\"\n")
        f.write("    fi\n")
        f.write("done\n\n")
        f.write("if [ $MISSING -eq 0 ]; then\n")
        f.write("    echo \"✅ All required environment variables are set\"\n")
        f.write("    exit 0\n")
        f.write("else\n")
        f.write("    echo \"❌ Some environment variables are missing\"\n")
        f.write("    exit 1\n")
        f.write("fi\n")
    verify_script.chmod(0o755)
    
    # Run verification
    try:
        result = subprocess.run(
            ["bash", str(verify_script)],
            cwd=str(working_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=5
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
            print("⚠️  Environment verification failed - some variables may not be accessible")
        else:
            print("✓ Environment verification passed")
    except Exception as e:
        print(f"⚠️  Could not run verification check: {e}")
    
    # Also load .env file into env dict so it's available to codex-synth subprocess
    # This ensures environment variables are available both to synth-ai commands AND codex-synth
    if env_file_in_workspace.exists():
        print("✓ Loading .env file into environment for codex-synth...")
        with open(env_file_in_workspace) as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                # Only process KEY=VALUE lines
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        env[key] = value
                        if 'KEY' in key.upper() or 'SECRET' in key.upper() or 'TOKEN' in key.upper():
                            print(f"  ✓ Loaded {key}=*** into environment")
                        else:
                            print(f"  ✓ Loaded {key}={value} into environment")
    
    oneshot_script = REPO_ROOT / "scripts" / "create_tasks" / "run_codex_with_oneshot.sh"
    if not oneshot_script.exists():
        print(f"Error: Expected script not found: {oneshot_script}")
        sys.exit(1)

    # Check if codex-synth is available, install if missing
    print("Checking if codex-synth is available...", flush=True)
    codex_check = subprocess.run(
        ["which", "codex-synth"],
        capture_output=True,
        text=True
    )
    
    if codex_check.returncode != 0:
        print("⚠️  codex-synth not found in PATH. Attempting to install...", flush=True)
        
        # Check if npm is available
        npm_check = subprocess.run(["which", "npm"], capture_output=True, text=True)
        if npm_check.returncode != 0:
            print("❌ ERROR: npm not found. Cannot install codex-synth.")
            print("   Please install Node.js and npm first, or install codex-synth manually.")
            sys.exit(1)
        
        print("  → Installing @openai/codex globally via npm...", flush=True)
        try:
            subprocess.run(
                ["npm", "install", "-g", "@openai/codex"],
                check=True,
                capture_output=True,
                text=True
            )
            print("  ✓ Installed @openai/codex", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to install @openai/codex: {e}", flush=True)
            print("  Try manually: npm install -g @openai/codex", flush=True)
            sys.exit(1)
        
        # Check if codex CLI is now available
        codex_cli_check = subprocess.run(["which", "codex"], capture_output=True, text=True)
        if codex_cli_check.returncode != 0:
            print("  ❌ codex CLI not found after installation. Check npm PATH.", flush=True)
            sys.exit(1)
        
        # Install wrapper script
        bin_dir = Path.home() / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper = bin_dir / "codex-synth"
        
        wrapper_content = """#!/usr/bin/env bash
set -euo pipefail

# Minimal wrapper: delegate to codex CLI
exec codex "$@"
"""
        wrapper.write_text(wrapper_content)
        wrapper.chmod(0o755)
        
        print(f"  ✓ Installed wrapper: {wrapper}", flush=True)
        
        # Check if ~/.local/bin is in PATH
        current_path = os.environ.get("PATH", "")
        local_bin = str(bin_dir)
        if local_bin not in current_path:
            print(f"  ⚠️  WARNING: {local_bin} is not in your PATH", flush=True)
            print("  Add this to your ~/.zshrc or ~/.bashrc:", flush=True)
            print("     export PATH=\"$HOME/.local/bin:$PATH\"", flush=True)
            print("  For this session, adding to PATH...", flush=True)
            os.environ["PATH"] = f"{local_bin}:{current_path}"
        
        # Verify it's now available
        final_check = subprocess.run(["which", "codex-synth"], capture_output=True, text=True)
        if final_check.returncode != 0:
            print("  ❌ codex-synth still not found. Please add ~/.local/bin to PATH and try again.", flush=True)
            sys.exit(1)
        
        codex_path = final_check.stdout.strip()
        print(f"  ✓ codex-synth now available at: {codex_path}", flush=True)
    else:
        codex_path = codex_check.stdout.strip()
        print(f"✓ codex-synth found at: {codex_path}")
    sys.stdout.flush()

    print("\n✓ Setup complete!")
    print(f"  Repository: {repo_dir}")
    print(f"  Working dir: {working_dir}")
    print(f"  OneShot script: {oneshot_script}")
    sys.stdout.flush()

    script_args = [str(oneshot_script), "--workdir", str(working_dir)]
    if not pair_cfg.get("enable_tracing", True):
        script_args.append("--no-proxy")

    task_title = pair_cfg.get("task_title")
    if task_title:
        script_args.extend(["--title", task_title])
    task_description = pair_cfg.get("task_description")
    if task_description:
        script_args.extend(["--description", textwrap.dedent(task_description).strip()])

    codex_model = pair_cfg.get("codex_model") or "gpt-5-nano"
    env["OPENAI_MODEL"] = codex_model

    run_id = pair_cfg.get("run_id")
    if not run_id:
        run_id = f"pair_{datetime.now().strftime('%Y%m%d__%H-%M-%S')}"
    env["RUN_ID"] = run_id

    # Load environment variables from .env file
    # Default to OneShot/.env if not specified in TOML
    env_file_path = pair_cfg.get("env_file")
    if not env_file_path:
        # Default to OneShot repo's .env file
        default_env_file = REPO_ROOT / ".env"
        if default_env_file.exists():
            env_file_path = str(default_env_file)
            print(f"✓ Using default .env file: {default_env_file}")
    
    if env_file_path:
        env_file = Path(env_file_path).expanduser().resolve()
        if env_file.exists():
            print(f"✓ Loading environment variables from: {env_file}")
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith('#'):
                        continue
                    # Only process KEY=VALUE lines
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key:
                            env[key] = value
                            # Don't print sensitive values
                            if 'KEY' in key.upper() or 'SECRET' in key.upper() or 'TOKEN' in key.upper():
                                print(f"  ✓ Set {key}=***")
                            else:
                                print(f"  ✓ Set {key}={value}")
        else:
            print(f"⚠️  Warning: env_file specified but not found: {env_file}")
            print("  Falling back to environment variables from current shell")
    
    # Also copy environment variables from current process if they exist
    # (allows passing API keys via environment when running the script)
    for key in ["SYNTH_API_KEY", "ENVIRONMENT_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", 
                "BACKEND_BASE_URL", "SYNTH_BASE_URL"]:
        if key in os.environ:
            env[key] = os.environ[key]
            if 'KEY' in key or 'SECRET' in key or 'TOKEN' in key:
                print(f"✓ Using {key} from environment")
            else:
                print(f"✓ Using {key}={os.environ[key]} from environment")
    
    # Create .env file in temp workspace so synth-ai commands can find it
    # working_dir is defined later, so we need to compute it here
    working_dir = repo_dir / repo_subdir if repo_subdir else repo_dir
    env_file_in_workspace = working_dir / ".env"
    if env_file_in_workspace.exists():
        print("⚠️  Warning: .env already exists in workspace, not overwriting")
    else:
        # Write API keys to .env file in workspace
        env_vars_to_write = {}
        for key in ["SYNTH_API_KEY", "ENVIRONMENT_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
                    "BACKEND_BASE_URL", "SYNTH_BASE_URL"]:
            if key in env:
                env_vars_to_write[key] = env[key]
        
        if env_vars_to_write:
            with open(env_file_in_workspace, 'w') as f:
                f.write("# Environment variables for synth-ai commands\n")
                f.write("# Auto-generated by run_re_bench.py\n\n")
                for key, value in env_vars_to_write.items():
                    f.write(f"{key}={value}\n")
            print(f"✓ Created .env file in workspace: {env_file_in_workspace}")
            print(f"  Contains: {', '.join(env_vars_to_write.keys())}")

    # Set artifacts save path from TOML config (defaults to REPO_ROOT/data/tasks/created)
    artifacts_save_path = pair_cfg.get("artifacts_save_path")
    if artifacts_save_path:
        artifacts_path = Path(artifacts_save_path).expanduser().resolve()
        artifacts_path.mkdir(parents=True, exist_ok=True)
        env["ONESHOT_TASKS_DIR"] = str(artifacts_path)
        print(f"✓ Artifacts will be saved to: {artifacts_path}")
    else:
        # Default: save to REPO_ROOT (not temp workspace)
        created_dir = REPO_ROOT / "data" / "tasks" / "created"
        created_dir.mkdir(parents=True, exist_ok=True)
        env["ONESHOT_TASKS_DIR"] = str(created_dir)
        print(f"✓ Artifacts will be saved to: {created_dir} (default)")
    
    created_dir = Path(env["ONESHOT_TASKS_DIR"])
    created_dir.mkdir(parents=True, exist_ok=True)
    before_runs = {item.name for item in created_dir.iterdir() if item.is_dir()}

    print(f"\n{'='*60}")
    print("STARTING PAIR PROGRAMMING SESSION")
    print(f"{'='*60}")
    print(f"Repository: {repo_url}")
    if repo_branch:
        print(f"Branch: {repo_branch}")
    if repo_commit:
        print(f"Commit: {repo_commit}")
    print(f"Working directory: {working_dir}")
    print(f"Codex model: {codex_model}")
    print(f"Tracing: {'enabled' if pair_cfg.get('enable_tracing', True) else 'disabled'}")
    print(f"Run ID: {run_id}")
    
    # Verify critical environment variables are set
    missing_vars = []
    for var in ["SYNTH_API_KEY", "ENVIRONMENT_API_KEY", "GROQ_API_KEY"]:
        if var not in env or not env[var]:
            missing_vars.append(var)
    
    if missing_vars:
        print(f"\n⚠️  WARNING: Missing environment variables: {', '.join(missing_vars)}")
        print("  These are required for synth-ai commands (baseline, deploy, train)")
        print("  Set them in your shell before running, or specify env_file in TOML config")
        print("  Example: export GROQ_API_KEY=your_key")
        print("  The .env file will be created in the workspace, but these vars must be set first")
    else:
        print("\n✓ Environment variables configured: SYNTH_API_KEY, ENVIRONMENT_API_KEY, GROQ_API_KEY")
    
    # Create agent session from TOML config if session limits are specified
    session_id: Optional[str] = None
    session_config = config.get("session")
    if session_config and isinstance(session_config, dict):
        session_limit_cost = session_config.get("limit_cost_usd")
        session_limit_tokens = session_config.get("limit_tokens")
        session_limit_gpu_hours = session_config.get("limit_gpu_hours")
        
        if session_limit_cost or session_limit_tokens or session_limit_gpu_hours:
            print(f"\n{'='*60}")
            print("📊 CREATING AGENT SESSION WITH BUDGET LIMITS")
            print(f"{'='*60}")
            
            # Build limits list and display budget
            limits = []
            budget_parts = []
            if session_limit_cost:
                limits.append({
                    "limit_type": "hard",
                    "metric_type": "cost_usd",
                    "limit_value": float(session_limit_cost),
                })
                budget_parts.append(f"${session_limit_cost:.2f} cost")
            if session_limit_tokens:
                limits.append({
                    "limit_type": "hard",
                    "metric_type": "tokens",
                    "limit_value": float(session_limit_tokens),
                })
                budget_parts.append(f"{session_limit_tokens:,} tokens")
            if session_limit_gpu_hours:
                limits.append({
                    "limit_type": "hard",
                    "metric_type": "gpu_hours",
                    "limit_value": float(session_limit_gpu_hours),
                })
                budget_parts.append(f"{session_limit_gpu_hours:.2f} GPU hours")
            
            budget_str = " | ".join(budget_parts)
            print(f"💰 BUDGET: {budget_str}")
            print(f"{'='*60}\n")
            
            # Get backend URL and API key from environment
            backend_url = env.get("BACKEND_BASE_URL") or env.get("SYNTH_BASE_URL") or "http://localhost:8000"
            api_key = env.get("SYNTH_API_KEY")
            
            if not api_key:
                print("  ⚠️  Warning: SYNTH_API_KEY not set, skipping session creation")
            else:
                # Use uv run to ensure synth-ai SDK is available
                # Create a Python script to run with uv run
                create_session_script = """
import asyncio
import json
import sys
from synth_ai.session import AgentSessionClient

async def create_session():
    backend_url = sys.argv[1]
    api_key = sys.argv[2]
    limits_json = sys.argv[3]
    
    limits = json.loads(limits_json) if limits_json else []
    
    client = AgentSessionClient(f"{backend_url}/api", api_key)
    session = await client.create(
        org_id=None,  # Will be fetched from backend /me endpoint
        limits=limits,
        session_type="pair_programming",
    )
    print(session.session_id)

if __name__ == "__main__":
    asyncio.run(create_session())
"""
                
                try:
                    # Write script to temp file
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                        f.write(create_session_script)
                        script_path = f.name
                    
                    # Run with uv run from the cloned repo directory where synth-ai is installed
                    limits_json = json.dumps(limits)
                    
                    # First ensure synth-ai==0.2.23.dev1 is installed (with session support)
                    # Then run from repo_dir where synth-ai is installed via `uv pip install -e .`
                    result = subprocess.run(
                        ["uv", "run", "--with", "synth-ai==0.2.23.dev1", "python", script_path, backend_url, api_key, limits_json],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=str(repo_dir),  # Run from repo_dir where synth-ai is installed
                    )
                    
                    # Clean up temp file
                    try:
                        os.unlink(script_path)
                    except Exception:
                        pass
                    
                    if result.returncode == 0:
                        session_id = result.stdout.strip()
                        if session_id:
                            env["SYNTH_SESSION_ID"] = session_id
                            
                            print(f"{'='*60}")
                            print("✅ AGENT SESSION CREATED SUCCESSFULLY")
                            print(f"{'='*60}")
                            print(f"Session ID: {session_id}")
                            print(f"Budget: {budget_str}")
                            print("ℹ️  All synth-ai API calls will use this session automatically")
                            print("⚠️  Requests will be rejected if budget limit is exceeded")
                            print(f"{'='*60}\n")
                        else:
                            print("  ❌ Error: Session creation returned empty session ID")
                            print(f"    stderr: {result.stderr}")
                    else:
                        print("  ❌ Error: Failed to create agent session")
                        print(f"    Return code: {result.returncode}")
                        if result.stderr:
                            print(f"    stderr: {result.stderr}")
                        if result.stdout:
                            print(f"    stdout: {result.stdout}")
                        print("  ℹ️  Note: Session creation is optional. Continuing without budget tracking...")
                except subprocess.TimeoutExpired:
                    print("  ❌ Error: Session creation timed out after 30 seconds")
                    print("  ℹ️  Note: Session creation is optional. Continuing without budget tracking...")
                except FileNotFoundError:
                    print("  ❌ Error: 'uv' command not found. Please install uv.")
                except Exception as e:
                    print(f"  ❌ Error: Failed to create agent session: {e}")
                    print("  ℹ️  Note: Session creation is optional. Continuing without budget tracking...")
                    import traceback
                    traceback.print_exc()
    
    print(f"{'='*60}\n")
    print("📝 Instructions file and MCP tools will be configured automatically.")
    print("🤝 When the session starts, collaborate with Codex to improve the benchmark.")
    print("⚠️  IMPORTANT: Use repo.start_task.v1 at the beginning and repo.end_task.v1 when complete.\n")
    print(f"🚀 Launching codex-synth in: {working_dir}\n")
    print(f"Command: {' '.join(script_args)}\n")
    print("=" * 60)
    print("NOTE: codex-synth will launch in INTERACTIVE mode.")
    print("You should see the codex-synth prompt appear below.")
    print("If you don't see anything, codex-synth may not be installed or in PATH.")
    print("=" * 60 + "\n")
    sys.stdout.flush()
    sys.stderr.flush()

    try:
        # Launch codex-synth interactively - ensure proper terminal handling
        print(">>> LAUNCHING CODEX-SYNTH NOW <<<", flush=True)
        print(">>> The interactive TUI should appear below <<<", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        
        # For interactive TUI apps, we need direct terminal access
        # Check if we're in a TTY
        if sys.stdin.isatty() and sys.stdout.isatty():
            # We have a TTY, use it directly for full interactivity
            proc = subprocess.Popen(
                script_args,
                env=env,
                cwd=str(working_dir),
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            exit_code = proc.wait()
        else:
            # No TTY - warn but try anyway
            print("⚠️  Warning: Not running in a TTY. Interactive mode may be limited.", flush=True)
            print("   Try running directly in a terminal for full TUI support.", flush=True)
            proc = subprocess.Popen(
                script_args,
                env=env,
                cwd=str(working_dir),
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            exit_code = proc.wait()
        
        print(f"\n>>> codex-synth exited with code {exit_code} <<<", flush=True)
        if exit_code != 0:
            sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n>>> Interrupted by user <<<", flush=True)
        if 'proc' in locals():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Failed to launch codex-synth: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # End session if created
        if session_id:
            try:
                print(f"\n📊 Ending agent session: {session_id}...")
                import asyncio
                from synth_ai.session import AgentSessionClient
                
                backend_url = env.get("BACKEND_BASE_URL") or env.get("SYNTH_BASE_URL") or "http://localhost:8000"
                api_key = env.get("SYNTH_API_KEY")
                
                if api_key:
                    async def end_session():
                        client = AgentSessionClient(f"{backend_url}/api", api_key)
                        await client.end(session_id)
                    
                    asyncio.run(end_session())
                    print("  ✓ Session ended")
            except Exception as e:
                print(f"  ⚠️  Warning: Failed to end agent session: {e}")
    
    # Cleanup and final reporting
    after_runs = {item.name for item in created_dir.iterdir() if item.is_dir()}

    new_runs = sorted(after_runs - before_runs)
    if new_runs:
        print("\nNew tasks captured:")
        for run in new_runs:
            print(f"  - {run}")
        print("Artifacts located in data/tasks/created/<task_slug>/")
    else:
        print("\nNo new tasks detected. Ensure repo.start_task/end_task tools were used.")

    keep_workspace = pair_cfg.get("keep_workspace", True)
    if getattr(args, "cleanup", False):
        keep_workspace = False

    if keep_workspace or not base_created or getattr(args, "workspace_dir", None):
        print(f"\n✓ Workspace preserved at: {workspace_dir}")
    else:
        print(f"\n🧹 Cleaning up workspace at: {workspace_dir}")
        shutil.rmtree(workspace_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    print("NEXT STEPS")
    print("=" * 60)
    if new_runs:
        print(f"1. Review artifacts in: data/tasks/created/{new_runs[0]}/")
        print("2. Follow Phase 5 in research_bench/pair_programming.txt to bundle the datum")
        print("3. Create a prepared task under data/tasks/prepared/")
        print("4. Add an eval config to research_bench/eval_configs/")
    else:
        print("1. Ensure Codex called repo.start_task.v1 and repo.end_task.v1")
        print("2. Check data/tasks/created/ for any new tasks")
        print("3. If no tasks were created, review the session logs above")
    print("=" * 60)


def add_eval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to TOML config file (e.g., research_bench/eval_configs/banking77.toml)",
    )
    parser.add_argument(
        "--task",
        help="Task name (e.g., 're-bench-banking77') or path to task directory",
    )
    parser.add_argument(
        "--num-seeds",
        type=int,
        default=1,
        help="Number of seeds to run (default: 1)",
    )
    parser.add_argument(
        "--model",
        help="Model to use (e.g., 'gpt-5-nano'). Overrides codex config.",
    )
    parser.add_argument(
        "--codex-config",
        type=Path,
        help="Path to codex config directory (contains config.toml)",
    )
    parser.add_argument(
        "--run-baseline-comparison",
        action="store_true",
        help="Run baseline comparison for each run (can be slow)",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Skip evaluation if results already exist",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to save batch summary (default: data/runs/<batch_id>)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show verbose output",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=2,
        help="Maximum number of concurrent Docker runs (default: 2)",
    )


def run_evaluation(args: argparse.Namespace) -> None:
    config: Dict[str, Any] = {}
    config_runs: List[Dict[str, Any]] = []
    defaults: Dict[str, Any] = {}

    if args.config:
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = REPO_ROOT / config_path

        if not config_path.exists():
            print(f"Error: Config file not found: {args.config}")
            print(f"  Tried: {config_path}")
            sys.exit(1)

        try:
            config = load_toml_file(config_path)
            print(f"Loaded config from: {config_path}")
        except Exception as exc:  # pragma: no cover - best effort parsing
            print(f"Error: Could not load config file: {exc}")
            sys.exit(1)

        defaults = config.get("defaults", {})
        config_runs = config.get("runs", [])

        if not config_runs and config.get("task"):
            config_runs = [config]

    if config_runs:
        print(f"Found {len(config_runs)} run(s) in config")

        all_results: List[Dict[str, Any]] = []
        for idx, run_config in enumerate(config_runs):
            print(f"\n{'='*60}")
            print(f"Run {idx + 1}/{len(config_runs)}")
            print(f"{'='*60}")

            merged_config = {**defaults, **run_config}

            task = merged_config.get("task")
            num_seeds = merged_config.get("num_runs", 1)
            model = merged_config.get("model")
            codex_config = (
                Path(merged_config.get("codex_config", "~/.codex")).expanduser()
                if merged_config.get("codex_config")
                else None
            )
            run_baseline_comparison = merged_config.get("run_baseline_comparison", False)
            skip_eval = merged_config.get("skip_eval_if_exists", False)
            verbose = merged_config.get("verbose", False)
            baseline_num_seeds = merged_config.get("baseline_num_seeds", 10)
            baseline_model = merged_config.get("baseline_model")
            max_concurrent = merged_config.get("max_concurrent", 2)

            if args.task:
                task = args.task
            if args.num_seeds != 1:
                num_seeds = args.num_seeds
            if args.model:
                model = args.model
            if args.codex_config:
                codex_config = args.codex_config
            if args.run_baseline_comparison:
                run_baseline_comparison = True
            if args.skip_eval:
                skip_eval = True
            if args.verbose:
                verbose = True
            if args.max_concurrent != 2:
                max_concurrent = args.max_concurrent

            if not task:
                print(f"Error: Run {idx + 1} missing 'task' field")
                continue

            result = run_single_config(
                task=task,
                num_seeds=num_seeds,
                model=model,
                codex_config=codex_config,
                run_baseline_comparison=run_baseline_comparison,
                skip_eval=skip_eval,
                output_dir=args.output_dir,
                verbose=verbose,
                baseline_num_seeds=baseline_num_seeds,
                baseline_model=baseline_model,
                max_concurrent=max_concurrent,
            )
            all_results.append(result)

        print(f"\n{'='*60}")
        print("BATCH SUMMARY (All Runs)")
        print(f"{'='*60}")
        total_completed = sum(r["summary"]["completed"] for r in all_results)
        total_failed = sum(r["summary"]["failed"] for r in all_results)
        print(f"Total Runs: {len(all_results)}")
        print(f"Total Completed: {total_completed}")
        print(f"Total Failed: {total_failed}")

        if args.output_dir:
            output_dir = Path(args.output_dir)
        else:
            output_dir = REPO_ROOT / "data" / "runs" / f"batch_{time.strftime('%Y%m%d_%H%M%S')}"
        output_dir.mkdir(parents=True, exist_ok=True)

        combined_results = {
            "config_file": str(args.config) if args.config else None,
            "total_runs": len(all_results),
            "runs": all_results,
        }
        combined_file = output_dir / "combined_batch_results.json"
        combined_file.write_text(json.dumps(combined_results, indent=2))
        print(f"\nCombined results saved to: {combined_file}")

        return

    task = args.task or config.get("task")
    num_seeds = (
        args.num_seeds
        if args.num_seeds != 1 or "num_runs" not in config
        else config.get("num_runs", 1)
    )
    model = args.model or config.get("model")
    codex_config = args.codex_config or (
        Path(config.get("codex_config", "~/.codex")).expanduser()
        if config.get("codex_config")
        else None
    )
    run_baseline_comparison = args.run_baseline_comparison or config.get(
        "run_baseline_comparison", False
    )
    skip_eval = args.skip_eval or config.get("skip_eval_if_exists", False)
    output_dir = args.output_dir or (
        Path(config.get("output_dir")).expanduser()
        if config.get("output_dir")
        else None
    )
    verbose = args.verbose or config.get("verbose", False)
    baseline_num_seeds = config.get("baseline_num_seeds", 10)
    baseline_model = config.get("baseline_model")
    max_concurrent = (
        args.max_concurrent
        if args.max_concurrent != 2
        else config.get("max_concurrent", 2)
    )

    if not task:
        print("Error: --task is required (or provide --config with 'task' field or 'runs' array)")
        sys.exit(1)

    run_single_config(
        task=task,
        num_seeds=num_seeds,
        model=model,
        codex_config=codex_config,
        run_baseline_comparison=run_baseline_comparison,
        skip_eval=skip_eval,
        output_dir=output_dir,
        verbose=verbose,
        baseline_num_seeds=baseline_num_seeds,
        baseline_model=baseline_model,
        max_concurrent=max_concurrent,
    )


def main():
    print("\n" + "=" * 60, flush=True)
    print("PARSING ARGUMENTS", flush=True)
    print("=" * 60, flush=True)
    
    parser = argparse.ArgumentParser(
        description="Run Codex evaluation or launch pair programming sessions"
    )
    add_eval_arguments(parser)

    subparsers = parser.add_subparsers(dest="command")

    pair_parser = subparsers.add_parser(
        "pair",
        help="Start a pair programming session to create a new research bench datum",
    )
    pair_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to pair programming TOML config",
    )
    pair_parser.add_argument(
        "--workspace-dir",
        type=Path,
        help="Use an existing directory instead of creating a temporary workspace",
    )
    pair_parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the workspace after the session completes",
    )
    pair_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show verbose output",
    )

    eval_parser = subparsers.add_parser(
        "eval",
        help="Run re-bench evaluation (default)",
    )
    add_eval_arguments(eval_parser)

    print("Parsing command line arguments...", flush=True)
    args = parser.parse_args()
    print(f"Command: {args.command}", flush=True)
    print(f"Args: {vars(args)}", flush=True)
    print("=" * 60 + "\n", flush=True)

    if args.command == "pair":
        print(">>> CALLING run_pair_programming <<<", flush=True)
        run_pair_programming(args)
    else:
        print(">>> CALLING run_evaluation <<<", flush=True)
        run_evaluation(args)


if __name__ == "__main__":
    main()

