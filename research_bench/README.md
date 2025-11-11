# Research Bench

Tools and workflows for running and evaluating re-bench tasks (like banking77).

## Overview: Evaluation Flow

Research bench tasks follow a three-stage evaluation pipeline:

### Stage 1: Run Codex on Task

**Script:** `scripts/run_codex_box.sh`  
**Command:** `uvx oneshot-rebench` or `python scripts/run_re_bench.py`

1. **Task Preparation**: Loads task from `data/tasks/prepared/<task-name>/`
   - Task includes `tb_meta.json` with evaluation rubrics
   - Task includes repository baseline at a specific commit SHA

2. **Codex Execution**: Runs Codex agent in Docker container
   - Agent receives task instructions and repository context
   - Agent makes changes to solve the task
   - Changes are captured as `artifacts/diff.patch`

3. **Artifacts Generated**:
   - `artifacts/diff.patch`: Git diff of agent's changes
   - `artifacts/baseline_sha.txt`: Baseline commit SHA
   - `artifacts/codex-run.log`: Execution logs
   - `artifacts/codex-config.toml`: Model configuration
   - `metadata.json`: Run metadata (task, model, timestamps)

**Output**: Run directory at `data/runs/<run_id>/`

### Stage 2: Evaluate Run Against Rubrics

**Script:** `src/one_shot/evaluate_run.py`  
**Command:** Automatically called by `run_re_bench.py`, or manually: `python -m one_shot.evaluate_run <run_dir> <task_dir>`

1. **Load Task Rubrics**: Reads `tb_meta.json` to get evaluation criteria
   - LLM-based rubrics (qualitative scoring)
   - Optional deterministic test scripts

2. **LLM Rubric Evaluation**: Uses OpenAI API to score agent's work
   - Each rubric is evaluated independently
   - LLM provides score, reasoning, evidence, and suggestions
   - Scores are weighted and combined into a weighted average

3. **Quantitative Metrics**: Extracts from execution traces
   - Token counts (input/output, cached)
   - Tool calls
   - Time taken
   - Cost (using model pricing LUT)

4. **Results Saved**:
   - `evaluation_results.json`: Complete evaluation results
   - `scoring_results.md`: Human-readable report
   - `task_dir/evaluation_results/<run_id>.json`: For aggregation

**Output**: Evaluation scores and metrics

### Stage 3: Baseline Comparison (Optional)

**Script:** `scripts/re_bench_compare.py`  
**Command:** `uvx oneshot-compare <run_dir>` or `python scripts/re_bench_compare.py <run_dir>`

1. **Extract Patch**: Reads `artifacts/diff.patch` from run directory

2. **Baseline Evaluation (Before Patch)**:
   - Clones repository at baseline SHA
   - Runs baseline evaluation (typically 10 seeds)
   - Measures baseline performance score

3. **Patched Evaluation (After Patch)**:
   - Applies agent's patch to baseline repository
   - Runs evaluation on patched version (same seeds)
   - Measures patched performance score

4. **Code Quality Checks**:
   - Runs pytest, ruff, type_check on both versions
   - Detects regressions (pass->fail)
   - Compares violation counts

5. **LLM Rubric Comparison**:
   - Evaluates rubrics on both baseline and patched versions
   - Compares scores to measure improvement

6. **Combined Scoring**:
   - Baseline delta: `patched_score - baseline_score`
   - Rubric scores: Weighted LLM evaluation
   - Pass->pass score: Code quality checks
   - Final combined score: Weighted combination of all metrics

7. **Results Saved**:
   - `re_bench_comparison.json`: Complete comparison with reward types
   - `re_bench_comparison.txt`: Human-readable report
   - `task_dir/evaluation_results/<run_id>.json`: For aggregation

**Output**: Baseline comparison metrics and combined score

### Complete Flow Example

