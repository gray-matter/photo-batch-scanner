import logging
import shutil
import tempfile
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from crop import detect_photo_regions, extract_photo, load_scan_image, validate_quad_points, write_crop_outputs
from escl import discover_scanner, scan_to_file
from exiftags import apply_tags, read_tag_status
from scan_jobs import ScanCoordinator

BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / "scans" / "raw"
CROPPED_DIR = BASE_DIR / "scans" / "cropped"
DONE_DIR = BASE_DIR / "scans" / "done"
for d in (RAW_DIR, CROPPED_DIR, DONE_DIR):
    d.mkdir(parents=True, exist_ok=True)

NOMINATIM_HEADERS = {"User-Agent": "mass-scanner/0.1 (personal photo geotagging tool)"}

app = FastAPI()


class _ScanStatusAccessLogFilter(logging.Filter):
    def filter(self, record):
        args = record.args
        if (
            record.name == "uvicorn.access"
            and isinstance(args, tuple)
            and len(args) >= 5
            and args[1] == "GET"
            and args[2].split("?", 1)[0] == "/api/scan/status"
        ):
            if logging.getLogger(record.name).getEffectiveLevel() > logging.DEBUG:
                return False
            record.levelno = logging.DEBUG
            record.levelname = "DEBUG"
        return True


logging.getLogger("uvicorn.access").addFilter(_ScanStatusAccessLogFilter())

scan_coordinator = ScanCoordinator(
    discover=discover_scanner,
    transfer=scan_to_file,
    load_image=load_scan_image,
    detect=detect_photo_regions,
    raw_directory=RAW_DIR,
)


def safe_cropped_path(filename: str) -> Path:
    path = (CROPPED_DIR / filename).resolve()
    if path.parent != CROPPED_DIR.resolve() or not path.is_file():
        raise HTTPException(404, "Photo not found")
    return path


def safe_raw_path(filename: str) -> Path:
    path = (RAW_DIR / filename).resolve()
    if path.parent != RAW_DIR.resolve() or not path.is_file():
        raise HTTPException(404, "Scan not found")
    return path


def safe_done_path(filename: str) -> Path:
    path = (DONE_DIR / filename).resolve()
    if path.parent != DONE_DIR.resolve() or not path.is_file():
        raise HTTPException(404, "Photo not found")
    return path


def safe_tag_path(filename: str) -> Path:
    for directory in (CROPPED_DIR, DONE_DIR):
        path = (directory / filename).resolve()
        if path.parent == directory.resolve() and path.is_file():
            return path
    raise HTTPException(404, "Photo not found")


@app.post("/api/scan")
def api_scan():
    if not scan_coordinator.start():
        raise HTTPException(409, "A scan is already in progress")
    return {"started": True}


@app.get("/scan-control")
def scan_control_page():
    return FileResponse(BASE_DIR / "static" / "scan-control.html")


@app.get("/api/scan/status")
def api_scan_status() -> dict[str, Any]:
    return dict(scan_coordinator.status())


class DetectBody(BaseModel):
    expected_count: int | None = Field(default=None, ge=1, le=20, strict=True)


@app.post("/api/raw/{filename}/detect")
def api_detect(filename: str, body: DetectBody) -> dict[str, Any]:
    path = safe_raw_path(filename)
    try:
        return dict(scan_coordinator.redetect(path, filename=filename, expected_count=body.expected_count))
    except (OSError, ValueError, cv2.error) as error:
        raise HTTPException(422, f"Couldn't detect photos: {error}") from error


@app.get("/api/photos")
def api_photos():
    files = sorted(CROPPED_DIR.glob("*.jpg"), reverse=True)
    tag_status = read_tag_status(files)
    return {
        "photos": [
            {"filename": f.name, **tag_status.get(f.name, {"gps": False, "time": False})}
            for f in files
        ]
    }


@app.get("/api/photos/done")
def api_photos_done():
    files = sorted(DONE_DIR.glob("*.jpg"), reverse=True)
    tag_status = read_tag_status(files)
    return {
        "photos": [
            {"filename": f.name, **tag_status.get(f.name, {"gps": False, "time": False})}
            for f in files
        ]
    }


@app.get("/api/photos/{filename}")
def api_get_photo(filename: str):
    return FileResponse(safe_cropped_path(filename))


@app.delete("/api/photos/{filename}")
def api_delete_photo(filename: str):
    path = safe_cropped_path(filename)
    path.unlink()
    return {"ok": True}


@app.post("/api/photos/{filename}/done")
def api_mark_done(filename: str):
    path = safe_cropped_path(filename)
    dest = DONE_DIR / path.name
    shutil.move(str(path), str(dest))
    return {"ok": True}


@app.post("/api/photos/mark-tagged-done")
def api_mark_tagged_done():
    files = sorted(CROPPED_DIR.glob("*.jpg"), reverse=True)
    tag_status = read_tag_status(files)
    marked = [
        path for path in files
        if tag_status.get(path.name, {}).get("gps") and tag_status.get(path.name, {}).get("time")
    ]
    for path in marked:
        shutil.move(str(path), str(DONE_DIR / path.name))
    return {"photos": [path.name for path in marked]}


