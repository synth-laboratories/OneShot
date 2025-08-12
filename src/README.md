# Codex Coach - AI Task Creation and Testing Framework

### TL;DR: Create and evaluate a new question

- Ensure prerequisites: Docker running, `.env` at repo root with `OPENAI_API_KEY`.
- Start tracing workers (proxy + cleaner):
  - `./development/codex_coach/run_synth_workers.sh`
- Create a task (pick one):
  - Interactive: `./development/codex_coach/create_sb_tasks/citb.sh` (then use `repo_start_task` → do work → `repo_end_task`)
  - One‑shot: `./development/codex_coach/create_sb_tasks/create_task.sh "Add unit tests for X"`
- Convert the created task to prepared format:
  - `uv run python ./development/codex_coach/one_shot_bench/prepare_task_for_eval.py ./development/codex_coach/one_shot_bench/tasks/created/<task_id>`
  - Output goes to: `./development/codex_coach/one_shot_bench/tasks/prepared/<task_name>`
- Run the evaluation in Docker:
  - `./development/codex_coach/one_shot_bench/run_codex_box.sh ./development/codex_coach/one_shot_bench/tasks/prepared/<task_name> 1800 100000`
- View results and logs:
  - Runs dir: `./data/runs/<run_id>`
  - Artifacts, diffs, pytest output, and scoring summaries are saved per run.

Note: `tasks/prepared/` is the canonical target (replaces the deprecated `tasks/generated/`).

A comprehensive system for creating coding tasks with AI agents (via codex-synth) and testing other AI agents on those tasks in isolated Docker containers.

## Overview

Codex Coach enables you to:
1. **Create tasks** by capturing AI coding sessions with full git history and API traces
2. **Test AI agents** on those tasks in reproducible Docker environments
3. **Evaluate performance** by comparing agent outputs to reference implementations

## Quick Start

### Prerequisites

- Docker installed and running
- Node.js and npm (for codex-synth)
- Python 3.8+
- OpenAI API key in `.env` file at repo root

### Setup

