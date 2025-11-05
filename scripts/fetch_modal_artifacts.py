#!/usr/bin/env python3
"""
Helper script to fetch artifacts from Modal volumes to local filesystem.
This replicates the Docker artifact structure locally.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def fetch_artifacts(run_id: str, output_dir: str = None):
    """
    Fetch artifacts from Modal volume for a specific run.
    
    Args:
        run_id: The run identifier
        output_dir: Local directory to save artifacts (defaults to ./runs/{run_id})
    """
    if not output_dir:
        output_dir = f"./data/runs/{run_id}"
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Fetching artifacts for run: {run_id}")
    print(f"Output directory: {output_path.absolute()}")
    
    # Use Modal CLI to fetch the volume contents
    try:
        # Fetch the entire run directory
        cmd = [
            "modal", "volume", "get",
            "codex-artifacts",
            f"{run_id}/",
            str(output_path) + "/"
        ]
        
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error fetching artifacts: {result.stderr}")
            return False
        
        print("Artifacts fetched successfully!")
        
        # List fetched files
        print("\nFetched files:")
        for file in output_path.rglob("*"):
            if file.is_file():
                print(f"  - {file.relative_to(output_path)}")
        
        # Check for completion.json to show status
        completion_file = output_path / "completion.json"
        if completion_file.exists():
            with open(completion_file) as f:
                completion_data = json.load(f)
                print("\nRun status:")
                print(f"  Completed: {completion_data.get('completed', False)}")
                print(f"  Timestamp: {completion_data.get('timestamp', 'Unknown')}")
                
                if "evaluation_results" in completion_data:
                    eval_results = completion_data["evaluation_results"]
                    print(f"  Evaluation passed: {eval_results.get('passed', False)}")
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Error running modal command: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False


def list_runs():
    """
    List all available runs in the Modal volume.
    """
    print("Listing available runs in Modal volume...")
    
    try:
        cmd = ["modal", "volume", "ls", "codex-artifacts"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error listing runs: {result.stderr}")
            return False
        
        print(result.stdout)
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Error running modal command: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Fetch artifacts from Modal runs to local filesystem"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Fetch command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch artifacts for a run")
    fetch_parser.add_argument("run_id", help="Run ID to fetch artifacts for")
    fetch_parser.add_argument(
        "--output-dir", "-o",
        help="Output directory (defaults to ./runs/{run_id})"
    )
    
    # List command
    subparsers.add_parser("list", help="List available runs")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Check if Modal is installed
    try:
        subprocess.run(
            ["modal", "--version"],
            capture_output=True,
            check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: Modal CLI not found. Please install with: pip install modal")
        return 1
    
    # Check Modal authentication
    try:
        subprocess.run(
            ["modal", "token", "ls"],
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError:
        print("Error: Not authenticated with Modal. Please run: modal setup")
        return 1
    
    if args.command == "fetch":
        success = fetch_artifacts(args.run_id, args.output_dir)
        return 0 if success else 1
    elif args.command == "list":
        success = list_runs()
        return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
