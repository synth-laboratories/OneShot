# OneShot Task Creation System

A system for capturing coding sessions with codex-synth through MITM proxy and materializing task scaffolds for review. This runs codex-synth interactively with MCP tools for task tracking.

## Overview

This system provides codex-synth with MCP tools (start_task/end_task) to automatically capture coding sessions, generate diffs, save traces, and create complete task artifacts.

## Components

### Core Files

- **`mcp_oneshot_server.py`** - MCP server providing start-task and end-task tools for codex-synth
- **`oneshot.sh`** - Simple wrapper to run codex-synth with OneShot MCP tools
- **`create_task.sh`** - One-shot task creation with automatic start/end instructions
- **`run_codex_with_oneshot.sh`** - Run codex-synth with task title and description
- **`tool_server.py`** - HTTP fallback server (if MCP not available)
- **`test_oneshot.py`** - Comprehensive test suite
- **`Makefile`** - Convenient interface for all operations

### Features

- **MCP and HTTP modes** - Support for both MCP (stdio) and HTTP tool servers
- **Worktree readiness checks** - Validates git state before starting tasks
- **Automatic task scaffolding** - Generates complete task structure with all artifacts
- **Trace integration** - Captures and exports cleaned traces from proxy
- **Docker support** - Can run in containerized environment

## Quick Start

### Prerequisites

1. Ensure proxy workers are running (optional but recommended):
```bash
./scripts/start_synth_workers.sh
```

2. Install codex-synth (if not already installed):
```bash
npm install -g codex-synth
```

### Usage Methods

#### Method 1: One-shot task creation
```bash
# Simple task with automatic start/end
make create-task TASK="Add a section about testing to README.md"

# Or directly:
./create_task.sh "Fix the login bug in auth.js"
```

#### Method 2: Interactive session with OneShot tools
```bash
# Start interactive codex-synth with MCP tools available
make interactive

# Or:
./oneshot.sh
```

Then tell the agent to use `repo.start_task.v1` and `repo.end_task.v1` tools.

#### Method 3: Task with detailed instructions
```bash
make run-task TITLE="Refactor auth" DESC="Refactor authentication module for better security"

# Or:
./run_codex_with_oneshot.sh -t "Refactor auth" -d "Detailed instructions here"
```

### Test the System

Run quick tests:
```bash
python3 test_oneshot.py --quick
```

Test HTTP server:
```bash
make test-server
```

Test readiness checks:
```bash
make test-readiness
```

## Output Structure

Tasks are created under `data/tasks/created/<task_slug>/`:

```
<task_slug>/
├── tb_meta.json              # Task metadata
├── LM_INSTRUCTIONS.md        # Instructions given to agent
├── repo_info.json            # Git repository information
├── diff.patch                # Complete diff of changes
├── trace/
│   ├── session_id.txt        # Trace session identifier
│   └── session_clean.json    # Cleaned trace data
├── evaluation/
│   ├── rubric_template.md    # Evaluation rubric template
│   └── tests_skeleton/       # Test skeleton files
└── notes.md                  # Task notes and TODOs
```

## How It Works

1. **MCP Configuration**: The scripts automatically configure codex-synth to use the CITB MCP server by creating/updating `~/.codex-synth/mcp_settings.json`

2. **Task Tracking**: The agent is instructed to:
   - Call `repo.start_task.v1` at the beginning (creates git commit, captures start state)
   - Work on the requested task
   - Call `repo.end_task.v1` when done (creates diff, exports traces, generates artifacts)

3. **Output**: Tasks are saved to `data/tasks/created/<task_slug>/` with all artifacts

## HTTP API Endpoints

When using HTTP mode (default):

- `GET /health` - Health check
- `POST /start-task` - Start a new task
- `POST /end-task` - End current task
- `POST /check-readiness` - Check worktree readiness
- `POST /autofix-readiness` - Auto-fix worktree issues

## Advanced Usage

### Docker Mode
```bash
make create-task TITLE="Task" DOCKER=1
```

### With Proxy Start
```bash
make create-task TITLE="Task" PROXY=1
```

### Using MCP
```bash
make create-task TITLE="Task" MCP=1
```

## Troubleshooting

1. **Port conflicts**: Default ports are 8080 (tool server) and 18080 (proxy)
2. **Git issues**: Run `make test-readiness` to check worktree state
3. **Trace not found**: Ensure proxy workers are running
4. **MCP not working**: Check `~/.codex/config.toml` configuration

## Development

Run all tests:
```bash
python3 test_oneshot.py -v
```

Clean temporary files:
```bash
make clean
```

Check dependencies:
```bash
make install-deps
```