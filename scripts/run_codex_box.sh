#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/synth_models.sh"

# Load secrets from .env if present
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env"
  set +a
fi

RUN_ID="${RUN_ID:-$(date +%Y%m%d__%H-%M-%S)}"
RUN_DIR="${REPO_ROOT}/data/runs/${RUN_ID}"
TRACE_DIR="${REPO_ROOT}/data/traces/v3"

MODEL_ENV="${OPENAI_MODEL:-gpt-5-mini}"
IS_SYNTH_MODEL=false

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
    uv run python -m one_shot.prepare_task_for_eval "$TASK_PATH_INPUT"
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

# Apply overrides.json (prompt and file overlays) if requested
if [[ "${ROLLOUT_APPLY_OVERRIDES:-1}" == "1" ]]; then
    if [[ -n "${ROLLOUT_OVERRIDES_FILE:-}" ]]; then
        OVERRIDES_FILE="${ROLLOUT_OVERRIDES_FILE}"
    else
        OVERRIDES_FILE="$TASK_PATH_INPUT/overrides.json"
    fi
    if [[ -f "$OVERRIDES_FILE" ]]; then
        echo "[overrides] Using overrides file: $OVERRIDES_FILE"
        if ! command -v jq >/dev/null 2>&1; then
            echo "Error: jq is required to process overrides.json" >&2
            exit 1
        fi

        # Ensure overlay directories exist
        mkdir -p "$TASK_PATH_INPUT/overlay_files"
        mkdir -p "$TASK_PATH_INPUT/overlay_repo_files"

        # Prompt override: supports .prompt or .lm_instructions
        PROMPT_OVERRIDE=$(jq -r '(.prompt // .lm_instructions // empty)' "$OVERRIDES_FILE")
        if [[ -n "$PROMPT_OVERRIDE" && "$PROMPT_OVERRIDE" != "null" ]]; then
            echo "[overrides] Overriding prompt via overlay_files/LM_INSTRUCTIONS.md"
            printf "%s" "$PROMPT_OVERRIDE" > "$TASK_PATH_INPUT/overlay_files/LM_INSTRUCTIONS.md"
        fi

        # Overlay files under /app (overlay_files)
        if jq -e '.overlay_files // empty' "$OVERRIDES_FILE" >/dev/null; then
            echo "[overrides] Writing overlay_files entries"
            while IFS= read -r entry; do
                key=$(echo "$entry" | base64 --decode | jq -r '.key')
                val=$(echo "$entry" | base64 --decode | jq -r '.value')
                out_path="$TASK_PATH_INPUT/overlay_files/$key"
                mkdir -p "$(dirname "$out_path")"
                printf "%s" "$val" > "$out_path"
                echo "  -> /overlay_files/$key"
            done < <(jq -r '(.overlay_files // {}) | to_entries[] | @base64' "$OVERRIDES_FILE")
        fi

        # Overlay files into cloned repo (overlay_repo_files)
        if jq -e '.overlay_repo_files // empty' "$OVERRIDES_FILE" >/dev/null; then
            echo "[overrides] Writing overlay_repo_files entries"
            while IFS= read -r entry; do
                key=$(echo "$entry" | base64 --decode | jq -r '.key')
                val=$(echo "$entry" | base64 --decode | jq -r '.value')
                out_path="$TASK_PATH_INPUT/overlay_repo_files/$key"
                mkdir -p "$(dirname "$out_path")"
                printf "%s" "$val" > "$out_path"
                echo "  -> /overlay_repo_files/$key"
            done < <(jq -r '(.overlay_repo_files // {}) | to_entries[] | @base64' "$OVERRIDES_FILE")
        fi

        # New: remove_repo_paths (list of repo-relative paths to delete in container before baseline)
        if jq -e '.remove_repo_paths // empty' "$OVERRIDES_FILE" >/dev/null; then
            echo "[overrides] Writing remove_repo_paths to overlay_files/remove_repo_paths.txt"
            mkdir -p "$TASK_PATH_INPUT/overlay_files"
            jq -r '.remove_repo_paths[]' "$OVERRIDES_FILE" > "$TASK_PATH_INPUT/overlay_files/remove_repo_paths.txt"
            echo "  -> /overlay_files/remove_repo_paths.txt"
        fi

        # Evaluation overrides (replace defaults): prefer .evaluation, else .rubrics/.test_scripts
        if jq -e '.evaluation // .rubrics // .test_scripts' "$OVERRIDES_FILE" >/dev/null; then
            echo "[overrides] Applying evaluation overrides to tb_meta.json"
            TB_META="$TASK_PATH_INPUT/tb_meta.json"
            if [[ -f "$TB_META" ]]; then
                # Build a normalized evaluation JSON from overrides
                EVAL_JSON=$(jq -c '{evaluation: (if .evaluation then .evaluation else {rubrics: (.rubrics // []), test_scripts: (.test_scripts // [])} end)}' "$OVERRIDES_FILE")
                tmp_meta=$(mktemp)
                jq --argjson ov "$EVAL_JSON" '(.evaluation) = $ov.evaluation' "$TB_META" > "$tmp_meta" && mv "$tmp_meta" "$TB_META"
            else
                echo "Warning: tb_meta.json not found for evaluation override" >&2
            fi
        fi

        # Repo overrides: allow overriding git_url/branch/start_commit_sha
        if jq -e '.repo // empty' "$OVERRIDES_FILE" >/dev/null; then
            echo "[overrides] Applying repo overrides to tb_meta.json"
            TB_META="$TASK_PATH_INPUT/tb_meta.json"
            if [[ -f "$TB_META" ]]; then
                tmp_meta=$(mktemp)
                jq -c --slurpfile ov "$OVERRIDES_FILE" '.repo = ($ov[0].repo // .repo)' "$TB_META" > "$tmp_meta" && mv "$tmp_meta" "$TB_META"
            else
                echo "Warning: tb_meta.json not found for repo override" >&2
            fi
        fi
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
    OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
    OPENAI_API_KEY="${OPENAI_API_KEY:-}"
    MODEL_ENV="${OPENAI_MODEL:-gpt-5-mini}"
    IS_SYNTH_MODEL=false
    if is_synth_model "$MODEL_ENV"; then
        IS_SYNTH_MODEL=true
        SYNTH_BASE_URL="${SYNTH_BASE_URL:-$(get_default_synth_base_url)}"
        export SYNTH_BASE_URL
        if [[ -z "${SYNTH_API_KEY:-}" ]]; then
            echo "Error: OPENAI_MODEL=${MODEL_ENV} requires SYNTH_API_KEY (not found in environment)." >&2
            echo "Set SYNTH_API_KEY in ${REPO_ROOT}/.env or export it before running this script." >&2
            exit 1
        fi
        OPENAI_API_KEY="${SYNTH_API_KEY}"
        export OPENAI_API_KEY
        OPENROUTER_API_KEY=""
        unset OPENROUTER_API_KEY
        OPENAI_BASE_URL="${OPENAI_BASE_URL:-$SYNTH_BASE_URL}"
        export OPENAI_BASE_URL
        export FORCE_OPENAI=1
        echo "[synth] Using Synth backend at ${OPENAI_BASE_URL}"
    else
        if [[ -z "$OPENROUTER_API_KEY" && -z "$OPENAI_API_KEY" ]]; then
        echo "Error: billing=api but neither OPENAI_API_KEY nor OPENROUTER_API_KEY is set in the environment." >&2
        echo "Hint: export OPENAI_API_KEY=sk-... or export OPENROUTER_API_KEY=sk-or-... and retry." >&2
        exit 1
        fi
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
		if [[ -n "${PRIVATE_GITHUB_PAT:-}" ]]; then
			printf 'PRIVATE_GITHUB_PAT=%s\n' "$PRIVATE_GITHUB_PAT" > "$TASK_PATH_INPUT/.env"
			echo "[auth] Created .env with PRIVATE_GITHUB_PAT for Docker COPY"
		else
			: > "$TASK_PATH_INPUT/.env"
			echo "[auth] Created empty .env for Docker COPY"
		fi
	else
		# API mode: ensure .env exists with OPENAI_API_KEY
		# Check for OpenRouter support
		OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
		if [[ -n "$OPENROUTER_API_KEY" ]]; then
			# Use OpenRouter
			OPENAI_API_KEY_VALUE="$OPENROUTER_API_KEY"
			OPENAI_BASE_URL_VALUE="https://openrouter.ai/api/v1"
        OPENAI_MODEL_VALUE="${MODEL_ENV}"
			echo "[openrouter] Detected OPENROUTER_API_KEY, configuring OpenRouter"
			echo "[openrouter] Model: ${OPENAI_MODEL_VALUE}"
		else
			OPENAI_API_KEY_VALUE="${OPENAI_API_KEY}"
			OPENAI_BASE_URL_VALUE="${OPENAI_BASE_URL:-}"
        OPENAI_MODEL_VALUE="${MODEL_ENV}"
		fi
    MODEL_ENV="${OPENAI_MODEL_VALUE}"
		
		if [[ ! -f "$TASK_PATH_INPUT/.env" ]]; then
			{
				echo "OPENAI_API_KEY=${OPENAI_API_KEY_VALUE}"
				if [[ -n "$OPENAI_BASE_URL_VALUE" ]]; then
					echo "OPENAI_BASE_URL=${OPENAI_BASE_URL_VALUE}"
				fi
				if [[ -n "${PRIVATE_GITHUB_PAT:-}" ]]; then
					echo "PRIVATE_GITHUB_PAT=${PRIVATE_GITHUB_PAT}"
				fi
			} > "$TASK_PATH_INPUT/.env"
			echo "Wrote .env with OPENAI_API_KEY for build"
		else
			if [[ -n "${PRIVATE_GITHUB_PAT:-}" ]]; then
				if ! grep -q '^PRIVATE_GITHUB_PAT=' "$TASK_PATH_INPUT/.env" 2>/dev/null; then
					printf '\nPRIVATE_GITHUB_PAT=%s\n' "$PRIVATE_GITHUB_PAT" >> "$TASK_PATH_INPUT/.env"
					echo "[auth] Appended PRIVATE_GITHUB_PAT to existing .env"
				fi
			fi
			# Ensure API key and base URL entries exist
			if ! grep -q '^OPENAI_API_KEY=' "$TASK_PATH_INPUT/.env" 2>/dev/null; then
				echo "OPENAI_API_KEY=${OPENAI_API_KEY_VALUE}" >> "$TASK_PATH_INPUT/.env"
			fi
			if [[ -n "$OPENAI_BASE_URL_VALUE" ]]; then
				if ! grep -q '^OPENAI_BASE_URL=' "$TASK_PATH_INPUT/.env" 2>/dev/null; then
					echo "OPENAI_BASE_URL=${OPENAI_BASE_URL_VALUE}" >> "$TASK_PATH_INPUT/.env"
				fi
			fi
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
	if [[ "$GIT_URL" != https://* ]]; then
		echo "Error: git_url must be an HTTPS Git URL. Got: $GIT_URL" >&2
		exit 1
	fi

	AUTH_GIT_URL="$GIT_URL"
	if [[ -n "${PRIVATE_GITHUB_PAT:-}" && "$GIT_URL" == https://* ]]; then
		AUTH_GIT_URL="https://x-access-token:${PRIVATE_GITHUB_PAT}@${GIT_URL#https://}"
		echo "[preflight] Using GitHub token for remote access"
	fi

	# Check remote reachable
	if ! git ls-remote "$AUTH_GIT_URL" >/dev/null 2>&1; then
		echo "Error: Cannot reach remote repository: $GIT_URL" >&2
		exit 1
	fi
	echo "[preflight] Remote is reachable"

	# Check branch exists
	if ! git ls-remote --heads "$AUTH_GIT_URL" "$BRANCH" | grep -q .; then
		echo "Error: Branch '$BRANCH' not found on remote $GIT_URL" >&2
		exit 1
	fi
	echo "[preflight] Branch exists on remote"

	# Check commit exists on remote if provided and not HEAD
	if [[ -n "$COMMIT" && "$COMMIT" != "HEAD" ]]; then
		REMOTE_REFS="$(git ls-remote "$AUTH_GIT_URL")"
		if ! printf '%s\n' "$REMOTE_REFS" | awk '{print $1}' | grep -q "^$COMMIT$"; then
			echo "Error: Commit '$COMMIT' not found on remote $GIT_URL." >&2
			echo "Hint: push the commit to remote, or update tb_meta.repo.start_commit_sha to a remote SHA (e.g., the current tip of '$BRANCH')." >&2
			exit 1
		fi
		echo "[preflight] Commit exists on remote"
	fi
	echo "[preflight] All checks passed"
	unset AUTH_GIT_URL
fi

mkdir -p "$RUN_DIR"

if ! docker_is_running; then
    echo "Docker daemon is not running" >&2
    exit 1
fi

# If task path is a prepared task, optionally refresh from the most recent created source
# Set REFRESH_PREPARED=1 to enable refresh; default is disabled to preserve manual edits
if [[ "${REFRESH_PREPARED:-0}" == "1" ]]; then
    if [[ -d "$TASK_PATH_INPUT" && -f "$TASK_PATH_INPUT/Dockerfile" && -f "$TASK_PATH_INPUT/tb_meta.json" ]]; then
        SLUG_PREPARED="$(basename "$TASK_PATH_INPUT")"
        CREATED_CANDIDATE=$(ls -dt "${REPO_ROOT}/data/tasks/created/${SLUG_PREPARED}_"*/ 2>/dev/null | head -n1 || true)
        if [[ -n "$CREATED_CANDIDATE" && -d "$CREATED_CANDIDATE" ]]; then
            echo "[prepare] Refreshing prepared task from created source: $CREATED_CANDIDATE"
            export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"
            uv run python -m one_shot.prepare_task_for_eval "$CREATED_CANDIDATE"
            # Reset TASK_PATH_INPUT to regenerated prepared path (same slug)
            if [[ -d "${REPO_ROOT}/data/tasks/prepared/${SLUG_PREPARED}" ]]; then
                TASK_PATH_INPUT="${REPO_ROOT}/data/tasks/prepared/${SLUG_PREPARED}"
                echo "[prepare] Using refreshed prepared dir: $TASK_PATH_INPUT"
            fi
        fi
    fi
fi

# Delegate to existing sandbox runner if present, else run a basic docker build/run
if [[ -x "${REPO_ROOT}/scripts/run_sandbox.sh" ]]; then
    "${REPO_ROOT}/scripts/run_sandbox.sh" "$TASK_PATH_INPUT" "$RUN_DIR" "$TRACE_DIR" "${@:2}"
else
    echo "[build] Building Docker image..."
    # Enable BuildKit for better caching and performance
    export DOCKER_BUILDKIT=1
    BUILD_ARGS=(-t oneshot-task)
    # Only use --no-cache if explicitly requested
    if [[ "${DOCKER_NO_CACHE:-0}" == "1" ]]; then
        BUILD_ARGS+=(--no-cache)
        echo "[build] Using --no-cache (DOCKER_NO_CACHE=1)"
    else
        echo "[build] Using Docker layer caching (set DOCKER_NO_CACHE=1 to disable)"
    fi
    if [[ -n "${PRIVATE_GITHUB_PAT:-}" ]]; then
        BUILD_ARGS+=(--build-arg "GITHUB_PAT=${PRIVATE_GITHUB_PAT}")
    fi
    docker build "${BUILD_ARGS[@]}" "$TASK_PATH_INPUT"

    echo "[run] Auth mode (billing) = $BILLING_MODE"

    DOCKER_RUN_OPTS=(--rm -v "$RUN_DIR:/runs")

    # Relax security similar to working synth-research setup (removed -it for non-interactive shells)
    DOCKER_RUN_OPTS+=( --security-opt seccomp=unconfined --security-opt apparmor=unconfined --cap-add SYS_ADMIN --cap-add SYS_PTRACE )

    # Mount codex auth dir if using auth mode
    if [[ "$BILLING_MODE" == "auth" ]]; then
        DOCKER_RUN_OPTS+=( -v "$HOME/.codex:/root/.codex:ro" )
        echo "[run] Mounting ~/.codex into container (read-only)"
    fi

    FORCE_OPENAI="${FORCE_OPENAI:-0}"
    OPENAI_MODEL_VALUE="${OPENAI_MODEL:-gpt-5-mini}"
    if [[ "$BILLING_MODE" == "api" ]]; then
        OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
        OPENAI_API_KEY="${OPENAI_API_KEY:-}"
        ACTIVE_API_KEY="${OPENAI_API_KEY}"
        MODEL_FOR_SHIM="${OPENAI_MODEL:-gpt-5-mini}"
        SHIM_REQUIRED="0"

        if [[ "$FORCE_OPENAI" != "1" && -n "$OPENROUTER_API_KEY" ]]; then
            if PYTHONPATH="${REPO_ROOT}/src" MODEL_FOR_SHIM="$MODEL_FOR_SHIM" python3 - <<'PY'
import os
from one_shot.agents.openrouter_responses_shim import requires_responses_api

MODEL_FOR_SHIM = os.environ["MODEL_FOR_SHIM"]
if requires_responses_api(MODEL_FOR_SHIM):
    raise SystemExit(0)
raise SystemExit(1)
PY
            then
                SHIM_REQUIRED="1"
            fi
        fi

        if [[ "$SHIM_REQUIRED" == "1" ]]; then
            echo "[shim] Model ${MODEL_FOR_SHIM} requires Responses API shim"
            export OPENAI_MODEL="${MODEL_FOR_SHIM}"
            export OPENROUTER_API_KEY
            python3 "${REPO_ROOT}/scripts/start_responses_shim.py" > /tmp/oneshot_shim.log 2>&1 &
            SHIM_PID=$!
            echo "[shim] Started shim in background (PID: ${SHIM_PID})"
            sleep 3
            if ! kill -0 "${SHIM_PID}" 2>/dev/null; then
                echo "[shim] ERROR: Shim process died. Check /tmp/oneshot_shim.log"
                cat /tmp/oneshot_shim.log 2>/dev/null || true
                exit 1
            fi

            if [[ "$(uname)" == "Linux" ]]; then
                DOCKER_RUN_OPTS+=( --network host )
                SHIM_BASE_URL="http://127.0.0.1:63051"
                echo "[shim] Using host network mode (Linux)"
            else
                SHIM_BASE_URL="http://host.docker.internal:63051"
                DOCKER_RUN_OPTS+=( --add-host host.docker.internal:host-gateway )
                echo "[shim] Using host.docker.internal (Mac/Windows)"
            fi

            DOCKER_RUN_OPTS+=( -e "OPENAI_BASE_URL=${SHIM_BASE_URL}" )
            DOCKER_RUN_OPTS+=( -e "OPENAI_API_KEY=${OPENROUTER_API_KEY}" )
            OPENAI_MODEL_VALUE="${OPENAI_MODEL:-gpt-5-mini}"
            ACTIVE_API_KEY="${OPENROUTER_API_KEY}"
            echo "[run] Using Responses API shim at ${SHIM_BASE_URL}"
            echo "${SHIM_PID}" > /tmp/oneshot_shim_pid.txt
        else
            if [[ "$FORCE_OPENAI" != "1" && -n "$OPENROUTER_API_KEY" ]]; then
                DOCKER_RUN_OPTS+=( -e "OPENAI_API_KEY=${OPENROUTER_API_KEY}" )
                DOCKER_RUN_OPTS+=( -e "OPENAI_BASE_URL=https://openrouter.ai/api/v1" )
                OPENAI_MODEL_VALUE="${OPENAI_MODEL:-gpt-5-mini}"
                ACTIVE_API_KEY="${OPENROUTER_API_KEY}"
                echo "[run] Using OpenRouter directly (Chat Completions API)"
            else
                OPENAI_MODEL_VALUE="${OPENAI_MODEL:-gpt-5-mini}"
            fi
        fi
    fi

    echo "[run] Passing OPENAI_API_KEY to container"
    # Force model for this run unless caller overrides OPENAI_MODEL in env
    if [[ -n "${OPENAI_BASE_URL_VALUE:-}" ]]; then
        DOCKER_RUN_OPTS+=( -e "OPENAI_BASE_URL=${OPENAI_BASE_URL_VALUE}" )
    fi
    DOCKER_RUN_OPTS+=( -e "OPENAI_MODEL=${OPENAI_MODEL_VALUE:-gpt-5-mini}" )
    # Also pass CODEX_MODEL to align with Codex CLI expectations
    DOCKER_RUN_OPTS+=( -e "CODEX_MODEL=${OPENAI_MODEL_VALUE:-gpt-5-mini}" )
    if [[ -n "${PRIVATE_GITHUB_PAT:-}" ]]; then
        DOCKER_RUN_OPTS+=( -e "PRIVATE_GITHUB_PAT=${PRIVATE_GITHUB_PAT}" )
        echo "[run] Passing PRIVATE_GITHUB_PAT to container environment"
    fi

    # Provide a per-run Codex config via bind mount so we don't rely on image-baked config
    OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
    if [[ -n "$OPENROUTER_API_KEY" ]]; then
		MODEL_ENV="${OPENAI_MODEL:-gpt-5-mini}"
	else
		MODEL_ENV="${OPENAI_MODEL:-gpt-5-mini}"
	fi
    MODEL_ENV="${OPENAI_MODEL_VALUE:-${OPENAI_MODEL:-gpt-5-mini}}"
    if [[ "$MODEL_ENV" == gpt-5-nano* ]]; then
        REASONING_CONFIG="reasoning_effort = \"medium\""
    else
        REASONING_CONFIG=""
    fi
    CODEX_HOME_DIR="$RUN_DIR/codex_home/.codex"
    mkdir -p "$CODEX_HOME_DIR"
    mkdir -p "$CODEX_HOME_DIR/sessions"
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        cat > "$CODEX_HOME_DIR/config.toml" <<EOF
model_provider = "openai"
model = "${MODEL_ENV}"

[model_providers.openai]
name = "Synth Backend"
base_url = "${OPENAI_BASE_URL}"
wire_api = "chat"
env_key = "OPENAI_API_KEY"
EOF
    else
        printf 'model_provider = "openai"\nmodel = "%s"\n' "$MODEL_ENV" > "$CODEX_HOME_DIR/config.toml"
    fi
    if [[ -n "$REASONING_CONFIG" ]]; then
        printf '%s\n' "$REASONING_CONFIG" >> "$CODEX_HOME_DIR/config.toml"
    fi
    # In API mode, also create auth.json with the API key for Codex
    if [[ "$BILLING_MODE" == "api" ]]; then
		if [[ -n "$OPENROUTER_API_KEY" ]]; then
			API_KEY_VALUE="$OPENROUTER_API_KEY"
		else
			API_KEY_VALUE="${OPENAI_API_KEY:-}"
		fi
		if [[ -n "$API_KEY_VALUE" ]]; then
			cat > "$CODEX_HOME_DIR/auth.json" <<EOF
{
  "OPENAI_API_KEY": "${API_KEY_VALUE}",
  "tokens": null,
  "last_refresh": null
}
EOF
			echo "[run] Created auth.json with API key for Codex"
		fi
    fi
    cp -f "$CODEX_HOME_DIR/config.toml" "$RUN_DIR/artifacts/codex-config.host.toml" 2>/dev/null || true
    DOCKER_RUN_OPTS+=( -v "$CODEX_HOME_DIR:/root/.codex" )
    echo "[run] Mounting Codex config with model: $MODEL_ENV"

    echo "[run] Starting container..."
    # Sanitize RUN_ID for Docker container name (colons and others -> underscore)
    SANITIZED_RUN_ID=$(echo "$RUN_ID" | sed 's/[^a-zA-Z0-9_.-]/_/g')
    CONTAINER_NAME="oneshot_${SANITIZED_RUN_ID}"
    mkdir -p "$RUN_DIR/logs" "$RUN_DIR/artifacts"
    # Bind-mount artifacts to host so logs/results land directly without a watcher
    DOCKER_RUN_OPTS+=( -v "$RUN_DIR/artifacts:/app/artifacts" )
    # Suppress verbose docker output
    DOCKER_RUN_OPTS+=( --log-driver none )
    # Write run metadata for downstream evaluators
    START_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$RUN_DIR/metadata.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "task_dir": "${TASK_PATH_INPUT}",
  "task_id": "$(basename "${TASK_PATH_INPUT}")",
  "start_time": "${START_TIME_UTC}"
}
EOF

    # Run container, showing only essential output (suppress verbose Codex logs but show errors)
    # Use a filter to hide verbose Codex telemetry but show actual output
    docker run --name "$CONTAINER_NAME" "${DOCKER_RUN_OPTS[@]}" oneshot-task 2>&1 | \
        grep -v "codex_otel::otel_event_manager" | \
        grep -v "INFO codex" | \
        grep -v "^$" || true
    EXIT_CODE=${PIPESTATUS[0]}
    END_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    
    # Cleanup shim if it was started
    if [[ -f /tmp/oneshot_shim_pid.txt ]]; then
        SHIM_PID=$(cat /tmp/oneshot_shim_pid.txt)
        if kill -0 "${SHIM_PID}" 2>/dev/null; then
            echo "[shim] Stopping shim (PID: ${SHIM_PID})"
            kill "${SHIM_PID}" 2>/dev/null || true
            sleep 1
            kill -9 "${SHIM_PID}" 2>/dev/null || true
        fi
        rm -f /tmp/oneshot_shim_pid.txt
    fi

    # Copy container artifacts/logs and ensure canonical diff.patch
    if docker ps -a -q -f name="$CONTAINER_NAME" | grep -q .; then
        docker cp "$CONTAINER_NAME:/app/artifacts/." "$RUN_DIR/artifacts/" 2>/dev/null || true
        docker logs "$CONTAINER_NAME" > "$RUN_DIR/logs/container_full.log" 2>&1 || true
        if [[ ! -s "$RUN_DIR/artifacts/diff.patch" ]]; then
            if [[ -s "$RUN_DIR/artifacts/container_git_diff_from_baseline.patch" ]]; then
                cp -f "$RUN_DIR/artifacts/container_git_diff_from_baseline.patch" "$RUN_DIR/artifacts/diff.patch" 2>/dev/null || true
            elif [[ -s "$RUN_DIR/artifacts/container_git_diff.patch" ]]; then
                cp -f "$RUN_DIR/artifacts/container_git_diff.patch" "$RUN_DIR/artifacts/diff.patch" 2>/dev/null || true
            fi
        fi
    fi

    # Save minimal results with timing and exit code
    cat > "$RUN_DIR/results.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "task_dir": "${TASK_PATH_INPUT}",
  "exit_code": ${EXIT_CODE},
  "start_time": "${START_TIME_UTC}",
  "end_time": "${END_TIME_UTC}"
}
EOF

    # Print diff if available
    if [[ -f "$RUN_DIR/artifacts/diff.patch" && -s "$RUN_DIR/artifacts/diff.patch" ]]; then
        echo ""
        echo "========================================"
        echo "DIFF:"
        echo "========================================"
        cat "$RUN_DIR/artifacts/diff.patch"
        echo ""
    fi

    # Run evaluation and print scoring results
    if [[ -f "$TASK_PATH_INPUT/tb_meta.json" ]]; then
        echo "========================================"
        echo "SCORING RESULTS:"
        echo "========================================"
        export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"
        # Note: evaluate_run.py expects <run_dir> <task_dir> (not task_dir run_dir)
        if uv run python -m one_shot.evaluate_run "$RUN_DIR" "$TASK_PATH_INPUT" 2>&1; then
            echo ""
        else
            echo "⚠️  Evaluation failed (check logs above for details)"
            echo ""
        fi
    fi
fi

echo "Run artifacts in: $RUN_DIR"

# Explicitly exit to ensure script terminates
exit 0
