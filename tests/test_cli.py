import json

from typer.testing import CliRunner

from insights.cli import app

runner = CliRunner()


def test_version_command_prints_package_version_and_exits_successfully():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0.dev0"


def test_simulate_command_writes_the_three_files(tmp_path):
    out_dir = tmp_path / "sim-1"

    result = runner.invoke(
        app,
        [
            "simulate",
            "--seed",
            "1",
            "--days",
            "5",
            "--end",
            "2026-10-20T00:00:00",
            "--out",
            str(out_dir),
        ],
    )

    assert result.exit_code == 0
    assert "10 incidents" in result.stdout
    assert sorted(path.name for path in out_dir.iterdir()) == [
        "event_failures.jsonl",
        "events.jsonl",
        "truth.json",
    ]
    truth = json.loads((out_dir / "truth.json").read_text(encoding="utf-8"))
    assert truth["start"] == "2026-10-15T00:00:00Z"
    assert truth["end"] == "2026-10-20T00:00:00Z"


def test_simulate_command_rounds_end_down_to_whole_hour(tmp_path):
    result = runner.invoke(
        app,
        ["simulate", "--days", "5", "--end", "2026-10-20T08:45:12", "--out", str(tmp_path)],
    )

    assert result.exit_code == 0
    truth = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    assert truth["end"] == "2026-10-20T08:00:00Z"


def test_simulate_command_with_same_seed_writes_identical_files(tmp_path):
    arguments = ["simulate", "--seed", "3", "--days", "5", "--end", "2026-10-20T00:00:00"]

    runner.invoke(app, [*arguments, "--out", str(tmp_path / "first")])
    runner.invoke(app, [*arguments, "--out", str(tmp_path / "second")])

    for name in ["events.jsonl", "event_failures.jsonl", "truth.json"]:
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_simulate_command_rejects_too_few_days(tmp_path):
    result = runner.invoke(app, ["simulate", "--days", "3", "--out", str(tmp_path)])

    assert result.exit_code == 2
    assert not any(tmp_path.iterdir())
