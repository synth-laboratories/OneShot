#!/usr/bin/env python3
"""
Re-bench Banking77: Compare baseline performance with/without agent patch.

Usage:
    python re_bench_compare.py <run_dir> [--output re_bench.txt]
"""

import argparse
import json
import os
import subprocess
import tempfile
import shutil
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


def extract_patch(run_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    """Extract patch from agent run artifacts. Returns (patch_content, patch_source)."""
    artifacts_dir = run_dir / "artifacts"
    
    # Check diff files in priority order
    diff_candidates = [
        ("diff.patch", "diff.patch"),
        ("container_git_diff_from_baseline.patch", "container_git_diff_from_baseline.patch"),
        ("container_git_diff.patch", "container_git_diff.patch"),
    ]
    
    for filename, source_name in diff_candidates:
        diff_path = artifacts_dir / filename
        if diff_path.exists() and diff_path.stat().st_size > 0:
            try:
                content = diff_path.read_text()
                # Validate it looks like a patch
                if content.strip() and (
                    content.startswith("diff --git") 
                    or content.startswith("---") 
                    or "+++" in content
                ):
                    return content, source_name
            except Exception as e:
                print(f"Warning: Could not read {diff_path}: {e}")
                continue
    
    return None, None


def get_baseline_sha(run_dir: Path) -> Optional[str]:
    """Get baseline commit SHA from artifacts."""
    baseline_sha_path = run_dir / "artifacts" / "baseline_sha.txt"
    if baseline_sha_path.exists():
        try:
            sha = baseline_sha_path.read_text().strip()
            if sha:
                return sha
        except Exception as e:
            print(f"Warning: Could not read baseline_sha.txt: {e}")
    return None


def setup_repo(repo_url: str, branch: str, baseline_sha: Optional[str] = None) -> Path:
    """Set up clean synth-ai repository in temp directory."""
    temp_dir = tempfile.mkdtemp(prefix="re_bench_repo_")
    repo_dir = Path(temp_dir)
    
    print(f"Cloning repository to {repo_dir}...")
    # Clone repo with full history if we need to checkout a specific SHA
    if baseline_sha:
        # Full clone to allow checking out specific SHA
        subprocess.run(
            ["git", "clone", "--branch", branch, repo_url, str(repo_dir)],
            check=True,
            capture_output=True,
        )
        print(f"Checking out baseline SHA: {baseline_sha}")
        result = subprocess.run(
            ["git", "checkout", baseline_sha],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Warning: Could not checkout {baseline_sha}: {result.stderr}")
            print("Trying to fetch the SHA...")
            # Try fetching the SHA first
            fetch_result = subprocess.run(
                ["git", "fetch", "origin", baseline_sha],
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
            if fetch_result.returncode == 0:
                result = subprocess.run(
                    ["git", "checkout", baseline_sha],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                )
            if result.returncode != 0:
                print(f"Warning: Could not checkout {baseline_sha} after fetch")
                print("Continuing with HEAD...")
    else:
        # Shallow clone is fine if no specific SHA needed
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(repo_dir)],
            check=True,
            capture_output=True,
        )
    
    return repo_dir


def run_baseline(
    repo_dir: Path, 
    output_file: Path, 
    split: str = "test",
    seeds: Optional[str] = None,
    model: Optional[str] = None,
    env_file: Optional[Path] = None,
    verbose: bool = False
) -> Dict[str, Any]:
    """Run banking77 baseline in Docker container and return results."""
    print(f"Running baseline on {split} split in Docker container...")
    if model:
        print(f"Using model: {model}")
    
    # Build Docker image with synth-ai
    # Use a stable image tag so we can reuse the image across runs
    image_tag = "re-bench-baseline:latest"
    
    # Check if image already exists
    check_result = subprocess.run(
        ["docker", "images", "-q", image_tag],
        capture_output=True,
        text=True,
    )
    
    if check_result.stdout.strip():
        print(f"Using existing Docker image: {image_tag}")
        need_build = False
    else:
        print(f"Building Docker image: {image_tag}")
        print("(This may take a few minutes on first run - installing Rust and synth-ai dependencies)")
        need_build = True
    
    # Create a temporary Dockerfile
    dockerfile_content = """FROM python:3.11-slim

RUN apt-get update && apt-get install -y \\
    git \\
    curl \\
    build-essential \\
    cmake \\
    && rm -rf /var/lib/apt/lists/*

# Install Rust (needed for some synth-ai dependencies)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install uv (which provides uvx)
RUN pip install --no-cache-dir uv

# Pre-install synth-ai to cache dependencies (much faster on subsequent runs)
# This downloads all dependencies once during build, not on every run
RUN uvx synth-ai --version || echo "synth-ai will be installed on first use"

WORKDIR /workspace
"""
    
    dockerfile_path = repo_dir / "Dockerfile.baseline"
    dockerfile_path.write_text(dockerfile_content)
    
    try:
        # Build image (only if it doesn't exist)
        if need_build:
            print("Building Docker image (this may take several minutes)...")
            build_result = subprocess.run(
                ["docker", "build", "-t", image_tag, "-f", str(dockerfile_path), str(repo_dir)],
                capture_output=not verbose,  # Show output if verbose
                text=True,
            )
            
            if build_result.returncode != 0:
                if verbose:
                    print(build_result.stderr)
                raise RuntimeError(f"Docker build failed:\n{build_result.stderr}")
            print("✅ Docker image built successfully")
        
        # Prepare command
        cmd = [
            "uvx", "synth-ai", "baseline", "banking77",
            "--split", split,
            "--output", "/output/results.json",
        ]
        
        if seeds:
            cmd.extend(["--seeds", seeds])
        
        if model:
            cmd.extend(["--model", model])
        
        if verbose:
            cmd.append("--verbose")
        
        # Prepare environment variables
        env_vars = {}
        
        # Load from env_file if provided
        if env_file and env_file.exists():
            try:
                from dotenv import dotenv_values
                env_vars.update({k: v for k, v in dotenv_values(env_file).items() if v})
            except ImportError:
                # Fallback: simple parsing
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            env_vars[key.strip()] = value.strip().strip('"').strip("'")
        
        # Add current environment variables (for API keys)
        for key in ["GROQ_API_KEY", "OPENAI_API_KEY", "SYNTH_API_KEY"]:
            if key in os.environ:
                env_vars[key] = os.environ[key]
        
        # Prepare Docker run command
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{repo_dir}:/workspace",
            "-v", f"{output_file.parent}:/output",
            "-w", "/workspace",
        ]
        
        # Add environment variables
        for key, value in env_vars.items():
            docker_cmd.extend(["-e", f"{key}={value}"])
        
        # Add image and command
        docker_cmd.append(image_tag)
        docker_cmd.extend(cmd)
        
        print(f"Running baseline in Docker container...")
        
        # Run in Docker
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
        )
        
        if result.returncode != 0:
            print("Docker run failed. Full output:")
            if result.stdout:
                print("STDOUT:", result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
            raise RuntimeError(
                f"Baseline run failed in Docker:\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}"
            )
        
        if verbose and result.stdout:
            print(result.stdout)
        
        # Load results
        results_path = output_file.parent / "results.json"
        if not results_path.exists():
            raise FileNotFoundError(f"Baseline output file not created: {results_path}")
        
        with open(results_path) as f:
            results = json.load(f)
        
        # Move results to expected location
        if results_path != output_file:
            output_file.write_text(json.dumps(results, indent=2))
            results_path.unlink()
        
        return results
        
    finally:
        # Cleanup Dockerfile
        if dockerfile_path.exists():
            dockerfile_path.unlink()
        
        # Optionally remove image (commented out to allow reuse)
        # subprocess.run(["docker", "rmi", image_tag], capture_output=True)


def apply_patch(repo_dir: Path, patch_content: str) -> bool:
    """Apply patch to repository. Returns True if successful."""
    print("Applying patch...")
    
    # Write patch to temp file
    patch_file = repo_dir / "agent.patch"
    patch_file.write_text(patch_content)
    
    # Try clean apply first
    result = subprocess.run(
        ["git", "apply", str(patch_file)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    
    if result.returncode == 0:
        print("Patch applied successfully (clean)")
        return True
    
    # Try 3-way merge
    print("Clean apply failed, trying 3-way merge...")
    result = subprocess.run(
        ["git", "apply", "--3way", str(patch_file)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    
    if result.returncode == 0:
        print("Patch applied successfully (3-way)")
        return True
    
    print(f"ERROR: Patch application failed:\n{result.stderr}")
    return False


def get_patch_summary(repo_dir: Path) -> Dict[str, Any]:
    """Get summary of changes in patch."""
    result = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        return {"files_changed": 0, "summary": "Unable to get patch summary"}
    
    # Parse git diff --stat output
    lines = result.stdout.strip().split("\n")
    if not lines:
        return {"files_changed": 0, "summary": "No changes"}
    
    # Last line has totals
    last_line = lines[-1]
    parts = last_line.split()
    
    files_changed = 0
    lines_added = 0
    lines_deleted = 0
    
    for line in lines[:-1]:
        if "|" in line:
            files_changed += 1
    
    if len(parts) >= 4:
        try:
            lines_added = int(parts[-2].replace("+", ""))
            lines_deleted = int(parts[-1].replace("-", ""))
        except (ValueError, IndexError):
            pass
    
    return {
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
        "summary": result.stdout.strip(),
    }


def compare_results(baseline: Dict, patched: Dict) -> Dict[str, Any]:
    """Compare baseline and patched results."""
    baseline_metrics = baseline.get("aggregate_metrics", {})
    patched_metrics = patched.get("aggregate_metrics", {})
    
    baseline_score = baseline_metrics.get("mean_outcome_reward", 0.0)
    patched_score = patched_metrics.get("mean_outcome_reward", 0.0)
    
    absolute_improvement = patched_score - baseline_score
    relative_lift = (absolute_improvement / baseline_score * 100) if baseline_score > 0 else 0.0
    
    # Per-seed comparison if available
    baseline_results = baseline.get("results", [])
    patched_results = patched.get("results", [])
    
    seeds_improved = 0
    seeds_regressed = 0
    seeds_unchanged = 0
    
    if baseline_results and patched_results:
        # Create lookup by seed
        baseline_by_seed = {r["seed"]: r.get("outcome_reward", 0.0) for r in baseline_results}
        patched_by_seed = {r["seed"]: r.get("outcome_reward", 0.0) for r in patched_results}
        
        all_seeds = set(baseline_by_seed.keys()) | set(patched_by_seed.keys())
        
        for seed in all_seeds:
            baseline_reward = baseline_by_seed.get(seed, 0.0)
            patched_reward = patched_by_seed.get(seed, 0.0)
            
            if patched_reward > baseline_reward:
                seeds_improved += 1
            elif patched_reward < baseline_reward:
                seeds_regressed += 1
            else:
                seeds_unchanged += 1
    
    # Determine status
    if relative_lift > 0.1:  # > 0.1% improvement
        status = "✅ Improvement"
    elif relative_lift < -0.1:  # > 0.1% regression
        status = "❌ Regression"
    else:
        status = "⚖️  No Change"
    
    return {
        "baseline_score": baseline_score,
        "patched_score": patched_score,
        "absolute_improvement": absolute_improvement,
        "relative_lift_percent": relative_lift,
        "status": status,
        "seeds_improved": seeds_improved,
        "seeds_regressed": seeds_regressed,
        "seeds_unchanged": seeds_unchanged,
        "baseline_metrics": baseline_metrics,
        "patched_metrics": patched_metrics,
    }


def generate_report(
    comparison: Dict[str, Any],
    run_dir: Path,
    patch_source: Optional[str],
    baseline_sha: Optional[str],
    patch_summary: Dict[str, Any],
) -> str:
    """Generate formatted report."""
    baseline_metrics = comparison["baseline_metrics"]
    patched_metrics = comparison["patched_metrics"]
    
    lines = []
    lines.append("=" * 60)
    lines.append("Re-Bench Banking77: Baseline Comparison Results")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Run ID: {run_dir.name}")
    lines.append(f"Task: re-bench-banking77")
    if patch_source:
        lines.append(f"Patch Source: {patch_source}")
    if baseline_sha:
        lines.append(f"Baseline SHA: {baseline_sha}")
    lines.append("")
    lines.append("BASELINE (Without Patch):")
    lines.append(f"  Mean Outcome Reward: {comparison['baseline_score']:.4f} ({comparison['baseline_score']*100:.2f}%)")
    lines.append(f"  Success Rate: {baseline_metrics.get('success_rate', 0.0)*100:.2f}%")
    lines.append(f"  Total Tasks: {baseline_metrics.get('total_tasks', 0)}")
    lines.append(f"  Successful Tasks: {baseline_metrics.get('successful_tasks', 0)}")
    lines.append(f"  Failed Tasks: {baseline_metrics.get('failed_tasks', 0)}")
    lines.append("")
    lines.append("WITH PATCH (Agent's Changes):")
    lines.append(f"  Mean Outcome Reward: {comparison['patched_score']:.4f} ({comparison['patched_score']*100:.2f}%)")
    lines.append(f"  Success Rate: {patched_metrics.get('success_rate', 0.0)*100:.2f}%")
    lines.append(f"  Total Tasks: {patched_metrics.get('total_tasks', 0)}")
    lines.append(f"  Successful Tasks: {patched_metrics.get('successful_tasks', 0)}")
    lines.append(f"  Failed Tasks: {patched_metrics.get('failed_tasks', 0)}")
    lines.append("")
    lines.append("IMPROVEMENT:")
    lines.append(f"  Absolute: {comparison['absolute_improvement']:+.4f} ({comparison['absolute_improvement']*100:+.2f} percentage points)")
    lines.append(f"  Relative: {comparison['relative_lift_percent']:+.2f}% lift")
    lines.append(f"  Status: {comparison['status']}")
    lines.append("")
    lines.append("PER-SEED COMPARISON:")
    lines.append(f"  Seeds improved: {comparison['seeds_improved']}")
    lines.append(f"  Seeds regressed: {comparison['seeds_regressed']}")
    lines.append(f"  Seeds unchanged: {comparison['seeds_unchanged']}")
    lines.append("")
    lines.append("PATCH SUMMARY:")
    lines.append(f"  Files changed: {patch_summary.get('files_changed', 0)}")
    lines.append(f"  Lines added: {patch_summary.get('lines_added', 0)}")
    lines.append(f"  Lines deleted: {patch_summary.get('lines_deleted', 0)}")
    if patch_summary.get('summary'):
        lines.append("")
        lines.append("  Change summary:")
        for line in patch_summary['summary'].split('\n')[:10]:  # Limit to 10 lines
            lines.append(f"    {line}")
    lines.append("")
    lines.append("=" * 60)
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Compare baseline performance with/without agent patch"
    )
    parser.add_argument("run_dir", type=Path, help="Path to run directory")
    parser.add_argument(
        "--output", 
        type=Path, 
        help="Output file path (default: re_bench_comparison.txt in run_dir)"
    )
    parser.add_argument(
        "--split",
        default="test",
        help="Data split to use (default: test). Use 'train' for more seeds."
    )
    parser.add_argument(
        "--seeds",
        help="Comma-separated seeds to evaluate (overrides split defaults). Default: 0-9 (10 seeds)"
    )
    parser.add_argument(
        "--num-seeds",
        type=int,
        default=10,
        help="Number of seeds to evaluate if --seeds not specified (default: 10)"
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/synth-laboratories/synth-ai",
        help="Repository URL (default: synth-ai)"
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Repository branch (default: main)"
    )
    parser.add_argument(
        "--model",
        help="Model identifier to use (overrides baseline default, e.g., 'groq:llama-3.3-70b-versatile')"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to .env file to load (for API keys like GROQ_API_KEY)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--keep-repo",
        action="store_true",
        help="Keep temporary repository directory (for debugging)"
    )
    
    args = parser.parse_args()
    
    if not args.run_dir.exists():
        print(f"Error: Run directory not found: {args.run_dir}")
        sys.exit(1)
    
    # Extract patch
    patch_content, patch_source = extract_patch(args.run_dir)
    if not patch_content:
        print("Error: No valid patch found in run artifacts")
        sys.exit(1)
    
    print(f"Found patch: {patch_source}")
    
    # Get baseline SHA
    baseline_sha = get_baseline_sha(args.run_dir)
    if baseline_sha:
        print(f"Baseline SHA: {baseline_sha}")
    
    # Set default seeds if not specified
    seeds_to_use = args.seeds
    if not seeds_to_use:
        seeds_to_use = ",".join(str(i) for i in range(args.num_seeds))
        print(f"Using {args.num_seeds} seeds: {seeds_to_use}")
    
    # Set up repository
    repo_dir = None
    try:
        repo_dir = setup_repo(args.repo_url, args.branch, baseline_sha)
        
        # Run baseline without patch
        baseline_output = repo_dir / "baseline_without_patch.json"
        baseline_results = run_baseline(
            repo_dir, 
            baseline_output, 
            split=args.split,
            seeds=seeds_to_use,
            model=args.model,
            env_file=args.env_file,
            verbose=args.verbose
        )
        
        # Apply patch
        if not apply_patch(repo_dir, patch_content):
            print("ERROR: Failed to apply patch. Cannot continue comparison.")
            sys.exit(1)
        
        # Get patch summary
        patch_summary = get_patch_summary(repo_dir)
        
        # Run baseline with patch
        patched_output = repo_dir / "baseline_with_patch.json"
        patched_results = run_baseline(
            repo_dir,
            patched_output,
            split=args.split,
            seeds=seeds_to_use,
            model=args.model,
            env_file=args.env_file,
            verbose=args.verbose
        )
        
        # Compare results
        comparison = compare_results(baseline_results, patched_results)
        
        # Generate report
        report = generate_report(
            comparison,
            args.run_dir,
            patch_source,
            baseline_sha,
            patch_summary,
        )
        
        # Output report
        print("\n" + report)
        
        # Save to file
        output_path = args.output or (args.run_dir / "re_bench_comparison.txt")
        output_path.write_text(report)
        print(f"\nReport saved to: {output_path}")
        
        # Also save JSON comparison
        json_output = args.run_dir / "re_bench_comparison.json"
        comparison_json = {
            "run_id": args.run_dir.name,
            "baseline_results": baseline_results,
            "patched_results": patched_results,
            "comparison": comparison,
            "patch_summary": patch_summary,
        }
        json_output.write_text(json.dumps(comparison_json, indent=2))
        print(f"JSON comparison saved to: {json_output}")
        
    finally:
        # Cleanup
        if repo_dir and not args.keep_repo:
            print(f"\nCleaning up temporary repository: {repo_dir}")
            shutil.rmtree(repo_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

