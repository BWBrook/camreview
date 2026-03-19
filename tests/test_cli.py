from pathlib import Path

from typer.testing import CliRunner

from camreview.cli import app


def test_review_subcommand_is_exposed() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["review", "--help"])
    assert result.exit_code == 0
    assert "review [OPTIONS] REGION_ROOT" in result.output


def test_review_subcommand_invokes_launch_app(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    called: dict[str, Path] = {}

    def fake_launch_app(region_root: Path) -> None:
        called["region_root"] = region_root

    monkeypatch.setattr("camreview.cli.launch_app", fake_launch_app)
    result = runner.invoke(app, ["review", str(tmp_path)])
    assert result.exit_code == 0
    assert called["region_root"] == tmp_path
