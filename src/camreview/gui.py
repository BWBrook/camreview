from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStatusBar, QVBoxLayout, QWidget

from .controller import ProjectController
from .models import FolderRecord, FolderStatus, ImageRecord, ReviewLabel


STATUS_COLORS = {
    FolderStatus.PENDING: QColor("#6b7280"),
    FolderStatus.IN_PROGRESS: QColor("#0f766e"),
    FolderStatus.READY_TO_COMMIT: QColor("#1d4ed8"),
    FolderStatus.COMMITTED: QColor("#7c3aed"),
    FolderStatus.DONE: QColor("#4b5563"),
    FolderStatus.ERROR: QColor("#b91c1c"),
}


class ImageViewer(QScrollArea):
    zoomChanged = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        self._label = QLabel(alignment=Qt.AlignCenter)
        self._label.setMinimumSize(240, 180)
        self.setWidget(self._label)
        self._pixmap: QPixmap | None = None
        self._fit_mode = True
        self._scale_factor = 1.0

    def clear_image(self) -> None:
        self._pixmap = None
        self._label.clear()
        self.zoomChanged.emit(1.0)

    def load_path(self, path: Path) -> None:
        with Image.open(path) as image:
            prepared = ImageOps.exif_transpose(image).convert("RGBA")
            qt_image = ImageQt(prepared)
            self._pixmap = QPixmap.fromImage(qt_image)
        self.fit_to_window()

    def fit_to_window(self) -> None:
        self._fit_mode = True
        self._scale_factor = 1.0
        self._refresh_pixmap()

    def actual_size(self) -> None:
        self._fit_mode = False
        self._scale_factor = 1.0
        self._refresh_pixmap()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode:
            self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._pixmap is None:
            self._label.clear()
            return
        if self._fit_mode:
            size = self.viewport().size()
            if size.width() <= 0 or size.height() <= 0:
                return
            scaled = self._pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._label.setPixmap(scaled)
            if self._pixmap.width():
                self._scale_factor = scaled.width() / self._pixmap.width()
        else:
            scaled = self._pixmap.scaled(
                int(self._pixmap.width() * self._scale_factor),
                int(self._pixmap.height() * self._scale_factor),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._label.setPixmap(scaled)
        self._label.adjustSize()
        self.zoomChanged.emit(self._scale_factor)


class MainWindow(QMainWindow):
    def __init__(self, region_root: Path) -> None:
        super().__init__()
        self.controller = ProjectController(region_root)
        self.setWindowTitle(f"camreview — {self.controller.region_root}")
        self.resize(1750, 1040)

        self.current_folder_name: str | None = None
        self.current_images: list[ImageRecord] = []
        self.current_index = 0
        self._loading_note = False
        self._loaded_folder_name: str | None = None
        self._thumbnail_cache: dict[str, QIcon] = {}

        self._build_ui()
        self._build_menu()
        self._build_shortcuts()
        self.refresh_folders()
        self.statusBar().showMessage("Ready")

    def _build_ui(self) -> None:
        self.folder_list = QListWidget()
        self.folder_list.currentItemChanged.connect(self._on_folder_selected)

        self.viewer = ImageViewer()
        self.viewer.zoomChanged.connect(self._on_zoom_changed)

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setViewMode(QListWidget.IconMode)
        self.thumbnail_list.setFlow(QListWidget.LeftToRight)
        self.thumbnail_list.setResizeMode(QListWidget.Adjust)
        self.thumbnail_list.setWrapping(False)
        self.thumbnail_list.setIconSize(QSize(110, 80))
        self.thumbnail_list.setMaximumHeight(140)
        self.thumbnail_list.currentRowChanged.connect(self._on_thumbnail_selected)

        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.addWidget(self.viewer, stretch=1)
        center_layout.addWidget(self.thumbnail_list, stretch=0)

        self.folder_status_label = QLabel()
        self.folder_status_label.setWordWrap(True)
        self.timestamp_label = QLabel("Timestamp: —")
        self.path_label = QLabel("Path: —")
        self.path_label.setWordWrap(True)
        self.parse_warning_label = QLabel()
        self.parse_warning_label.setWordWrap(True)
        self.image_position_label = QLabel("Image: —")

        self.blank_button = QPushButton("Reset to Blank [B]")
        self.blank_button.clicked.connect(lambda: self.apply_label(ReviewLabel.BLANK))
        self.animal_button = QPushButton("Animal [A]")
        self.animal_button.clicked.connect(lambda: self.apply_label(ReviewLabel.ANIMAL))
        self.site_photo_button = QPushButton("Site Photo [S]")
        self.site_photo_button.clicked.connect(lambda: self.apply_label(ReviewLabel.SITE_PHOTO))

        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText("Optional note or species hint")
        self.note_edit.textChanged.connect(self._save_note)

        self.commit_button = QPushButton("Commit Folder")
        self.commit_button.clicked.connect(self.commit_folder)
        self.cleanup_button = QPushButton("Trash Residual Blanks")
        self.cleanup_button.clicked.connect(self.cleanup_folder)
        self.rescan_button = QPushButton("Rescan Root")
        self.rescan_button.clicked.connect(self.rescan_root)
        self.undo_button = QPushButton("Undo [Cmd+Z]")
        self.undo_button.clicked.connect(self.undo_last)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(self.folder_status_label)
        right_layout.addWidget(self.image_position_label)
        right_layout.addWidget(self.timestamp_label)
        right_layout.addWidget(self.path_label)
        right_layout.addWidget(self.parse_warning_label)
        right_layout.addWidget(self.blank_button)
        right_layout.addWidget(self.animal_button)
        right_layout.addWidget(self.site_photo_button)
        right_layout.addWidget(QLabel("Notes"))
        right_layout.addWidget(self.note_edit, stretch=1)
        right_layout.addWidget(self.undo_button)
        right_layout.addWidget(self.commit_button)
        right_layout.addWidget(self.cleanup_button)
        right_layout.addWidget(self.rescan_button)
        right_layout.addStretch(1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.folder_list)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([220, 1200, 290])

        self.setCentralWidget(splitter)
        self.setStatusBar(QStatusBar())

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        rescan_action = QAction("Rescan Root", self)
        rescan_action.triggered.connect(self.rescan_root)
        file_menu.addAction(rescan_action)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _build_shortcuts(self) -> None:
        self._shortcuts = [
            QShortcut(QKeySequence(Qt.Key_Right), self, activated=self.next_image),
            QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.next_image),
            QShortcut(QKeySequence(Qt.Key_Left), self, activated=self.previous_image),
            QShortcut(QKeySequence("A"), self, activated=lambda: self.apply_label(ReviewLabel.ANIMAL)),
            QShortcut(QKeySequence("B"), self, activated=lambda: self.apply_label(ReviewLabel.BLANK)),
            QShortcut(QKeySequence("S"), self, activated=lambda: self.apply_label(ReviewLabel.SITE_PHOTO)),
            QShortcut(QKeySequence("0"), self, activated=lambda: self.apply_label(ReviewLabel.BLANK, auto_advance=False)),
            QShortcut(QKeySequence.Undo, self, activated=self.undo_last),
            QShortcut(QKeySequence("F"), self, activated=self.viewer.fit_to_window),
            QShortcut(QKeySequence("1"), self, activated=self.viewer.actual_size),
            QShortcut(QKeySequence("Meta+Return"), self, activated=self.commit_folder),
            QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.commit_folder),
        ]

    def refresh_folders(self, preferred_folder: str | None = None) -> None:
        folders = self.controller.folders()
        self.folder_list.blockSignals(True)
        self.folder_list.clear()
        for folder in folders:
            item = QListWidgetItem(self._folder_text(folder))
            item.setData(Qt.UserRole, folder.folder_name)
            item.setForeground(STATUS_COLORS[folder.status])
            self.folder_list.addItem(item)
        self.folder_list.blockSignals(False)

        target = preferred_folder or self.current_folder_name
        if target:
            for row in range(self.folder_list.count()):
                item = self.folder_list.item(row)
                if item.data(Qt.UserRole) == target:
                    self.folder_list.setCurrentRow(row)
                    return
        for row in range(self.folder_list.count()):
            item = self.folder_list.item(row)
            folder_name = item.data(Qt.UserRole)
            folder = next((entry for entry in folders if entry.folder_name == folder_name), None)
            if folder and folder.status != FolderStatus.DONE:
                self.folder_list.setCurrentRow(row)
                return
        if self.folder_list.count():
            self.folder_list.setCurrentRow(0)

    def _folder_text(self, folder: FolderRecord) -> str:
        return f"{folder.folder_name} [{folder.status.value}] {folder.reviewed_images}/{folder.total_images}"

    def _on_folder_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        if current is None:
            return
        folder_name = current.data(Qt.UserRole)
        self.load_folder(folder_name)

    def load_folder(self, folder_name: str, *, target_index: int | None = None, target_image_id: int | None = None) -> None:
        self.current_folder_name = folder_name
        self.current_images = self.controller.open_folder(folder_name)
        if self._loaded_folder_name != folder_name:
            self._thumbnail_cache.clear()
            self.thumbnail_list.blockSignals(True)
            self.thumbnail_list.clear()
            for image in self.current_images:
                item = QListWidgetItem(image.filename)
                item.setData(Qt.UserRole, image.image_id)
                if image.current_relpath:
                    item.setIcon(self._thumbnail_icon(self.controller.region_root / image.current_relpath))
                self.thumbnail_list.addItem(item)
            self.thumbnail_list.blockSignals(False)
            self._loaded_folder_name = folder_name

        if not self.current_images:
            self.current_index = 0
            self._refresh_current_image()
            return

        if target_image_id is not None:
            for index, image in enumerate(self.current_images):
                if image.image_id == target_image_id:
                    self.current_index = index
                    break
            else:
                self.current_index = 0
        elif target_index is not None:
            self.current_index = max(0, min(target_index, len(self.current_images) - 1))
        else:
            self.current_index = next(
                (index for index, image in enumerate(self.current_images) if image.reviewed_at is None),
                0,
            )
        self._refresh_current_image()
        if not self.note_edit.hasFocus():
            self.viewer.setFocus()

    def _thumbnail_icon(self, path: Path) -> QIcon:
        cache_key = str(path)
        cached = self._thumbnail_cache.get(cache_key)
        if cached is not None:
            return cached
        with Image.open(path) as image:
            prepared = ImageOps.exif_transpose(image).convert("RGB")
            prepared.thumbnail((110, 80))
            icon = QPixmap.fromImage(ImageQt(prepared))
        qicon = QIcon(icon)
        self._thumbnail_cache[cache_key] = qicon
        return qicon

    def _folder_list_item(self, folder_name: str) -> QListWidgetItem | None:
        for row in range(self.folder_list.count()):
            item = self.folder_list.item(row)
            if item.data(Qt.UserRole) == folder_name:
                return item
        return None

    def _update_folder_list_item(self, folder_name: str) -> None:
        item = self._folder_list_item(folder_name)
        if item is None:
            return
        folder = self.controller.store.get_folder(folder_name)
        item.setText(self._folder_text(folder))
        item.setForeground(STATUS_COLORS[folder.status])

    def _refresh_current_folder_after_edit(self, *, target_index: int | None = None) -> None:
        if self.current_folder_name is None:
            return
        self.current_images = self.controller.images(self.current_folder_name)
        if self.current_images:
            if target_index is None:
                self.current_index = min(self.current_index, len(self.current_images) - 1)
            else:
                self.current_index = max(0, min(target_index, len(self.current_images) - 1))
        else:
            self.current_index = 0
        self._update_folder_list_item(self.current_folder_name)
        self._refresh_current_image()

    def _refresh_current_image(self) -> None:
        folder = self.controller.store.get_folder(self.current_folder_name) if self.current_folder_name else None
        if folder is None:
            return

        self.folder_status_label.setText(
            f"Folder: {folder.folder_name}\n"
            f"Status: {folder.status.value}\n"
            f"Reviewed: {folder.reviewed_images}/{folder.total_images}\n"
            f"Tagged: {folder.animal_count} animal, {folder.site_photo_count} site photo"
        )
        self.folder_status_label.setStyleSheet(
            f"color: {STATUS_COLORS[folder.status].name()}; font-weight: 600;"
        )
        readonly = folder.status in {FolderStatus.COMMITTED, FolderStatus.DONE}
        self.commit_button.setEnabled(folder.status == FolderStatus.READY_TO_COMMIT)
        self.cleanup_button.setEnabled(folder.status == FolderStatus.COMMITTED)
        self.blank_button.setEnabled(not readonly)
        self.animal_button.setEnabled(not readonly)
        self.site_photo_button.setEnabled(not readonly)
        self.note_edit.setReadOnly(readonly)
        self.undo_button.setEnabled(not readonly)

        if not self.current_images:
            self.viewer.clear_image()
            self.timestamp_label.setText("Timestamp: —")
            self.path_label.setText("Path: —")
            self.parse_warning_label.setText("")
            self.image_position_label.setText("Image: 0/0")
            self.note_edit.setPlainText("")
            return

        image = self.current_images[self.current_index]
        if image.reviewed_at is None and folder.status not in {FolderStatus.COMMITTED, FolderStatus.DONE, FolderStatus.ERROR}:
            self.controller.mark_reviewed(image.image_id)
            folder = self.controller.store.get_folder(self.current_folder_name)
            self.current_images = self.controller.images(self.current_folder_name)
            image = self.current_images[self.current_index]
        self.thumbnail_list.blockSignals(True)
        self.thumbnail_list.setCurrentRow(self.current_index)
        self.thumbnail_list.scrollToItem(self.thumbnail_list.item(self.current_index))
        self.thumbnail_list.blockSignals(False)

        resolved = self._resolved_path(image)
        if resolved and resolved.exists():
            self.viewer.load_path(resolved)
        else:
            self.viewer.clear_image()
        timestamp = image.parsed_timestamp.isoformat(sep=" ") if image.parsed_timestamp else "mtime fallback"
        self.timestamp_label.setText(f"Timestamp: {timestamp}")
        current_path_text = image.current_relpath or "(trashed)"
        self.path_label.setText(f"Path: {current_path_text}")
        self.parse_warning_label.setText(image.parse_warning or "")
        self.parse_warning_label.setStyleSheet("color: #b45309; font-weight: 600;")
        self.image_position_label.setText(
            f"Image: {self.current_index + 1}/{len(self.current_images)} — {image.review_label.value}"
        )

        self._loading_note = True
        self.note_edit.setPlainText(image.note)
        self._loading_note = False
        self.statusBar().showMessage(f"{folder.folder_name} — {image.filename}")

    def _resolved_path(self, image: ImageRecord) -> Path | None:
        if image.current_relpath is None:
            return None
        return self.controller.region_root / image.current_relpath

    def _on_thumbnail_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.current_images):
            return
        self.current_index = row
        self._refresh_current_image()

    def next_image(self) -> None:
        if self.note_edit.hasFocus() or not self.current_images:
            return
        self.current_index = min(self.current_index + 1, len(self.current_images) - 1)
        self._refresh_current_image()

    def previous_image(self) -> None:
        if self.note_edit.hasFocus() or not self.current_images:
            return
        self.current_index = max(self.current_index - 1, 0)
        self._refresh_current_image()

    def apply_label(self, label: ReviewLabel, *, auto_advance: bool = True) -> None:
        if self.note_edit.hasFocus() or not self.current_images or self.current_folder_name is None:
            return
        folder = self.controller.store.get_folder(self.current_folder_name)
        if folder.status in {FolderStatus.COMMITTED, FolderStatus.DONE}:
            return
        image = self.current_images[self.current_index]
        try:
            self.controller.label_image(image.image_id, label)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        next_index = self.current_index + 1 if auto_advance and self.current_index < len(self.current_images) - 1 else self.current_index
        self._refresh_current_folder_after_edit(target_index=next_index)

    def undo_last(self) -> None:
        if self.current_folder_name is None:
            return
        if not self.controller.undo_last(self.current_folder_name):
            self.statusBar().showMessage("Nothing to undo")
            return
        self._refresh_current_folder_after_edit(target_index=self.current_index)

    def _save_note(self) -> None:
        if self._loading_note or not self.current_images:
            return
        image = self.current_images[self.current_index]
        self.controller.set_note(image.image_id, self.note_edit.toPlainText())

    def rescan_root(self) -> None:
        preferred_folder = self.current_folder_name
        self.controller.rescan()
        self.refresh_folders(preferred_folder=preferred_folder)

    def commit_folder(self) -> None:
        if self.current_folder_name is None:
            return
        try:
            plan = self.controller.commit_preview(self.current_folder_name)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        lines = [
            f"Commit folder {plan.folder_name}?",
            "",
            f"Move {plan.animal_count} animal image(s) into ANIMAL/",
            f"Move {plan.site_photo_count} site photo into SITE_PHOTOS/",
        ]
        for move in plan.moves[:8]:
            lines.append(f"{move.source_relpath} → {move.destination_relpath}")
        if len(plan.moves) > 8:
            lines.append(f"... and {len(plan.moves) - 8} more")
        reply = QMessageBox.question(self, "Commit Folder", "\n".join(lines))
        if reply != QMessageBox.Yes:
            return
        try:
            self.controller.commit_folder(self.current_folder_name)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.refresh_folders(preferred_folder=self.current_folder_name)
        self.load_folder(self.current_folder_name, target_index=self.current_index)

    def cleanup_folder(self) -> None:
        if self.current_folder_name is None:
            return
        scope = self._choose_cleanup_scope()
        if scope is None:
            return
        if scope == "selected":
            self._cleanup_selected_folder()
            return
        self._cleanup_all_committed()

    def _choose_cleanup_scope(self) -> str | None:
        committed_count = len(self.controller.cleanup_all_preview())
        if committed_count <= 1:
            return "selected"
        options = ["Selected Folder", f"All Committed ({committed_count})"]
        selection, ok = QInputDialog.getItem(
            self,
            "Cleanup Scope",
            "Trash residual blanks from the current committed folder, or from all committed folders?",
            options,
            0,
            False,
        )
        if not ok:
            return None
        if selection == options[0]:
            return "selected"
        if selection == options[1]:
            return "all"
        return None

    def _cleanup_selected_folder(self) -> None:
        try:
            plan = self.controller.cleanup_preview(self.current_folder_name)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        lines = [
            f"Trash {len(plan.file_relpaths)} residual blank file(s) from {plan.folder_name}?",
            "",
            "This will send the remaining blank images to the OS Trash and then trash the empty folder.",
        ]
        for relpath in plan.file_relpaths[:8]:
            lines.append(relpath)
        if len(plan.file_relpaths) > 8:
            lines.append(f"... and {len(plan.file_relpaths) - 8} more")
        reply = QMessageBox.warning(self, "Cleanup Folder", "\n".join(lines), QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.controller.cleanup_folder(self.current_folder_name)
        except Exception as exc:
            self._show_error(str(exc))
            return
        current = self.current_folder_name
        self.refresh_folders()
        if self.current_folder_name == current:
            self.load_folder(current, target_index=0)

    def _cleanup_all_committed(self) -> None:
        plans = self.controller.cleanup_all_preview()
        total_files = sum(len(plan.file_relpaths) for plan in plans)
        lines = [
            f"Trash residual blanks from all {len(plans)} committed folder(s)?",
            "",
            f"This will send {total_files} blank image(s) to the OS Trash and then trash each empty folder.",
        ]
        for plan in plans[:10]:
            lines.append(f"{plan.folder_name}: {len(plan.file_relpaths)} file(s)")
        if len(plans) > 10:
            lines.append(f"... and {len(plans) - 10} more folder(s)")
        reply = QMessageBox.warning(self, "Cleanup All Committed", "\n".join(lines), QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.controller.cleanup_all_committed()
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.refresh_folders()

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 7000)
        QMessageBox.critical(self, "camreview", message)

    def _on_zoom_changed(self, scale_factor: float) -> None:
        percent = int(scale_factor * 100)
        self.statusBar().showMessage(f"Zoom: {percent}%")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.matches(QKeySequence.Undo):
            self.undo_last()
            event.accept()
            return
        if self.note_edit.hasFocus():
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Right, Qt.Key_Space):
            self.next_image()
            event.accept()
            return
        if event.key() == Qt.Key_Left:
            self.previous_image()
            event.accept()
            return
        if event.key() == Qt.Key_A:
            self.apply_label(ReviewLabel.ANIMAL)
            event.accept()
            return
        if event.key() == Qt.Key_B:
            self.apply_label(ReviewLabel.BLANK)
            event.accept()
            return
        if event.key() == Qt.Key_S:
            self.apply_label(ReviewLabel.SITE_PHOTO)
            event.accept()
            return
        if event.key() == Qt.Key_0:
            self.apply_label(ReviewLabel.BLANK, auto_advance=False)
            event.accept()
            return
        if event.key() == Qt.Key_F:
            self.viewer.fit_to_window()
            event.accept()
            return
        if event.key() == Qt.Key_1:
            self.viewer.actual_size()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & (Qt.MetaModifier | Qt.ControlModifier):
            self.commit_folder()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.controller.close()
        super().closeEvent(event)


def launch_app(region_root: Path) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(region_root)
    window.show()
    app.exec()
