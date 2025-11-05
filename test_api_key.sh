#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Testing OpenAI API Key ==="
echo ""

# Load .env
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "ERROR: OPENAI_API_KEY not found in .env"
    exit 1
fi

echo "Testing API key: ${OPENAI_API_KEY:0:20}..."
echo ""

# Test the API key
http_code=$(curl -s -w "%{http_code}" -o /tmp/openai_test.json https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json")

body=$(cat /tmp/openai_test.json 2>/dev/null || echo "{}")

if [[ "$http_code" == "200" ]]; then
    echo "✅ API Key is VALID!"
    echo ""
    echo "Available models:"
    echo "$body" | python3 -c "import sys, json; models = json.load(sys.stdin)['data']; print('\\n'.join(f\"  - {m['id']}\" for m in models[:10]))"
else
    echo "❌ API Key is INVALID or EXPIRED"
    echo ""
    echo "HTTP Status: $http_code"
    echo "Response: $body"
    echo ""
    echo "Please update your OPENAI_API_KEY in .env file"
    echo "Get a new key from: https://platform.openai.com/api-keys"
fi

