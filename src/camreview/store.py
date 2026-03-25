from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from send2trash import send2trash

from .models import (
    CleanupPlan,
    CommitMove,
    CommitPlan,
    FileSnapshot,
    FolderRecord,
    FolderStatus,
    ImageRecord,
    LabelChange,
    LabelSnapshot,
    ReviewLabel,
)
from .parser import sort_key_from_metadata
from .scanner import ANIMAL_DIRNAME, SITE_PHOTO_DIRNAME, discover_all_actual_files, discover_site_folders, discover_source_images, ensure_project_dirs


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _snapshot_payload(snapshot: LabelSnapshot) -> dict[str, str | int | None]:
    return {
        "image_id": snapshot.image_id,
        "label": snapshot.label.value,
        "reviewed_at": snapshot.reviewed_at,
    }


class ProjectStore:
    def __init__(self, region_root: Path) -> None:
        self.region_root = Path(region_root).expanduser().resolve()
        self.state_dir = ensure_project_dirs(self.region_root)
        self.db_path = self.state_dir / "state.sqlite3"
        self.audit_path = self.state_dir / "actions.jsonl"
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_db()
        self.reconcile()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _init_db(self) -> None:
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS folders (
                    folder_id INTEGER PRIMARY KEY,
                    folder_name TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    total_images INTEGER NOT NULL DEFAULT 0,
                    reviewed_images INTEGER NOT NULL DEFAULT 0,
                    animal_count INTEGER NOT NULL DEFAULT 0,
                    blank_count INTEGER NOT NULL DEFAULT 0,
                    site_photo_count INTEGER NOT NULL DEFAULT 0,
                    last_opened_at TEXT,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS images (
                    image_id INTEGER PRIMARY KEY,
                    folder_id INTEGER NOT NULL REFERENCES folders(folder_id) ON DELETE CASCADE,
                    original_relpath TEXT NOT NULL UNIQUE,
                    current_relpath TEXT,
                    filename TEXT NOT NULL,
                    parsed_timestamp TEXT,
                    parse_warning TEXT,
                    filesize INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    review_label TEXT NOT NULL DEFAULT 'blank',
                    note TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    folder_id INTEGER REFERENCES folders(folder_id) ON DELETE SET NULL,
                    image_id INTEGER REFERENCES images(image_id) ON DELETE SET NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                UPDATE images
                SET review_label = ?
                WHERE review_label = ?
                """,
                (ReviewLabel.BLANK.value, ReviewLabel.UNREVIEWED.value),
            )

    def _log_event(
        self,
        event_type: str,
        *,
        folder_id: int | None = None,
        image_id: int | None = None,
        payload: dict | None = None,
    ) -> None:
        payload = payload or {}
        created_at = _now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO events (event_type, folder_id, image_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_type, folder_id, image_id, json.dumps(payload, sort_keys=True), created_at),
            )
        record = {
            "created_at": created_at,
            "event_type": event_type,
            "folder_id": folder_id,
            "image_id": image_id,
            "payload": payload,
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def reconcile(self) -> None:
        ensure_project_dirs(self.region_root)
        source_images = discover_source_images(self.region_root)
        actual_files = discover_all_actual_files(self.region_root)
        site_folders = [folder.name for folder in discover_site_folders(self.region_root)]

        with self.transaction() as conn:
            for folder_name in site_folders:
                conn.execute(
                    """
                    INSERT INTO folders (folder_name, status)
                    VALUES (?, ?)
                    ON CONFLICT(folder_name) DO NOTHING
                    """,
                    (folder_name, FolderStatus.PENDING.value),
                )

            for folder_name, snapshots in source_images.items():
                folder_id = self._folder_id(folder_name)
                for snapshot in snapshots:
                    existing = conn.execute(
                        """
                        SELECT image_id, current_relpath
                        FROM images
                        WHERE original_relpath = ?
                        """,
                        (snapshot.relpath,),
                    ).fetchone()
                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO images (
                                folder_id,
                                original_relpath,
                                current_relpath,
                                filename,
                                parsed_timestamp,
                                parse_warning,
                                filesize,
                                mtime,
                                review_label
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                folder_id,
                                snapshot.relpath,
                                snapshot.relpath,
                                snapshot.filename,
                                snapshot.parsed_timestamp.isoformat(sep=" ") if snapshot.parsed_timestamp else None,
                                snapshot.parse_warning,
                                snapshot.filesize,
                                snapshot.mtime,
                                ReviewLabel.BLANK.value,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE images
                            SET filename = ?,
                                parsed_timestamp = ?,
                                parse_warning = ?
                            WHERE image_id = ?
                            """,
                            (
                                snapshot.filename,
                                snapshot.parsed_timestamp.isoformat(sep=" ") if snapshot.parsed_timestamp else None,
                                snapshot.parse_warning,
                                existing["image_id"],
                            ),
                        )

            for row in conn.execute(
                """
                SELECT image_id, current_relpath
                FROM images
                """
            ).fetchall():
                current_relpath = row["current_relpath"]
                if current_relpath and current_relpath in actual_files:
                    snapshot = actual_files[current_relpath]
                    conn.execute(
                        """
                        UPDATE images
                        SET filesize = ?, mtime = ?
                        WHERE image_id = ?
                        """,
                        (snapshot.filesize, snapshot.mtime, row["image_id"]),
                    )

        for folder_name in self._all_folder_names():
            self._recalculate_folder(folder_name, actual_files)

        self._log_event(
            "rescan",
            payload={
                "site_folders": site_folders,
                "actual_file_count": len(actual_files),
            },
        )

    def _all_folder_names(self) -> list[str]:
        rows = self._conn.execute("SELECT folder_name FROM folders ORDER BY folder_name").fetchall()
        return [row["folder_name"] for row in rows]

    def _folder_id(self, folder_name: str) -> int:
        row = self._conn.execute(
            "SELECT folder_id FROM folders WHERE folder_name = ?",
            (folder_name,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown folder: {folder_name}")
        return int(row["folder_id"])

    def _row_to_folder(self, row: sqlite3.Row) -> FolderRecord:
        return FolderRecord(
            folder_id=int(row["folder_id"]),
            folder_name=row["folder_name"],
            status=FolderStatus(row["status"]),
            total_images=int(row["total_images"]),
            reviewed_images=int(row["reviewed_images"]),
            animal_count=int(row["animal_count"]),
            blank_count=int(row["blank_count"]),
            site_photo_count=int(row["site_photo_count"]),
            last_opened_at=row["last_opened_at"],
            error_message=row["error_message"],
        )

    def _row_to_image(self, row: sqlite3.Row) -> ImageRecord:
        parsed_timestamp = datetime.fromisoformat(row["parsed_timestamp"]) if row["parsed_timestamp"] else None
        return ImageRecord(
            image_id=int(row["image_id"]),
            folder_id=int(row["folder_id"]),
            folder_name=row["folder_name"],
            original_relpath=row["original_relpath"],
            current_relpath=row["current_relpath"],
            filename=row["filename"],
            parsed_timestamp=parsed_timestamp,
            parse_warning=row["parse_warning"],
            filesize=int(row["filesize"]),
            mtime=float(row["mtime"]),
            review_label=ReviewLabel(row["review_label"]),
            note=row["note"],
            reviewed_at=row["reviewed_at"],
        )

    def list_folders(self) -> list[FolderRecord]:
        rows = self._conn.execute("SELECT * FROM folders").fetchall()
        status_order = {
            FolderStatus.ERROR: 0,
            FolderStatus.IN_PROGRESS: 1,
            FolderStatus.READY_TO_COMMIT: 2,
            FolderStatus.PENDING: 3,
            FolderStatus.COMMITTED: 4,
            FolderStatus.DONE: 5,
        }
        folders = [self._row_to_folder(row) for row in rows]
        folders.sort(key=lambda folder: (status_order[folder.status], folder.folder_name))
        return folders

    def get_folder(self, folder_name: str) -> FolderRecord:
        row = self._conn.execute(
            "SELECT * FROM folders WHERE folder_name = ?",
            (folder_name,),
        ).fetchone()
        if row is None:
            raise KeyError(folder_name)
        return self._row_to_folder(row)

    def touch_folder(self, folder_name: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE folders SET last_opened_at = ? WHERE folder_name = ?",
                (_now(), folder_name),
            )

    def get_images(self, folder_name: str) -> list[ImageRecord]:
        rows = self._conn.execute(
            """
            SELECT images.*, folders.folder_name
            FROM images
            JOIN folders USING(folder_id)
            WHERE folders.folder_name = ?
            """,
            (folder_name,),
        ).fetchall()
        images = [self._row_to_image(row) for row in rows]
        snapshots = [
            FileSnapshot(
                folder_name=image.folder_name,
                relpath=image.current_relpath or image.original_relpath,
                filename=image.filename,
                filesize=image.filesize,
                mtime=image.mtime,
                parsed_timestamp=image.parsed_timestamp,
                parse_warning=image.parse_warning,
            )
            for image in images
        ]
        sort_order = {
            image.image_id: sort_key_from_metadata(snapshot)
            for image, snapshot in zip(images, snapshots, strict=True)
        }
        images.sort(key=lambda image: sort_order[image.image_id])
        return images

    def get_image(self, image_id: int) -> ImageRecord:
        row = self._conn.execute(
            """
            SELECT images.*, folders.folder_name
            FROM images
            JOIN folders USING(folder_id)
            WHERE image_id = ?
            """,
            (image_id,),
        ).fetchone()
        if row is None:
            raise KeyError(image_id)
        return self._row_to_image(row)

    def set_label(self, image_id: int, label: ReviewLabel) -> LabelChange:
        image = self.get_image(image_id)
        if label == ReviewLabel.UNREVIEWED:
            label = ReviewLabel.BLANK
        if label == ReviewLabel.SITE_PHOTO and image.review_label == ReviewLabel.ANIMAL:
            raise ValueError("An animal image cannot be marked as the site photo.")
        folder_name = image.folder_name
        before: list[LabelSnapshot] = []
        reviewed_at = _now()
        with self.transaction() as conn:
            if label == ReviewLabel.SITE_PHOTO:
                site_rows = conn.execute(
                    """
                    SELECT image_id, review_label, reviewed_at
                    FROM images
                    WHERE folder_id = ? AND review_label = ?
                    """,
                    (image.folder_id, ReviewLabel.SITE_PHOTO.value),
                ).fetchall()
                for row in site_rows:
                    before.append(
                        LabelSnapshot(
                            image_id=int(row["image_id"]),
                            label=ReviewLabel(row["review_label"]),
                            reviewed_at=row["reviewed_at"],
                        )
                    )
                    if int(row["image_id"]) != image_id:
                        conn.execute(
                            """
                            UPDATE images
                            SET review_label = ?, reviewed_at = ?
                            WHERE image_id = ?
                            """,
                            (ReviewLabel.BLANK.value, _now(), row["image_id"]),
                        )

            row = conn.execute(
                "SELECT review_label, reviewed_at FROM images WHERE image_id = ?",
                (image_id,),
            ).fetchone()
            before.append(
                LabelSnapshot(
                    image_id=image_id,
                    label=ReviewLabel(row["review_label"]),
                    reviewed_at=row["reviewed_at"],
                )
            )
            conn.execute(
                """
                UPDATE images
                SET review_label = ?, reviewed_at = ?
                WHERE image_id = ?
                """,
                (label.value, reviewed_at, image_id),
            )

        self._recalculate_folder(folder_name)
        change = LabelChange(folder_name=folder_name, before=before)
        self._log_event(
            "label",
            folder_id=image.folder_id,
            image_id=image_id,
            payload={
                "folder_name": folder_name,
                "before": [_snapshot_payload(snapshot) for snapshot in before],
                "after": label.value,
            },
        )
        return change

    def mark_reviewed(self, image_id: int) -> None:
        image = self.get_image(image_id)
        if image.reviewed_at is not None:
            return
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE images
                SET reviewed_at = ?
                WHERE image_id = ? AND reviewed_at IS NULL
                """,
                (_now(), image_id),
            )
        self._recalculate_folder(image.folder_name)
        self._log_event(
            "reviewed",
            folder_id=image.folder_id,
            image_id=image_id,
            payload={"folder_name": image.folder_name},
        )

    def restore_label_change(self, change: LabelChange) -> None:
        with self.transaction() as conn:
            for snapshot in change.before:
                conn.execute(
                    """
                    UPDATE images
                    SET review_label = ?, reviewed_at = ?
                    WHERE image_id = ?
                    """,
                    (snapshot.label.value, snapshot.reviewed_at, snapshot.image_id),
                )
        self._recalculate_folder(change.folder_name)
        self._log_event(
            "undo_label",
            folder_id=self._folder_id(change.folder_name),
            payload={"folder_name": change.folder_name, "restored": [_snapshot_payload(snapshot) for snapshot in change.before]},
        )

    def set_note(self, image_id: int, note: str) -> None:
        image = self.get_image(image_id)
        note = note.strip()
        if note == image.note:
            return
        with self.transaction() as conn:
            conn.execute(
                "UPDATE images SET note = ? WHERE image_id = ?",
                (note, image_id),
            )
        self._log_event(
            "note",
            folder_id=image.folder_id,
            image_id=image_id,
            payload={"note": note},
        )

    def preview_commit(self, folder_name: str) -> CommitPlan:
        folder = self.get_folder(folder_name)
        if folder.status != FolderStatus.READY_TO_COMMIT:
            raise ValueError("Folder is not ready to commit.")
        moves: list[CommitMove] = []
        for image in self.get_images(folder_name):
            if image.current_relpath is None:
                raise ValueError("Cannot commit a folder with trashed images.")
            if image.review_label == ReviewLabel.ANIMAL:
                moves.append(
                    CommitMove(
                        image_id=image.image_id,
                        filename=image.filename,
                        source_relpath=image.current_relpath,
                        destination_relpath=f"{ANIMAL_DIRNAME}/{image.filename}",
                    )
                )
            elif image.review_label == ReviewLabel.SITE_PHOTO:
                moves.append(
                    CommitMove(
                        image_id=image.image_id,
                        filename=image.filename,
                        source_relpath=image.current_relpath,
                        destination_relpath=f"{SITE_PHOTO_DIRNAME}/{image.filename}",
                    )
                )
        return CommitPlan(folder_name=folder_name, moves=moves)

    def commit_folder(self, folder_name: str) -> CommitPlan:
        plan = self.preview_commit(folder_name)
        if any((self.region_root / move.destination_relpath).exists() for move in plan.moves):
            with self.transaction() as conn:
                conn.execute(
                    "UPDATE folders SET status = ?, error_message = ? WHERE folder_name = ?",
                    (FolderStatus.ERROR.value, "Destination collision detected during commit.", folder_name),
                )
            raise FileExistsError("Destination collision detected during commit.")

        moved: list[tuple[Path, Path]] = []
        try:
            for move in plan.moves:
                source = self.region_root / move.source_relpath
                destination = self.region_root / move.destination_relpath
                destination.parent.mkdir(exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append((destination, source))
                with self.transaction() as conn:
                    conn.execute(
                        "UPDATE images SET current_relpath = ? WHERE image_id = ?",
                        (move.destination_relpath, move.image_id),
                    )
        except Exception:
            for destination, source in reversed(moved):
                if destination.exists():
                    shutil.move(str(destination), str(source))
            self._recalculate_folder(folder_name, discover_all_actual_files(self.region_root))
            raise

        actual_files = discover_all_actual_files(self.region_root)
        self._recalculate_folder(folder_name, actual_files)
        self._log_event(
            "commit",
            folder_id=self._folder_id(folder_name),
            payload={
                "moves": [
                    {
                        "image_id": move.image_id,
                        "filename": move.filename,
                        "source_relpath": move.source_relpath,
                        "destination_relpath": move.destination_relpath,
                    }
                    for move in plan.moves
                ]
            },
        )
        return plan

    def preview_cleanup(self, folder_name: str) -> CleanupPlan:
        folder = self.get_folder(folder_name)
        if folder.status != FolderStatus.COMMITTED:
            raise ValueError("Folder is not ready for cleanup.")
        images = self.get_images(folder_name)
        invalid = [
            image.current_relpath
            for image in images
            if image.current_relpath
            and image.current_relpath.startswith(f"{folder_name}/")
            and image.review_label in {ReviewLabel.ANIMAL, ReviewLabel.SITE_PHOTO}
        ]
        if invalid:
            raise ValueError("Source folder still contains animal or site_photo files.")
        file_relpaths = [
            image.current_relpath
            for image in images
            if image.current_relpath and image.current_relpath.startswith(f"{folder_name}/")
        ]
        return CleanupPlan(
            folder_name=folder_name,
            source_folder_relpath=folder_name,
            file_relpaths=sorted(file_relpaths),
        )

    def list_committed_folder_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT folder_name FROM folders WHERE status = ? ORDER BY folder_name",
            (FolderStatus.COMMITTED.value,),
        ).fetchall()
        return [row["folder_name"] for row in rows]

    def preview_cleanup_all_committed(self) -> list[CleanupPlan]:
        return [self.preview_cleanup(folder_name) for folder_name in self.list_committed_folder_names()]

    def cleanup_folder(self, folder_name: str) -> CleanupPlan:
        plan = self.preview_cleanup(folder_name)
        for relpath in plan.file_relpaths:
            path = self.region_root / relpath
            if path.exists():
                send2trash(str(path))
        folder_path = self.region_root / folder_name
        if folder_path.exists():
            send2trash(str(folder_path))
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE images
                SET current_relpath = NULL
                WHERE folder_id = ? AND review_label = ?
                """,
                (self._folder_id(folder_name), ReviewLabel.BLANK.value),
            )
        self._recalculate_folder(folder_name, discover_all_actual_files(self.region_root))
        self._log_event(
            "cleanup",
            folder_id=self._folder_id(folder_name),
            payload={"trashed_files": plan.file_relpaths, "folder_name": folder_name},
        )
        return plan

    def cleanup_all_committed(self) -> list[CleanupPlan]:
        plans: list[CleanupPlan] = []
        for folder_name in self.list_committed_folder_names():
            plans.append(self.cleanup_folder(folder_name))
        return plans

    def _recalculate_folder(self, folder_name: str, actual_files: dict[str, FileSnapshot] | None = None) -> None:
        folder_id = self._folder_id(folder_name)
        rows = self._conn.execute(
            """
            SELECT images.*, folders.folder_name
            FROM images
            JOIN folders USING(folder_id)
            WHERE folder_id = ?
            """,
            (folder_id,),
        ).fetchall()
        images = [self._row_to_image(row) for row in rows]
        total_images = len(images)
        reviewed_images = sum(1 for image in images if image.reviewed_at is not None)
        animal_count = sum(1 for image in images if image.review_label == ReviewLabel.ANIMAL)
        blank_count = sum(1 for image in images if image.review_label == ReviewLabel.BLANK)
        site_photo_count = sum(1 for image in images if image.review_label == ReviewLabel.SITE_PHOTO)
        source_folder_exists = (self.region_root / folder_name).exists()

        error_message: str | None = None
        status = FolderStatus.PENDING

        if actual_files is None:
            missing_current = [
                image.current_relpath
                for image in images
                if image.current_relpath and not (self.region_root / image.current_relpath).exists()
            ]
        else:
            missing_current = [
                image.current_relpath
                for image in images
                if image.current_relpath and image.current_relpath not in actual_files
            ]
        if missing_current:
            error_message = "Tracked files are missing or were moved outside camreview."
            status = FolderStatus.ERROR
        elif not source_folder_exists:
            done = all(
                (
                    image.review_label == ReviewLabel.BLANK
                    and image.current_relpath is None
                )
                or (
                    image.review_label == ReviewLabel.ANIMAL
                    and image.current_relpath == f"{ANIMAL_DIRNAME}/{image.filename}"
                )
                or (
                    image.review_label == ReviewLabel.SITE_PHOTO
                    and image.current_relpath == f"{SITE_PHOTO_DIRNAME}/{image.filename}"
                )
                for image in images
            )
            if done:
                status = FolderStatus.DONE
            else:
                status = FolderStatus.ERROR
                error_message = "Source folder is missing before cleanup completed."
        else:
            committed_layout = all(
                (
                    image.review_label == ReviewLabel.BLANK
                    and image.current_relpath == image.original_relpath
                )
                or (
                    image.review_label == ReviewLabel.ANIMAL
                    and image.current_relpath == f"{ANIMAL_DIRNAME}/{image.filename}"
                )
                or (
                    image.review_label == ReviewLabel.SITE_PHOTO
                    and image.current_relpath == f"{SITE_PHOTO_DIRNAME}/{image.filename}"
                )
                for image in images
            )
            if committed_layout and reviewed_images == total_images and site_photo_count == 1:
                status = FolderStatus.COMMITTED
            elif reviewed_images == total_images and site_photo_count == 1:
                status = FolderStatus.READY_TO_COMMIT
            elif reviewed_images > 0:
                status = FolderStatus.IN_PROGRESS
            else:
                status = FolderStatus.PENDING

            if any(image.current_relpath is None for image in images):
                status = FolderStatus.ERROR
                error_message = "Some images were trashed while the source folder still exists."

        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE folders
                SET status = ?,
                    total_images = ?,
                    reviewed_images = ?,
                    animal_count = ?,
                    blank_count = ?,
                    site_photo_count = ?,
                    error_message = ?
                WHERE folder_id = ?
                """,
                (
                    status.value,
                    total_images,
                    reviewed_images,
                    animal_count,
                    blank_count,
                    site_photo_count,
                    error_message,
                    folder_id,
                ),
            )
