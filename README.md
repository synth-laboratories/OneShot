# one-shot-bench
Quick host setup for codex-synth:

```bash
./scripts/install_codex_synth.sh
# then restart your shell or ensure ~/.local/bin is in PATH
```

### Start MITM workers and trust the proxy (full walkthrough)

1) Start the proxy and trace cleaner (runs in background)

```bash
uv tool install mitmproxy
./scripts/start_synth_workers.sh
```

This launches:
- a mitmproxy on `localhost:18080` writing logs to `/tmp/codex_mitm.out`
- a trace cleaner runs locally and copies raw traces to a clean DB under `data/traces/v3/clean_synth_ai.db/traces.sqlite3`

2) Install and trust the MITM CA certificate (one-time)

- The certificate is generated at `~/.mitmproxy/mitmproxy-ca-cert.pem`.
- On macOS: open Keychain Access → import that file → set Trust to “Always Trust”.
- Or visit `http://mitm.it` while the proxy is running and follow the OS instructions.

3) Verify the proxy is reachable

```bash
curl -x http://localhost:18080 https://api.openai.com/v1/models | cat
```

4) Route Codex traffic through the proxy (host sessions)

Either set environment variables for the current shell:

```bash
export HTTP_PROXY=http://127.0.0.1:18080
export HTTPS_PROXY=http://127.0.0.1:18080
export ALL_PROXY=http://127.0.0.1:18080
codex-synth
```

…or configure a system-level HTTPS proxy to `127.0.0.1:18080` before running `codex-synth`.

5) Container runs (no extra setup)

- When using `src/one_shot_bench/run_codex_box.sh`, the script auto-detects the proxy and injects `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` into the container. It also copies the mitm CA into the build context if available, so you don’t need to repeat steps 2–4 for container runs.
Scalably converting pair programming cli trajectories into challenging digital agent tasks
