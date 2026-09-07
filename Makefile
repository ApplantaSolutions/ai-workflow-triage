.DEFAULT_GOAL := help
PY ?= python

.PHONY: help install test lint format-check secret-scan examples examples-check demo

help:
	@echo "install       editable install with dev extras"
	@echo "test          run the offline test suite"
	@echo "lint          ruff check"
	@echo "format-check  ruff format --check"
	@echo "secret-scan   regex secret scan"
	@echo "examples      regenerate examples/audit-*.json"
	@echo "examples-check fail if any committed example is stale"
	@echo "demo          run one scenario through the CLI"

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

format-check:
	$(PY) -m ruff format --check .

secret-scan:
	$(PY) scripts/scan_secrets.py

examples:
	$(PY) scripts/build_examples.py

examples-check:
	$(PY) scripts/build_examples.py --check

demo:
	$(PY) -m ai_workflow_triage.cli analyze --scenario security-prompt-injection
