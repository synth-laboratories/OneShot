# Benchmarking - Running Multiple Tasks

Configure and run multiple tasks in parallel with rollouts to create comprehensive benchmarks.

## Understanding Benchmarking

Benchmarking involves running multiple tasks with different configurations:
- **Multiple tasks** in parallel or sequence
- **Multiple rollouts** per task (for statistical significance)
- **Different models** and parameters
- **Comprehensive result aggregation**

## Configuration File Structure

Create `configs/your_benchmark.toml`:

```toml
name = "my_benchmark"
parallel = 4

[[tasks]]
prepared_dir = "data/tasks/prepared/task_1"
model = "gpt-4"
rollouts = 3
apply_overrides = true
overrides = "data/tasks/prepared/task_1/overrides.json"

[[tasks]]
prepared_dir = "data/tasks/prepared/task_2"
model = "claude-3-sonnet"
rollouts = 5
apply_overrides = false

[[tasks]]
prepared_dir = "data/tasks/prepared/task_3"
model = "gpt-3.5-turbo"
rollouts = 1
```

## Running Benchmarks

### Basic Benchmark Run

```bash
# Run with default settings (quiet mode with status polling)
python scripts/eval_rollouts.py run configs/your_benchmark.toml

# Run with full verbosity
python scripts/eval_rollouts.py run configs/your_benchmark.toml -vv

# Run with progress info only
python scripts/eval_rollouts.py run configs/your_benchmark.toml -v
```

### Summarize Results

```bash
# Get latest results
python scripts/eval_rollouts.py summarize my_benchmark --latest

# Get specific rollout results
python scripts/eval_rollouts.py summarize my_benchmark --rollout 20250824__12-34-56
```

### Evaluate Results

```bash
# Evaluate latest benchmark
python scripts/eval_rollouts.py eval my_benchmark --latest

# Evaluate specific rollout
python scripts/eval_rollouts.py eval my_benchmark --rollout 20250824__12-34-56
```

## Parallel Execution

### Understanding Parallelism

**Task-level parallelism:**
```toml
parallel = 4  # Run up to 4 tasks simultaneously
```

**Rollout-level parallelism:**
```toml
[[tasks]]
rollouts = 3  # Run 3 independent evaluations of this task
```

### Performance Considerations

**Optimal settings:**
- Start with `parallel = 2-4` for local development
- Use `parallel = 8-16` for cloud environments
- Keep `rollouts = 3-5` for statistical significance
- Monitor resource usage (CPU, memory, API quotas)

## Advanced Configuration

### Model Comparison Benchmark

```toml
name = "model_comparison"
parallel = 2

[[tasks]]
prepared_dir = "data/tasks/prepared/algorithm_task"
model = "gpt-4"
rollouts = 5

[[tasks]]
prepared_dir = "data/tasks/prepared/algorithm_task"
model = "claude-3-sonnet"
rollouts = 5

[[tasks]]
prepared_dir = "data/tasks/prepared/algorithm_task"
model = "gemini-pro"
rollouts = 5
```

### Parameter Sweep

```toml
name = "parameter_sweep"
parallel = 3

[[tasks]]
prepared_dir = "data/tasks/prepared/coding_task"
model = "gpt-4"
rollouts = 3
# Custom timeout
environment_variables = { "TIMEOUT_SEC" = "1800" }

[[tasks]]
prepared_dir = "data/tasks/prepared/coding_task"
model = "gpt-4"
rollouts = 3
# Different timeout
environment_variables = { "TIMEOUT_SEC" = "3600" }
```

### Mixed Workloads

```toml
name = "mixed_workloads"
parallel = 4

# Coding tasks
[[tasks]]
prepared_dir = "data/tasks/prepared/refactor_code"
model = "gpt-4"
rollouts = 3

# Algorithm tasks
[[tasks]]
prepared_dir = "data/tasks/prepared/optimize_algorithm"
model = "claude-3-sonnet"
rollouts = 3

# Documentation tasks
[[tasks]]
prepared_dir = "data/tasks/prepared/write_docs"
model = "gpt-3.5-turbo"
rollouts = 2
```

