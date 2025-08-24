# Hello World - Basic Task Workflow

Get started with OneShot Bench by capturing a task, saving it as a data point, and running evaluation.

## Quick Start

### 1. Install Codex Synth

```bash
# Install the codex-synth wrapper
bash scripts/install_codex_synth.sh

# Restart your shell or ensure ~/.local/bin is in PATH
```

### 2. Create Your First Task

**Interactive Method (Recommended):**

```bash
# Start a Codex session
export RUN_ID="hello_world_$(date +%s)"
codex-synth
```

In the Codex chat:
> Hi Codex, please add a "Hello World" section to the README. Use the start_task tool to begin and end_task tool to finish.

**Non-Interactive Method:**

```bash
# Use the helper script
./scripts/create_tasks/create_task.sh "Add Hello World section to README"
```

### 3. Run the Task

**Direct from Created Task (auto-prepares):**
```bash
bash scripts/run_codex_box.sh data/tasks/created/add-hello-world-section_20250101_120000
```

**From Prepared Task:**
```bash
# First prepare the task
uv run one_shot.prepare_task_for_eval --task-dir data/tasks/created/add-hello-world-section_20250101_120000

# Then run it
scripts/run_codex_box.sh data/tasks/prepared/add-hello-world-section
```

## What Happens During Task Creation

When you create a task, the following artifacts are saved:

```
data/tasks/created/<task_slug>/
├── tb_meta.json              # Task metadata (title, rubrics, tests)
├── overlay_files/           # Files to inject into sandbox
│   ├── LM_INSTRUCTIONS.md   # Instructions for the agent
│   ├── diff.patch          # Your successful changes
│   ├── repo_info.json      # Repository state
│   └── notes.md            # Task notes
├── evaluation/             # Test scaffolding
└── trace/                  # Session traces (if tracing enabled)
```

## Task Preparation

The preparation step converts a created task into an evaluation-ready format:

```bash
uv run one_shot.prepare_task_for_eval --task-dir data/tasks/created/<task_slug>
```

This creates:
```
data/tasks/prepared/<task_slug>/
├── tb_meta.json              # Enhanced with evaluation config
├── Dockerfile               # Sandbox environment
├── overlay_files/           # Merged injection files
└── evaluation/              # Rubrics and unit tests
```

## Running Evaluation

### Docker (Local)

```bash
# Basic run
scripts/run_codex_box.sh data/tasks/prepared/hello-world-task

# With custom timeout and token limit
scripts/run_codex_box.sh data/tasks/prepared/hello-world-task 900 50000
```

**Results Location:**
- Run artifacts: `data/runs/<run_id>/`
- Logs and diffs: `data/runs/<run_id>/artifacts/`
- Evaluation scores: `data/runs/<run_id>/evaluation_results.json`

### Modal (Cloud)

```bash
# Set up Modal (one-time)
uv tool install modal && modal setup

# Run on Modal
export OPENAI_API_KEY="your-key-here"
SANDBOX_BACKEND=modal scripts/run_codex_box.sh data/tasks/prepared/hello-world-task
```

## Understanding Results

After evaluation, check the results:

```bash
# View evaluation summary
cat data/runs/<run_id>/scoring_results.md

# View detailed results
cat data/runs/<run_id>/evaluation_results.json
```

**Example Output:**
```
Rubric total score: 85%
- task_completion: 100% (weight=0.4)
- code_quality: 80% (weight=0.3)
- testing: 80% (weight=0.3)
Unit tests: 2 passed, 0 failed
```

## Common Issues

### Task Creation Fails
- Ensure MCP tools are enabled: `bash scripts/create_tasks/setup_codex_mcp.sh`
- Check that Codex can access the tools by asking "What tools do you have?"

### Docker Run Fails
- Ensure Docker is running: `docker ps`
- Check if codex is installed: `which codex`
- Verify task directory exists and has `tb_meta.json`

### Modal Run Fails
- Ensure Modal is set up: `modal setup`
- Check Modal secrets: `modal secret list`
- Verify OpenAI API key is set

## Next Steps

- **Multiple Tasks**: See [Benchmarking Guide](../benchmarking/README.md)
- **Hugging Face**: See [Hugging Face Guide](../huggingface/README.md)
- **Advanced Overrides**: See [Overrides Guide](../overrides/README.md)
