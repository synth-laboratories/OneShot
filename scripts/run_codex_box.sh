#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/synth_models.sh"

# Function to validate API key format
validate_api_key_format() {
  local key="$1"
  local key_type="$2"  # "openai" or "synth"
  
  if [[ -z "$key" ]]; then
    return 1
  fi
  
  if [[ "$key_type" == "synth" ]]; then
    if [[ "$key" =~ ^sk-synth- ]] || [[ "$key" =~ ^sk_live_ ]]; then
      return 0
    else
      echo "Warning: SYNTH_API_KEY should start with 'sk-synth-' or 'sk_live_'" >&2
      return 1
    fi
  else
    # For OpenAI keys, REJECT OpenRouter keys (sk-or-v1-...)
    if [[ "$key" =~ ^sk-or-v1- ]]; then
      echo "Error: OpenRouter keys (sk-or-v1-...) are not supported. Use a real OpenAI API key (sk-...)." >&2
      return 1
    fi
    # Must be a real OpenAI key (sk-... but NOT sk-or-v1-...)
    if [[ "$key" =~ ^sk- ]]; then
      return 0
    else
      echo "Warning: OPENAI_API_KEY should start with 'sk-' (not OpenRouter's sk-or-v1-...)" >&2
      return 1
    fi
  fi
}

# Function to validate API key by making a test request
validate_api_key_validity() {
  local key="$1"
  local base_url="${2:-https://api.openai.com/v1}"
  
  if [[ -z "$key" ]]; then
    return 1
  fi
  
  # Skip validation if curl is not available
  if ! command -v curl >/dev/null 2>&1; then
    echo "[auth] Warning: curl not found, skipping API key validation" >&2
    return 0
  fi
  
  # Normalize base_url - ensure it ends with /v1 or similar, or add /v1
  local test_url="${base_url}"
  if [[ ! "$test_url" =~ /v[0-9]+$ ]] && [[ ! "$test_url" =~ /models$ ]] && [[ ! "$test_url" =~ /chat$ ]]; then
    # If base_url doesn't end with a version or endpoint, try /v1/models
    if [[ "$test_url" =~ /$ ]]; then
      test_url="${test_url}v1/models"
    else
      test_url="${test_url}/v1/models"
    fi
  elif [[ "$test_url" =~ /v[0-9]+$ ]]; then
    # If it ends with /v1, add /models
    test_url="${test_url}/models"
  fi
  
  # Make a minimal test request to validate the key
  local response
  response=$(curl -s -w "\n%{http_code}" \
    -H "Authorization: Bearer $key" \
    -H "Content-Type: application/json" \
    "$test_url" \
    --max-time 5 2>/dev/null || echo -e "\n000")
  
  local http_code
  http_code=$(echo "$response" | tail -n1)
  
  if [[ "$http_code" == "200" ]]; then
    echo "[auth] API key validation successful"
    return 0
  elif [[ "$http_code" == "401" ]]; then
    echo "[auth] Error: API key is invalid (401 Unauthorized)" >&2
    return 1
  elif [[ "$http_code" == "000" ]]; then
    echo "[auth] Warning: Could not reach API endpoint at $test_url, skipping validation" >&2
    return 0
  else
    echo "[auth] Warning: API key validation returned HTTP $http_code for $test_url, proceeding anyway" >&2
    return 0
  fi
}

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

