import pytest

from camreview.models import FolderStatus, ReviewLabel
from camreview.store import ProjectStore


def test_initial_scan_creates_output_dirs_and_registers_images(make_region) -> None:
    root = make_region(
        {
            "12_2": [
                "12_2_01-01-2020_09-00-00.jpg",
                "12_2_01-01-2020_10-00-00.jpg",
                "bad_name.jpg",
            ]
        }
    )
    store = ProjectStore(root)
    assert (root / "ANIMAL").exists()
    assert (root / "SITE_PHOTOS").exists()
    images = store.get_images("12_2")
    assert [image.filename for image in images[:2]] == [
        "12_2_01-01-2020_09-00-00.jpg",
        "12_2_01-01-2020_10-00-00.jpg",
    ]
    assert images[-1].parse_warning is not None
    store.close()


def test_site_photo_reassignment_demotes_previous_exemplar(make_region) -> None:
    root = make_region(
        {
            "12_2": [
                "12_2_01-01-2020_09-00-00.jpg",
                "12_2_01-01-2020_10-00-00.jpg",
            ]
        }
    )
    store = ProjectStore(root)
    images = store.get_images("12_2")
    store.set_label(images[0].image_id, ReviewLabel.SITE_PHOTO)
    store.set_label(images[1].image_id, ReviewLabel.SITE_PHOTO)
    updated = store.get_images("12_2")
    assert updated[0].review_label == ReviewLabel.BLANK
    assert updated[1].review_label == ReviewLabel.SITE_PHOTO
    store.close()


def test_commit_and_cleanup_flow(make_region) -> None:
    root = make_region(
        {
            "12_2": [
                "12_2_01-01-2020_09-00-00.jpg",
                "12_2_01-01-2020_10-00-00.jpg",
                "12_2_01-01-2020_11-00-00.jpg",
            ]
        }
    )
    store = ProjectStore(root)
    images = store.get_images("12_2")
    store.set_label(images[0].image_id, ReviewLabel.ANIMAL)
    store.set_label(images[1].image_id, ReviewLabel.BLANK)
    store.set_label(images[2].image_id, ReviewLabel.SITE_PHOTO)
    assert store.get_folder("12_2").status == FolderStatus.READY_TO_COMMIT

    plan = store.commit_folder("12_2")
    assert plan.animal_count == 1
    assert plan.site_photo_count == 1
    assert (root / "ANIMAL" / "12_2_01-01-2020_09-00-00.jpg").exists()
    assert (root / "SITE_PHOTOS" / "12_2_01-01-2020_11-00-00.jpg").exists()
    assert store.get_folder("12_2").status == FolderStatus.COMMITTED

    store.cleanup_folder("12_2")
    assert not (root / "12_2").exists()
    assert store.get_folder("12_2").status == FolderStatus.DONE
    store.close()


def test_commit_collision_marks_folder_error(make_region) -> None:
    root = make_region({"12_2": ["12_2_01-01-2020_09-00-00.jpg", "12_2_01-01-2020_10-00-00.jpg"]})
    (root / "ANIMAL").mkdir()
    existing = root / "ANIMAL" / "12_2_01-01-2020_09-00-00.jpg"
    existing.write_bytes(b"collision")

    store = ProjectStore(root)
    images = store.get_images("12_2")
    store.set_label(images[0].image_id, ReviewLabel.ANIMAL)
    store.set_label(images[1].image_id, ReviewLabel.SITE_PHOTO)
    with pytest.raises(FileExistsError):
        store.commit_folder("12_2")
    assert store.get_folder("12_2").status == FolderStatus.ERROR
    store.close()


def test_rescan_flags_external_move(make_region) -> None:
    root = make_region({"12_2": ["12_2_01-01-2020_09-00-00.jpg", "12_2_01-01-2020_10-00-00.jpg"]})
    store = ProjectStore(root)
    image_path = root / "12_2" / "12_2_01-01-2020_09-00-00.jpg"
    moved = root / "12_2" / "moved.jpg"
    image_path.rename(moved)
    store.reconcile()
    assert store.get_folder("12_2").status == FolderStatus.ERROR
    store.close()
