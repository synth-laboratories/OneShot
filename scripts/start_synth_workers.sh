#!/usr/bin/env bash
set -euo pipefail

# Start a local MITM proxy used by one-shot-bench. Logs to /tmp and runs in background.
# Also ensures codex-synth and MCP tools are installed and configured.

PORT="${PORT:-18080}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

kill_if_pid() {
  local f="$1"
  if [ -f "$f" ]; then
    local pid
    pid=$(cat "$f" || true)
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      echo "[run] killing pid $pid from $f"
      kill "$pid" 2>/dev/null || true
      sleep 0.2 || true
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$f" || true
  fi
}

kill_port_listeners() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
    if [ -n "$pids" ]; then
      echo "[run] killing listeners on port $port: $pids"
      for pid in $pids; do kill "$pid" 2>/dev/null || true; done
      sleep 0.2 || true
      for pid in $pids; do kill -9 "$pid" 2>/dev/null || true; done
    fi
  fi
}

# Check if codex-synth is installed
check_codex_synth() {
  if command -v codex-synth >/dev/null 2>&1; then
    echo "[setup] ✓ codex-synth is already installed"
    return 0
  else
    echo "[setup] Installing codex-synth..."
    if bash "$REPO_ROOT/scripts/install_codex_synth.sh"; then
      echo "[setup] ✓ codex-synth installed successfully"
      return 0
    else
      echo "[setup] ✗ Failed to install codex-synth"
      return 1
    fi
  fi
}

# Check if MCP is configured
check_mcp_config() {
  local config_file="$HOME/.codex/config.toml"
  if [ -f "$config_file" ] && grep -q "\[mcp_servers.oneshot\]" "$config_file" 2>/dev/null; then
    echo "[setup] ✓ MCP tools are already configured"
    return 0
  else
    echo "[setup] Setting up MCP tools..."
    if bash "$REPO_ROOT/scripts/create_tasks/setup_codex_mcp.sh"; then
      echo "[setup] ✓ MCP tools configured successfully"
      return 0
    else
      echo "[setup] ✗ Failed to configure MCP tools"
      return 1
    fi
  fi
}

# Clean up prior runs more aggressively
echo "[cleanup] Cleaning up previous proxy processes..."

# Kill any existing proxy processes
kill_if_pid /tmp/codex_mitm.pid
kill_if_pid /tmp/trace_cleaner.pid

# Kill any mitmdump processes
pkill -f "mitmdump.*mitm_tracer.py" || true

# Kill any trace cleaner processes
pkill -f "trace_cleaner" || true

# Force kill any remaining processes on the port
kill_port_listeners "$PORT"

# Clean up log files
: > /tmp/codex_mitm.out
: > /tmp/trace_cleaner.out

echo "[cleanup] Cleanup completed"

# Setup codex-synth and MCP if needed
echo "[setup] Checking codex-synth installation..."
check_codex_synth
echo ""
echo "[setup] Checking MCP configuration..."
check_mcp_config
echo ""

echo "[run] starting mitmproxy on 0.0.0.0:${PORT}"
nohup env PYTHONPATH="$REPO_ROOT" \
  mitmdump -s "$REPO_ROOT/src/local_tracing/mitm_tracer.py" \
  --listen-host 0.0.0.0 --listen-port "${PORT}" \
  >/tmp/codex_mitm.out 2>&1 &
echo $! > /tmp/codex_mitm.pid

echo "[run] Proxy started. PID: $(cat /tmp/codex_mitm.pid 2>/dev/null || true)"

# Start trace cleaner
RAW_DB="data/traces/v3/raw_synth_ai.db/traces.sqlite3"
CLEAN_DB="data/traces/v3/clean_synth_ai.db/traces.sqlite3"
nohup env UV_NO_SYNC=1 PYTHONPATH="$REPO_ROOT/src" \
  uv run -m local_tracing.trace_cleaner "$RAW_DB" "$CLEAN_DB" 5 15 \
  >/tmp/trace_cleaner.out 2>&1 &
echo $! > /tmp/trace_cleaner.pid

echo "[run] Cleaner started. PID: $(cat /tmp/trace_cleaner.pid 2>/dev/null || true)"

echo "[run] streaming logs (Ctrl-C to stop streaming, workers continue running)..."
stdbuf -oL tail -n +1 -F /tmp/codex_mitm.out /tmp/trace_cleaner.out \
  | awk 'BEGIN{file=""} /^==>/ {file=$0; next} { cmd="date +%Y-%m-%dT%H:%M:%S"; cmd | getline d; close(cmd); print d, file, $0 }' \
  | tee -a /tmp/synth_workers.stream &
echo $! > /tmp/synth_stream.pid
wait $(cat /tmp/synth_stream.pid)


