.PHONY: setup test test-contract lint typecheck smoke metrics scores evidence policy policy-evaluate quality db release acceptance refresh-government refresh-policies app

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

scores:
	uv run sponsor-intel scores build

evidence:
	uv run sponsor-intel evidence build --everify-limit 0

policy:
	uv run sponsor-intel policy build --enrichment-limit 200

policy-evaluate:
	uv run sponsor-intel policy evaluate

quality:
	uv run sponsor-intel quality report

db:
	uv run sponsor-intel db build

release:
	uv run sponsor-intel release bundle

acceptance:
	uv run python scripts/run_v1_acceptance.py --verify-restore

refresh-government:
	uv run sponsor-intel refresh government --everify-limit 0

refresh-policies:
	uv run sponsor-intel refresh policies

app:
	uv run sponsor-intel app
