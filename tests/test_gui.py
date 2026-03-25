from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from camreview.gui import MainWindow
from camreview.models import FolderStatus, ReviewLabel


def test_hotkey_labeling_auto_advances(qtbot, make_region) -> None:
    root = make_region(
        {
            "12_2": [
                "12_2_01-01-2020_09-00-00.jpg",
                "12_2_01-01-2020_10-00-00.jpg",
                "12_2_01-01-2020_11-00-00.jpg",
            ]
        }
    )
    window = MainWindow(root)
    qtbot.addWidget(window)
    window.show()

    assert window.current_index == 0
    first_image = window.controller.images("12_2")[0]
    assert first_image.reviewed_at is not None
    assert first_image.review_label == ReviewLabel.BLANK
    qtbot.keyClick(window, Qt.Key_A)
    assert window.current_index == 1
    images = window.controller.images("12_2")
    assert images[0].review_label == ReviewLabel.ANIMAL

    qtbot.keyClick(window, Qt.Key_B)
    qtbot.keyClick(window, Qt.Key_S)
    assert window.controller.store.get_folder("12_2").status == FolderStatus.READY_TO_COMMIT
    assert window.commit_button.isEnabled()


def test_undo_restores_previous_label(qtbot, make_region) -> None:
    root = make_region({"12_2": ["12_2_01-01-2020_09-00-00.jpg", "12_2_01-01-2020_10-00-00.jpg"]})
    window = MainWindow(root)
    qtbot.addWidget(window)
    window.show()

    qtbot.keyClick(window, Qt.Key_A)
    qtbot.keySequence(window, QKeySequence.Undo)
    image = window.controller.images("12_2")[0]
    assert image.review_label == ReviewLabel.BLANK


def test_parse_warning_is_visible_for_bad_filename(qtbot, make_region) -> None:
    root = make_region({"12_2": ["bad_name.jpg"]})
    window = MainWindow(root)
    qtbot.addWidget(window)
    window.show()

    assert "could not be parsed" in window.parse_warning_label.text().lower()


def test_cleanup_scope_chooser_can_select_all_committed(qtbot, make_region, monkeypatch) -> None:
    root = make_region(
        {
            "12_2": ["12_2_01-01-2020_09-00-00.jpg", "12_2_01-01-2020_10-00-00.jpg"],
            "13_1": ["13_1_01-01-2020_09-00-00.jpg", "13_1_01-01-2020_10-00-00.jpg"],
        }
    )
    window = MainWindow(root)
    qtbot.addWidget(window)
    window.show()

    for folder_name in ["12_2", "13_1"]:
        images = window.controller.images(folder_name)
        window.controller.mark_reviewed(images[0].image_id)
        window.controller.label_image(images[1].image_id, ReviewLabel.SITE_PHOTO)
        window.controller.commit_folder(folder_name)
    window.refresh_folders(preferred_folder="12_2")

    monkeypatch.setattr(
        "camreview.gui.QInputDialog.getItem",
        lambda *args, **kwargs: ("All Committed (2)", True),
    )
    called = {"all": False}

    def fake_cleanup_all() -> None:
        called["all"] = True

    monkeypatch.setattr(window, "_cleanup_all_committed", fake_cleanup_all)
    window.cleanup_folder()
    assert called["all"] is True
