#!/usr/bin/env python3
"""
OneShot CLI

Usage:
  uvx one-shot serve
  uv run python -m one_shot_bench.cli serve

Runs mitmproxy with the local tracing addon to capture OpenAI API traffic.
"""

from __future__ import annotations

import os
import sys
import subprocess
import shutil
from pathlib import Path
from subprocess import run


def _mitm_addon_path() -> str:
    # Resolve the packaged mitm tracer addon file path
    import local_tracing.mitm_tracer as mt  # noqa: WPS433 (import within function is fine here)

    return str(Path(mt.__file__).resolve())


def serve() -> int:
    port = int(os.environ.get("MITM_PORT", "18080"))
    addon = _mitm_addon_path()
    # Ensure DB base dir exists if RAW_TRACE_DB is set
    raw_db = os.environ.get("RAW_TRACE_DB")
    if raw_db:
        Path(raw_db).parent.mkdir(parents=True, exist_ok=True)

    # Kill any existing listeners on this port to avoid EADDRINUSE
    print(f"[one-shot] killing any existing listeners on :{port}...")
    res = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
    pids = [pid for pid in (res.stdout.strip().splitlines() if res.stdout else []) if pid]
    for pid in pids:
        subprocess.run(["kill", "-9", pid])

    # Prefer mitmdump binary; fallback to module invocation if not found
    mitmdump_bin = shutil.which("mitmdump")
    if mitmdump_bin:
        version_cmd = [mitmdump_bin, "--version"]
        base_cmd = [mitmdump_bin]
    else:
        version_cmd = [sys.executable, "-m", "mitmproxy.tools.main", "mitmdump", "--version"]
        base_cmd = [sys.executable, "-m", "mitmproxy.tools.main", "mitmdump"]

    # Show mitmdump version for quick sanity
    subprocess.run(version_cmd)

    # Run mitmdump with our addon; block_global=false allows remote clients
    cmd = base_cmd + [
        "-v",
        "-p", str(port),
        "-s", addon,
        "--set", "block_global=false",
        "--set", "termlog_verbosity=info",
        "--set", "flow_detail=3",
        "--listen-host", "0.0.0.0",
        "--ssl-insecure",
    ]
    print(f"[one-shot] starting mitmproxy on 0.0.0.0:{port} with addon {addon}")
    print("[one-shot] to route traffic: export HTTPS_PROXY=http://127.0.0.1:" + str(port))
    print("[one-shot] exec:", " ".join(cmd))
    return run(cmd).returncode


def setup() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "install_codex_synth.sh"
    if not script.exists():
        print(f"setup script missing: {script}", file=sys.stderr)
        return 1
    print("[one-shot] Installing codex-synth wrapper and CLI...")
    rc = run(["bash", str(script)]).returncode
    if rc == 0:
        bin_dir = os.path.expanduser("~/.local/bin")
        print("[one-shot] Done. Ensure on PATH (e.g. add to ~/.zshrc):")
        print(f"export PATH=\"{bin_dir}:$PATH\"")
    return rc


def main() -> None:
    # Minimal subcommand routing without argparse in __main__
    if len(sys.argv) >= 2:
        if sys.argv[1] == "serve":
            sys.exit(serve())
        if sys.argv[1] == "setup":
            sys.exit(setup())
    print("Usage: one-shot serve | one-shot setup", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()


