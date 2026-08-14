.PHONY: setup test test-contract lint typecheck smoke metrics evidence db app

setup:
	uv sync --frozen
	uv run playwright install chromium

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

metrics:
	uv run sponsor-intel metrics build

evidence:
	uv run sponsor-intel evidence build --everify-limit 0

db:
	uv run sponsor-intel db build

app:
	uv run sponsor-intel app
