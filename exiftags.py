"""Thin wrapper around the `exiftool` CLI for writing GPS position and date tags."""
import json
import subprocess
from pathlib import Path


def read_tag_status(files: list[Path]) -> dict[str, dict[str, bool]]:
    """Return {filename: {"gps": bool, "time": bool}} telling whether GPS/date tags are set."""
    if not files:
        return {}
    cmd = ["exiftool", "-j", "-GPSLatitude", "-DateTimeOriginal", *[str(f) for f in files]]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return {
        Path(entry["SourceFile"]).name: {
            "gps": "GPSLatitude" in entry,
            "time": "DateTimeOriginal" in entry,
        }
        for entry in json.loads(result.stdout)
    }


def apply_tags(
    files: list[Path],
    lat: float | None,
    lon: float | None,
    date: str | None,
    clear_gps: bool = False,
    clear_date: bool = False,
) -> None:
    """Write or remove any combination of GPS position and date/time on all given files in one exiftool call.

    `date`, if given, must be in exiftool's format: "YYYY:MM:DD HH:MM:SS".
    The `*=` form lets exiftool derive GPSLatitudeRef/GPSLongitudeRef from the sign.
    Passing `lat`/`lon` without `date`, or vice versa, writes only that tag group.
    `clear_gps`/`clear_date` remove the existing tags instead of writing new ones,
    and take precedence over `lat`/`lon`/`date` being set.
    """
    if not files:
        return
    tag_args = []
    if clear_gps:
        tag_args.append("-gps:all=")
    elif lat is not None and lon is not None:
        tag_args += [f"-GPSLatitude*={lat}", f"-GPSLongitude*={lon}"]
    if clear_date:
        tag_args.append("-AllDates=")
    elif date is not None:
        tag_args.append(f"-AllDates={date}")
    if not tag_args:
        return
    cmd = ["exiftool", *tag_args, "-overwrite_original", *[str(f) for f in files]]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
