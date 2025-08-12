# Makefile for CITB task creation
# Usage: make create-task TITLE="Task title" NOTES="Task instructions"

.PHONY: help create-task test-server test-mcp test-readiness test-end-to-end clean

# Default target
help:
	@echo "CITB Task Creation Makefile"
	@echo ""
	@echo "Main Targets:"
	@echo "  create-task      - Create a task with automatic start/end (requires TASK)"
	@echo "  interactive      - Start interactive codex-synth with CITB tools"
	@echo "  run-task         - Run task with title and description"
	@echo ""
	@echo "Testing Targets:"
	@echo "  test-server      - Test the HTTP tool server"
	@echo "  test-mcp         - Test the MCP server"
	@echo "  test-readiness   - Test worktree readiness checks"
	@echo "  test-end-to-end  - Run end-to-end test"
	@echo "  clean            - Clean up temporary files"
	@echo ""
	@echo "Examples:"
	@echo "  make create-task TASK=\"Add a README section about testing\""
	@echo "  make interactive"
	@echo "  make run-task TITLE=\"Fix bug\" DESC=\"Fix the login issue\""
	@echo ""
	@echo "The system will:"
	@echo "  1. Configure codex-synth with MCP tools"
	@echo "  2. Instruct the agent to use start_task and end_task"
	@echo "  3. Capture all changes and generate task artifacts"

# Create a new task using codex-synth with CITB
create-task:
	@if [ -z "$(TASK)" ]; then \
		echo "Error: TASK is required"; \
		echo "Usage: make create-task TASK=\"Your task description\""; \
		echo "Example: make create-task TASK=\"Add a README section about testing\""; \
		exit 1; \
	fi
	@echo "Creating task: $(TASK)"
	@./create_task.sh "$(TASK)"

# Start interactive codex-synth session with CITB tools
interactive:
	@echo "Starting interactive CITB session..."
	@./citb.sh

# Run codex-synth with specific task
run-task:
	@if [ -z "$(TITLE)" ] || [ -z "$(DESC)" ]; then \
		echo "Usage: make run-task TITLE=\"Title\" DESC=\"Description\""; \
		exit 1; \
	fi
	@./run_codex_with_citb.sh -t "$(TITLE)" -d "$(DESC)"

# Test the HTTP tool server
test-server:
	@echo "Testing HTTP tool server..."
	@python3 tool_server.py --port 8081 &
	@SERVER_PID=$$!; \
	sleep 2; \
	echo "Testing /health endpoint..."; \
	curl -s http://localhost:8081/health | jq .; \
	echo ""; \
	echo "Testing /check-readiness endpoint..."; \
	curl -s -X POST http://localhost:8081/check-readiness -H "Content-Type: application/json" -d '{}' | jq .; \
	kill $$SERVER_PID 2>/dev/null || true
	@echo "Server test completed"

# Test the MCP server
test-mcp:
	@echo "Testing MCP server..."
	@echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python3 mcp_citb_server.py 2>/dev/null | jq .
	@echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 mcp_citb_server.py 2>/dev/null | jq .
	@echo "MCP test completed"

# Test worktree readiness
test-readiness:
	@echo "Testing worktree readiness..."
	@python3 -c "from mcp_citb_server import WorktreeReadiness; import json; print(json.dumps(WorktreeReadiness.check_readiness(), indent=2))"

# Run end-to-end test
test-end-to-end:
	@echo "Running end-to-end test..."
	@echo "Starting tool server..."
	@python3 tool_server.py --port 8082 &
	@SERVER_PID=$$!; \
	sleep 2; \
	echo "Creating test task..."; \
	curl -s -X POST http://localhost:8082/start-task \
		-H "Content-Type: application/json" \
		-d '{"task_title":"Test Task","notes":"This is a test"}' | jq .; \
	echo ""; \
	echo "Ending test task..."; \
	curl -s -X POST http://localhost:8082/end-task \
		-H "Content-Type: application/json" \
		-d '{"summary":"Test completed"}' | jq .; \
	kill $$SERVER_PID 2>/dev/null || true
	@echo "End-to-end test completed"
	@echo "Check for created task in: ../synth_bench/tasks/created/"

# Clean up temporary files
clean:
	@echo "Cleaning up temporary files..."
	@rm -f /tmp/citb_state.json
	@rm -f /tmp/citb_prompt_*.txt
	@rm -f /tmp/citb_mcp_server.out
	@rm -f /tmp/citb_tool_server.out
	@echo "Cleanup completed"

# Install dependencies
install-deps:
	@echo "Checking Python dependencies..."
	@python3 -c "import sqlite3, json, subprocess, pathlib" && echo "✓ Core Python modules available" || echo "✗ Missing Python modules"
	@echo ""
	@echo "Checking external tools..."
	@command -v git >/dev/null 2>&1 && echo "✓ git installed" || echo "✗ git not found"
	@command -v jq >/dev/null 2>&1 && echo "✓ jq installed" || echo "✗ jq not found (optional, for testing)"
	@command -v curl >/dev/null 2>&1 && echo "✓ curl installed" || echo "✗ curl not found"
	@command -v codex >/dev/null 2>&1 && echo "✓ codex installed" || echo "✗ codex not found (install with: npm install -g @anthropic/codex)"

# Shortcuts
.PHONY: test
test: test-readiness test-server

.PHONY: all
all: install-deps test