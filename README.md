# OneShot Bench

Scalably converting pair-programming CLI trajectories into challenging digital agent tasks.

## Overview

OneShot Bench is a comprehensive evaluation framework for AI coding agents. It captures successful human-AI pair programming sessions and converts them into standardized evaluation tasks that can be run across different models and environments.

### Key Features

- **Task Capture**: Record successful coding sessions with Codex MCP tools
- **Reproducible Evaluation**: Run tasks in Docker or Modal sandboxes
- **Multi-Model Support**: Compare different AI models on the same tasks
- **Dataset Management**: Share and reuse tasks via Hugging Face
- **Advanced Overrides**: Customize evaluation environments
- **Benchmarking**: Run large-scale parallel evaluations

## Quick Start

### 1. Installation

```bash
# Install codex-synth wrapper
bash scripts/install_codex_synth.sh

# Install Python dependencies
uv sync
```

### 2. Start MITM workers

```bash
# install the proxy
uv tool install mitmproxy

# start the proxy
bash scripts/start_synth_workers.sh

# stop the proxy when finished using OneShot
# bash scripts/trace_session_monitor.sh cleanup
```

### 3. Create and Run Your First Task

```bash
# Start Codex with MCP tools enabled
codex-synth

# In Codex, create a task:
# "Add a hello world section to the README. Use start_task tool to begin and end_task tool to finish."

# Run the created task
bash scripts/run_codex_box.sh data/tasks/created/your-task-slug
```

### 4. View Results

Results are saved in `data/runs/<run_id>/` with:
- Evaluation scores and rubric details
- Generated code diffs and artifacts
- Detailed logs and debugging information

## Documentation

The guides are organized into four main sections:

### 📚 **Hello World** - Basic Task Workflow
[View Guide](guides/hello_world/README.md)

Get started with the basic workflow:
- Installing and setting up Codex
- Creating your first task interactively
- Running tasks in Docker and Modal
- Understanding evaluation results

### 🤗 **Hugging Face** - Dataset Management
[View Guide](guides/huggingface/README.md)

Share and reuse evaluation tasks:
- Export prepared tasks to JSONL format
- Upload datasets to Hugging Face
- Download and run tasks from HF datasets
- Parallel evaluation with Modal

### ⚙️ **Overrides** - Environment Customization
[View Guide](guides/overrides/README.md)

Control the evaluation sandbox:
- Remove/add files to the environment
- Customize repository settings
- Inject custom files and configurations
- Set environment variables and LM instructions

### 📊 **Benchmarking** - Large-Scale Evaluation
[View Guide](guides/benchmarking/README.md)

Run comprehensive benchmarks:
- Configure multiple tasks in parallel
- Multiple rollouts per task for statistics
- Compare different models and settings
- Analyze and aggregate results

## Architecture

```
OneShot Bench Components:
├── Task Creation (MCP Tools)
├── Task Preparation (Docker Images)
├── Evaluation Runners (Docker/Modal)
├── Result Analysis (Rubrics & Metrics)
├── Dataset Management (Hugging Face)
└── Benchmark Orchestration (Parallel Execution)
```

## Example Commands

```bash
# Create a task interactively
codex-synth

# Prepare a created task
uv run one_shot.prepare_task_for_eval --task-dir data/tasks/created/task-slug

# Run evaluation
scripts/run_codex_box.sh data/tasks/prepared/task-slug

# Run with overrides
uv run one_shot run-with-overrides data/tasks/prepared/task-slug overrides.json

# Run benchmark
python scripts/eval_rollouts.py run configs/your_benchmark.toml

# Export to Hugging Face
uv run one_shot.hf.export --out dataset.jsonl --split train
```

## Common Workflows

### Individual Task Evaluation
1. Create task with Codex MCP tools
2. Prepare task for evaluation
3. Run in Docker or Modal
4. Review results and scores

### Dataset Creation
1. Create multiple tasks
2. Export to JSONL format
3. Upload to Hugging Face
4. Share with community

### Model Comparison
1. Set up benchmark configuration
2. Run across multiple models
3. Compare results and metrics
4. Identify best performing models

## Contributing

- **Bug Reports**: Open issues on GitHub
- **Feature Requests**: Use GitHub discussions
- **Code Contributions**: Submit pull requests
- **Dataset Contributions**: Upload tasks to Hugging Face

## Resources

- **Hugging Face Dataset**: [JoshPurtell/one-shot-bench](https://huggingface.co/datasets/JoshPurtell/one-shot-bench)
- **Documentation**: [OneShot Bench Guides](./guides/)
- **Source Code**: [GitHub Repository](https://github.com/your-org/one-shot-bench)

---

**Quick Links:**
- [Hello World Guide](guides/hello_world/README.md) - Get started now!
- [Hugging Face Guide](guides/huggingface/README.md) - Share your tasks
- [Overrides Guide](guides/overrides/README.md) - Customize environments
- [Benchmarking Guide](guides/benchmarking/README.md) - Scale up evaluation