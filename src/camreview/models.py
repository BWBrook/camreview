from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReviewLabel(StrEnum):
    UNREVIEWED = "unreviewed"
    BLANK = "blank"
    ANIMAL = "animal"
    SITE_PHOTO = "site_photo"


class FolderStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    READY_TO_COMMIT = "ready_to_commit"
    COMMITTED = "committed"
    DONE = "done"
    ERROR = "error"


@dataclass(slots=True)
class FolderRecord:
    folder_id: int
    folder_name: str
    status: FolderStatus
    total_images: int
    reviewed_images: int
    animal_count: int
    blank_count: int
    site_photo_count: int
    last_opened_at: str | None
    error_message: str | None


@dataclass(slots=True)
class ImageRecord:
    image_id: int
    folder_id: int
    folder_name: str
    original_relpath: str
    current_relpath: str | None
    filename: str
    parsed_timestamp: datetime | None
    parse_warning: str | None
    filesize: int
    mtime: float
    review_label: ReviewLabel
    note: str
    reviewed_at: str | None


@dataclass(slots=True)
class LabelSnapshot:
    image_id: int
    label: ReviewLabel
    reviewed_at: str | None


@dataclass(slots=True)
class LabelChange:
    folder_name: str
    before: list[LabelSnapshot]


@dataclass(slots=True)
class CommitMove:
    image_id: int
    filename: str
    source_relpath: str
    destination_relpath: str


@dataclass(slots=True)
class CommitPlan:
    folder_name: str
    moves: list[CommitMove]

    @property
    def animal_count(self) -> int:
        return sum(1 for move in self.moves if move.destination_relpath.startswith("ANIMAL/"))

    @property
    def site_photo_count(self) -> int:
        return sum(1 for move in self.moves if move.destination_relpath.startswith("SITE_PHOTOS/"))


@dataclass(slots=True)
class CleanupPlan:
    folder_name: str
    source_folder_relpath: str
    file_relpaths: list[str]


@dataclass(slots=True)
class FileSnapshot:
    folder_name: str | None
    relpath: str
    filename: str
    filesize: int
    mtime: float
    parsed_timestamp: datetime | None
    parse_warning: str | None

