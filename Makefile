.PHONY: setup test test-contract lint typecheck smoke app

setup:
	uv sync --frozen

test:
	uv run pytest

test-contract:
	SPONSOR_INTEL_RUN_NETWORK_TESTS=1 uv run pytest tests/contracts

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run pyright

smoke:
	uv run python scripts/smoke_streamlit.py

app:
	uv run sponsor-intel app