@app.delete("/api/photos/done/{filename}")
def api_delete_done_photo(filename: str):
    path = safe_done_path(filename)
    path.unlink()
    return {"ok": True}


@app.post("/api/photos/done/{filename}/restore")
def api_restore_done_photo(filename: str):
    path = safe_done_path(filename)
    dest = CROPPED_DIR / path.name
    shutil.move(str(path), str(dest))
    return {"ok": True}


class RotateBody(BaseModel):
    degrees: int  # 90, 180, or 270 (counter-clockwise)


@app.post("/api/photos/{filename}/rotate")
def api_rotate(filename: str, body: RotateBody) -> dict[str, bool]:
    if body.degrees not in (90, 180, 270):
        raise HTTPException(400, "degrees must be 90, 180, or 270")
    path = safe_cropped_path(filename)
    temporary_path: Path | None = None
    try:
        with Image.open(path) as source:
            with ImageOps.exif_transpose(source) as oriented:
                exif = oriented.getexif()
                exif[274] = 1
                with (
                    oriented.rotate(body.degrees, expand=True) as rotated,
                    tempfile.NamedTemporaryFile(
                        dir=path.parent, prefix=f".{path.stem}-", suffix=".jpg", delete=False
                    ) as temporary_file,
                ):
                    temporary_path = Path(temporary_file.name)
                    rotated.save(
                        temporary_file, format="JPEG", quality=92, exif=exif,
                        icc_profile=source.info.get("icc_profile"),
                    )
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return {"ok": True}


class ExtractBody(BaseModel):
    quads: list[list[list[float]]]  # each selection: polygon points in raw-image pixel coords


@app.post("/api/raw/{filename}/extract")
def api_extract(filename: str, body: ExtractBody):
    if not body.quads:
        raise HTTPException(400, "At least one selection is required")
    for i, quad in enumerate(body.quads, start=1):
        try:
            validate_quad_points(quad)
        except ValueError as exc:
            raise HTTPException(400, f"Selection {i}: {exc}") from exc
    path = safe_raw_path(filename)
    img = load_scan_image(path)
    stem = path.stem

    def outputs() -> Iterator[tuple[Path, Any]]:
        for i, quad in enumerate(body.quads, start=1):
            crop = extract_photo(img, quad)
            if crop is not None:
                yield CROPPED_DIR / f"{stem}_photo_{i:02d}.jpg", crop

    try:
        output_paths = write_crop_outputs(outputs(), 92)
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc
    scan_coordinator.remove_review(filename)
    return {"photos": [path.name for path in output_paths]}


@app.post("/api/raw/{filename}/discard")
def api_discard_raw(filename: str):
    path = safe_raw_path(filename)
    path.unlink()
    scan_coordinator.remove_review(filename)
    return {"ok": True}


@app.get("/api/geocode")
def api_geocode(q: str):
    if not q.strip():
        return {"results": []}
    resp = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": q, "format": "json", "limit": 5},
        headers=NOMINATIM_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    time.sleep(1)  # stay within Nominatim's 1 req/sec usage policy
    return {
        "results": [
            {"display_name": r["display_name"], "lat": float(r["lat"]), "lon": float(r["lon"])}
            for r in resp.json()
        ]
    }


class TagBody(BaseModel):
    filenames: list[str]
    lat: float | None = None
    lon: float | None = None
    date: str | None = None  # "YYYY-MM-DD" (day only) or "YYYY-MM-DDTHH:MM" (day + hour)
    clear_gps: bool = False
    clear_date: bool = False


# Time to write for a day-only date, chosen to avoid a timezone-aware reader
# rolling the date to the previous/next day.
DAY_ONLY_TIME = "12:00:00"


@app.post("/api/tag")
def api_tag(body: TagBody):
    if not body.filenames:
        raise HTTPException(400, "No files selected")
    if (body.lat is None) != (body.lon is None):
        raise HTTPException(400, "Both lat and lon are required together")
    if body.lat is not None and body.clear_gps:
        raise HTTPException(400, "Cannot set and remove GPS location at once")
    if body.date is not None and body.clear_date:
        raise HTTPException(400, "Cannot set and remove the date at once")
    if body.lat is None and body.date is None and not body.clear_gps and not body.clear_date:
        raise HTTPException(400, "Provide a GPS location, a date, or a removal, or a combination")

    paths = [safe_tag_path(f) for f in body.filenames]

    exif_date = None
    if body.date is not None:
        try:
            if len(body.date) == len("YYYY-MM-DD"):
                exif_date = datetime.fromisoformat(body.date).strftime(f"%Y:%m:%d {DAY_ONLY_TIME}")
            else:
                exif_date = datetime.fromisoformat(body.date).strftime("%Y:%m:%d %H:%M:%S")
        except ValueError:
            raise HTTPException(400, "Invalid date format")

    apply_tags(paths, body.lat, body.lon, exif_date, clear_gps=body.clear_gps, clear_date=body.clear_date)

    return {"tagged": [p.name for p in paths]}


app.mount("/cropped", StaticFiles(directory=CROPPED_DIR), name="cropped")
app.mount("/done", StaticFiles(directory=DONE_DIR), name="done")
app.mount("/raw", StaticFiles(directory=RAW_DIR), name="raw")
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
