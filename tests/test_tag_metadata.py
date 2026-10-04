import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import app


class TagMetadataTests(unittest.TestCase):
    exiftool: str

    @classmethod
    def setUpClass(cls: type["TagMetadataTests"]) -> None:
        exiftool = shutil.which("exiftool")
        if exiftool is None:
            raise unittest.SkipTest("exiftool is required to verify written photo metadata")
        cls.exiftool = exiftool

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cropped_directory = Path(self.temporary_directory.name)
        self.filename = "tag_fixture.jpg"
        Image.new("RGB", (32, 24), "white").save(self.cropped_directory / self.filename)
        self.client_context = patch.object(app, "CROPPED_DIR", self.cropped_directory)
        self.client_context.start()
        self.client = TestClient(app.app)

    def tearDown(self) -> None:
        self.client.close()
        self.client_context.stop()
        self.temporary_directory.cleanup()

    def read_metadata(self) -> dict[str, str | float]:
        result = subprocess.run(
            [
                self.exiftool,
                "-j",
                "-n",
                "-DateTimeOriginal",
                "-GPSLatitude",
                "-GPSLatitudeRef",
                "-GPSLongitude",
                "-GPSLongitudeRef",
                str(self.cropped_directory / self.filename),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)[0]

    def test_day_only_date_is_written_at_noon_without_adding_gps(self) -> None:
        response = self.client.post("/api/tag", json={"filenames": [self.filename], "date": "2024-02-29"})

        self.assertEqual(response.status_code, 200, response.text)
        metadata = self.read_metadata()
        self.assertEqual(metadata.get("DateTimeOriginal"), "2024:02:29 12:00:00")
        self.assertNotIn("GPSLatitude", metadata)
        self.assertNotIn("GPSLongitude", metadata)

    def test_negative_coordinates_round_trip_with_south_and_west_references(self) -> None:
        response = self.client.post(
            "/api/tag",
            json={"filenames": [self.filename], "lat": -33.8688, "lon": -151.2093},
        )

        self.assertEqual(response.status_code, 200, response.text)
        metadata = self.read_metadata()
        self.assertAlmostEqual(metadata["GPSLatitude"], -33.8688, places=4)
        self.assertEqual(metadata["GPSLatitudeRef"], "S")
        self.assertAlmostEqual(metadata["GPSLongitude"], -151.2093, places=4)
        self.assertEqual(metadata["GPSLongitudeRef"], "W")

    def test_clearing_date_preserves_gps_and_clearing_gps_preserves_date(self) -> None:
        initial = self.client.post(
            "/api/tag",
            json={"filenames": [self.filename], "lat": -33.8688, "lon": -151.2093, "date": "2024-02-29T08:15"},
        )
        self.assertEqual(initial.status_code, 200, initial.text)

        cleared_date = self.client.post("/api/tag", json={"filenames": [self.filename], "clear_date": True})
        self.assertEqual(cleared_date.status_code, 200, cleared_date.text)
        after_date_clear = self.read_metadata()
        self.assertNotIn("DateTimeOriginal", after_date_clear)
        self.assertAlmostEqual(after_date_clear["GPSLatitude"], -33.8688, places=4)
        self.assertAlmostEqual(after_date_clear["GPSLongitude"], -151.2093, places=4)

        restored_date = self.client.post(
            "/api/tag", json={"filenames": [self.filename], "date": "2024-02-29T08:15"}
        )
        self.assertEqual(restored_date.status_code, 200, restored_date.text)
        cleared_gps = self.client.post("/api/tag", json={"filenames": [self.filename], "clear_gps": True})
        self.assertEqual(cleared_gps.status_code, 200, cleared_gps.text)
        after_gps_clear = self.read_metadata()
        self.assertEqual(after_gps_clear.get("DateTimeOriginal"), "2024:02:29 08:15:00")
        self.assertNotIn("GPSLatitude", after_gps_clear)
        self.assertNotIn("GPSLongitude", after_gps_clear)


if __name__ == "__main__":
    unittest.main()
