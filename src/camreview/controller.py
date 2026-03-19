from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .models import CleanupPlan, CommitPlan, FolderRecord, ImageRecord, LabelChange, ReviewLabel
from .store import ProjectStore


class ProjectController:
    def __init__(self, region_root: Path) -> None:
        self.store = ProjectStore(region_root)
        self._undo_history: dict[str, list[LabelChange]] = defaultdict(list)

    @property
    def region_root(self) -> Path:
        return self.store.region_root

    def close(self) -> None:
        self.store.close()

    def rescan(self) -> list[FolderRecord]:
        self.store.reconcile()
        return self.folders()

    def folders(self) -> list[FolderRecord]:
        return self.store.list_folders()

    def open_folder(self, folder_name: str) -> list[ImageRecord]:
        self.store.touch_folder(folder_name)
        return self.images(folder_name)

    def images(self, folder_name: str) -> list[ImageRecord]:
        return self.store.get_images(folder_name)

    def label_image(self, image_id: int, label: ReviewLabel) -> None:
        image = self.store.get_image(image_id)
        change = self.store.set_label(image_id, label)
        self._undo_history[image.folder_name].append(change)

    def undo_last(self, folder_name: str) -> bool:
        history = self._undo_history[folder_name]
        if not history:
            return False
        change = history.pop()
        self.store.restore_label_change(change)
        return True

    def set_note(self, image_id: int, note: str) -> None:
        self.store.set_note(image_id, note)

    def commit_preview(self, folder_name: str) -> CommitPlan:
        return self.store.preview_commit(folder_name)

    def commit_folder(self, folder_name: str) -> CommitPlan:
        self._undo_history[folder_name].clear()
        return self.store.commit_folder(folder_name)

    def cleanup_preview(self, folder_name: str) -> CleanupPlan:
        return self.store.preview_cleanup(folder_name)

    def cleanup_folder(self, folder_name: str) -> CleanupPlan:
        self._undo_history[folder_name].clear()
        return self.store.cleanup_folder(folder_name)

