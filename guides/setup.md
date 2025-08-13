### Setup: install, workers, MCP

Use this guide to install the CLI, start local workers for tracing, and enable MCP tools for task creation.

## Install codex-synth wrapper

```bash
./scripts/install_codex_synth.sh
# then restart your shell or ensure ~/.local/bin is in PATH
```

## Start MITM workers and trust the proxy

1) Start the proxy and trace cleaner (runs in background)

```bash
uv tool install mitmproxy
./scripts/start_synth_workers.sh
```

This launches:
- a mitmproxy on `localhost:18080` writing logs to `/tmp/codex_mitm.out`
- a trace cleaner that copies raw traces to a clean DB under `data/traces/v3/clean_synth_ai.db/traces.sqlite3`

### Tracing data model

- Raw DB: `data/traces/v3/raw_synth_ai.db/traces.sqlite3`, table `traces` (one row per HTTP transaction; includes `meta_json.session_id` from `RUN_ID`).
- Clean DB: `data/traces/v3/clean_synth_ai.db/traces.sqlite3`, table `cleaned_sessions` (one row per session; `formatted_json` aggregates events chronologically). Updated every ~5s by the cleaner.

Inspect a few cleaned sessions:
```bash
sqlite3 data/traces/v3/clean_synth_ai.db/traces.sqlite3 \
  'SELECT session_id, substr(formatted_json,1,160) FROM cleaned_sessions LIMIT 3;'
```

2) Install and trust the MITM CA certificate (one-time)

- The certificate is at `~/.mitmproxy/mitmproxy-ca-cert.pem`.
- On macOS: open Keychain Access → import that file → set Trust to “Always Trust”.
- Or visit `http://mitm.it` while the proxy is running and follow the OS instructions.

3) Verify the proxy is reachable

```bash
curl -x http://localhost:18080 https://api.openai.com/v1/models | cat
```

4) Route Codex traffic through the proxy (host sessions)

```bash
export HTTP_PROXY=http://127.0.0.1:18080
export HTTPS_PROXY=http://127.0.0.1:18080
export ALL_PROXY=http://127.0.0.1:18080
export RUN_ID="host_$(date +%s)"
codex-synth
```

…or configure a system-level HTTPS proxy to `127.0.0.1:18080` before running `codex-synth`.

5) Container runs (no extra setup)

- When using `scripts/run_codex_box.sh`, the script auto-detects the proxy and injects `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` into the container. It also copies the mitm CA into the build context if available.

## Enable MCP tools (repo.start_task / repo.end_task)

Enable Codex MCP tools that save tasks into `data/tasks/created`:

```bash
./scripts/create_tasks/setup_codex_mcp.sh
```

This writes `~/.codex/config.toml` to point Codex at `scripts/create_tasks/mcp_oneshot_server.py`.

Verify inside Codex: ask “What tools do you have?” and ensure you see
`repo.start_task.v1`, `repo.end_task.v1`, `repo.check_readiness.v1`, `repo.autofix_readiness.v1`.

If tasks still save under `development/...`, remove stale aliases/functions and reinstall our wrapper and MCP config:

```bash
./scripts/install_codex_synth.sh
./scripts/create_tasks/setup_codex_mcp.sh
exec $SHELL -l
type -a codex-synth  # should show ~/.local/bin/codex-synth
```


