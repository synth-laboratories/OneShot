#!/usr/bin/env python3
"""
Parallel Modal runner for executing multiple agent tasks concurrently.
Collects results in a table format for analysis.
"""

import argparse
import json
import subprocess
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
import sys


def load_config(config_path: str) -> Dict:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def run_single_task(task_name: str, task_path: str, config: Dict) -> Dict:
    """
    Run a single task using Modal backend.
    Returns results dictionary with scores, timing, etc.
    """
    start_time = time.time()
    
    # Construct task directory path
    task_dir = Path(task_path) / task_name
    if not task_dir.exists():
        return {
            'task': task_name,
            'status': 'error',
            'error': f'Task directory not found: {task_dir}',
            'duration': 0,
            'score': 0
        }
    
    # Set up environment variables
    env = {
        **os.environ.copy(),
        'SANDBOX_BACKEND': 'modal',
        'AGENT_TIMEOUT_SEC': str(config['agents']['timeout_sec']),
        'AGENT_MAX_TOKENS': str(config['agents']['token_limit']),
        'OPENAI_MODEL': config['agents']['model']
    }
    
    try:
        print(f"🚀 Starting task: {task_name}")
        
        # Run Modal directly using the codex_modal_runner with the main entrypoint
        cmd = [
            'modal', 'run', 'codex_modal_runner.py::main',
            '--task-dir', str(task_dir),
            '--timeout', str(config['agents']['timeout_sec']),
            '--token-limit', str(config['agents']['token_limit']),
            '--model', config['agents']['model']
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config['agents']['timeout_sec'] + 60,  # Add buffer for Modal overhead
            env=env,
        )
        
        # Parse output to extract scores
        output = result.stdout
        score = 0.0
        rubric_scores = {}
        
        # Look for evaluation results in output
        test_results = {}
        if "Total Score:" in output:
            for line in output.split('\n'):
                if "Total Score:" in line:
                    try:
                        score = float(line.split(':')[1].strip().rstrip('%')) / 100
                    except (IndexError, ValueError):
                        pass
                elif "• " in line and "%" in line and "weight:" in line:
                    # Parse rubric scores
                    try:
                        parts = line.split('•')[1].split(':')
                        rubric_name = parts[0].strip()
                        score_part = parts[1].split('(')[0].strip().rstrip('%')
                        rubric_scores[rubric_name] = float(score_part) / 100
                    except (IndexError, ValueError):
                        pass
                elif "✅" in line or "❌" in line:
                    # Parse test results
                    try:
                        if "tests/" in line:
                            test_name = line.split("tests/")[1].split(":")[0].strip()
                            passed = "✅" in line
                            test_results[test_name] = passed
                    except (IndexError, ValueError):
                        pass
        
        duration = time.time() - start_time
        
        return {
            'task': task_name,
            'status': 'success' if result.returncode == 0 else 'failed',
            'score': score,
            'rubric_scores': rubric_scores,
            'test_results': test_results,
            'duration': duration,
            'exit_code': result.returncode,
            'error': result.stderr if result.returncode != 0 else None
        }
        
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return {
            'task': task_name,
            'status': 'timeout',
            'score': 0,
            'duration': duration,
            'error': f'Task exceeded timeout of {config["agents"]["timeout_sec"]}s'
        }
    except Exception as e:
        duration = time.time() - start_time
        return {
            'task': task_name,
            'status': 'error',
            'score': 0,
            'duration': duration,
            'error': str(e)
        }


