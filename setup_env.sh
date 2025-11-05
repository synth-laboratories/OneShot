#!/bin/bash
# Quick script to source .env file
# Usage: source setup_env.sh

if [ -f .env ]; then
    set -a
    source .env
    set +a
    echo "✅ Environment variables loaded from .env"
else
    echo "❌ .env file not found"
    return 1 2>/dev/null || exit 1
fi
