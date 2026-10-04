import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageCms

import app


class RotateMetadataTests(unittest.TestCase):
    @unittest.expectedFailure
    def test_rotation_preserves_date_gps_and_icc_profile(self) -> None:
        """Rotating an already tagged JPEG must retain its descriptive metadata."""
        exiftool = shutil.which("exiftool")
        if exiftool is None:
            self.skipTest("exiftool is required to create and inspect the metadata fixture")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cropped_directory = root / "cropped"
            cropped_directory.mkdir()
            filename = "asymmetric.jpg"
            photo_path = cropped_directory / filename
            icc_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()

            image = Image.new("RGB", (80, 40), "white")
            for x in range(40):
                for y in range(40):
                    image.putpixel((x, y), (240, 20, 20))
            for x in range(40, 80):
                for y in range(20, 40):
                    image.putpixel((x, y), (20, 20, 240))
            image.save(photo_path, quality=100, icc_profile=icc_profile)
            subprocess.run(
                [
                    exiftool,
                    "-overwrite_original",
                    "-DateTimeOriginal=2001:02:03 04:05:06",
                    "-GPSLatitude=48.8584",
                    "-GPSLongitude=2.2945",
                    str(photo_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            with patch.object(app, "CROPPED_DIR", cropped_directory):
                with TestClient(app.app) as client:
                    response = client.post(f"/api/photos/{filename}/rotate", json={"degrees": 90})

            self.assertEqual(response.status_code, 200, response.text)
            with Image.open(photo_path) as rotated:
                self.assertEqual(rotated.size, (40, 80))
                self.assertGreater(rotated.getpixel((30, 10))[2], rotated.getpixel((30, 10))[0])

            metadata = json.loads(
                subprocess.run(
                    [exiftool, "-j", "-n", "-DateTimeOriginal", "-GPSLatitude", "-GPSLongitude", str(photo_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )[0]
            with Image.open(photo_path) as rotated:
                resulting_icc_profile = rotated.info.get("icc_profile")
            metadata_failures = []
            if metadata.get("DateTimeOriginal") != "2001:02:03 04:05:06":
                metadata_failures.append(f"DateTimeOriginal={metadata.get('DateTimeOriginal')!r}")
            if abs(metadata.get("GPSLatitude", 0) - 48.8584) > 0.0001:
                metadata_failures.append(f"GPSLatitude={metadata.get('GPSLatitude')!r}")
            if abs(metadata.get("GPSLongitude", 0) - 2.2945) > 0.0001:
                metadata_failures.append(f"GPSLongitude={metadata.get('GPSLongitude')!r}")
            if resulting_icc_profile != icc_profile:
                metadata_failures.append("ICC profile changed or was removed")
            self.assertEqual(metadata_failures, [], "; ".join(metadata_failures))