```bash
# 1. Run Codex on task (creates run directory)
uv run oneshot-rebench --config research_bench/eval_configs/banking77.toml

# This internally:
#   - Runs Codex → creates data/runs/<run_id>/
#   - Evaluates run → creates evaluation_results.json
#   - Optionally compares baseline → creates re_bench_comparison.json

# 2. Results are available in:
#   - data/runs/<batch_id>/batch_results.json (batch summary)
#   - data/runs/<run_id>/evaluation_results.json (individual run)
#   - data/runs/<run_id>/re_bench_comparison.json (baseline comparison)
#   - data/tasks/prepared/re-bench-banking77/evaluation_results/<run_id>.json (aggregation)
```

## Creating New Research Bench Data Points

To create a new research bench datum using pair programming with Codex:

1. **Read the guide**: See `research_bench/pair_programming.txt` for complete instructions
2. **Quick summary**:
   - Set up temporary synth-ai workspace
   - Start Codex with OneShot MCP tools and tracing
   - Work with Codex to improve a benchmark
   - Bundle the session into a research bench datum
   - Test and validate the datum
3. **Prepare a config**: Create a TOML file with a `[pair_programming]` section (see `research_bench/eval_configs/pair_programming_example.toml`)
4. **Launch session**: `uv run oneshot-rebench pair --config research_bench/eval_configs/pair_programming_example.toml`
5. **Bundle results**: Follow Phase 5 in `research_bench/pair_programming.txt` to convert the captured session into a prepared task

The guide covers:
- Setting up Codex with tracing and MCP
- Pair programming workflow
- Capturing artifacts and traces
- Bundling into research bench format
- Testing and validation

Example config snippet:
```toml
[pair_programming]
repo_url = "https://github.com/synth-laboratories/synth-ai"
repo_branch = "main"
install_commands = ["uv pip install -e ."]
task_title = "Improve banking77 benchmark"
task_description = """
Collaborate with the user to improve the banking77 benchmark in synth-ai.
1. Inspect the baseline.
2. Propose improvements (prompt optimization, SFT, etc.).
3. Validate improvements with synth-ai commands.
4. Summarise the changes before ending the session.
"""
codex_model = "gpt-5-nano"
enable_tracing = true
keep_workspace = true
```

## Quick Start

### Using Config Files (Recommended)

Create a config file in `research_bench/eval_configs/`:

```bash
# Using uv run (recommended for local development)
uv run oneshot-rebench --config research_bench/eval_configs/banking77.toml

# Or directly
python scripts/run_re_bench.py --config research_bench/eval_configs/banking77.toml
```

### Using Command Line Arguments

Run Codex on a re-bench task:

```bash
# Using uv run
uv run oneshot-rebench --task re-bench-banking77 --num-seeds 1 --run-baseline-comparison

# Or directly
python scripts/run_re_bench.py \
  --task re-bench-banking77 \
  --num-seeds 1 \
  --model gpt-5-nano \
  --run-baseline-comparison
```

## Configuration Files

Config files are TOML files that specify evaluation settings. They support two modes:

### Single Run Mode

A simple config with a single task:

```toml
# research_bench/eval_configs/banking77.toml
task = "re-bench-banking77"
model = "gpt-5-nano"
codex_config = "~/.codex"
run_baseline_comparison = true
skip_eval_if_exists = true
num_runs = 1
baseline_num_seeds = 10
verbose = false
```

### Multiple Runs Mode (Recommended for Big Evaluations)

A config with multiple runs for batch evaluation:

```toml
# research_bench/eval_configs/multi_model_banking77.toml

# Default settings (applied to all runs unless overridden)
[defaults]
codex_config = "~/.codex"
run_baseline_comparison = true
skip_eval_if_exists = true
num_runs = 1
baseline_num_seeds = 10
verbose = false

# Individual run configurations
# Each run can override defaults
[[runs]]
task = "re-bench-banking77"
model = "gpt-5-nano"
run_baseline_comparison = true

[[runs]]
task = "re-bench-banking77"
model = "gpt-5-mini"
run_baseline_comparison = true

[[runs]]
task = "re-bench-banking77"
model = "gpt-4o-mini"
run_baseline_comparison = true

# You can also run different tasks
[[runs]]
task = "re-bench-other-task"
model = "gpt-5-nano"
run_baseline_comparison = false
```

### Config File Options

