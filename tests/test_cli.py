from typer.testing import CliRunner

from insights.cli import app

runner = CliRunner()


def test_version_command_prints_package_version_and_exits_successfully():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0.dev0"
