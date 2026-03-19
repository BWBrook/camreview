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
    assert image.review_label == ReviewLabel.UNREVIEWED


def test_parse_warning_is_visible_for_bad_filename(qtbot, make_region) -> None:
    root = make_region({"12_2": ["bad_name.jpg"]})
    window = MainWindow(root)
    qtbot.addWidget(window)
    window.show()

    assert "could not be parsed" in window.parse_warning_label.text().lower()
