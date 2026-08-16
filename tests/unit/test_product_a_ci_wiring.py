"""Contracts for Product A local and continuous-integration entry points."""

from pathlib import Path


def test_make_acceptance_uses_product_a_runner() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    acceptance = makefile.split("\nacceptance:\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert "scripts/run_product_a_acceptance.py" in acceptance
    assert "run_v1_acceptance.py" not in acceptance
    assert (
        "acceptance-ci:\n\tuv run pytest --no-cov tests/integration/test_product_a_acceptance.py"
    ) in makefile


def test_ci_explicitly_exercises_product_a_offline_contracts() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    fixture = "Build sanitized Product A metrics and DuckDB fixture"
    quality = "Exercise Product A quality contracts without network or secrets"
    smoke = "Smoke-test Streamlit against the nonempty Product A fixture"
    acceptance = "Exercise the Product A acceptance runner on an offline fixture"

    assert "SPONSOR_INTEL_CI_FIXTURE_ROOT: .ci/product-a-fixture" in workflow
    assert "scripts/build_phase10_ci_fixture.py" in workflow
    assert "tests/unit/test_quality_release.py" in workflow
    assert "run: make smoke" in workflow
    assert "run: make acceptance-ci" in workflow
    assert "OPENAI_API_KEY" not in workflow
    assert workflow.index(fixture) < workflow.index(quality) < workflow.index(smoke)
    assert workflow.index(smoke) < workflow.index(acceptance)
