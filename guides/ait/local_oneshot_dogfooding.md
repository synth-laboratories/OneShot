# Local OneShot Dogfooding: Creating New Data Points

This guide walks through creating a new data point for OneShot Bench by adding information about the repo to the README using traced codex-synth.

## Quick Reference: Key Shell Scripts

| Script | Purpose | Location |
|--------|---------|----------|
| `start_synth_workers.sh` | **MAIN SCRIPT** - Start MITM proxy, install codex-synth, setup MCP | `scripts/start_synth_workers.sh` |
| `test_mitm_proxy.py` | Test proxy functionality | `guides/ait/test_mitm_proxy.py` |
| `install_codex_synth.sh` | Install codex-synth wrapper (auto-called by main script) | `scripts/install_codex_synth.sh` |
| `setup_codex_mcp.sh` | Enable MCP tools for task creation (auto-called by main script) | `scripts/create_tasks/setup_codex_mcp.sh` |
| `create_task.sh` | One-shot task creation script | `scripts/create_tasks/create_task.sh` |
| `monitor_live.sh` | Monitor MCP server activity | `scripts/create_tasks/monitor_live.sh` |

## Overview

OneShot Bench creates evaluation datasets by capturing successful human-AI pair programming sessions. You'll:
1. **First: Set up and test MITM proxy** (CRITICAL - this automatically installs everything needed)
2. Verify the complete setup
3. Use codex-synth with tracing enabled to capture your interaction
4. Create a task that adds repo information to the README
5. Verify task creation and trace capture
6. Convert to evaluation-ready format
7. Test evaluation locally
8. Refine and share your data point

## Step 1: Set Up and Test MITM Proxy (CRITICAL - Do This First)

The MITM proxy is essential for capturing codex-synth interactions. **All other steps will fail without a working proxy.**

### 1.1 Install mitmproxy
```bash
uv tool install mitmproxy
```

### 1.2 Start the proxy and tracing workers

**The main shell script is: `scripts/start_synth_workers.sh`**

```bash
bash scripts/start_synth_workers.sh
```

This script starts:
- **MITM proxy** on `localhost:18080` (main proxy for intercepting codex-synth traffic)
- **Trace cleaner** that processes raw traces into clean database format
- **Background processes** that run continuously and log to `/tmp/`

The script will automatically:
1. **Check and install codex-synth** if not already installed
2. **Check and configure MCP tools** if not already configured
3. Kill any existing proxy processes on the same port
4. Start `mitmdump` with the custom tracer script
5. Start the trace cleaner daemon
6. Stream logs to your terminal (Ctrl-C to stop streaming, workers keep running)

### 1.3 Run proxy tests

Test the proxy functionality using the provided test script:

```bash
# Run the proxy tests
python guides/ait/test_mitm_proxy.py
```

This will verify:
- ✅ Proxy is running and can handle HTTP traffic
- ✅ Proxy can intercept HTTPS traffic through OpenAI API
- ✅ Trace databases are accessible
- ✅ Certificate trust status (optional for testing)

### 1.4 Certificate Setup (Critical for HTTPS)

The MITM proxy needs a trusted certificate for HTTPS interception:

**macOS:**
```bash
# Visit this URL while proxy is running
open http://mitm.it
# Follow OS instructions to install certificate
```

**Linux:**
```bash
# Copy certificate to system trust store
sudo cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
sudo update-ca-certificates
```

**Manual Import (any OS):**
```bash
# Import to browser/system keychain
open ~/.mitmproxy/mitmproxy-ca-cert.pem
# Follow OS-specific import instructions
```

### 1.5 Verify Certificate Trust

After setup, re-run the proxy tests:
```bash
python guides/ait/test_mitm_proxy.py
```

All tests should pass before proceeding to Step 2.

### 1.6 Monitor Proxy Activity (Optional but Helpful)

While working with OneShot Bench, you can monitor proxy activity in separate terminals:

**Monitor MITM proxy logs:**
```bash
# View live proxy traffic
tail -f /tmp/codex_mitm.out

# Or use the structured log stream
tail -f /tmp/synth_workers.stream
```

**Monitor trace processing:**
```bash
# View trace cleaner activity
tail -f /tmp/trace_cleaner.out
```

**Check background processes:**
```bash
# See if proxy processes are running
ps aux | grep -E "(mitmdump|trace_cleaner)" | grep -v grep

# Check PIDs
cat /tmp/codex_mitm.pid 2>/dev/null && echo "Proxy PID: $(cat /tmp/codex_mitm.pid 2>/dev/null)"
cat /tmp/trace_cleaner.pid 2>/dev/null && echo "Cleaner PID: $(cat /tmp/trace_cleaner.pid 2>/dev/null)"
```

