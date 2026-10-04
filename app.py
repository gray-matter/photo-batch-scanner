import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crop import detect_photo_regions, extract_photo, load_scan_image, validate_quad_points
from escl import discover_scanner, scan_to_file
from exiftags import apply_tags, read_tag_status
from photo_processing import extract_crop_outputs
from photo_store import PhotoNotFound, PhotoStore
from scan_jobs import ScanCoordinator

BASE_DIR = Path(__file__).parent
photo_store = PhotoStore(
    raw_directory=BASE_DIR / "scans" / "raw",
    cropped_directory=BASE_DIR / "scans" / "cropped",
    done_directory=BASE_DIR / "scans" / "done",
)

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
    raw_directory=photo_store.raw_directory,
)


@app.exception_handler(PhotoNotFound)
def photo_not_found_handler(request: Request, error: PhotoNotFound) -> JSONResponse:
    detail = "Scan not found" if error.directory == "raw" else "Photo not found"
    return JSONResponse(status_code=404, content={"detail": detail})


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
    path = photo_store.path("raw", filename)
    try:
        return dict(scan_coordinator.redetect(path, filename=filename, expected_count=body.expected_count))
    except (OSError, ValueError, cv2.error) as error:
        raise HTTPException(422, f"Couldn't detect photos: {error}") from error


@app.get("/api/photos")
def api_photos():
    files = photo_store.photos("cropped")
    tag_status = read_tag_status(files)
    return {
        "photos": [
            {"filename": f.name, **tag_status.get(f.name, {"gps": False, "time": False})}
            for f in files
        ]
    }


@app.get("/api/photos/done")
def api_photos_done():
    files = photo_store.photos("done")
    tag_status = read_tag_status(files)
    return {
        "photos": [
            {"filename": f.name, **tag_status.get(f.name, {"gps": False, "time": False})}
            for f in files
        ]
    }


@app.get("/api/photos/{filename}")
def api_get_photo(filename: str):
    return FileResponse(photo_store.path("cropped", filename))


@app.delete("/api/photos/{filename}")
def api_delete_photo(filename: str):
    photo_store.delete("cropped", filename)
    return {"ok": True}


@app.post("/api/photos/{filename}/done")
def api_mark_done(filename: str):
    photo_store.move("cropped", "done", filename)
    return {"ok": True}


@app.post("/api/photos/mark-tagged-done")
def api_mark_tagged_done():
    files = photo_store.photos("cropped")
    tag_status = read_tag_status(files)
    marked = [
        path for path in files
        if tag_status.get(path.name, {}).get("gps") and tag_status.get(path.name, {}).get("time")
    ]
    photo_store.mark_listed_done(path.name for path in marked)
    return {"photos": [path.name for path in marked]}


@app.delete("/api/photos/done/{filename}")
def api_delete_done_photo(filename: str):
    photo_store.delete("done", filename)
    return {"ok": True}


@app.post("/api/photos/done/{filename}/restore")
def api_restore_done_photo(filename: str):
    photo_store.move("done", "cropped", filename)
    return {"ok": True}


class RotateBody(BaseModel):
    degrees: int  # 90, 180, or 270 (counter-clockwise)


@app.post("/api/photos/{filename}/rotate")
def api_rotate(filename: str, body: RotateBody) -> dict[str, bool]:
    if body.degrees not in (90, 180, 270):
        raise HTTPException(400, "degrees must be 90, 180, or 270")
    photo_store.rotate(filename, body.degrees)
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
    path = photo_store.path("raw", filename)
    img = load_scan_image(path)
    try:
        output_paths = extract_crop_outputs(
            img, body.quads, path.stem, photo_store.cropped_directory, 92, extractor=extract_photo,
        )
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc
    scan_coordinator.remove_review(filename)
    return {"photos": [path.name for path in output_paths]}


@app.post("/api/raw/{filename}/discard")
def api_discard_raw(filename: str):
    photo_store.delete("raw", filename)
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

    paths = [photo_store.tag_path(f) for f in body.filenames]

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


app.mount("/cropped", StaticFiles(directory=photo_store.cropped_directory), name="cropped")
app.mount("/done", StaticFiles(directory=photo_store.done_directory), name="done")
app.mount("/raw", StaticFiles(directory=photo_store.raw_directory), name="raw")
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