def run_parallel_tasks(tasks: List[Tuple[str, str]], config: Dict) -> List[Dict]:
    """
    Run multiple tasks in parallel using ThreadPoolExecutor.
    Returns list of result dictionaries.
    """
    max_workers = config['agents'].get('max_parallel', 5)
    results = []
    
    print(f"\n📊 Running {len(tasks)} tasks with max {max_workers} parallel workers\n")
    print("=" * 80)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_task = {
            executor.submit(run_single_task, task_name, task_path, config): (task_name, task_path)
            for task_name, task_path in tasks
        }
        
        # Process completed tasks
        completed = 0
        for future in as_completed(future_to_task):
            task_name, _ = future_to_task[future]
            try:
                result = future.result()
                results.append(result)
                completed += 1
                
                # Print progress
                status_emoji = {
                    'success': '✅',
                    'failed': '❌',
                    'timeout': '⏱️',
                    'error': '🔥'
                }.get(result['status'], '❓')
                
                score_str = f"{result['score']*100:.1f}%" if result['score'] > 0 else "N/A"
                print(f"{status_emoji} [{completed}/{len(tasks)}] {task_name}: {result['status']} (Score: {score_str}, Time: {result['duration']:.1f}s)")
                
            except Exception as e:
                print(f"❌ [{completed}/{len(tasks)}] {task_name}: Exception - {e}")
                results.append({
                    'task': task_name,
                    'status': 'error',
                    'score': 0,
                    'duration': 0,
                    'error': str(e)
                })
    
    return results


def generate_markdown_table(results: List[Dict]) -> str:
    """Generate a markdown table from results."""
    lines = []
    lines.append("\n## Results Summary\n")
    lines.append(f"**Run Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Total Tasks:** {len(results)}")
    
    # Calculate statistics
    successful = sum(1 for r in results if r['status'] == 'success')
    avg_score = sum(r['score'] for r in results) / len(results) if results else 0
    total_time = sum(r['duration'] for r in results)
    
    lines.append(f"**Success Rate:** {successful}/{len(results)} ({successful/len(results)*100:.1f}%)")
    lines.append(f"**Average Score:** {avg_score*100:.1f}%")
    lines.append(f"**Total Time:** {total_time:.1f}s")
    lines.append(f"**Average Time:** {total_time/len(results):.1f}s per task\n")
    
    # Create detailed table with separate columns for different evaluation types
    lines.append("### Evaluation Results\n")
    lines.append("| Task | Total Score | LLM Rubrics (weighted) | Unit Tests | Time |")
    lines.append("|------|-------------|------------------------|------------|------|")
    
    for result in sorted(results, key=lambda x: x['task']):
        task_name = result['task']
        if len(task_name) > 35:
            task_name = task_name[:32] + "..."
        
        # Calculate total score - could be from rubrics, tests, or both
        total_score = "-"
        if result.get('score') and result['score'] > 0:
            total_score = f"{result['score']*100:.0f}%"
        elif not result.get('rubric_scores') and result.get('test_results'):
            # If only tests exist, calculate score from test pass rate
            passed = sum(1 for v in result['test_results'].values() if v)
            total_tests = len(result['test_results'])
            if total_tests > 0:
                total_score = f"{(passed/total_tests)*100:.0f}%"
        duration = f"{result['duration']:.1f}s"
        
        # Format LLM rubric scores (evaluated by language model)
        rubric_str = "N/A"
        if result.get('rubric_scores') and len(result['rubric_scores']) > 0:
            rubric_parts = []
            for k, v in sorted(result['rubric_scores'].items()):
                # Show both score and weight if we can determine it
                rubric_parts.append(f"{k}: {v*100:.0f}%")
            rubric_str = "<br>".join(rubric_parts) if len(rubric_parts) > 2 else " / ".join(rubric_parts)
        
        # Format unit test results (Python tests)
        test_str = "N/A"
        if result.get('test_results') and len(result['test_results']) > 0:
            passed = sum(1 for v in result['test_results'].values() if v)
            total_tests = len(result['test_results'])
            test_str = f"✅ {passed}/{total_tests}"
            
            # Add details about which tests failed
            failed_tests = [k for k, v in result['test_results'].items() if not v]
            if failed_tests and len(failed_tests) <= 2:
                test_str += f"<br>❌ {', '.join(failed_tests[:2])}"
        
        # Handle errors
        if result['status'] == 'failed':
            error_msg = result.get('error', '')[:30] if result.get('error') else 'Unknown error'
            lines.append(f"| {task_name} | ❌ FAILED | {error_msg} | - | {duration} |")
        else:
            lines.append(f"| {task_name} | **{total_score}** | {rubric_str} | {test_str} | {duration} |")
    
    # Add detailed explanation
    lines.append("\n### Evaluation Methods\n")
    lines.append("**LLM Rubrics**: Criteria evaluated by GPT-4 using structured outputs")
    lines.append("- **content**: Does the solution include the required functionality?")
    lines.append("- **location**: Is the change in the correct location/section?")  
    lines.append("- **clarity**: Is the implementation clear and well-written?")
    lines.append("")
    lines.append("**Unit Tests**: Python pytest tests that validate agent outputs")
    lines.append("- Tests check specific requirements programmatically")
    lines.append("- Each test maps to a rubric for scoring")
    
    return "\n".join(lines)


