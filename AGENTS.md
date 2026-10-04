# Mass Scanner — agent guide

Setup and external tools: see README [Requirements](README.md#requirements) and [Setup](README.md#setup).

## Commands

```bash
uv run python -m unittest discover -s tests -v # Run Python regressions and browser DOM tests
uv run pyright # Check Python types in standard mode
node --check static/app.js # Check main browser script syntax
node --check static/scan-control.js # Check scan-control script syntax
for script in static/js/*.js; do node --check "$script" || exit; done # Check feature module syntax
```

Primary run workflow: same as README **[Run](README.md#run)**.

## Conventions

- Module layout: see README **[Layout](README.md#layout)**.
- Use `uv run` for Python commands; update `uv.lock` with uv when changing dependencies.
- Preserve one hardware scan at a time, background detection, and queued reviews that allow subsequent scans to start.
- Use temporary files and mocked scanner calls for verification; keep personal files in `scans/` out of fixtures and commits.

## Gotchas

- Keep `app.py` in one server process: `ScanCoordinator` owns process-local locks, status, and pending reviews. Importing `app.py` creates the scan directories.
- Keep review coordinates in full-resolution pixels after EXIF orientation correction by `crop.load_scan_image`; canvas display coordinates are scaled.
- Treat the `@unittest.expectedFailure` tests in `tests/` as known defects, not passing coverage. See [REFACTORING_PLAN.md](REFACTORING_PLAN.md); remove each decorator when its defect is fixed.
- Check skipped tests: metadata tests require `exiftool`; gallery DOM tests require Chrome or Chromium.
