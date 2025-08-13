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

# Ensure required build context files exist for Docker
if [[ -d "$TASK_PATH_INPUT" ]]; then
	# Inject codex installation if missing
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

	# Provide .env for Docker COPY (populate with OPENAI_API_KEY if available)
	if [[ ! -f "$TASK_PATH_INPUT/.env" ]]; then
		if [[ -n "${OPENAI_API_KEY:-}" ]]; then
			echo "OPENAI_API_KEY=${OPENAI_API_KEY}" > "$TASK_PATH_INPUT/.env"
			echo "Wrote .env with OPENAI_API_KEY for build"
		else
			touch "$TASK_PATH_INPUT/.env"
			echo "Created empty .env to satisfy Docker build"
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

	# Check branch exists
	if ! git ls-remote --heads "$GIT_URL" "$BRANCH" | grep -q .; then
		echo "Error: Branch '$BRANCH' not found on remote $GIT_URL" >&2
		exit 1
	fi

	# Check commit exists on remote if provided and not HEAD
	if [[ -n "$COMMIT" && "$COMMIT" != "HEAD" ]]; then
		REMOTE_REFS="$(git ls-remote "$GIT_URL")"
		if ! printf '%s\n' "$REMOTE_REFS" | awk '{print $1}' | grep -q "^$COMMIT$"; then
			echo "Error: Commit '$COMMIT' not found on remote $GIT_URL." >&2
			echo "Hint: push the commit to remote, or update tb_meta.repo.start_commit_sha to a remote SHA (e.g., the current tip of '$BRANCH')." >&2
			exit 1
		fi
	fi
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
    docker build -t oneshot-task "$TASK_PATH_INPUT"
    docker run --rm -v "$RUN_DIR:/runs" oneshot-task
fi

echo "Run artifacts in: $RUN_DIR"