def generate_csv_table(results: List[Dict]) -> str:
    """Generate CSV format from results."""
    import csv
    import io
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(['Task', 'Status', 'Score', 'Duration', 'Exit Code', 'Error'])
    
    # Data rows
    for result in sorted(results, key=lambda x: x['task']):
        writer.writerow([
            result['task'],
            result['status'],
            f"{result['score']*100:.1f}" if result['score'] > 0 else "0",
            f"{result['duration']:.1f}",
            result.get('exit_code', ''),
            result.get('error', '')[:100] if result.get('error') else ''
        ])
    
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description='Run multiple Modal tasks in parallel')
    parser.add_argument('--config', required=True, help='Path to configuration YAML file')
    parser.add_argument('--dataset', help='Specific dataset to run (overrides config)')
    parser.add_argument('--max-parallel', type=int, help='Max parallel runs (overrides config)')
    parser.add_argument('--output', help='Output file path for results')
    parser.add_argument('--format', choices=['markdown', 'csv', 'json'], default='markdown',
                       help='Output format for results')
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)
    
    # Override config with command-line args
    if args.max_parallel:
        config['agents']['max_parallel'] = args.max_parallel
    
    # Collect tasks to run
    tasks_to_run = []
    
    if args.dataset:
        # Run specific dataset
        if args.dataset in config['datasets']:
            dataset = config['datasets'][args.dataset]
            if dataset.get('enabled', True):
                task_path = dataset['path']
                for task in dataset.get('tasks', []):
                    tasks_to_run.append((task, task_path))
        else:
            print(f"Error: Dataset '{args.dataset}' not found in config")
            return 1
    else:
        # Run all enabled datasets
        for dataset_name, dataset in config['datasets'].items():
            if dataset.get('enabled', True):
                task_path = dataset['path']
                tasks = dataset.get('tasks', [])
                
                # Auto-discover tasks if list is empty
                if not tasks and Path(task_path).exists():
                    tasks = [d.name for d in Path(task_path).iterdir() if d.is_dir()]
                
                for task in tasks:
                    tasks_to_run.append((task, task_path))
    
    if not tasks_to_run:
        print("No tasks to run. Check your configuration.")
        return 1
    
    print(f"🎯 Preparing to run {len(tasks_to_run)} tasks")
    print(f"📦 Model: {config['agents']['model']}")
    print(f"⚡ Max parallel: {config['agents']['max_parallel']}")
    
    # Run tasks in parallel
    results = run_parallel_tasks(tasks_to_run, config)
    
    # Generate output
    if args.format == 'markdown':
        output_content = generate_markdown_table(results)
    elif args.format == 'csv':
        output_content = generate_csv_table(results)
    else:  # json
        output_content = json.dumps(results, indent=2)
    
    # Save or print results
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(output_content)
        print(f"\n📝 Results saved to: {output_path}")
    else:
        print(output_content)
    
    # Save detailed results if configured
    if config['output'].get('save_artifacts', True):
        results_dir = Path(config['output']['results_dir'])
        results_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        detailed_path = results_dir / f"parallel_run_{timestamp}.json"
        
        with open(detailed_path, 'w') as f:
            json.dump({
                'config': config,
                'timestamp': timestamp,
                'results': results
            }, f, indent=2)
        
        print(f"📊 Detailed results saved to: {detailed_path}")
    
    return 0


if __name__ == "__main__":
    import os
    sys.exit(main())
