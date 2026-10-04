# Mass Scanner

A local web app for digitizing batches of printed photos: it drives a Canon TS7750i (or any eSCL/AirScan-capable scanner) directly over the network, proposes a split of a multi-photo flatbed scan for you to validate and adjust, lets you fix orientation, and geotags/dates the results in bulk — all from one page, backed by `exiftool` and OpenStreetMap.

## Requirements

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) for dependency management
- [exiftool](https://exiftool.org/) (`brew install exiftool`)
- A scanner reachable on the local network over eSCL/AirScan (most network-connected Canon, Epson, and HP scanners from the last decade support this)

## Setup

```bash
uv sync
```

## Run

```bash
uv run uvicorn app:app --host 0.0.0.0
```

On the computer running the app, open http://localhost:8000. To let another device on the same network start scans, open `http://<computer-ip>:8000/scan-control` on that device. Keep the main app open at `http://<computer-ip>:8000`; when a scan is ready, its split-review dialog opens there automatically.

The server must stay running on the computer that can reach the scanner. Allow incoming connections to port 8000 in that computer's firewall if prompted. The app currently has no sign-in or access code, so only use this on a trusted network: devices that can reach it can use its scan and photo-management endpoints.

Workflow:

1. Place several prints on the scanner bed (a small gap between them helps detection, but touching prints can still be split apart in the next step).
2. Click **Scan now** in the main app or on the `/scan-control` page. The app discovers the scanner over mDNS, triggers a 600dpi scan, and detects a candidate photo on the bed for each print — no files are created yet.
3. A review modal opens with one four-corner outline per detected photo. Drag a corner to adjust the selection, drag inside it to move it, or draw a new box over an empty area. Remove any selection you don't want, then click **Create photos** — only then are the crops written to `scans/cropped/`. **Discard scan** abandons the whole scan instead.
   Set **Photos on this scan** (1–20, or leave blank for automatic count) and click **Detect again** to replace the current selections using the saved raw scan. Your browser remembers the count and applies it to subsequent scan reviews. Detection tries nearby thresholds and clear local gaps to approach the requested count; unresolved mismatches remain visible for manual adjustment.
4. Each created photo is auto-rotated upright if a face was detected clearly enough to tell which way is up (otherwise, rotate it manually from the gallery).
5. Select the photos that share a date and place, search the address on the map, pick a date, and click **Apply & tag**. This writes GPS and date EXIF tags via `exiftool` and moves the files to `scans/done/`.

You can also skip steps 2–3 and drop TIFF/JPEG scans straight into `scans/raw/` from another scanning app; run `uv run python crop.py scans/raw/<file>` to auto-crop one straight to `scans/cropped/` without the review step.

## Layout

```
app.py              # FastAPI app: scan trigger, review/extract, gallery, rotate, geocode proxy, EXIF tagging
escl.py             # eSCL (AirScan) client: mDNS discovery + HTTP scan job control, no vendor driver needed
crop.py             # Multi-photo detection (candidate quads) + perspective extraction + auto-rotate by face detection (also runnable standalone)
exiftags.py         # exiftool wrapper for writing GPS position + date tags
static/
  index.html        # Single-page UI: gallery, review-split modal, rotate controls, date + map form
  app.js            # Client logic: gallery state, canvas-based quad review/split tool, Leaflet map, geocoding
  scan-control.html # Scan-only page for a second device on the local network
  scan-control.js   # Scan trigger and progress/status display for the control page
  style.css         # Design tokens (light/dark) and layout
scans/              # Runtime data, gitignored: raw/ (whole scans, pending review or archived), cropped/ (pending tag), done/ (tagged)
data/               # face_detection_yunet.onnx: OpenCV's YuNet face detector, used to guess upright orientation
pyproject.toml      # Dependencies (fastapi, httpx, zeroconf, opencv-python, pillow, ...)
```

## Tests

Run the standard-library test suite with:

```bash
uv run python -m unittest discover -s tests -v
```

The gallery filename safety tests also run `static/app.js` in headless Chrome or Chromium to inspect the rendered DOM. They skip when neither browser is installed.