## Results Organization

### Output Structure

```
data/rollouts/
├── benchmark_name/
│   ├── 20250824__12-34-56/     # Rollout directory
│   │   ├── manifest.json       # Task list and results
│   │   ├── runs.txt           # List of run directories
│   │   └── task_results/      # Individual task results
│   └── 20250824__12-34-57/     # Another rollout
└── temp/
    └── rollout_benchmark_name__20250824__12-34-56.json
```

### Result Files

**Manifest (`manifest.json`):**
```json
{
  "config": "configs/your_benchmark.toml",
  "name": "your_benchmark",
  "created_at": "20250824__12-34-56",
  "results": [
    {
      "task_dir": "data/tasks/prepared/task_1",
      "run_id": "20250824__12-34-56-0",
      "status": "launched",
      "launch_rc": 0
    }
  ]
}
```

**Individual Run Results:**
```
data/runs/20250824__12-34-56-0/
├── artifacts/
│   ├── diff.patch
│   ├── notes.md
│   └── repo_info.json
├── evaluation_results.json
├── scoring_results.md
└── logs/
    └── eval.log
```

## Monitoring Progress

### Status Polling (Default)

When running in default mode, you'll see:
```
[rollouts] config=configs/your_benchmark.toml name=your_benchmark tasks=3 parallel=2
[rollouts] output=data/rollouts/your_benchmark/20250824__12-34-56
[rollouts] Running in quiet mode - use -vv for full output
[rollouts] Starting 3 tasks with parallel=2
⠋ Running tasks... 0/3 completed
✓ Task 20250824__12-34-56-0 completed (launched)
✓ Task 20250824__12-34-56-1 completed (launched)
⠙ Running tasks... 2/3 completed
✓ Task 20250824__12-34-56-2 completed (launched)
✓ All 3 tasks completed!
```

### Verbose Mode

With `-vv` flag, you'll see full Docker logs and detailed output.

### Progress-Only Mode

With `-v` flag, you'll see basic progress without status icons.

## Analyzing Results

### Quick Summary

```bash
# Get summary of latest benchmark
python scripts/eval_rollouts.py summarize your_benchmark --latest

# Get detailed evaluation results
python scripts/eval_rollouts.py eval your_benchmark --latest
```

### Custom Analysis

```python
import json
from pathlib import Path

# Load benchmark results
rollout_dir = Path("data/rollouts/your_benchmark/20250824__12-34-56")
manifest = json.loads((rollout_dir / "manifest.json").read_text())

# Analyze results
for result in manifest["results"]:
    run_dir = Path(result["run_dir"])
    if (run_dir / "evaluation_results.json").exists():
        eval_data = json.loads((run_dir / "evaluation_results.json").read_text())
        print(f"Task: {result['task_dir']}")
        print(f"Score: {eval_data.get('total_score', 'N/A')}")
        print("---")
```

## Best Practices

### Benchmark Design

1. **Task Selection**
   - Choose diverse tasks representing your use case
   - Include tasks of varying difficulty
   - Use a mix of task types (coding, algorithm, documentation)

2. **Statistical Significance**
   - Use `rollouts = 3-5` for reliable results
   - Run the same benchmark multiple times if needed
   - Consider statistical tests for significance

3. **Resource Management**
   - Monitor API quotas and costs
   - Use appropriate parallelism for your setup
   - Schedule large benchmarks during off-peak hours

### Configuration Management

1. **Version Control**
   - Keep benchmark configs in git
   - Tag important benchmark runs
   - Document changes between versions

2. **Reproducibility**
   - Pin specific model versions when possible
   - Use consistent timeout and token limits
   - Document environment and setup