**Top-level or defaults section:**
- `task`: Task name or path (required in single-run mode or in each `[[runs]]` entry)
- `model`: Model to use for Codex (e.g., `gpt-5-nano`)
- `codex_config`: Path to codex config directory (default: `~/.codex`)
- `run_baseline_comparison`: Whether to run baseline comparison (default: `false`)
- `skip_eval_if_exists`: Skip evaluation if results exist (default: `true`)
- `num_runs`: Number of Codex runs per task (default: `1`)
- `baseline_num_seeds`: Seeds for baseline evaluation (default: `10`)
- `baseline_model`: Model for baseline (default: task default)
- `output_dir`: Output directory (default: auto-generated)
- `verbose`: Verbose output (default: `false`)

**In `[[runs]]` entries:**
- All of the above options can be specified per-run
- Each run inherits from `[defaults]` and can override any setting

Command-line arguments override config file values.

## Command Reference

### `oneshot-rebench` (uv run) or `run_re_bench.py`

Orchestrates running Codex on re-bench tasks.

**Subcommands:**
- `eval` (default) – run standard re-bench evaluation
- `pair` – launch a pair programming session to capture a new research bench datum

**Using uv run:**
```bash
# Evaluation mode (default)
uv run oneshot-rebench --config research_bench/eval_configs/banking77.toml
uv run oneshot-rebench --task re-bench-banking77 --num-seeds 1

# Pair programming mode
uv run oneshot-rebench pair --config research_bench/eval_configs/pair_programming_example.toml
```

**Direct usage:**
```bash
python scripts/run_re_bench.py --config research_bench/eval_configs/banking77.toml
python scripts/run_re_bench.py eval --task re-bench-banking77
python scripts/run_re_bench.py pair --config research_bench/eval_configs/pair_programming_example.toml
```

**Eval Arguments:**
- `--config`: Path to TOML config file
- `--task`: Task name or path (required if no config)
- `--num-seeds`: Number of seeds to run (default: 1)
- `--model`: Model to use
- `--codex-config`: Path to codex config directory
- `--run-baseline-comparison`: Run baseline comparison
- `--skip-eval`: Skip evaluation if results exist
- `--output-dir`: Output directory
- `--max-concurrent`: Max concurrent Docker runs (default: 2)
- `--verbose`, `-v`: Verbose output

**Pair Arguments:**
- `--config`: Path to pair programming TOML config (required)
- `--workspace-dir`: Use an existing directory instead of a temporary workspace
- `--cleanup`: Remove the workspace after the session completes
- `--verbose`, `-v`: Verbose output

### `oneshot-compare` (uv run) or `re_bench_compare.py`

Compares baseline performance with/without agent's patch.

**Using uv run:**
```bash
uv run oneshot-compare data/runs/20250101__12-34-56_seed000
```

**Direct usage:**
```bash
python scripts/re_bench_compare.py <run_dir> [options]
```

**Options:**
- `--num-seeds`: Number of seeds for baseline evaluation (default: 10)
- `--model`: Model to use for baseline
- `--env-file`: Path to `.env` file with API keys
- `--verbose`: Show verbose output
- `--rebuild`: Force rebuild Docker image

## Workflow Examples

### Example 1: Full Evaluation Pipeline

```bash
# Step 1: Run Codex on 10 seeds with baseline comparison
python scripts/run_re_bench.py \
  --task re-bench-banking77 \
  --num-seeds 10 \
  --model gpt-5-nano \
  --run-baseline-comparison \
  --output-dir data/runs/banking77_full_eval

# Results are saved to:
# - data/runs/banking77_full_eval/batch_results.json
# - data/runs/banking77_full_eval/runs.txt
```

### Example 2: Run First, Compare Later

```bash
# Step 1: Run Codex and evaluate (no baseline comparison)
python scripts/run_re_bench.py \
  --task re-bench-banking77 \
  --num-seeds 10 \
  --model gpt-5-nano

# Step 2: Run baseline comparison on specific runs later
python scripts/re_bench_compare.py data/runs/20250101__12-34-56_seed000
python scripts/re_bench_compare.py data/runs/20250101__12-34-57_seed001
# ... etc
```

