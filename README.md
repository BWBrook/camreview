# camreview

Camreview is a PySide6 desktop app for first-pass review of wildlife camera images.

It is designed for a narrow, safe workflow:

- review one `site_camera` folder at a time
- label each image as `blank`, `animal`, or `site_photo`
- move `animal` and `site_photo` files only after an explicit commit
- move residual blanks to the OS Trash only after a second explicit confirmation
- keep portable review state inside the region root under `.camreview/`

## Quickstart

```bash
cd /Users/bwbrook/git/camreview
uv sync --extra dev
uv run camreview review /Users/bwbrook/fppf
```

## Region Layout

Camreview expects a region root containing:

- top-level site folders named like `12_2`
- flat output folders `ANIMAL/` and `SITE_PHOTOS/`
- JPG images inside each site folder

Review state is written to `<region_root>/.camreview/`.

## Development

```bash
uv run pytest
uv run ruff check .
```

