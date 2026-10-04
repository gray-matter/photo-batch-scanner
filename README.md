# Mass Scanner

A local FastAPI web app for digitizing batches of printed photos with an eSCL/AirScan network scanner. Review and adjust the detected photo boundaries before extracting JPEGs, then rotate, date, and geotag the photos from a browser.

## Requirements

- Python 3.11 or newer (`.python-version` selects 3.11).
- uv for Python dependency management.
- `exiftool` on `PATH` for reading and writing photo metadata.
- An eSCL/AirScan scanner reachable through local-network mDNS and HTTP for the scan workflow.
- Internet access for Leaflet assets, OpenStreetMap tiles, and Nominatim address search.
- Development only: Node.js for JavaScript syntax checks; Google Chrome or Chromium for the gallery DOM tests. The Python tests use standard-library `unittest`.

## Setup

From the repository root:

```bash
uv sync --locked
```

## Run

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

Open [localhost:8000](http://localhost:8000) on the server computer. For another device on the same network, use `http://<computer-ip>:8000` for the main app or `http://<computer-ip>:8000/scan-control` for scan controls only. Keep the main app open to review scans started from the control page. Allow incoming connections on port 8000 if the firewall blocks access.

The server has no authentication and serves the scan files directly. Run it on a trusted network. Pending reviews are held in memory and disappear on restart, although saved raw files remain on disk.

1. Place prints on the scanner bed with gaps between them, then click **Scan now**. The app discovers a scanner, saves a 600 dpi JPEG in `scans/raw/`, and proposes photo boundaries.
2. In the review dialog, drag corners or whole selections, draw new boxes, or remove unwanted selections. Optionally set **Photos on this scan** (1–20) and click **Detect again**; the browser remembers the count. Check the result and adjust any remaining mismatches manually.
3. Click **Create photos** to write JPEGs to `scans/cropped/`. Face detection attempts to orient each crop upright; use the gallery controls to adjust rotation. **Discard scan** deletes the raw scan instead. Successful extraction retains the raw scan.
4. Select photos, choose a map location and/or date, and click **Apply & tag** to write metadata. The dialog also supports removing location or date tags. Tagging leaves photos in their current folder.
5. Click **Mark tagged done** to move pending photos with both location and date tags into `scans/done/`, or mark an individual photo done. Enable **Show done** to view completed photos and restore them to the pending gallery.

To auto-crop an existing TIFF/JPEG without the review dialog:

```bash
uv run python crop.py /path/to/scan.tiff
```

The CLI writes JPEGs to `photos_decoupees/` beside the input file. Move those JPEGs into `scans/cropped/` to manage them in the gallery; refresh the page after copying them.

## Layout

```text
mass-scanner/                      # Local scanner app and photo-processing tools
├── app.py                         # FastAPI entrypoint, scan/review, gallery and tagging routes
├── scan_jobs.py                   # Scan coordinator, hardware lock, background detection and review queue
├── photo_store.py                 # Scan/photo directories, safe lookup, listing, moves and metadata-safe rotation
├── photo_processing.py            # Shared API/CLI crop naming, JPEG staging and publication
├── escl.py                        # mDNS discovery and eSCL HTTP scan jobs; also a standalone scan CLI
├── crop.py                        # Photo detection, perspective extraction, face-based rotation and crop CLI
├── exiftags.py                    # exiftool subprocess wrapper for metadata status and edits
├── static/                        # Browser UI served directly by FastAPI
│   ├── index.html                 # Gallery, scan review and tagging dialogs
│   ├── app.js                     # Gallery state, boundary editor, Leaflet map and API calls
│   ├── scan-control.html          # Scan-only interface for a second device
│   ├── scan-control.js            # Remote scan trigger and progress polling
│   └── style.css                  # Shared layout and light/dark styling
├── data/                          # Bundled face-detection model
│   └── face_detection_yunet.onnx   # OpenCV YuNet model used to estimate upright orientation
├── tests/                         # Geometry, metadata, write-failure, scan lifecycle and gallery DOM tests
├── pyproject.toml                 # Project metadata and Python dependency requirements
└── uv.lock                        # Locked Python dependency versions
```

## Additional docs

- [AGENTS.md](AGENTS.md) — development commands, conventions, and agent gotchas.