### Example 3: Re-run Baseline Comparison

If you want to re-run baseline comparison with different parameters:

```bash
python scripts/re_bench_compare.py data/runs/20250101__12-34-56_seed000 \
  --num-seeds 20 \
  --model groq:llama-3.3-70b-versatile \
  --verbose
```

## Output Structure

### Batch Results (`batch_results.json`)

```json
{
  "summary": {
    "batch_id": "re-bench-banking77_20250101_123456",
    "task": "data/tasks/prepared/re-bench-banking77",
    "num_seeds": 10,
    "completed": 10,
    "failed": 0,
    "mean_evaluation_score": 0.75,
    "mean_combined_score": 0.72,
    "total_elapsed_seconds": 3600.0
  },
  "runs": [
    {
      "run_dir": "data/runs/20250101__12-34-56_seed000",
      "run_id": "20250101__12-34-56_seed000",
      "seed": 0,
      "status": "completed",
      "elapsed_seconds": 360.0,
      "evaluation_score": 0.75,
      "lm_score": 0.80,
      "combined_score": 0.72,
      "baseline_delta": 0.15,
      "evaluation": {...},
      "baseline_comparison": {...}
    },
    ...
  ]
}
```

### Run Directories (`runs.txt`)

Simple text file with one run directory per line:

```
data/runs/20250101__12-34-56_seed000
data/runs/20250101__12-34-57_seed001
data/runs/20250101__12-34-58_seed002
...
```

### Individual Run Results

Each run directory contains:
- `artifacts/`: Codex execution artifacts (diff.patch, logs, traces)
- `evaluation_results.json`: Task evaluation results
- `re_bench_comparison.json`: Baseline comparison results (if run)
- `re_bench_comparison.txt`: Human-readable comparison report (if run)

### Task Folder Results

Results are also saved to the task folder for aggregation:
- `task_dir/evaluation_results/<run_id>.json`: Complete evaluation results

## Querying Results

### Using Reward Types

Each evaluation result includes standardized `reward_types` for easy querying:

```python
import json
from pathlib import Path

# Load batch results
with open("data/runs/banking77_full_eval/batch_results.json") as f:
    batch = json.load(f)

# Get all baseline deltas
baseline_deltas = []
for run in batch["runs"]:
    if run.get("baseline_comparison"):
        comparison = run["baseline_comparison"]
        rewards = comparison.get("reward_types", [])
        deltas = [r for r in rewards if r["type"] == "baseline_delta"]
        if deltas:
            baseline_deltas.append(deltas[0]["value"])

print(f"Mean baseline delta: {sum(baseline_deltas) / len(baseline_deltas):.3f}")

# Get costs
costs = []
for run in batch["runs"]:
    if run.get("baseline_comparison"):
        comparison = run["baseline_comparison"]
        rewards = comparison.get("reward_types", [])
        cost_rewards = [r for r in rewards if r["type"] == "quantitative_metric" and r["subtype"] == "cost_usd"]
        if cost_rewards:
            costs.append(cost_rewards[0]["value"])

print(f"Mean cost: ${sum(costs) / len(costs):.4f}")
```

### Querying Across Tasks

Results are saved to task folders, making it easy to aggregate across multiple batches:

```python
from pathlib import Path
import json

task_dir = Path("data/tasks/prepared/re-bench-banking77")
eval_dir = task_dir / "evaluation_results"

# Load all evaluation results
all_results = []
for result_file in eval_dir.glob("*.json"):
    with open(result_file) as f:
        all_results.append(json.load(f))

# Aggregate metrics
baseline_deltas = []
for result in all_results:
    rewards = result.get("reward_types", [])
    deltas = [r for r in rewards if r["type"] == "baseline_delta"]
    if deltas:
        baseline_deltas.append(deltas[0]["value"])

print(f"Total runs: {len(all_results)}")
print(f"Mean baseline delta: {sum(baseline_deltas) / len(baseline_deltas):.3f}")
```

## Reward Types

Each evaluation includes standardized reward types for querying:

### `baseline_delta`
Baseline improvement/regression metrics.

