import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

import app


@contextmanager
def isolated_client() -> Iterator[tuple[TestClient, Path, Path, list[dict], dict]]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        raw_directory = root / "raw"
        cropped_directory = root / "cropped"
        done_directory = root / "done"
        for directory in (raw_directory, cropped_directory, done_directory):
            directory.mkdir()

        pending_reviews: list[dict] = []
        scan_state = {
            "stage": "idle",
            "done": None,
            "total": None,
            "error": None,
            "raw": None,
            "quads": None,
        }
        with (
            patch.object(app, "RAW_DIR", raw_directory),
            patch.object(app, "CROPPED_DIR", cropped_directory),
            patch.object(app, "DONE_DIR", done_directory),
            patch.object(app, "_scan_lock", threading.Lock()),
            patch.object(app, "_scan_state_lock", threading.Lock()),
            patch.object(app, "_pending_reviews", pending_reviews),
            patch.object(app, "_scan_state", scan_state),
        ):
            with TestClient(app.app) as client:
                yield client, raw_directory, cropped_directory, pending_reviews, scan_state


class ScanReviewLifecycleTests(unittest.TestCase):
    def test_scan_request_returns_conflict_while_scanner_lock_is_held(self) -> None:
        with isolated_client() as (client, _, _, _, _):
            self.assertTrue(app._scan_lock.acquire(blocking=False))
            try:
                response = client.post("/api/scan")
            finally:
                app._scan_lock.release()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "A scan is already in progress")

    def test_scan_can_start_while_a_review_is_pending(self) -> None:
        with isolated_client() as (client, raw_directory, _, pending_reviews, _):
            filename = "scan_pending.jpg"
            (raw_directory / filename).write_bytes(b"fixture")
            review = {"raw": filename, "quads": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}
            pending_reviews.append(review)

            with patch("app.threading.Thread") as thread:
                response = client.post("/api/scan")
                status = client.get("/api/scan/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"started": True})
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()
        self.assertEqual(status.json()["pending_review"], review)

    def test_reviews_are_ordered_redetection_updates_its_review_and_removal_advances_queue(self) -> None:
        first = "scan_20260101.jpg"
        second = "scan_20260102.jpg"
        third = "scan_20260103.jpg"
        quads = [
            [[[0, 0], [10, 0], [10, 10], [0, 10]]],
            [[[1, 1], [11, 1], [11, 11], [1, 11]]],
            [[[2, 2], [12, 2], [12, 12], [2, 12]]],
            [[[3, 3], [13, 3], [13, 13], [3, 13]]],
        ]
        with isolated_client() as (client, raw_directory, cropped_directory, pending_reviews, _):
            for filename in (third, first, second):
                (raw_directory / filename).write_bytes(b"fixture")

            with (
                patch("app.load_scan_image", return_value=np.zeros((2, 2, 3), dtype=np.uint8)),
                patch("app.detect_photo_regions", side_effect=quads[:3]),
            ):
                for filename in (third, first, second):
                    app._detect_scan(raw_directory / filename)

            status = client.get("/api/scan/status")
            self.assertEqual(status.json()["pending_review"]["raw"], first)
            self.assertEqual([review["raw"] for review in app._pending_reviews], [first, second, third])

            with (
                patch("app.load_scan_image", return_value=np.zeros((2, 2, 3), dtype=np.uint8)),
                patch("app.detect_photo_regions", return_value=quads[3]),
            ):
                redetected = client.post(f"/api/raw/{second}/detect", json={})

            self.assertEqual(redetected.status_code, 200, redetected.text)
            self.assertEqual(redetected.json()["quads"], quads[3])
            self.assertEqual(
                client.get("/api/scan/status").json()["pending_review"],
                {"raw": first, "quads": quads[1]},
            )

            discarded = client.post(f"/api/raw/{first}/discard")
            status_after_discard = client.get("/api/scan/status")
            self.assertEqual(discarded.status_code, 200)
            self.assertEqual(status_after_discard.json()["pending_review"], {
                "raw": second,
                "quads": quads[3],
                "expected_count": None,
            })

            with (
                patch("app.load_scan_image", return_value=np.zeros((2, 2, 3), dtype=np.uint8)),
                patch("app.extract_photo", return_value=np.zeros((2, 2, 3), dtype=np.uint8)),
            ):
                extracted = client.post(
                    f"/api/raw/{second}/extract",
                    json={"quads": quads[3]},
                )

            status_after_extract = client.get("/api/scan/status")
            self.assertEqual(extracted.status_code, 200, extracted.text)
            self.assertEqual(extracted.json()["photos"], ["scan_20260102_photo_01.jpg"])
            self.assertEqual(status_after_extract.json()["pending_review"]["raw"], third)
            self.assertEqual([review["raw"] for review in pending_reviews], [third])
            self.assertTrue((cropped_directory / "scan_20260102_photo_01.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
