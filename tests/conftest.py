import os
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _write_jpg(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 60), color).save(path, "JPEG")


@pytest.fixture()
def make_region(tmp_path):
    def _make_region(spec: dict[str, list[str]]) -> Path:
        root = tmp_path / "region"
        root.mkdir()
        for folder_name, filenames in spec.items():
            folder = root / folder_name
            folder.mkdir()
            for index, filename in enumerate(filenames):
                _write_jpg(folder / filename, ((index * 53) % 255, (index * 97) % 255, (index * 149) % 255))
        return root

    return _make_region

