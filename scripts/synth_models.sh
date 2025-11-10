#!/usr/bin/env bash

# List of supported Synth models
SYNTH_MODELS=("synth-small" "synth-medium")

# Default Synth base URL (can be overridden by SYNTH_BASE_URL env var)
SYNTH_DEFAULT_BASE_URL="https://synth-backend-dev-docker.onrender.com/api/synth-research"

is_synth_model() {
    local model_name="$1"
    for sm in "${SYNTH_MODELS[@]}"; do
        if [[ "$model_name" == "$sm" ]]; then
            return 0 # Is a synth model
        fi
    done
    return 1 # Not a synth model
}

