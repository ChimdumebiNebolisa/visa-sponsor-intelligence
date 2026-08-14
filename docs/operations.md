# Operations

## Local foundation workflow

1. Install Python 3.12 and `uv`.
2. Run `uv sync --frozen`.
3. Run Ruff, Pyright, and pytest.
4. Start the shell with `uv run sponsor-intel app`.

Full refresh, recovery, release, and scheduled-workflow procedures will be added with the corresponding implementation phases. Generated data and databases must remain outside Git.
