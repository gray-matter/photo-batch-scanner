import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, BinaryIO
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image, ImageCms

import app


class RotateMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.cropped_directory = Path(self.temporary_directory.name)
        self.photo_path = self.cropped_directory / "asymmetric.jpg"
        self.icc_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self.colors = [(240, 20, 20), (20, 240, 20), (20, 20, 240), (240, 240, 20)]
        directory_patch = patch.object(app, "CROPPED_DIR", self.cropped_directory)
        directory_patch.start()
        self.addCleanup(directory_patch.stop)
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)

    def write_photo(self, orientation: int = 1) -> None:
        with Image.new("RGB", (80, 40), "white") as image:
            image.paste(self.colors[0], (0, 0, 40, 20))
            image.paste(self.colors[1], (40, 0, 80, 20))
            image.paste(self.colors[2], (40, 20, 80, 40))
            image.paste(self.colors[3], (0, 20, 40, 40))
            exif = Image.Exif()
            exif[274] = orientation
            image.save(self.photo_path, quality=100, exif=exif, icc_profile=self.icc_profile)

    def rotate(self, degrees: int) -> httpx.Response:
        return self.client.post(f"/api/photos/{self.photo_path.name}/rotate", json={"degrees": degrees})

    def test_rotation_preserves_date_gps_and_icc_profile(self) -> None:
        exiftool = shutil.which("exiftool")
        if exiftool is None:
            self.skipTest("exiftool is required to create and inspect the metadata fixture")

        for degrees in (90, 180, 270):
            with self.subTest(degrees=degrees):
                self.write_photo(orientation=6)
                subprocess.run(
                    [
                        exiftool,
                        "-overwrite_original",
                        "-DateTimeOriginal=2001:02:03 04:05:06",
                        "-CreateDate=2001:02:03 04:05:06",
                        "-ModifyDate=2001:02:03 04:05:06",
                        "-GPSLatitude=48.8584",
                        "-GPSLatitudeRef=N",
                        "-GPSLongitude=2.2945",
                        "-GPSLongitudeRef=E",
                        str(self.photo_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                response = self.rotate(degrees)

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"ok": True})
                metadata = json.loads(
                    subprocess.run(
                        [
                            exiftool, "-j", "-n", "-DateTimeOriginal", "-CreateDate", "-ModifyDate",
                            "-GPSLatitude", "-GPSLatitudeRef", "-GPSLongitude", "-GPSLongitudeRef",
                            "-Orientation", str(self.photo_path),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout
                )[0]
                for tag in ("DateTimeOriginal", "CreateDate", "ModifyDate"):
                    self.assertEqual(metadata.get(tag), "2001:02:03 04:05:06", tag)
                self.assertAlmostEqual(metadata["GPSLatitude"], 48.8584, places=4)
                self.assertEqual(metadata["GPSLatitudeRef"], "N")
                self.assertAlmostEqual(metadata["GPSLongitude"], 2.2945, places=4)
                self.assertEqual(metadata["GPSLongitudeRef"], "E")
                self.assertEqual(metadata.get("Orientation"), 1)
                with Image.open(self.photo_path) as rotated:
                    self.assertEqual(rotated.info.get("icc_profile"), self.icc_profile)

    def test_rotation_applies_exif_orientation_then_counterclockwise_degrees(self) -> None:
        # Corner indexes are clockwise from top left after the EXIF transform.
        oriented_corners = {
            1: [0, 1, 2, 3],
            2: [1, 0, 3, 2],
            3: [2, 3, 0, 1],
            4: [3, 2, 1, 0],
            5: [0, 3, 2, 1],
            6: [3, 0, 1, 2],
            7: [2, 1, 0, 3],
            8: [1, 2, 3, 0],
        }
        quality_reference = self.cropped_directory / "quality-reference.jpg"
        with Image.new("RGB", (8, 8)) as image:
            image.save(quality_reference, quality=92)
        with Image.open(quality_reference) as reference:
            expected_quantization = reference.quantization
        quality_reference.unlink()

        for orientation, corners in oriented_corners.items():
            for degrees in (90, 180, 270):
                with self.subTest(orientation=orientation, degrees=degrees):
                    self.write_photo(orientation)
                    response = self.rotate(degrees)
                    self.assertEqual(response.status_code, 200, response.text)
                    swap_dimensions = (orientation >= 5) != (degrees != 180)
                    expected_size = (40, 80) if swap_dimensions else (80, 40)
                    quarter_turns = degrees // 90
                    expected_corners = corners[quarter_turns:] + corners[:quarter_turns]
                    with Image.open(self.photo_path) as rotated:
                        self.assertEqual(rotated.size, expected_size)
                        self.assertEqual(rotated.getexif().get(274), 1)
                        self.assertEqual(rotated.info.get("icc_profile"), self.icc_profile)
                        self.assertEqual(rotated.quantization, expected_quantization)
                        width, height = rotated.size
                        samples = [(10, 10), (width - 10, 10), (width - 10, height - 10), (10, height - 10)]
                        for sample, color_index in zip(samples, expected_corners):
                            actual = rotated.getpixel(sample)
                            expected = self.colors[color_index]
                            self.assertLess(max(abs(a - e) for a, e in zip(actual, expected)), 15)

    def test_partial_encoding_failure_preserves_source_and_cleans_temporary_file(self) -> None:
        self.write_photo()
        original_bytes = self.photo_path.read_bytes()

        def fail_save(image: Image.Image, destination: BinaryIO | str | Path, **kwargs: Any) -> None:
            if isinstance(destination, (str, Path)):
                Path(destination).write_bytes(b"incomplete JPEG")
            else:
                destination.write(b"incomplete JPEG")
            raise OSError("encoding failed")

        with patch.object(Image.Image, "save", autospec=True, side_effect=fail_save):
            with self.assertRaisesRegex(OSError, "encoding failed"):
                self.rotate(90)

        self.assertEqual(self.photo_path.read_bytes(), original_bytes)
        self.assertEqual(list(self.cropped_directory.iterdir()), [self.photo_path])
        with Image.open(self.photo_path) as original:
            original.load()
            self.assertEqual(original.size, (80, 40))

    def test_failed_replace_preserves_source_and_cleans_temporary_file(self) -> None:
        self.write_photo()
        original_bytes = self.photo_path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("replacement failed")):
            with self.assertRaisesRegex(OSError, "replacement failed"):
                self.rotate(90)
        self.assertEqual(self.photo_path.read_bytes(), original_bytes)
        self.assertEqual(list(self.cropped_directory.iterdir()), [self.photo_path])

    def test_invalid_degrees_keep_existing_400_response_and_source(self) -> None:
        self.write_photo()
        original_bytes = self.photo_path.read_bytes()
        for degrees in (0, 45, -90, 360):
            with self.subTest(degrees=degrees):
                response = self.rotate(degrees)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"detail": "degrees must be 90, 180, or 270"})
                self.assertEqual(self.photo_path.read_bytes(), original_bytes)
        self.assertEqual(list(self.cropped_directory.iterdir()), [self.photo_path])