3. **Incremental Testing**
   - Start with small benchmarks for testing
   - Gradually increase complexity
   - Validate results at each step

## Troubleshooting

### Common Issues

**Tasks failing to start:**
- Check Docker installation: `docker ps`
- Verify codex installation: `which codex`
- Ensure task directories exist and have `tb_meta.json`

**High failure rate:**
- Reduce parallelism: `parallel = 1`
- Check API keys and quotas
- Monitor system resources

**Slow execution:**
- Increase timeouts in task configuration
- Check network connectivity
- Monitor API rate limits

### Debugging

**Check logs:**
```bash
# View evaluation logs
cat data/runs/<run_id>/logs/eval.log

# View Docker build logs (verbose mode)
python scripts/eval_rollouts.py run config.toml -vv
```

**Inspect failed tasks:**
```bash
# Check task preparation
ls -la data/tasks/prepared/your-task/

# Check for overrides issues
cat data/tasks/prepared/your-task/overrides.json
```

### Performance Tuning

**Optimize parallelism:**
```toml
# Start conservative
parallel = 2
rollouts = 3

# Scale up based on results
parallel = 8
rollouts = 5
```

**Monitor resource usage:**
```bash
# Check CPU/memory usage
top

# Monitor Docker containers
docker stats

# Check API usage (if available)
# Your API provider dashboard
```

## Advanced Features

### Custom Metrics

Add custom evaluation metrics:

```python
# In your task's evaluation script
custom_metrics = {
    "code_complexity": calculate_complexity(diff),
    "test_coverage": calculate_coverage(tests),
    "performance_score": measure_performance(code)
}

# Include in evaluation results
result = {
    "standard_metrics": standard_scores,
    "custom_metrics": custom_metrics
}
```

### Automated Benchmarking

Create scripts for regular benchmark runs:

```bash
#!/bin/bash
# daily_benchmark.sh

DATE=$(date +%Y%m%d)
CONFIG="configs/daily_benchmark.toml"

echo "Starting daily benchmark: $DATE"
python scripts/eval_rollouts.py run "$CONFIG"

echo "Benchmark complete, results in data/rollouts/daily_benchmark/$DATE"
```

### Result Comparison

Compare results across different model versions:

```python
# Load multiple benchmark results
benchmarks = {
    "gpt4_v1": load_benchmark("data/rollouts/model_v1/20250824"),
    "gpt4_v2": load_benchmark("data/rollouts/model_v2/20250824"),
    "claude_v1": load_benchmark("data/rollouts/claude_v1/20250824")
}

# Compare performance
for task_name in benchmark_tasks:
    scores = {}
    for model, results in benchmarks.items():
        scores[model] = get_task_score(results, task_name)

    print(f"Task: {task_name}")
    for model, score in scores.items():
        print(f"  {model}: {score}")
```

## Scaling Up

### Large-Scale Benchmarking

For running hundreds of tasks:

```toml
name = "large_scale_benchmark"
parallel = 16  # High parallelism for cloud

[[tasks]]
prepared_dir = "data/tasks/prepared/task_001"
model = "gpt-4"
rollouts = 5

# ... hundreds more tasks
```

### Cloud Infrastructure

**Modal for parallel execution:**
```bash
# Use Modal for cloud-based parallel execution
export SANDBOX_BACKEND=modal
python scripts/eval_rollouts.py run configs/large_benchmark.toml
```

**AWS Batch or similar:**
- Containerize the evaluation process
- Use cloud batch processing
- Aggregate results in cloud storage

### Continuous Benchmarking

Set up automated benchmarking:
```bash
# crontab entry for nightly benchmarks
0 2 * * * /path/to/repo/scripts/run_nightly_benchmark.sh

# GitHub Actions for weekly model comparisons
# .github/workflows/benchmark.yml
```

This comprehensive benchmarking setup allows you to systematically evaluate and compare different models and configurations across large numbers of tasks.
