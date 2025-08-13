SHELL := /bin/bash

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
LATEST := $(shell ls -1d $(REPO_ROOT)/data/runs/* 2>/dev/null | sort | tail -n 1)

.PHONY: latest run start-proxy

latest:
	@echo $(LATEST)

run:
	@RUN_ID=$${RUN_ID:-$$(date +%Y%m%d__%H-%M-%S)}; \
	$(REPO_ROOT)/scripts/run_codex_box.sh $(TASK)

start-proxy:
	@PROXY_PORT=$${PROXY_PORT:-8899}; \
	RUN_ID=$${RUN_ID:-$$(date +%Y%m%d__%H-%M-%S)}; \
	PYTHONPATH=$(REPO_ROOT)/src uv run local_tracing.mitm_tracer


