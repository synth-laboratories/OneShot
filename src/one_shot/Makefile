# Makefile for Codex-in-the-Box automation

.PHONY: help setup test run clean proxy start-proxy stop-proxy

# Default task path (under repo data directory)
TASK_PATH ?= ../../data/tasks/prepared/add-lm-tracing-readme
TIMEOUT ?= 1800
TOKEN_LIMIT ?= 100000
PROXY_PORT ?= 18080

help:
	@echo "Codex-in-the-Box Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  setup        - Set up environment and dependencies"
	@echo "  test         - Run all tests"
	@echo "  test-env     - Test environment setup"
	@echo "  test-build   - Test container build"
	@echo "  test-runtime - Test runtime components"
	@echo "  run          - Run a task"
	@echo "  proxy        - Start MITM proxy"
	@echo "  stop-proxy   - Stop MITM proxy"
	@echo "  clean        - Clean up containers and temp files"
	@echo "  results      - Show latest run results"
	@echo ""
	@echo "Variables:"
	@echo "  TASK_PATH=$(TASK_PATH)"
	@echo "  TIMEOUT=$(TIMEOUT)"
	@echo "  TOKEN_LIMIT=$(TOKEN_LIMIT)"
	@echo "  PROXY_PORT=$(PROXY_PORT)"
	@echo ""
	@echo "Examples:"
	@echo "  make run TASK_PATH=../../data/tasks/prepared/my-task"
	@echo "  make test"
	@echo "  make proxy PROXY_PORT=8080"

setup:
	@echo "Setting up Codex-in-the-Box..."
	@chmod +x *.sh
	@chmod +x *.py
	@chmod +x $(TASK_PATH)/overlay_files/*.sh 2>/dev/null || true
	@echo "Checking prerequisites..."
	@bash -c "source common.sh && check_prerequisites"
	@echo "✅ Setup complete"

test: setup
	@echo "Running full test suite..."
	@bash test_phases.sh "$(TASK_PATH)" all

test-env: setup
	@echo "Testing environment..."
	@bash test_phases.sh "$(TASK_PATH)" env

test-build: setup
	@echo "Testing container build..."
	@bash test_phases.sh "$(TASK_PATH)" build

test-runtime: setup
	@echo "Testing runtime components..."
	@bash test_phases.sh "$(TASK_PATH)" runtime

run: setup
	@echo "Running task: $(TASK_PATH)"
	@bash run_codex_box.sh "$(TASK_PATH)" "$(TIMEOUT)" "$(TOKEN_LIMIT)"

proxy: start-proxy

start-proxy:
	@echo "Starting MITM proxy on port $(PROXY_PORT)..."
	@if pgrep -f "mitmdump.*$(PROXY_PORT)" > /dev/null; then \
		echo "Proxy already running on port $(PROXY_PORT)"; \
	else \
		nohup mitmdump -p $(PROXY_PORT) \
			> /tmp/codex_mitm.out 2>&1 & \
		echo "Proxy started with PID $$!"; \
		sleep 2; \
		if curl -s -x "http://localhost:$(PROXY_PORT)" https://api.openai.com/v1/models >/dev/null 2>&1; then \
			echo "✅ Proxy running on port $(PROXY_PORT)"; \
		else \
			echo "⚠️  Proxy may not be running correctly"; \
		fi \
	fi

stop-proxy:
	@echo "Stopping MITM proxy..."
	@pkill -f "mitmdump.*$(PROXY_PORT)" 2>/dev/null || echo "No proxy running"
	@echo "Proxy stopped"

clean:
	@echo "Cleaning up..."
	@echo "Removing Codex-in-the-Box containers..."
	@docker rm -f $$(docker ps -a -q --filter "name=codex_box_") 2>/dev/null || true
	@echo "Removing test containers..."
	@docker rm -f $$(docker ps -a -q --filter "name=test_watchdog_") 2>/dev/null || true
	@echo "Removing Codex-in-the-Box images..."
	@docker rmi -f $$(docker images -q "codex_box:*") 2>/dev/null || true
	@docker rmi -f $$(docker images -q "codex_box_test:*") 2>/dev/null || true
	@echo "✅ Cleanup complete"

results:
	@echo "Latest run results:"
	@if [ -d "data/runs" ] && [ "$$(ls -A data/runs)" ]; then \
		LATEST=$$(ls -t data/runs/ | head -1); \
		echo "Run ID: $$LATEST"; \
		echo ""; \
		if [ -f "data/runs/$$LATEST/summary.txt" ]; then \
			cat "data/runs/$$LATEST/summary.txt"; \
		else \
			echo "No summary found"; \
		fi \
	else \
		echo "No runs found"; \
	fi

.DEFAULT_GOAL := help