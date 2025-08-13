#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/common.sh"

RUN_ID="${RUN_ID:-$(date +%Y%m%d__%H-%M-%S)}"
RUN_DIR="${REPO_ROOT}/data/runs/${RUN_ID}"
TRACE_DIR="${REPO_ROOT}/data/traces/v3"

TASK_PATH_INPUT="${1:-}"
if [[ -z "$TASK_PATH_INPUT" ]]; then
    echo "Usage: $0 <path-to-task> [extra docker args]"
    exit 1
fi

# Normalize to absolute path
if [[ "${TASK_PATH_INPUT}" != /* ]]; then
    TASK_PATH_INPUT="${REPO_ROOT}/${TASK_PATH_INPUT}"
fi
echo "[run_codex_box] Task path: ${TASK_PATH_INPUT}"

# If pointing at a generated task, convert it to a created task first
GEN_PREFIX="${REPO_ROOT}/data/tasks/generated/"
if [[ "${TASK_PATH_INPUT}" == ${GEN_PREFIX}* ]]; then
    echo "Detected generated task. Converting to created format..."
    SLUG="$(basename "${TASK_PATH_INPUT}")"
    CREATED_DIR="${REPO_ROOT}/data/tasks/created/${SLUG}"
    mkdir -p "${CREATED_DIR}"

    if [[ -f "${TASK_PATH_INPUT}/tb_meta.json" ]]; then
        cp "${TASK_PATH_INPUT}/tb_meta.json" "${CREATED_DIR}/"
    else
        echo "Error: ${TASK_PATH_INPUT}/tb_meta.json not found" >&2
        exit 1
    fi

    if [[ -d "${TASK_PATH_INPUT}/overlay_files" ]]; then
        mkdir -p "${CREATED_DIR}/overlay_files"
        cp -a "${TASK_PATH_INPUT}/overlay_files/." "${CREATED_DIR}/overlay_files/"
    fi

    if [[ -d "${TASK_PATH_INPUT}/evaluation" ]]; then
        mkdir -p "${CREATED_DIR}/evaluation"
        cp -a "${TASK_PATH_INPUT}/evaluation/." "${CREATED_DIR}/evaluation/"
    fi

    if [[ -f "${TASK_PATH_INPUT}/.env" ]]; then
        cp "${TASK_PATH_INPUT}/.env" "${CREATED_DIR}/"
    fi

    TASK_PATH_INPUT="${CREATED_DIR}"
    echo "Created task at: ${TASK_PATH_INPUT}"
fi

# Auto-prepare created tasks to prepared if needed
if [[ -f "$TASK_PATH_INPUT/tb_meta.json" && ! -f "$TASK_PATH_INPUT/Dockerfile" ]]; then
    echo "Detected created task. Preparing for evaluation..."
    export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"
    uv run python -m one_shot_bench.prepare_task_for_eval "$TASK_PATH_INPUT"
    # Find prepared path by slug
    SLUG="$(basename "$TASK_PATH_INPUT")"
    # Strip trailing timestamp parts like _YYYYMMDD_HHMMSS if present
    TMP_SLUG="${SLUG%_*}"
    BASE_SLUG="${TMP_SLUG%_*}"
    if [[ -d "${REPO_ROOT}/data/tasks/prepared/${BASE_SLUG}" ]]; then
        PREPARED_DIR="${REPO_ROOT}/data/tasks/prepared/${BASE_SLUG}"
    else
        PREPARED_DIR="${REPO_ROOT}/data/tasks/prepared/${SLUG}"
    fi
    if [[ -d "$PREPARED_DIR" ]]; then
        TASK_PATH_INPUT="$PREPARED_DIR"
        echo "Prepared task at: $TASK_PATH_INPUT"
    fi
fi

# Determine auth mode from eval.toml (default: api) and validate inputs
BILLING_MODE="api"
if [[ -f "${REPO_ROOT}/eval.toml" ]]; then
    BILLING_VAL=$(grep -E '^[[:space:]]*eval_billing[[:space:]]*=' "${REPO_ROOT}/eval.toml" | sed -E 's/.*=[[:space:]]*"(.*)".*/\1/' | tr '[:upper:]' '[:lower:]' || true)
    if [[ -z "$BILLING_VAL" ]]; then
        BILLING_VAL=$(grep -E '^[[:space:]]*billing[[:space:]]*=' "${REPO_ROOT}/eval.toml" | sed -E 's/.*=[[:space:]]*"(.*)".*/\1/' | tr '[:upper:]' '[:lower:]' || true)
    fi
    if [[ -n "$BILLING_VAL" ]]; then
        BILLING_MODE="$BILLING_VAL"
    fi
fi
echo "[auth] billing mode = $BILLING_MODE"

if [[ "$BILLING_MODE" == "api" ]]; then
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
        echo "Error: billing=api but OPENAI_API_KEY is not set in the environment." >&2
        echo "Hint: export OPENAI_API_KEY=sk-... and retry." >&2
        exit 1
    fi
else
    if [[ ! -d "$HOME/.codex" ]]; then
        echo "Error: billing=auth but ~/.codex does not exist. Run codex login on host first." >&2
        exit 1
    fi
fi

# Optional: delegate to Modal backend like synth-research if requested
if [[ "${SANDBOX_BACKEND:-docker}" == "modal" ]]; then
    echo "[sandbox] SANDBOX_BACKEND=modal → using in-repo Modal runner"
    # Ensure absolute task path
    TASK_ABS="$TASK_PATH_INPUT"
    if [[ "${TASK_ABS}" != /* ]]; then TASK_ABS="${REPO_ROOT}/${TASK_ABS}"; fi
    # Default model if not provided
    MODEL_ENV="${OPENAI_MODEL:-gpt-5-mini}"
    echo "[sandbox] Using model: $MODEL_ENV"
    echo "[sandbox] Invoking in-repo Modal runner..."
    # Use Modal CLI to run the local entrypoint (mirrors synth-research pattern)
    if command -v modal >/dev/null 2>&1; then
        # Export env so they are visible to modal process without inline assignments
        export OPENAI_MODEL="$MODEL_ENV"
        if [[ -n "${MODAL_PROFILE:-}" ]]; then export MODAL_PROFILE; fi
        # OPENAI_API_KEY is already in env from earlier validation
        modal run scripts/modal_runner.py --task-dir "$TASK_ABS" --model "$OPENAI_MODEL"
    else
        echo "Error: modal CLI not found. Install with: uv tool install modal && modal setup" >&2
        exit 1
    fi
    exit $?
fi

# Ensure required build context files exist for Docker
if [[ -d "$TASK_PATH_INPUT" ]]; then
	# Inject codex installation if missing
	if [[ ! -d "$TASK_PATH_INPUT/codex-files" ]]; then
		# First try to resolve via 'which codex' like synth-research
		CODEX_BIN="$(command -v codex || true)"
		if [[ -n "$CODEX_BIN" ]]; then
			CODEX_REAL="$(realpath "$CODEX_BIN" 2>/dev/null || echo "$CODEX_BIN")"
			CODEX_PACK_DIR="$(dirname "$(dirname "$CODEX_REAL")")"
			if [[ -d "$CODEX_PACK_DIR/lib/node_modules/@openai/codex" ]]; then
				CODEX_SRC="$CODEX_PACK_DIR/lib/node_modules/@openai/codex"
			elif [[ -d "$CODEX_PACK_DIR/@openai/codex" ]]; then
				CODEX_SRC="$CODEX_PACK_DIR/@openai/codex"
			else
				CODEX_SRC=""
			fi
			if [[ -n "$CODEX_SRC" && -d "$CODEX_SRC" ]]; then
				mkdir -p "$TASK_PATH_INPUT/codex-files"
				cp -a "$CODEX_SRC/." "$TASK_PATH_INPUT/codex-files/"
				echo "Injected codex-files from $CODEX_SRC"
			fi
		fi

		# Fallback to npm global root
		if [[ ! -d "$TASK_PATH_INPUT/codex-files" ]]; then
			if command -v npm >/dev/null 2>&1; then
				NPM_ROOT="$(npm root -g 2>/dev/null || true)"
				CODEX_SRC="${NPM_ROOT}/@openai/codex"
				if [[ -d "$CODEX_SRC" ]]; then
					mkdir -p "$TASK_PATH_INPUT/codex-files"
					cp -a "$CODEX_SRC/." "$TASK_PATH_INPUT/codex-files/"
					echo "Injected codex-files from $CODEX_SRC"
				else
					echo "Warning: @openai/codex not found in npm global root; run ./scripts/install_codex_synth.sh" >&2
				fi
			else
				echo "Warning: npm not found; cannot inject codex-files" >&2
			fi
		fi
	fi

	# Auth mode: ensure no secret leaks; provide empty .env so Docker COPY succeeds
	if [[ "$BILLING_MODE" == "auth" ]]; then
		if [[ -f "$TASK_PATH_INPUT/.env" ]]; then
			rm -f "$TASK_PATH_INPUT/.env"
			echo "[auth] Removed existing .env from build context (auth mode)"
		fi
		touch "$TASK_PATH_INPUT/.env"
		echo "[auth] Created empty .env for Docker COPY"
	else
        # API mode: ensure .env exists with OPENAI_API_KEY
        if [[ ! -f "$TASK_PATH_INPUT/.env" ]]; then
            echo "OPENAI_API_KEY=${OPENAI_API_KEY}" > "$TASK_PATH_INPUT/.env"
            echo "Wrote .env with OPENAI_API_KEY for build"
        fi
    fi

	# Copy mitmproxy CA cert into build context if available
	if [[ -f "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" && ! -f "$TASK_PATH_INPUT/mitmproxy-ca-cert.pem" ]]; then
		cp "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" "$TASK_PATH_INPUT/mitmproxy-ca-cert.pem"
		echo "Copied mitmproxy CA cert into build context"
	fi
fi

# Preflight: validate repo URL/branch/commit from tb_meta.json
if [[ -f "$TASK_PATH_INPUT/tb_meta.json" ]]; then
	echo "[preflight] Starting preflight checks..."
	if ! command -v jq >/dev/null 2>&1; then
		echo "Error: jq is required for preflight checks. Install jq and retry." >&2
		exit 1
	fi
	if ! command -v git >/dev/null 2>&1; then
		echo "Error: git is required for preflight checks. Install git and retry." >&2
		exit 1
	fi

	GIT_URL="$(jq -r '.repo.git_url // empty' "$TASK_PATH_INPUT/tb_meta.json")"
	BRANCH="$(jq -r '.repo.branch // "main"' "$TASK_PATH_INPUT/tb_meta.json")"
	COMMIT="$(jq -r '.repo.start_commit_sha // empty' "$TASK_PATH_INPUT/tb_meta.json")"
	echo "[preflight] repo.git_url=$GIT_URL"
	echo "[preflight] repo.branch=$BRANCH"
	if [[ -n "$COMMIT" ]]; then
		SHORT_COMMIT="${COMMIT:0:12}"
		echo "[preflight] repo.start_commit_sha=$SHORT_COMMIT..."
	else
		echo "[preflight] repo.start_commit_sha is empty"
	fi

	if [[ -z "$GIT_URL" ]]; then
		echo "Error: tb_meta.repo.git_url is missing" >&2
		exit 1
	fi
	if [[ "$GIT_URL" != https://github.com/* ]]; then
		echo "Error: git_url must be a public HTTPS GitHub URL. Got: $GIT_URL" >&2
		exit 1
	fi

	# Check remote reachable
	if ! git ls-remote "$GIT_URL" >/dev/null 2>&1; then
		echo "Error: Cannot reach remote repository: $GIT_URL" >&2
		exit 1
	fi
	echo "[preflight] Remote is reachable"

	# Check branch exists
	if ! git ls-remote --heads "$GIT_URL" "$BRANCH" | grep -q .; then
		echo "Error: Branch '$BRANCH' not found on remote $GIT_URL" >&2
		exit 1
	fi
	echo "[preflight] Branch exists on remote"

	# Check commit exists on remote if provided and not HEAD
	if [[ -n "$COMMIT" && "$COMMIT" != "HEAD" ]]; then
		REMOTE_REFS="$(git ls-remote "$GIT_URL")"
		if ! printf '%s\n' "$REMOTE_REFS" | awk '{print $1}' | grep -q "^$COMMIT$"; then
			echo "Error: Commit '$COMMIT' not found on remote $GIT_URL." >&2
			echo "Hint: push the commit to remote, or update tb_meta.repo.start_commit_sha to a remote SHA (e.g., the current tip of '$BRANCH')." >&2
			exit 1
		fi
		echo "[preflight] Commit exists on remote"
	fi
	echo "[preflight] All checks passed"
fi

mkdir -p "$RUN_DIR"

if ! docker_is_running; then
    echo "Docker daemon is not running" >&2
    exit 1
fi

# Delegate to existing sandbox runner if present, else run a basic docker build/run
if [[ -x "${REPO_ROOT}/scripts/run_sandbox.sh" ]]; then
    "${REPO_ROOT}/scripts/run_sandbox.sh" "$TASK_PATH_INPUT" "$RUN_DIR" "$TRACE_DIR" "${@:2}"
else
    echo "[build] Building Docker image..."
    docker build -t oneshot-task "$TASK_PATH_INPUT"

    echo "[run] Auth mode (billing) = $BILLING_MODE"

    DOCKER_RUN_OPTS=(--rm -v "$RUN_DIR:/runs")

    # Allocate TTY and relax security similar to working synth-research setup
    DOCKER_RUN_OPTS+=( -it --security-opt seccomp=unconfined --security-opt apparmor=unconfined --cap-add SYS_ADMIN --cap-add SYS_PTRACE )

    # Mount codex auth dir if using auth mode
    if [[ "$BILLING_MODE" == "auth" ]]; then
        DOCKER_RUN_OPTS+=( -v "$HOME/.codex:/root/.codex:ro" )
        echo "[run] Mounting ~/.codex into container (read-only)"
    fi

    # Pass API key at runtime (api mode)
    if [[ "$BILLING_MODE" == "api" ]]; then
        DOCKER_RUN_OPTS+=( -e "OPENAI_API_KEY=${OPENAI_API_KEY}" )
        echo "[run] Passing OPENAI_API_KEY to container"
    fi

    # Force model for this run unless caller overrides OPENAI_MODEL in env
    DOCKER_RUN_OPTS+=( -e "OPENAI_MODEL=${OPENAI_MODEL:-gpt-5-mini}" )

    echo "[run] Starting container (attached with TTY)…"
    CONTAINER_NAME="oneshot_${RUN_ID}"
    mkdir -p "$RUN_DIR/logs" "$RUN_DIR/artifacts"
    # Bind-mount artifacts to host so logs/results land directly without a watcher
    DOCKER_RUN_OPTS+=( -v "$RUN_DIR/artifacts:/app/artifacts" )
    echo "[debug] docker run options: ${DOCKER_RUN_OPTS[*]}"
    docker run --name "$CONTAINER_NAME" "${DOCKER_RUN_OPTS[@]}" oneshot-task

    # After exit, collect logs and metadata
    echo "[collect] Copying logs and metadata"
    docker logs "$CONTAINER_NAME" >"$RUN_DIR/logs/container_full.log" 2>&1 || true
    docker cp "$CONTAINER_NAME:/app/tb_meta.json" "$RUN_DIR/artifacts/" 2>/dev/null || true

    # Display key results like synth-research
    echo "[results] ========================================"
    echo "[results] Git diff (container):"
    if [[ -s "$RUN_DIR/artifacts/diff.patch" ]]; then
        cat "$RUN_DIR/artifacts/diff.patch" | cat
    else
        echo "(empty)"
    fi
    echo "[results] ----------------------------------------"
    if [[ -s "$RUN_DIR/artifacts/tb_evaluation_results.json" ]]; then
        EVAL_JSON_PATH="$RUN_DIR/artifacts/tb_evaluation_results.json" python3 - << 'PY'
import os, json
p = os.environ['EVAL_JSON_PATH']
if not os.path.exists(p):
    print('[results] No tb_evaluation_results.json found')
    raise SystemExit(0)
data = json.load(open(p))
evaluation = data.get('evaluation', {})
total = evaluation.get('total_score', 0.0)
print(f"[results] Rubric total score: {total:.0%}")
rubrics = evaluation.get('rubrics', {})
if isinstance(rubrics, dict):
    for rid, r in rubrics.items():
        score = r.get('score', 0.0)
        weight = r.get('weight', 1)
        print(f"[results]  - {rid}: {score:.0%} (weight={weight})")
tests = data.get('test_results', {})
passed = sum(1 for v in tests.values() if v.get('success'))
failed = sum(1 for v in tests.values() if not v.get('success'))
print(f"[results] Unit tests: {passed} passed, {failed} failed")
PY
    else
        echo "[results] No tb_evaluation_results.json found"
    fi
    echo "[results] ========================================"

    echo "[cleanup] Removing container"
    docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

echo "Run artifacts in: $RUN_DIR"