**Stop proxy processes:**
```bash
# Kill all proxy processes
pkill -f mitmdump
pkill -f trace_cleaner
rm -f /tmp/codex_mitm.pid /tmp/trace_cleaner.pid
```

## Step 2: Verify Setup (Already Done by Step 1)

The `start_synth_workers.sh` script automatically handled the installation and configuration. Verify everything is working:

```bash
# Verify codex-synth is installed
type codex-synth

# Verify MCP tools are configured
codex-synth
# In Codex, ask: "What tools do you have?"
# You should see: repo.start_task.v1, repo.end_task.v1, repo.check_readiness.v1, repo.autofix_readiness.v1
```

**Note:** If you need to manually reinstall or reconfigure, you can still run:
- `bash scripts/install_codex_synth.sh`
- `bash scripts/create_tasks/setup_codex_mcp.sh`

## Step 3: Create Task with Traced Codex

### Method A: Interactive MCP (Recommended)

1. **Set up environment variables with proxy:**
   ```bash
   export HTTP_PROXY=http://127.0.0.1:18080
   export HTTPS_PROXY=http://127.0.0.1:18080
   export ALL_PROXY=http://127.0.0.1:18080
   export RUN_ID="dogfood_readme_$(date +%s)"
   ```

2. **Launch codex-synth:**
   ```bash
   codex-synth
   ```

3. **In Codex, start the task:**
   ```
   Use the repo.start_task.v1 tool with:
   - Title: "Add comprehensive repo information to README"
   - Notes: "Creating OneShot Bench data point for README enhancement"
   - Labels: ["documentation", "readme", "dogfooding"]
   ```

4. **Complete the work:**
   Ask Codex to analyze the repo and add relevant information to the README, such as:
   - Architecture overview
   - Key components and their purposes
   - Setup and installation instructions
   - Usage examples
   - Contributing guidelines

5. **End the task:**
   ```
   Use repo.end_task.v1 with a summary of what was added
   ```

### Method B: One-Shot Script

Use the helper script for non-interactive creation:

```bash
./scripts/create_tasks/create_task.sh "Add comprehensive repository information to README including architecture overview, setup instructions, and usage examples"
```

## Step 4: Verify Task Creation and Trace Capture

Check that your task was created successfully and traces were captured:

```bash
# List created tasks
ls -la data/tasks/created/

# Check the latest task directory
LATEST_TASK=$(ls -td data/tasks/created/*/ | head -1)
ls -la "$LATEST_TASK"

# Verify trace capture
echo "=== Verifying Trace Capture ==="
sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 \
  "SELECT session_id, substr(formatted_json,1,100) FROM cleaned_sessions ORDER BY session_id DESC LIMIT 3;"
```

The task directory should contain:
- `tb_meta.json` - Task metadata
- `overlay_files/` - Task artifacts including:
  - `diff.patch` - The changes made
  - `LM_INSTRUCTIONS.md` - Instructions for the agent
  - `repo_info.json` - Repository information
  - `notes.md` - Task notes

**Critical:** If no traces appear, the proxy setup failed. Return to Step 1 and fix proxy issues.

## Step 5: Prepare Task for Evaluation

Convert the created task to evaluation-ready format:

```bash
# Prepare the task (replace with your actual task directory)
uv run one_shot_bench.prepare_task_for_eval --task-dir data/tasks/created/add-repo-info-readme_20250101_120000
```

This will create a prepared version in `data/tasks/prepared/` with:
- Docker-ready environment
- Evaluation rubric and unit tests
- Cleaned trace data
- All necessary artifacts for evaluation

## Step 6: Test Evaluation Locally

Run the prepared task locally to test the evaluation:

```bash
# Run with Docker (adjust path to your prepared task)
bash scripts/run_codex_box.sh data/tasks/prepared/add-repo-info-readme 900 50000
```

Check the results:
```bash
# View the latest run results
LATEST_RUN=$(ls -td data/runs/*/ | head -1)
cat "$LATEST_RUN/results.json"
cat "$LATEST_RUN/scoring_results.md"
```

## Step 7: Refine and Iterate

If the evaluation results aren't satisfactory:

1. **Adjust the evaluation rubric:**
   - Edit `data/tasks/prepared/<task>/evaluation/rubric_template.md`
   - Modify scoring criteria and weights