**Subtype:** `None`

**Metadata:**
- `baseline_score`: Baseline score before patch
- `patched_score`: Score after patch
- `absolute_improvement`: Absolute improvement
- `relative_lift_percent`: Relative lift percentage
- `weight`: Weight in combined score

### `qualitative_rubric`
LLM-based rubric scores.

**Subtypes:** Individual rubric IDs (e.g., `code_quality`, `functionality`) or `weighted_average`

**Metadata:**
- `weight`: Rubric weight
- `reasoning`: LLM reasoning
- `evidence`: Supporting evidence
- `suggestions`: Improvement suggestions

### `pass_pass_test`
Code quality checks (pytest, ruff, type_check).

**Subtypes:** `pytest`, `ruff`, `type_check`, `overall`

**Metadata:**
- `baseline_success`: Whether baseline passed
- `patched_success`: Whether patched version passed
- `baseline_violations`: Violation count (for ruff/type_check)
- `patched_violations`: Violation count (for ruff/type_check)
- `violation_delta`: Change in violations

### `quantitative_metric`
Quantitative metrics from execution traces.

**Subtypes:** `cost_usd`, `time_taken_seconds`, `input_tokens`, `output_tokens`, `tool_calls_count`, `llm_calls`

**Metadata:**
- `category`: Metric category (cost, time, tokens, etc.)
- `unit`: Unit of measurement

### `combined_score`
Final weighted combined score.

**Subtype:** `None`

**Metadata:**
- `baseline_delta_weight`: Weight of baseline delta
- `rubric_weight`: Weight of rubric scores
- `pass_pass_weight`: Weight of pass->pass tests

## Tips and Best Practices

1. **Start Small**: Run with `--num-seeds 1` first to verify everything works
2. **Skip Baseline Comparison Initially**: Baseline comparison is slow. Run it separately if needed
3. **Use `--skip-eval`**: If re-running, use `--skip-eval` to reuse existing evaluations
4. **Check Logs**: Each run directory contains detailed logs in `artifacts/`
5. **Aggregate Results**: Use the reward types structure to query across multiple batches

## Troubleshooting

### Codex Run Fails

- Check API keys are set correctly
- Verify codex config is valid
- Check task directory structure

### Evaluation Fails

- Ensure task has `tb_meta.json` with evaluation rubrics
- Check that `OPENAI_API_KEY` is set for LLM evaluation
- Verify run directory has required artifacts

### Baseline Comparison Fails

- Ensure `GROQ_API_KEY` or appropriate API key is set
- Check that patch file exists in `artifacts/diff.patch`
- Verify baseline SHA is available in `artifacts/baseline_sha.txt`

## Related Scripts

- `scripts/run_codex_box.sh`: Core script that runs Codex on a single task
- `src/one_shot/evaluate_run.py`: Evaluates a run against task rubrics
- `scripts/re_bench_compare.py`: Compares baseline vs patched performance

## Integration Tests

Integration tests verify the evaluation pipeline continues to work after changes.

**Run all integration tests:**
```bash
pytest tests/integration/test_rebench_evaluation.py -v
```

**Run fast tests only (skip slow baseline comparison):**
```bash
pytest tests/integration/test_rebench_evaluation.py -v -m "not slow"
```

**Run slow tests (includes baseline comparison):**
```bash
pytest tests/integration/test_rebench_evaluation.py -v -m slow
```

**Prerequisites:**
- `OPENAI_API_KEY` must be set (for Codex runs and LLM evaluation)
- `GROQ_API_KEY` must be set (for baseline comparison tests)
- A re-bench task must exist in `data/tasks/prepared/` (e.g., `re-bench-banking77`)

**Test Coverage:**
- `test_rebench_codex_run`: Verifies Codex can run on re-bench tasks and generate artifacts
- `test_rebench_evaluation`: Verifies evaluation pipeline works correctly
- `test_rebench_baseline_comparison`: Verifies baseline comparison (slow, requires GROQ_API_KEY)
- `test_rebench_batch_runner`: Verifies batch runner with config files

These tests ensure the evaluation pipeline remains functional after code changes.