1. Install codex-synth (OpenAI's Codex CLI):
```bash
npm install -g @openai/codex
```

2. Start the proxy and trace workers:
```bash
./run_synth_workers.sh
```
This starts:
- MITM proxy on port 18080 for API tracing
- Trace cleaner for processing captured API calls

3. Configure MCP tools for task creation:
```bash
./create_sb_tasks/citb.sh setup
```

## Creating Tasks with CITB (Codex-in-the-Box)

CITB allows you to capture coding sessions as reproducible tasks.

### Method 1: Interactive Session with MCP Tools

Start an interactive codex session with task tracking:
```bash
./create_sb_tasks/citb.sh
```

In the session:
1. Call `repo_start_task` to begin tracking
2. Complete your coding task
3. Call `repo_end_task` to save the task

### Method 2: One-Shot Task Creation

Create a task with a single command:
```bash
./create_sb_tasks/create_task.sh "Add unit tests for the user authentication module"
```

### Created Task Structure

Tasks are saved to `one_shot_bench/tasks/created/<task_id>/` with:
```
<task_id>/
├── tb_meta.json           # Task metadata and instructions
├── diff.patch             # Git diff of changes made
├── repo_info.json         # Repository context (branch, commits)
├── trace/
│   ├── session_clean.json # Cleaned API trace (can be large)
│   └── session_id.txt     # Session identifier
├── evaluation/            # Evaluation scaffolding
└── notes.md              # Human-readable notes
```

## Testing AI Agents on Tasks

### Running a Task in Docker

Test an AI agent on a captured task:
```bash
./one_shot_bench/run_codex_box.sh \
  one_shot_bench/tasks/generated/<task_name> \
  1800 \   # timeout in seconds
  100000   # token limit
```

This will:
1. Build a Docker container at the task's start commit
2. Provide the task instructions to the AI agent
3. Let the agent attempt the task
4. Save results for comparison

### Example Tasks

- `add-lm-tracing-readme` - Add documentation about LM tracing
- `add-first-unit-test-to-repo` - Create unit tests for trace_cleaner module

### Results

Results are saved to `one_shot_bench/runs/<timestamp>/` containing:
- Agent's code changes
- Execution logs
- Token usage statistics
- Success/failure status

## Architecture

### Components

1. **CITB (Codex-in-the-Box) Task Creator**
   - `create_sb_tasks/mcp_citb_server.py` - MCP server providing task tracking tools
   - `create_sb_tasks/citb.sh` - Wrapper for interactive sessions
   - `create_sb_tasks/create_task.sh` - One-shot task creation

2. **Proxy & Tracing**
   - `mitm_tracer.py` - MITM proxy addon for API tracing
   - `trace_cleaner.py` - Cleans and formats captured traces
   - `run_synth_workers.sh` - Starts proxy and trace workers

3. **Task Runner**
   - `one_shot_bench/run_codex_box.sh` - Main orchestrator for running tasks
   - `one_shot_bench/common.sh` - Shared utilities
   - Docker containers for isolated execution

### MCP Tools Available

When creating tasks, these MCP tools are available:
- `repo_start_task` - Begin tracking a task with title and labels
- `repo_end_task` - Complete task and save artifacts
- `repo_check_readiness` - Verify git repository state
- `repo_autofix_readiness` - Fix common git issues

## Converting Tasks

To convert a CITB-created task for use with run_codex_box.sh:

1. Copy core files to `one_shot_bench/tasks/generated/<name>/`
2. Add Docker configuration files (Dockerfile, bootstrap scripts)
3. Update instructions in tb_meta.json to be specific and actionable

## Environment Variables

Required in `.env` at repository root:
```bash
OPENAI_API_KEY=<your-key>
# Optional for other providers:
ANTHROPIC_API_KEY=<your-key>
```

## Troubleshooting

### Check proxy status
```bash
curl -x http://localhost:18080 https://api.openai.com/v1/models
```

### Monitor MCP server logs
```bash
tail -f /tmp/citb_mcp_server.out
```

### View trace database
```bash
sqlite3 development/codex_coach/traces/v3/clean_synth_ai.db/traces.sqlite3
```

## Advanced Usage

### Custom Task Creation

Create tasks programmatically by calling the MCP server directly:
```python
from development.codex_coach.create_sb_tasks.mcp_citb_server import CITBTaskManager

manager = CITBTaskManager()
manager.start_task("My Task", notes="Task details", labels=["test"])
# ... do work ...
manager.end_task("Completed task successfully", labels=["done"])
```

### Batch Testing

Run multiple tasks sequentially:
```bash
for task in one_shot_bench/tasks/generated/*/; do
  ./one_shot_bench/run_codex_box.sh "$task" 1800 100000
done
```

## Directory Structure

```
development/codex_coach/
├── create_sb_tasks/          # CITB task creation tools
│   ├── mcp_citb_server.py   # MCP server for task tracking
│   ├── citb.sh               # Interactive session wrapper
│   └── create_task.sh        # One-shot task creation
├── one_shot_bench/          # Current task runner
│   ├── tasks/
│   │   ├── created/          # Raw CITB task outputs
│   │   └── generated/        # Converted tasks ready to run
│   ├── runs/                 # Test execution results
│   └── run_codex_box.sh     # Main task runner
├── traces/                   # API trace databases
├── mitm_tracer.py           # Proxy addon for tracing
└── trace_cleaner.py         # Trace processing
```

## Legacy Proxy Tracing

The original README content below describes the proxy tracing system that powers the task creation:

---

# Codex Coach - Forward Proxy for Codex Tracing

Codex Coach is a mitmproxy-based forward proxy that intercepts and traces Codex CLI's interactions with the OpenAI API. It preserves your Pro authentication while logging all requests and responses to a SQLite database for analysis.

## Quick Start

```bash
# Install everything
./install.sh

# Start using Codex with tracing
codex-synth
```

That's it! The proxy will automatically start when you run `codex-synth`.

## Features

- **Transparent Proxying**: Routes all Codex traffic through mitmproxy on port 18080
- **Pro Authentication Preserved**: Uses forward proxy (not reverse) to maintain your OpenAI Pro auth
- **Request/Response Tracing**: Logs all API interactions to SQLite database
- **Session Management**: Automatically rotates sessions based on idle time
- **Debug Logging**: Comprehensive logging for troubleshooting
- **Statistics**: View usage patterns and costs with `codex-synth /stats`

## How It Works

1. **Forward Proxy Architecture**: Unlike reverse proxies that change the API endpoint, this uses a forward HTTPS proxy that intercepts traffic while preserving the original `api.openai.com` destination
2. **Clean Environment Launch**: Launches Codex in a sanitized environment to prevent configuration conflicts
3. **Automatic Proxy Management**: Starts mitmproxy automatically if not running

## Commands

The `codex-synth` wrapper provides several utility commands:

```bash
codex-synth              # Start Codex with proxy tracing
codex-synth /test        # Test proxy connectivity
codex-synth /logs        # View last 50 debug log entries  
codex-synth /stats       # View trace statistics from database
codex-synth /env         # Check environment variables
codex-synth /hello       # Test command - prints greeting
codex-synth /now         # Test command - prints current time
```

## File Structure

```
codex_coach/
├── README.md            # This file
├── install.sh           # One-click installer
├── run_mitm_proxy.sh    # Manual proxy starter (optional)
├── mitm_tracer.py       # mitmproxy addon for tracing
├── codex_stats.sh       # Statistics viewer
├── traces/              # SQLite database location
│   └── v3/
│       └── synth_ai.db/
│           └── traces.sqlite3
└── old/                 # Deprecated reverse proxy scripts
```

## Troubleshooting

### Proxy Not Routing Traffic

1. Check if proxy is running:
   ```bash
   lsof -iTCP:18080 -sTCP:LISTEN
   ```

2. Test proxy connectivity:
   ```bash
   codex-synth /test
   ```

3. View debug logs:
   ```bash
   tail -f /tmp/codex-synth.log
   # or
   codex-synth /logs
   ```

### Certificate Errors

If you see TLS/SSL errors:

1. The installer should have created the certificate at `~/.mitmproxy/mitmproxy-ca-cert.pem`
2. Start the proxy and visit http://mitm.it
3. Download and install the certificate for your OS
4. On macOS: Open Keychain Access, find the mitmproxy cert, and trust it for SSL

### Conflicting Configuration

If Codex is still using old configurations:

1. Check for shell functions/aliases:
   ```bash
   type codex-synth
   ```
   Should show the script path, not a function

2. Remove old environment variables:
   ```bash
   unset OPENAI_BASE_URL OPENAI_API_BASE OPENAI_API_HOST
   ```

3. Check ~/.zshrc or ~/.bashrc for old `codex-synth` definitions and remove them

### Database Location

Traces are stored in: `traces/v3/synth_ai.db/traces.sqlite3`

View recent traces:
```bash
codex-synth /stats
```

Or query directly:
```bash
sqlite3 traces/v3/synth_ai.db/traces.sqlite3 \
  "SELECT datetime(ts_ms/1000,'unixepoch'), 
          json_extract(request_json,'$.model'),
          json_extract(meta_json,'$.upstream_host')
   FROM traces ORDER BY ts_ms DESC LIMIT 10;"
```

## Manual Proxy Start

If you want to run the proxy in the foreground (for debugging):

```bash
./run_mitm_proxy.sh
```

Then in another terminal:
```bash
codex-synth
```

## Environment Variables

- `PROXY_PORT`: Change proxy port (default: 18080)
- `SESSION_IDLE_SECS`: Session rotation timeout (default: 120)
- `DEBUG`: Enable/disable debug logging (default: 1)

## Requirements

- **codex**: The Codex CLI must be installed
- **uv/uvx**: For running mitmproxy (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **mitmproxy**: Installed automatically via uvx

## Uninstalling

To remove the installation:

```bash
# Remove the wrapper
rm ~/.local/bin/codex-synth

# Kill any running proxy
pkill -f mitmdump || true

# Remove logs
rm -f /tmp/codex-synth.log /tmp/codex_mitm.log
```

## Old Scripts (Deprecated)

The `old/` directory contains deprecated reverse proxy scripts that modified `OPENAI_BASE_URL` to point to localhost. These are kept for reference but should not be used as they can break Pro authentication.

## Support

For issues or questions, check the debug logs first:
```bash
codex-synth /logs
```

The logs will show exactly what environment variables are set and whether the proxy is routing traffic correctly.