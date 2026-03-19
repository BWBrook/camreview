from camreview.models import FileSnapshot
from camreview.parser import parse_capture_filename, sort_key_from_metadata


def test_parse_capture_filename_handles_single_digit_time_parts() -> None:
    parsed = parse_capture_filename("12_2_14-07-2020_7-59-53.jpg")
    assert parsed is not None
    assert parsed.year == 2020
    assert parsed.month == 7
    assert parsed.day == 14
    assert parsed.hour == 7
    assert parsed.minute == 59
    assert parsed.second == 53


def test_sort_key_prefers_parsed_datetime_over_mtime() -> None:
    valid = FileSnapshot(
        folder_name="12_2",
        relpath="12_2/12_2_01-01-2020_09-00-00.jpg",
        filename="12_2_01-01-2020_09-00-00.jpg",
        filesize=1,
        mtime=200.0,
        parsed_timestamp=parse_capture_filename("12_2_01-01-2020_09-00-00.jpg"),
        parse_warning=None,
    )
    invalid = FileSnapshot(
        folder_name="12_2",
        relpath="12_2/bad.jpg",
        filename="bad.jpg",
        filesize=1,
        mtime=100.0,
        parsed_timestamp=None,
        parse_warning="warning",
    )
    assert sort_key_from_metadata(valid) < sort_key_from_metadata(invalid)

