import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

import app


class ExtractWriteFailureTests(unittest.TestCase):
    # Known defect: api_extract ignores a false cv2.imwrite result and clears review.
    @unittest.expectedFailure
    def test_failed_write_does_not_claim_output_or_remove_pending_review(self) -> None:
        """A failed image write must leave the scan available for review."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_directory = root / "raw"
            cropped_directory = root / "cropped"
            raw_directory.mkdir()
            cropped_directory.mkdir()
            raw_filename = "scan_write_failure.jpg"
            (raw_directory / raw_filename).write_bytes(b"fixture")
            review = {"raw": raw_filename, "quads": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}
            state = {
                "stage": "review",
                "done": None,
                "total": None,
                "error": None,
                "raw": raw_filename,
                "quads": review["quads"],
            }

            with (
                patch.object(app, "RAW_DIR", raw_directory),
                patch.object(app, "CROPPED_DIR", cropped_directory),
                patch.object(app, "_pending_reviews", [review]),
                patch.object(app, "_scan_state", state),
                patch.object(app, "load_scan_image", return_value=np.zeros((2, 2, 3), dtype=np.uint8)),
                patch.object(app, "extract_photo", return_value=np.zeros((2, 2, 3), dtype=np.uint8)),
                patch.object(app.cv2, "imwrite", return_value=False),
            ):
                with TestClient(app.app) as client:
                    response = client.post(
                        f"/api/raw/{raw_filename}/extract",
                        json={"quads": [[[0, 0], [1, 0], [1, 1], [0, 1]]]},
                    )
                    status_response = client.get("/api/scan/status")

            self.assertGreaterEqual(response.status_code, 400)
            self.assertNotIn("photos", response.json())
            self.assertEqual(list(cropped_directory.iterdir()), [])
            self.assertEqual(status_response.status_code, 200)
            self.assertEqual(status_response.json()["pending_review"], review)