# Load secrets from .env files (check multiple locations)
# Priority: task directory > repo root > current directory
ENV_LOADED=false
if [[ -f "${TASK_PATH_INPUT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${TASK_PATH_INPUT}/.env"
  set +a
  ENV_LOADED=true
  echo "[env] Loaded .env from task directory: ${TASK_PATH_INPUT}/.env"
elif [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env"
  set +a
  ENV_LOADED=true
  echo "[env] Loaded .env from repo root: ${REPO_ROOT}/.env"
elif [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source ".env"
  set +a
  ENV_LOADED=true
  echo "[env] Loaded .env from current directory: $(pwd)/.env"
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
    # Reload .env from new task location if it exists
    if [[ -f "${TASK_PATH_INPUT}/.env" && "$ENV_LOADED" != "true" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "${TASK_PATH_INPUT}/.env"
        set +a
        ENV_LOADED=true
        echo "[env] Reloaded .env from converted task directory: ${TASK_PATH_INPUT}/.env"
    fi
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
        # Reload .env from prepared task location if it exists
        if [[ -f "${TASK_PATH_INPUT}/.env" && "$ENV_LOADED" != "true" ]]; then
            set -a
            # shellcheck disable=SC1090
            source "${TASK_PATH_INPUT}/.env"
            set +a
            ENV_LOADED=true
            echo "[env] Reloaded .env from prepared task directory: ${TASK_PATH_INPUT}/.env"
        fi
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
    OPENAI_API_KEY="${OPENAI_API_KEY:-}"
    MODEL_ENV="${OPENAI_MODEL:-gpt-5-mini}"
    IS_SYNTH_MODEL=false
    if is_synth_model "$MODEL_ENV"; then
        IS_SYNTH_MODEL=true
        # Use SYNTH_BASE_URL from environment if set, otherwise use default
        if [[ -z "${SYNTH_BASE_URL:-}" ]]; then
            SYNTH_BASE_URL="$(get_default_synth_base_url)"
        fi
        export SYNTH_BASE_URL
        if [[ -z "${SYNTH_API_KEY:-}" ]]; then
            echo "Error: OPENAI_MODEL=${MODEL_ENV} requires SYNTH_API_KEY (not found in environment)." >&2
            echo "Checked for .env files in:" >&2
            echo "  - ${TASK_PATH_INPUT}/.env" >&2
            echo "  - ${REPO_ROOT}/.env" >&2
            echo "  - $(pwd)/.env" >&2
            echo "Set SYNTH_API_KEY in one of these .env files or export it before running this script." >&2
            exit 1
        fi
        # Detect mangled SYNTH_API_KEY (e.g., if .env has no newline between SYNTH_API_KEY and OPENAI_API_KEY)
        if [[ ${#SYNTH_API_KEY} -gt 100 ]] || [[ "$SYNTH_API_KEY" =~ OPENAI_API_KEY= ]]; then
            # Find which .env file has the issue
            ENV_FILE=""
            if [[ -f "${TASK_PATH_INPUT}/.env" ]] && grep -q "^SYNTH_API_KEY=" "${TASK_PATH_INPUT}/.env" 2>/dev/null; then
                ENV_FILE="${TASK_PATH_INPUT}/.env"
            elif [[ -f "${REPO_ROOT}/.env" ]] && grep -q "^SYNTH_API_KEY=" "${REPO_ROOT}/.env" 2>/dev/null; then
                ENV_FILE="${REPO_ROOT}/.env"
            elif [[ -f ".env" ]] && grep -q "^SYNTH_API_KEY=" ".env" 2>/dev/null; then
                ENV_FILE="$(pwd)/.env"
            fi
            echo "Error: SYNTH_API_KEY is mangled (length: ${#SYNTH_API_KEY})." >&2
            echo "This usually happens when .env has no newline between SYNTH_API_KEY=... and OPENAI_API_KEY=..." >&2
            if [[ -n "$ENV_FILE" ]]; then
                echo "Fix the .env file at: $ENV_FILE" >&2
                echo "Make sure SYNTH_API_KEY=... and OPENAI_API_KEY=... are on separate lines." >&2
            else
                echo "Check your .env files for mangled SYNTH_API_KEY values." >&2
            fi
            exit 1
        fi
        # Validate SYNTH_API_KEY format
        if ! validate_api_key_format "${SYNTH_API_KEY}" "synth"; then
            echo "Error: SYNTH_API_KEY format is invalid. Expected format: sk-synth-... or sk_live_..." >&2
            exit 1
        fi
        OPENAI_API_KEY="${SYNTH_API_KEY}"
        export OPENAI_API_KEY
        # Use SYNTH_BASE_URL from environment if set, otherwise fall back to OPENAI_BASE_URL
        OPENAI_BASE_URL="${OPENAI_BASE_URL:-$SYNTH_BASE_URL}"
        export OPENAI_BASE_URL
        # Set OPENAI_BASE_URL_VALUE for later use in Docker container
        OPENAI_BASE_URL_VALUE="${OPENAI_BASE_URL}"
        export OPENAI_BASE_URL_VALUE
        echo "[synth] Using Synth backend at ${OPENAI_BASE_URL}"
        # Validate API key validity
        if ! validate_api_key_validity "${OPENAI_API_KEY}" "${OPENAI_BASE_URL}"; then
            echo "Error: SYNTH_API_KEY validation failed. Please check your API key." >&2
            exit 1
        fi
    else
        if [[ -z "$OPENAI_API_KEY" ]]; then
            echo "Error: billing=api requires OPENAI_API_KEY in the environment." >&2
            echo "Checked for .env files in:" >&2
            echo "  - ${TASK_PATH_INPUT}/.env" >&2
            echo "  - ${REPO_ROOT}/.env" >&2
            echo "  - $(pwd)/.env" >&2
            echo "Set OPENAI_API_KEY in one of these .env files or export it: export OPENAI_API_KEY=sk-..." >&2
            exit 1
        fi
        # Trim whitespace from API key
        OPENAI_API_KEY=$(echo "$OPENAI_API_KEY" | xargs)
        
        # Detect mangled OPENAI_API_KEY (e.g., if .env has no newline between SYNTH_API_KEY and OPENAI_API_KEY)
        if [[ ${#OPENAI_API_KEY} -gt 200 ]] || [[ "$OPENAI_API_KEY" =~ SYNTH_API_KEY= ]] || [[ "$OPENAI_API_KEY" =~ ^sk_live_.*OPENAI_API_KEY= ]]; then
            # Find which .env file has the issue
            ENV_FILE=""
            if [[ -f "${TASK_PATH_INPUT}/.env" ]] && grep -q "^OPENAI_API_KEY=" "${TASK_PATH_INPUT}/.env" 2>/dev/null; then
                ENV_FILE="${TASK_PATH_INPUT}/.env"
            elif [[ -f "${REPO_ROOT}/.env" ]] && grep -q "^OPENAI_API_KEY=" "${REPO_ROOT}/.env" 2>/dev/null; then
                ENV_FILE="${REPO_ROOT}/.env"
            elif [[ -f ".env" ]] && grep -q "^OPENAI_API_KEY=" ".env" 2>/dev/null; then
                ENV_FILE="$(pwd)/.env"
            fi
            echo "Error: OPENAI_API_KEY is mangled (length: ${#OPENAI_API_KEY})." >&2
            echo "This usually happens when .env has no newline between SYNTH_API_KEY=... and OPENAI_API_KEY=..." >&2
            if [[ -n "$ENV_FILE" ]]; then
                echo "Fix the .env file at: $ENV_FILE" >&2
                echo "Make sure SYNTH_API_KEY=... and OPENAI_API_KEY=... are on separate lines." >&2
            else
                echo "Check your .env files for mangled OPENAI_API_KEY values." >&2
            fi
            exit 1
        fi
        
        # REJECT OpenRouter keys - require real OpenAI keys
        if [[ "$OPENAI_API_KEY" =~ ^sk-or-v1- ]]; then
            echo "Error: OpenRouter keys (sk-or-v1-...) are not supported. You must use a real OpenAI API key (sk-...)." >&2
            echo "Get your OpenAI API key from: https://platform.openai.com/api-keys" >&2
            echo "Set OPENAI_API_KEY in your .env file with a real OpenAI key." >&2
            exit 1
        fi
        
        # Validate OPENAI_API_KEY format - must be a real OpenAI key (sk-... but NOT sk-or-v1-...)
        if ! validate_api_key_format "${OPENAI_API_KEY}" "openai"; then
            echo "Error: OPENAI_API_KEY format is invalid. Expected format: sk-..." >&2
            echo "OpenRouter keys (sk-or-v1-...) are not supported. Use a real OpenAI API key." >&2
            exit 1
        fi
        
        # Debug: show which key we're validating (masked)
        KEY_PREFIX_DEBUG="${OPENAI_API_KEY:0:10}"
        KEY_SUFFIX_DEBUG="${OPENAI_API_KEY: -4}"
        KEY_LEN_DEBUG="${#OPENAI_API_KEY}"
        echo "[auth] Validating OPENAI_API_KEY: ${KEY_PREFIX_DEBUG}...${KEY_SUFFIX_DEBUG} (length: ${KEY_LEN_DEBUG})"
        
        # Validate API key validity - ALWAYS use OpenAI's API for validation
        if ! validate_api_key_validity "${OPENAI_API_KEY}" "https://api.openai.com/v1"; then
            echo "Error: OPENAI_API_KEY validation failed. Please check your API key." >&2
            echo "Validated against https://api.openai.com/v1" >&2
            echo "Key used: ${KEY_PREFIX_DEBUG}...${KEY_SUFFIX_DEBUG} (length: ${KEY_LEN_DEBUG})" >&2
            echo "Make sure you're using a real OpenAI API key (not OpenRouter)." >&2
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
		OPENAI_API_KEY_VALUE="${OPENAI_API_KEY}"
		OPENAI_BASE_URL_VALUE="${OPENAI_BASE_URL:-}"
		OPENAI_MODEL_VALUE="${MODEL_ENV}"

		if [[ ! -f "$TASK_PATH_INPUT/.env" ]]; then
			{
				echo "OPENAI_API_KEY=${OPENAI_API_KEY_VALUE}"
				# Only include OPENAI_BASE_URL for synth models, not for OpenAI provider
				if [[ "$IS_SYNTH_MODEL" == "true" && -n "$OPENAI_BASE_URL_VALUE" ]]; then
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
			# Remove OPENAI_BASE_URL from existing .env if using OpenAI provider (not synth)
			# This prevents OpenRouter from overriding the config file's base_url
			if [[ "$IS_SYNTH_MODEL" != "true" ]]; then
				if grep -q '^OPENAI_BASE_URL=' "$TASK_PATH_INPUT/.env" 2>/dev/null; then
					# Remove OPENAI_BASE_URL line from .env
					sed -i.bak '/^OPENAI_BASE_URL=/d' "$TASK_PATH_INPUT/.env" 2>/dev/null || \
						sed -i '' '/^OPENAI_BASE_URL=/d' "$TASK_PATH_INPUT/.env" 2>/dev/null || \
						(grep -v '^OPENAI_BASE_URL=' "$TASK_PATH_INPUT/.env" > "$TASK_PATH_INPUT/.env.tmp" && mv "$TASK_PATH_INPUT/.env.tmp" "$TASK_PATH_INPUT/.env")
					echo "[env] Removed OPENAI_BASE_URL from .env (using OpenAI provider, not OpenRouter)"
				fi
			fi
			# Ensure API key exists - ALWAYS overwrite to prevent mixing keys
			# Remove existing OPENAI_API_KEY line regardless of model type
			if grep -q '^OPENAI_API_KEY=' "$TASK_PATH_INPUT/.env" 2>/dev/null; then
				# Remove existing OPENAI_API_KEY line
				sed -i.bak '/^OPENAI_API_KEY=/d' "$TASK_PATH_INPUT/.env" 2>/dev/null || \
					sed -i '' '/^OPENAI_API_KEY=/d' "$TASK_PATH_INPUT/.env" 2>/dev/null || \
					(grep -v '^OPENAI_API_KEY=' "$TASK_PATH_INPUT/.env" > "$TASK_PATH_INPUT/.env.tmp" && mv "$TASK_PATH_INPUT/.env.tmp" "$TASK_PATH_INPUT/.env")
				if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
					echo "[env] Removed existing OPENAI_API_KEY from .env (replacing with Synth key)"
				else
					echo "[env] Removed existing OPENAI_API_KEY from .env (replacing with OpenAI key)"
				fi
			fi
			# Also check for and fix mangled SYNTH_API_KEY lines (SYNTH_API_KEY=...OPENAI_API_KEY=...)
			if grep -q '^SYNTH_API_KEY=.*OPENAI_API_KEY=' "$TASK_PATH_INPUT/.env" 2>/dev/null; then
				echo "[env] WARNING: Found mangled SYNTH_API_KEY line in .env, fixing it..."
				# Extract just the SYNTH_API_KEY value (before OPENAI_API_KEY=)
				CLEAN_SYNTH_KEY=$(grep '^SYNTH_API_KEY=' "$TASK_PATH_INPUT/.env" | head -1 | sed 's/^SYNTH_API_KEY=//' | sed 's/OPENAI_API_KEY=.*$//' | tr -d '\r\n' | sed 's/[[:space:]]*$//')
				# Remove the mangled line
				sed -i.bak '/^SYNTH_API_KEY=/d' "$TASK_PATH_INPUT/.env" 2>/dev/null || \
					sed -i '' '/^SYNTH_API_KEY=/d' "$TASK_PATH_INPUT/.env" 2>/dev/null || \
					(grep -v '^SYNTH_API_KEY=' "$TASK_PATH_INPUT/.env" > "$TASK_PATH_INPUT/.env.tmp" && mv "$TASK_PATH_INPUT/.env.tmp" "$TASK_PATH_INPUT/.env")
				# Write clean SYNTH_API_KEY if we extracted one
				if [[ -n "$CLEAN_SYNTH_KEY" ]] && [[ ${#CLEAN_SYNTH_KEY} -lt 100 ]]; then
					printf 'SYNTH_API_KEY=%s\n' "$CLEAN_SYNTH_KEY" >> "$TASK_PATH_INPUT/.env"
					echo "[env] Fixed mangled SYNTH_API_KEY line"
				fi
			fi
			# Always add the correct key
			printf '\nOPENAI_API_KEY=%s\n' "${OPENAI_API_KEY_VALUE}" >> "$TASK_PATH_INPUT/.env"
			if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
				echo "[env] Set OPENAI_API_KEY to Synth key in .env"
			else
				echo "[env] Set OPENAI_API_KEY to OpenAI key in .env"
			fi
			# Only add OPENAI_BASE_URL for synth models - FORCE overwrite to ensure correct URL
			if [[ "$IS_SYNTH_MODEL" == "true" && -n "$OPENAI_BASE_URL_VALUE" ]]; then
				if grep -q '^OPENAI_BASE_URL=' "$TASK_PATH_INPUT/.env" 2>/dev/null; then
					# Remove existing OPENAI_BASE_URL line
					sed -i.bak '/^OPENAI_BASE_URL=/d' "$TASK_PATH_INPUT/.env" 2>/dev/null || \
						sed -i '' '/^OPENAI_BASE_URL=/d' "$TASK_PATH_INPUT/.env" 2>/dev/null || \
						(grep -v '^OPENAI_BASE_URL=' "$TASK_PATH_INPUT/.env" > "$TASK_PATH_INPUT/.env.tmp" && mv "$TASK_PATH_INPUT/.env.tmp" "$TASK_PATH_INPUT/.env")
					echo "[env] Removed existing OPENAI_BASE_URL from .env (replacing with Synth base URL)"
				fi
				echo "OPENAI_BASE_URL=${OPENAI_BASE_URL_VALUE}" >> "$TASK_PATH_INPUT/.env"
				echo "[env] Set OPENAI_BASE_URL to Synth backend in .env"
			fi
		fi
		
		# Debug: show .env contents (masked) for synth models
		if [[ "$IS_SYNTH_MODEL" == "true" && -f "$TASK_PATH_INPUT/.env" ]]; then
			echo "[env] .env file contents (masked):"
			sed 's/\(OPENAI_API_KEY=\)[^=]*\(.\{4\}\)/\1***\2/g' "$TASK_PATH_INPUT/.env" | sed 's/^/  /'
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

# Always regenerate box_bootstrap.sh from template before building to ensure latest version
# This updates the file in the build context, so Docker will rebuild just that layer
# without breaking the cache for other layers
if [[ -d "$TASK_PATH_INPUT" && -f "$TASK_PATH_INPUT/Dockerfile" ]]; then
    echo "[bootstrap] Regenerating box_bootstrap.sh from latest template..."
    export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"
    if uv run python "${REPO_ROOT}/scripts/regenerate_bootstrap.py" "$TASK_PATH_INPUT" 2>/dev/null; then
        echo "[bootstrap] ✓ Updated box_bootstrap.sh"
    else
        echo "[bootstrap] ⚠️  Warning: Failed to regenerate box_bootstrap.sh, using existing version"
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

    OPENAI_MODEL_VALUE="${OPENAI_MODEL:-gpt-5-mini}"
    ACTIVE_API_KEY="${OPENAI_API_KEY:-}"
    
    # Ensure OPENAI_API_KEY is set from ACTIVE_API_KEY for Docker
    if [[ -n "$ACTIVE_API_KEY" ]]; then
        OPENAI_API_KEY="$ACTIVE_API_KEY"
    fi
    
    # Debug: show API key info (masked)
    if [[ -n "$ACTIVE_API_KEY" ]]; then
        KEY_PREFIX="${ACTIVE_API_KEY:0:10}"
        KEY_SUFFIX="${ACTIVE_API_KEY: -4}"
        echo "[run] Using API key: ${KEY_PREFIX}...${KEY_SUFFIX} (length: ${#ACTIVE_API_KEY})"
    else
        echo "[run] ERROR: ACTIVE_API_KEY is empty! Cannot proceed without API key." >&2
        exit 1
    fi

    echo "[run] Passing OPENAI_API_KEY to container"
    # Force model for this run unless caller overrides OPENAI_MODEL in env
    # For OpenAI provider, explicitly unset OPENAI_BASE_URL to ensure we use the config file's base_url
    # For synth models, OPENAI_BASE_URL_VALUE will be set and passed through
    if [[ "$IS_SYNTH_MODEL" == "true" && -n "${OPENAI_BASE_URL_VALUE:-}" ]]; then
        DOCKER_RUN_OPTS+=( -e "OPENAI_BASE_URL=${OPENAI_BASE_URL_VALUE}" )
        echo "[run] Passing OPENAI_BASE_URL=${OPENAI_BASE_URL_VALUE} to container (Synth model)"
    elif [[ "$IS_SYNTH_MODEL" != "true" ]]; then
        # Explicitly unset OPENAI_BASE_URL for OpenAI provider to prevent OpenRouter override
        DOCKER_RUN_OPTS+=( -e "OPENAI_BASE_URL=" )
        echo "[run] Unsetting OPENAI_BASE_URL for OpenAI provider"
    fi
    DOCKER_RUN_OPTS+=( -e "OPENAI_MODEL=${OPENAI_MODEL_VALUE:-gpt-5-mini}" )
    # Also pass CODEX_MODEL to align with Codex CLI expectations
    DOCKER_RUN_OPTS+=( -e "CODEX_MODEL=${OPENAI_MODEL_VALUE:-gpt-5-mini}" )
    # ALWAYS pass OPENAI_API_KEY - it's required
    DOCKER_RUN_OPTS+=( -e "OPENAI_API_KEY=${OPENAI_API_KEY}" )
    # Enable Rust logging and stacktraces for Codex debugging
    DOCKER_RUN_OPTS+=( -e "RUST_LOG=debug" )
    DOCKER_RUN_OPTS+=( -e "RUST_BACKTRACE=1" )
    DOCKER_RUN_OPTS+=( -e "DEBUG=1" )
    echo "[run] Passing OPENAI_API_KEY to container (masked: ${KEY_PREFIX}...${KEY_SUFFIX})"
    if [[ -n "${PRIVATE_GITHUB_PAT:-}" ]]; then
        DOCKER_RUN_OPTS+=( -e "PRIVATE_GITHUB_PAT=${PRIVATE_GITHUB_PAT}" )
        echo "[run] Passing PRIVATE_GITHUB_PAT to container environment"
    fi

    # Provide a per-run Codex config via bind mount so we don't rely on image-baked config
    MODEL_ENV="${OPENAI_MODEL_VALUE:-${OPENAI_MODEL:-gpt-5-mini}}"
    echo "[config] Model: ${MODEL_ENV}"
    
    # Determine if reasoning is required for this model
    # Models that require reasoning: any gpt-5* model, o1, o1-mini, etc.
    REASONING_REQUIRED=false
    REASONING_EFFORT="medium"
    if [[ "$MODEL_ENV" =~ ^gpt-5 ]] || [[ "$MODEL_ENV" =~ ^(o1|o1-mini|o1-preview) ]]; then
        REASONING_REQUIRED=true
        echo "[config] ✓ Detected reasoning-required model: ${MODEL_ENV}"
    else
        echo "[config] Model ${MODEL_ENV} does not require reasoning"
    fi
    
    CODEX_HOME_DIR="$RUN_DIR/codex_home/.codex"
    mkdir -p "$CODEX_HOME_DIR"
    mkdir -p "$CODEX_HOME_DIR/sessions"
    
    # Write base config
    if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
        # Ensure OPENAI_BASE_URL_VALUE is set for synth models
        # It should already be set earlier, but fallback if not
        if [[ -z "${OPENAI_BASE_URL_VALUE:-}" ]]; then
            OPENAI_BASE_URL_VALUE="${OPENAI_BASE_URL:-${SYNTH_BASE_URL:-$(get_default_synth_base_url)}}"
        fi
        WIRE_API_VALUE="${WIRE_API:-responses}"
        if [[ "$WIRE_API_VALUE" != "responses" && "$WIRE_API_VALUE" != "chat" ]]; then
            echo "[config] Unknown WIRE_API='${WIRE_API_VALUE}', defaulting to responses" >&2
            WIRE_API_VALUE="responses"
        fi
        cat > "$CODEX_HOME_DIR/config.toml" <<EOF
model_provider = "myproxy"
model = "${MODEL_ENV}"

[model_providers.myproxy]
name = "Synth Backend"
base_url = "${OPENAI_BASE_URL_VALUE}"
wire_api = "${WIRE_API_VALUE}"
env_key = "OPENAI_API_KEY"
EOF
        echo "[run] Using wire_api=${WIRE_API_VALUE}"
        echo "[config] Codex config.toml for synth model:"
        cat "$CODEX_HOME_DIR/config.toml" | sed 's/^/  /'
    else
        # For OpenAI provider, explicitly set base_url to ensure we use OpenAI directly, not OpenRouter
        cat > "$CODEX_HOME_DIR/config.toml" <<EOF
model_provider = "openai"
model = "${MODEL_ENV}"

[model_providers.openai]
name = "OpenAI"
base_url = "https://api.openai.com/v1"
EOF
    fi
    
    # Always set model_reasoning_effort for models that require it
    # NOTE: Codex uses model_reasoning_effort (not reasoning_effort) in config.toml
    # This ensures reasoning is never disabled for models that mandate it
    if [[ "$REASONING_REQUIRED" == "true" ]]; then
        # Set both model_reasoning_effort and reasoning_summaries
        printf 'model_reasoning_effort = "%s"\n' "$REASONING_EFFORT" >> "$CODEX_HOME_DIR/config.toml"
        printf 'reasoning_summaries = "auto"\n' >> "$CODEX_HOME_DIR/config.toml"
        echo "[config] ✓ Set model_reasoning_effort=${REASONING_EFFORT} and reasoning_summaries=auto for ${MODEL_ENV} (required)"
        # Always show config when reasoning is required for debugging
        echo "[config] Codex config.toml contents:"
        cat "$CODEX_HOME_DIR/config.toml" | sed 's/^/  /'
    fi
    
    # Debug: show config contents for troubleshooting (if requested)
    if [[ "${DEBUG_CODEX_CONFIG:-0}" == "1" && "$REASONING_REQUIRED" != "true" ]]; then
        echo "[config] Codex config.toml contents:"
        cat "$CODEX_HOME_DIR/config.toml" | sed 's/^/  /'
    fi
    # In API mode, also create auth.json with the API key for Codex
    if [[ "$BILLING_MODE" == "api" ]]; then
        if [[ -z "$ACTIVE_API_KEY" ]]; then
            echo "Error: OPENAI_API_KEY is empty but billing=api. Cannot create auth.json." >&2
            echo "This should have been caught earlier. Check your .env files or environment." >&2
            exit 1
        fi
        # Double-check format before writing to auth.json
        if [[ "$IS_SYNTH_MODEL" == "true" ]]; then
            if ! validate_api_key_format "$ACTIVE_API_KEY" "synth"; then
                echo "Error: SYNTH_API_KEY format invalid when creating auth.json" >&2
                exit 1
            fi
        else
            if ! validate_api_key_format "$ACTIVE_API_KEY" "openai"; then
                echo "Error: OPENAI_API_KEY format invalid when creating auth.json" >&2
                exit 1
            fi
        fi
        cat > "$CODEX_HOME_DIR/auth.json" <<EOF
{
  "api_key": "${ACTIVE_API_KEY}",
  "OPENAI_API_KEY": "${ACTIVE_API_KEY}",
  "tokens": null,
  "last_refresh": null
}
EOF
        echo "[run] Created auth.json with API key for Codex"
        # Debug: show auth.json contents (masked)
        if [[ "${DEBUG_CODEX_CONFIG:-0}" == "1" ]]; then
            echo "[config] auth.json contents (masked):"
            sed 's/"\([^"]*sk[^"]*\)[^"]*\([^"]\{4\}\)"/"\1...\2"/g' "$CODEX_HOME_DIR/auth.json" | sed 's/^/  /'
        fi
    fi
    # Verify config file exists and is readable
    if [[ ! -f "$CODEX_HOME_DIR/config.toml" ]]; then
        echo "Error: Failed to create Codex config.toml" >&2
        exit 1
    fi
    
    # Verify reasoning is set if required
    if [[ "$REASONING_REQUIRED" == "true" ]]; then
        if ! grep -q "model_reasoning_effort" "$CODEX_HOME_DIR/config.toml"; then
            echo "Error: model_reasoning_effort not found in config.toml for reasoning-required model" >&2
            exit 1
        fi
        if ! grep -q "reasoning_summaries" "$CODEX_HOME_DIR/config.toml"; then
            echo "Error: reasoning_summaries not found in config.toml for reasoning-required model" >&2
            exit 1
        fi
    fi
    
    cp -f "$CODEX_HOME_DIR/config.toml" "$RUN_DIR/artifacts/codex-config.host.toml" 2>/dev/null || true
    DOCKER_RUN_OPTS+=( -v "$CODEX_HOME_DIR:/root/.codex" )
    echo "[run] Mounting Codex config with model: $MODEL_ENV"
    echo "[run] Config directory: $CODEX_HOME_DIR -> /root/.codex"

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
    # CRITICAL: Capture PIPESTATUS immediately after pipe, before any other commands
    # Save full logs (including Rust debug output) to artifacts/codex.log and logs/codex_debugging.log
    docker run --name "$CONTAINER_NAME" "${DOCKER_RUN_OPTS[@]}" oneshot-task 2>&1 | \
        tee "$RUN_DIR/artifacts/codex.log" | \
        tee "$RUN_DIR/logs/codex_debugging.log" | \
        grep -v "codex_otel::otel_event_manager" | \
        grep -v "INFO codex" | \
        grep -v "^$" || true
    EXIT_CODE=${PIPESTATUS[0]}
    END_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
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
    if [[ -f "$TASK_PATH_INPUT/tb_meta.json" && "${SKIP_EVAL:-0}" != "1" ]]; then
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
    elif [[ "${SKIP_EVAL:-0}" == "1" ]]; then
        echo "[run] Skipping evaluation (SKIP_EVAL=1)"
    fi
fi

echo "Run artifacts in: $RUN_DIR"

# Explicitly exit to ensure script terminates
exit 0
