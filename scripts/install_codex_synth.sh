#!/usr/bin/env bash
set -euo pipefail

# Install the Codex CLI and a codex-synth wrapper on the host.
# - Ensures @openai/codex is available as `codex`
# - Installs ~/.local/bin/codex-synth that delegates to `codex`

echo "[install] Setting up codex-synth wrapper"

# Verify npm
if ! command -v npm >/dev/null 2>&1; then
  echo "[install] Error: npm not found. Install Node.js and npm first." >&2
  exit 1
fi

# Ensure Codex CLI is installed
if ! command -v codex >/dev/null 2>&1; then
  echo "[install] Installing @openai/codex globally via npm..."
  npm install -g @openai/codex >/dev/null 2>&1 || {
    echo "[install] Error: failed to install @openai/codex. Try: npm install -g @openai/codex" >&2
    exit 1
  }
  echo "[install] Installed @openai/codex"
else
  echo "[install] Detected codex CLI"
fi

# Install wrapper
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
WRAPPER="$BIN_DIR/codex-synth"
cat > "$WRAPPER" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

# Minimal wrapper: delegate to codex CLI
exec codex "$@"
SH
chmod +x "$WRAPPER"

echo "[install] Installed wrapper: $WRAPPER"
echo "[install] Ensure it's on your PATH (e.g., add to ~/.zshrc):"
echo "         export PATH=\"$HOME/.local/bin:\$PATH\""
echo "[install] Done. Test with: type codex-synth"


