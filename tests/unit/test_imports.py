"""Package import smoke tests."""

import sponsor_intel
from sponsor_intel import cli, config, logging, services


def test_public_package_modules_import() -> None:
    assert sponsor_intel.__version__
    assert cli.app.info.name == "sponsor-intel"
    assert config.DEFAULT_CONFIG_PATH.name == "settings.yaml"
    assert logging.get_logger("tests").name == "sponsor_intel.tests"
    assert services.get_explorer_service()
