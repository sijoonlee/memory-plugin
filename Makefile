.PHONY: install test

install:
	uv sync --extra dev
	uv run memory-mcp install-model

test:
	uv run --extra dev pytest
