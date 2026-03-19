from __future__ import annotations

from pathlib import Path

from .models import FileSnapshot
from .parser import parse_capture_filename

ANIMAL_DIRNAME = "ANIMAL"
SITE_PHOTO_DIRNAME = "SITE_PHOTOS"
STATE_DIRNAME = ".camreview"
OUTPUT_DIRS = {ANIMAL_DIRNAME, SITE_PHOTO_DIRNAME}


def ensure_project_dirs(region_root: Path) -> Path:
    region_root.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_DIRS:
        (region_root / name).mkdir(exist_ok=True)
    state_dir = region_root / STATE_DIRNAME
    state_dir.mkdir(exist_ok=True)
    return state_dir


def is_site_folder(path: Path) -> bool:
    parts = path.name.split("_")
    return (
        path.is_dir()
        and len(parts) == 2
        and all(part.isdigit() for part in parts)
        and path.name not in OUTPUT_DIRS
        and path.name != STATE_DIRNAME
    )


def discover_site_folders(region_root: Path) -> list[Path]:
    return sorted(path for path in region_root.iterdir() if is_site_folder(path))


def _snapshot_for_path(path: Path, region_root: Path, folder_name: str | None) -> FileSnapshot:
    stat = path.stat()
    parsed = parse_capture_filename(path.name)
    warning = None if parsed is not None else "Filename timestamp could not be parsed; using file modification time."
    return FileSnapshot(
        folder_name=folder_name,
        relpath=path.relative_to(region_root).as_posix(),
        filename=path.name,
        filesize=stat.st_size,
        mtime=stat.st_mtime,
        parsed_timestamp=parsed,
        parse_warning=warning,
    )


def discover_source_images(region_root: Path) -> dict[str, list[FileSnapshot]]:
    folders: dict[str, list[FileSnapshot]] = {}
    for folder in discover_site_folders(region_root):
        snapshots = []
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() == ".jpg":
                snapshots.append(_snapshot_for_path(path, region_root, folder.name))
        folders[folder.name] = snapshots
    return folders


def discover_all_actual_files(region_root: Path) -> dict[str, FileSnapshot]:
    actual: dict[str, FileSnapshot] = {}
    for folder_name, snapshots in discover_source_images(region_root).items():
        for snapshot in snapshots:
            actual[snapshot.relpath] = snapshot
    for output_name in OUTPUT_DIRS:
        output_dir = region_root / output_name
        if not output_dir.exists():
            continue
        for path in sorted(output_dir.iterdir()):
            if path.is_file() and path.suffix.lower() == ".jpg":
                snapshot = _snapshot_for_path(path, region_root, None)
                actual[snapshot.relpath] = snapshot
    return actual

