import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

import app
from photo_store import PhotoStore
from scan_jobs import ScanCoordinator


class ExtractGeometryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        raw_directory = root / "raw"
        self.cropped_directory = root / "cropped"
        raw_directory.mkdir()
        self.cropped_directory.mkdir()
        self.filename = "disposable_geometry.jpg"
        with Image.new("RGB", (20, 20), "white") as image:
            image.save(raw_directory / self.filename)
        self.valid_quad = [[0, 0], [19, 0], [19, 19], [0, 19]]
        self.review = {"raw": self.filename, "quads": [self.valid_quad]}
        coordinator = ScanCoordinator(
            discover=Mock(), transfer=Mock(), load_image=app.load_scan_image,
            detect=Mock(return_value=[self.valid_quad]), raw_directory=raw_directory,
        )
        coordinator.prepare_review(raw_directory / self.filename)
        for replacement in (
            patch.object(app, "photo_store", PhotoStore(raw_directory, self.cropped_directory, root / "done")),
            patch.object(app, "scan_coordinator", coordinator),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)

    def test_malformed_selection_returns_actionable_400_without_writing_or_removing_review(self) -> None:
        invalid_quads = {
            "empty": [],
            "three_points": self.valid_quad[:3],
            "five_points": self.valid_quad + [[10, 10]],
            "missing_coordinate": [[0], *self.valid_quad[1:]],
            "extra_coordinate": [[0, 0, 0], *self.valid_quad[1:]],
            "nan": [["NaN", 0], *self.valid_quad[1:]],
            "positive_infinity": [[0, "Infinity"], *self.valid_quad[1:]],
            "negative_infinity": [["-Infinity", 0], *self.valid_quad[1:]],
            "unsupported_range": [[1e100, 0], *self.valid_quad[1:]],
        }
        with patch.object(app, "extract_photo") as extract:
            for name, quad in invalid_quads.items():
                with self.subTest(selection=name):
                    response = self.client.post(
                        f"/api/raw/{self.filename}/extract", json={"quads": [self.valid_quad, quad]}
                    )
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn("Selection 2:", response.json()["detail"])
                    self.assertEqual(list(self.cropped_directory.iterdir()), [])
                    self.assertEqual(self.client.get("/api/scan/status").json()["pending_review"], self.review)
            extract.assert_not_called()

    def test_degenerate_selection_preserves_existing_empty_success(self) -> None:
        response = self.client.post(
            f"/api/raw/{self.filename}/extract", json={"quads": [[[0, 0]] * 4]}
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"photos": []})
        self.assertEqual(list(self.cropped_directory.iterdir()), [])
        self.assertIsNone(self.client.get("/api/scan/status").json()["pending_review"])


if __name__ == "__main__":
    unittest.main()
