# Agent E2E Integration Tests

These tests verify end-to-end agent execution for Codex and OpenCode.

## Prerequisites

1. **Docker**: Must be running and accessible
2. **API Keys**: Set in environment or `.env` file:
   - `OPENAI_API_KEY` - Required for all tests
   - `SYNTH_API_KEY` - Required for `test_codex_with_synth_small` and `test_codex_with_synth_small_local_backend`
3. **Test Task**: `data/tasks/prepared/hello-world-example` must exist
4. **Dependencies**: Codex and OpenCode must be properly installed
5. **Local Backend** (for `test_codex_with_synth_small_local_backend`):
   - Backend must be running at `http://127.0.0.1:8000`
   - Start with: `cd monorepo/backend && nohup uv run uvicorn app.routes.main:app --reload --host 127.0.0.1 --port 8000 > /tmp/synth_backend.log 2>&1 &`
   - Verify with: `curl http://127.0.0.1:8000/health`

## Running Tests

Run all integration tests:
```bash
pytest tests/integration/test_agent_e2e.py -v
```

Run a specific test:
```bash
pytest tests/integration/test_agent_e2e.py::test_codex_with_gpt5_nano -v
```

## Test Descriptions

### `test_codex_with_gpt5_nano`
Tests Codex agent with `gpt-5-nano` model using direct OpenAI API.
- Verifies agent completes execution
- Verifies `diff.patch` is created and contains valid changes

### `test_codex_with_synth_small`
Tests Codex agent with `synth-small` model via synth backend (remote).
- Verifies agent completes execution
- Verifies `diff.patch` is created and contains valid changes
- Requires both `SYNTH_API_KEY` and `OPENAI_API_KEY`

### `test_codex_with_synth_small_local_backend`
Tests Codex agent with `synth-small` model via **local** synth backend.
- Verifies agent connects to local backend at `http://host.docker.internal:8000/api/synth-research`
- Verifies stream completes successfully (no "stream disconnected before completion" errors)
- Verifies `diff.patch` is created and contains valid changes
- Requires both `SYNTH_API_KEY` and `OPENAI_API_KEY`
- **Requires local backend to be running** (see Prerequisites)

### `test_opencode_with_gpt5_nano`
Tests OpenCode agent with `gpt-5-nano` model.
- Runs in Docker mode for consistency
- Verifies diff is created (checks multiple locations)
- Falls back to checking git diff in repo if artifact file not found

## Test Output

Each test creates a run directory under `data/runs/test_<timestamp>/` containing:
- `artifacts/diff.patch` - The submitted diff
- `results.json` - Run metadata
- `logs/` - Container logs (if applicable)

## Troubleshooting

- **Test times out**: Increase timeout in test or check Docker/network issues
- **No diff found**: Check agent logs in run directory, verify task is valid
- **API key errors**: Ensure keys are set in environment or `.env` file

