# Codebase Rules and Conventions

This document captures practical conventions observed in `src/` and clarifies how to extend and maintain this repository. It is intentionally concise and opinionated; update it when behavior or patterns change.

## Repo Layout
- `src/one_shot/`: Automation for preparing, running, and evaluating tasks (Python + Bash + Makefile).
- `src/local_tracing/`: Local MITM tracing utilities (Python modules).
- `specs/`: Behavioral specs for key scripts/modules and this rules doc.
- `data/`: Tasks and run artifacts used during evaluation.
- `scripts/`, `crates/`, etc.: Supporting utilities; keep changes minimal and scoped.

## Python Conventions
- Shebang: use `#!/usr/bin/env python3` and a module docstring that states purpose and usage.
- Imports: prefer `pathlib.Path`, `typing`, and standard libs over ad‑hoc utilities; avoid global side effects at import time.
- Types: add type hints to public functions; use precise container types (e.g., `Dict[str, Any]`).
- Errors: raise explicit exceptions (e.g., `FileNotFoundError`, `ValueError`) with actionable messages; fail fast on invalid inputs.
- I/O: read/write JSON via `json` with clear schema assumptions; guard against empty or malformed files and continue gracefully when optional artifacts are missing.
- Paths: compute paths relative to `Path(__file__).resolve()`; do not rely on the current working directory.
- Logging: print short, user‑facing messages; warn instead of crashing for optional data; keep noise low in normal paths.

## Shell (Bash) Conventions
- Shebang: `#!/bin/bash`; start scripts with `set -euo pipefail` unless a script explicitly manages errors.
- Safety: quote variables, use arrays, and check required inputs; prefer `case` for dispatch and `getopts`/positional args for simple CLIs.
- Structure: define small functions in `lower_snake_case`; keep a `log()` helper with timestamp where useful.
- Paths: compute `SCRIPT_DIR` and `REPO_ROOT` via `BASH_SOURCE`/`dirname`/`pwd`; don’t assume caller’s CWD.
- UX: include a short header comment with purpose and usage; use colored output sparingly for statuses.
- Exit codes: propagate backend exit codes; treat unknown modes/inputs as errors with helpful usage.

## Makefile Conventions
- Targets: keep phony targets (`help`, `setup`, `test-*`, `run`, `proxy`, `clean`, `results`) lightweight and descriptive.
- Defaults: expose tunables via variables with sensible defaults (e.g., `TASK_PATH`, `TIMEOUT`, `TOKEN_LIMIT`, `PROXY_PORT`).
- Tolerance: guard optional steps with `|| true` and existence checks; avoid failing the whole run for missing auxiliaries.

## Task & Evaluation Patterns
- Prepared tasks live under `data/tasks/prepared/<task_name>/` with `tb_meta.json`, `Dockerfile`, and `overlay_files/`.
- Conversion utilities normalize Git URLs to HTTPS and reject non‑HTTPS or unsupported schemes.
- Legacy evaluation data is converted into `rubrics` and `test_scripts` with conservative defaults when absent.
- Runtime/evaluation artifacts (diffs, logs, traces) are written under each run’s `artifacts/` directory and may be optional.

## Naming & Style
- Files: prefer `lower_snake_case` for Python and shell; keep executable Bash scripts with `.sh` extension.
- Functions: `lower_snake_case`; keep functions small and focused.
- Modules/Packages: minimal, descriptive names; avoid deep nesting without need.

## Error Handling & Resilience
- Validate inputs early; print a clear message and exit non‑zero on misuse.
- Continue past optional data (e.g., missing traces/logs) with warnings, not crashes.
- When iterating directories, tolerate absent files and unreadable entries unless they are required.

## Environment & Backends
- Sandbox: default backend is Docker; allow alternate backends via `SANDBOX_BACKEND` and validate prerequisites before dispatch.
- Timeouts & limits: accept through environment vars or CLI args; provide defaults consistent with `Makefile`.
- Networking: support MITM proxy usage where applicable; don’t hard‑depend on network availability in conversion steps.

## When Adding or Changing Code
- Mirror existing patterns in `src/` for structure, error handling, and path computation.
- Update or add a `.spec` under `specs/src/...` when behavior changes or new tools are introduced.
- Keep messages actionable and short; prefer explicit failures over silent fallthroughs.
- Keep cross‑platform shell constructs (avoid GNU‑specific flags where possible in portable scripts).

## Quick Checklist
- [ ] Shebang + usage/header present
- [ ] `set -euo pipefail` (Bash) or explicit exception handling (Python)
- [ ] Paths derived from file location, not CWD
- [ ] Inputs validated; helpful error messages
- [ ] Defaults wired to env/Makefile variables
- [ ] Optional artifacts handled gracefully
- [ ] Specs updated as behavior changes
