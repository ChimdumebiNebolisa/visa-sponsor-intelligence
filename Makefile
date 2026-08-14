.PHONY: setup test lint typecheck smoke app

setup:
	uv sync --frozen

test:
	uv run pytest

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run pyright

smoke:
	uv run python scripts/smoke_streamlit.py

app:
	uv run sponsor-intel app
