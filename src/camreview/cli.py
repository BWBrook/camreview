from __future__ import annotations

from pathlib import Path

import typer

from .gui import launch_app

app = typer.Typer(help="PySide6 desktop reviewer for first-pass camera-trap image screening.")


@app.command()
def review(region_root: Path) -> None:
    """Launch the desktop reviewer against a region root."""
    launch_app(region_root)


if __name__ == "__main__":
    app()