2. **Update unit tests:**
   - Edit `data/tasks/prepared/<task>/evaluation/tests_skeleton/test_skeleton.py`
   - Add or modify test cases

3. **Re-run the evaluation:**
   ```bash
   bash scripts/run_codex_box.sh data/tasks/prepared/<task>
   ```

## Step 8: Share Your Data Point

Once satisfied with the results:

1. **Upload to Hugging Face (optional):**
   ```bash
   uv run python scripts/upload_prepared_task_hf.py \
     data/tasks/prepared/<task-slug> \
     JoshPurtell/one-shot-bench \
     tasks/<task-slug> \
     --yes
   ```

2. **Document your contribution:**
   - Update this guide with any lessons learned
   - Consider contributing improvements to the OneShot Bench framework

## Troubleshooting

### Critical: Proxy Issues (Most Common Cause of Failure)

1. **Proxy not running:**
   ```bash
   # Check if proxy is responding
   curl -x http://localhost:18080/health

   # Restart proxy and workers
   bash scripts/start_synth_workers.sh

   # Wait a moment, then test
   sleep 3
   python test_mitm_proxy.py
   ```

2. **Certificate not trusted:**
   ```bash
   # Visit this URL while proxy is running
   open http://mitm.it

   # Or manually install certificate
   open ~/.mitmproxy/mitmproxy-ca-cert.pem

   # Then re-run proxy tests
   python test_mitm_proxy.py
   ```

3. **No traces captured:**
   ```bash
   # Check if traces are being written
   sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 \
     'SELECT COUNT(*) FROM cleaned_sessions;'

   # If 0, check raw database
   sqlite3 data/traces/v3/raw_synth_ai.db/traces.sqlite3 \
     'SELECT COUNT(*) FROM traces;'
   ```

### Common Issues:

4. **MCP tools not available:**
   ```bash
   # Reinstall MCP configuration
   bash scripts/create_tasks/setup_codex_mcp.sh
   exec $SHELL -l

   # In codex-synth, ask: "What tools do you have?"
   ```

5. **Task creation fails:**
   - Check that `codex-synth` is in your PATH
   - Verify RUN_ID environment variable is set
   - Check file permissions on the data directory
   - Ensure proxy is running and tested

6. **Evaluation fails:**
   ```bash
   # Check Docker is running
   docker ps

   # Check prepared task structure
   ls -la data/tasks/prepared/<task>/
   ```

### Debug Commands:

```bash
# Check MCP server logs
tail -f /tmp/mcp_oneshot_server.log

# Inspect recent traces
sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 \
  'SELECT session_id, substr(formatted_json,1,200) FROM cleaned_sessions ORDER BY session_id DESC LIMIT 5;'

# Verify task structure
find data/tasks/created/ -name "*.json" -exec cat {} \; | jq '.'

# Check proxy logs
tail -f /tmp/codex_mitm.out
tail -f /tmp/trace_cleaner.out

# Check if processes are running
ps aux | grep -E "(mitmdump|trace_cleaner)" | grep -v grep
cat /tmp/codex_mitm.pid 2>/dev/null && echo "Proxy running with PID: $(cat /tmp/codex_mitm.pid 2>/dev/null)"
cat /tmp/trace_cleaner.pid 2>/dev/null && echo "Cleaner running with PID: $(cat /tmp/trace_cleaner.pid 2>/dev/null)"

# Full proxy diagnostic
python guides/ait/test_mitm_proxy.py
```

**Note:** The `start_synth_workers.sh` script automatically handles codex-synth installation and MCP configuration. If you need to manually reinstall, use the individual scripts listed in the Quick Reference table above.

## Best Practices

1. **Clear task descriptions:** Be specific about what you want to achieve
2. **Use descriptive titles:** Help identify tasks later in the dataset
3. **Test evaluation thoroughly:** Run multiple times to ensure consistency
4. **Document edge cases:** Note any special considerations for the task
5. **Version control:** Keep track of rubric and test changes

## Example Task Flow

Here's what a successful README enhancement task might look like:

1. **Task Creation:**
   - Start with MCP tool call
   - Codex analyzes repo structure
   - Codex adds architecture section to README
   - Codex adds setup instructions
   - End with MCP tool call

2. **Evaluation Setup:**
   - Rubric checks: content completeness, accuracy, formatting
   - Unit tests verify specific sections exist
   - Trace data captures the successful approach

3. **Results:**
   - Agent gets high scores for following the pattern
   - Trace data provides training signal for similar tasks
   - Dataset grows with high-quality examples

This process creates valuable data points that help train and evaluate coding agents on real-world documentation tasks.
