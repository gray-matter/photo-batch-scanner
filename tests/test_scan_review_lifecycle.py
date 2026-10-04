import tempfile
import threading
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import numpy as np
from fastapi.testclient import TestClient

import app
from photo_store import PhotoStore
from escl import ProgressCallback, ScannerInfo
from scan_jobs import PhotoDetector, ScanCoordinator, ScanTransfer

PHOTO = np.zeros((2, 2, 3), dtype=np.uint8)
QUAD = [[0, 0], [10, 0], [10, 10], [0, 10]]
SCANNER = ScannerInfo(name="fixture", base_url="http://scanner.invalid/eSCL")


@contextmanager
def isolated_client(
    *,
    discover: Callable[[], ScannerInfo] | None = None,
    transfer: ScanTransfer | None = None,
    load_image: Callable[[Path], np.ndarray] | None = None,
    detect: PhotoDetector | None = None,
) -> Iterator[tuple[TestClient, Path, Path, ScanCoordinator]]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        raw_directory = root / "raw"
        cropped_directory = root / "cropped"
        done_directory = root / "done"
        for directory in (raw_directory, cropped_directory, done_directory):
            directory.mkdir()

        coordinator = ScanCoordinator(
            discover=discover if discover is not None else Mock(return_value=SCANNER),
            transfer=transfer if transfer is not None else Mock(),
            load_image=load_image if load_image is not None else Mock(return_value=PHOTO),
            detect=detect if detect is not None else Mock(return_value=[QUAD]),
            raw_directory=raw_directory,
        )
        with (
            patch.object(app, "photo_store", PhotoStore(raw_directory, cropped_directory, done_directory)),
            patch.object(app, "scan_coordinator", coordinator),
            TestClient(app.app) as client,
        ):
            yield client, raw_directory, cropped_directory, coordinator


@contextmanager
def recorded_workers() -> Iterator[list[threading.Thread]]:
    workers: list[threading.Thread] = []

    def create_worker(*args: Any, **kwargs: Any) -> threading.Thread:
        worker = threading.Thread(*args, **kwargs)
        workers.append(worker)
        return worker

    with patch("scan_jobs.Thread", side_effect=create_worker):
        try:
            yield workers
        finally:
            for worker in workers:
                worker.join(timeout=5)
                if worker.is_alive():
                    raise AssertionError("Scan worker did not finish after its event was released")


class ScanReviewLifecycleTests(unittest.TestCase):
    def test_scan_conflicts_through_discovery_scanning_and_transfer(self) -> None:
        discovery_entered, release_discovery = threading.Event(), threading.Event()
        scanning_entered, release_scanning = threading.Event(), threading.Event()
        transfer_entered, release_transfer = threading.Event(), threading.Event()

        def discover() -> ScannerInfo:
            discovery_entered.set()
            release_discovery.wait(timeout=5)
            return SCANNER

        def transfer(scanner: ScannerInfo, output_path: str, *, on_progress: ProgressCallback) -> None:
            on_progress("scanning", None, None)
            scanning_entered.set()
            release_scanning.wait(timeout=5)
            on_progress("transferring", 7, 10)
            transfer_entered.set()
            release_transfer.wait(timeout=5)
            Path(output_path).write_bytes(b"fixture")

        with recorded_workers() as workers:
            with isolated_client(discover=discover, transfer=transfer) as (client, _, _, _):
                try:
                    started = client.post("/api/scan")
                    self.assertEqual(started.status_code, 200)
                    self.assertEqual(started.json(), {"started": True})
                    for entered, release, stage in (
                        (discovery_entered, release_discovery, "discovering"),
                        (scanning_entered, release_scanning, "scanning"),
                        (transfer_entered, release_transfer, "transferring"),
                    ):
                        self.assertTrue(entered.wait(timeout=5), stage)
                        status = client.get("/api/scan/status").json()
                        self.assertEqual(status["stage"], stage)
                        if stage == "transferring":
                            self.assertEqual((status["done"], status["total"]), (7, 10))
                        conflict = client.post("/api/scan")
                        self.assertEqual(conflict.status_code, 409)
                        self.assertEqual(conflict.json(), {"detail": "A scan is already in progress"})
                        release.set()
                    workers[0].join(timeout=5)
                    workers[1].join(timeout=5)
                    status = client.get("/api/scan/status").json()
                    self.assertEqual(status["stage"], "review")
                    self.assertEqual(status["pending_review"], {"raw": status["raw"], "quads": [QUAD]})
                    self.assertTrue(all(worker.daemon for worker in workers))
                finally:
                    release_discovery.set()
                    release_scanning.set()
                    release_transfer.set()

    def test_detection_overlaps_next_scan_and_removing_reviews_preserves_transfer_status(self) -> None:
        first_detection_entered, release_first_detection = threading.Event(), threading.Event()
        second_transfer_entered, release_second_transfer = threading.Event(), threading.Event()
        paths: list[Path] = []
        seed_filename = "scan_20000101.jpg"

        def transfer(scanner: ScannerInfo, output_path: str, *, on_progress: ProgressCallback) -> None:
            path = Path(output_path)
            paths.append(path)
            path.write_bytes(b"fixture")
            if len(paths) == 2:
                on_progress("transferring", 7, 10)
                second_transfer_entered.set()
                release_second_transfer.wait(timeout=5)

        def load_image(path: Path) -> np.ndarray:
            marker = 0 if path.name == seed_filename else paths.index(path) + 1
            return np.full((2, 2, 3), marker, dtype=np.uint8)

        def detect(image: np.ndarray, *, expected_count: int | None = None) -> list[list[list[float]]]:
            if image[0, 0, 0] == 1:
                first_detection_entered.set()
                release_first_detection.wait(timeout=5)
            return [QUAD]

        with recorded_workers() as workers:
            with isolated_client(transfer=transfer, load_image=load_image, detect=detect) as (
                client, raw_directory, cropped_directory, coordinator,
            ):
                (raw_directory / seed_filename).write_bytes(b"fixture")
                coordinator.prepare_review(raw_directory / seed_filename)
                try:
                    self.assertEqual(client.post("/api/scan").json(), {"started": True})
                    self.assertTrue(first_detection_entered.wait(timeout=5))
                    workers[0].join(timeout=5)
                    self.assertFalse(workers[0].is_alive())
                    self.assertEqual(client.get("/api/scan/status").json()["raw"], seed_filename)

                    self.assertEqual(client.post("/api/scan").json(), {"started": True})
                    self.assertTrue(second_transfer_entered.wait(timeout=5))
                    release_first_detection.set()
                    workers[1].join(timeout=5)
                    status = client.get("/api/scan/status").json()
                    self.assertEqual(status, {
                        "stage": "transferring", "done": 7, "total": 10, "error": None,
                        "raw": None, "quads": None,
                        "pending_review": {"raw": seed_filename, "quads": [QUAD]},
                    })

                    discarded = client.post(f"/api/raw/{seed_filename}/discard")
                    self.assertEqual(discarded.json(), {"ok": True})
                    status = client.get("/api/scan/status").json()
                    self.assertEqual(status["pending_review"]["raw"], paths[0].name)
                    self.assertEqual(status["stage"], "transferring")
                    with (
                        patch("app.load_scan_image", return_value=PHOTO),
                        patch("app.extract_photo", return_value=PHOTO),
                    ):
                        extracted = client.post(f"/api/raw/{paths[0].name}/extract", json={"quads": [QUAD]})
                    self.assertEqual(extracted.status_code, 200, extracted.text)
                    self.assertTrue((cropped_directory / extracted.json()["photos"][0]).is_file())
                    status = client.get("/api/scan/status").json()
                    self.assertEqual(status["stage"], "transferring")
                    self.assertEqual((status["done"], status["total"]), (7, 10))
                    self.assertIsNone(status["pending_review"])
                    self.assertIsNone(status["raw"])

                    release_second_transfer.set()
                    workers[2].join(timeout=5)
                    workers[3].join(timeout=5)
                    self.assertEqual(client.get("/api/scan/status").json()["pending_review"]["raw"], paths[1].name)
                    self.assertEqual(client.post(f"/api/raw/{paths[1].name}/discard").json(), {"ok": True})
                    self.assertEqual(client.get("/api/scan/status").json(), {
                        "stage": "idle", "done": None, "total": None, "error": None,
                        "raw": None, "quads": None, "pending_review": None,
                    })
                finally:
                    release_first_detection.set()
                    release_second_transfer.set()

    def test_overlapping_detection_completing_out_of_order_keeps_filename_queue_order(self) -> None:
        first_entered, release_first = threading.Event(), threading.Event()
        second_entered, release_second = threading.Event(), threading.Event()
        paths: list[Path] = []

        def transfer(scanner: ScannerInfo, output_path: str, *, on_progress: ProgressCallback) -> None:
            paths.append(Path(output_path))

        def load_image(path: Path) -> np.ndarray:
            return np.full((2, 2, 3), paths.index(path), dtype=np.uint8)

        def detect(image: np.ndarray, *, expected_count: int | None = None) -> list[list[list[float]]]:
            if image[0, 0, 0] == 0:
                entered, release = first_entered, release_first
            else:
                entered, release = second_entered, release_second
            entered.set()
            release.wait(timeout=5)
            return [QUAD]

        with recorded_workers() as workers:
            with isolated_client(transfer=transfer, load_image=load_image, detect=detect) as (client, _, _, _):
                try:
                    client.post("/api/scan")
                    self.assertTrue(first_entered.wait(timeout=5))
                    workers[0].join(timeout=5)
                    self.assertEqual(client.get("/api/scan/status").json()["stage"], "detecting")
                    self.assertEqual(client.post("/api/scan").json(), {"started": True})
                    self.assertTrue(second_entered.wait(timeout=5))
                    workers[2].join(timeout=5)

                    release_second.set()
                    workers[3].join(timeout=5)
                    self.assertEqual(client.get("/api/scan/status").json()["pending_review"]["raw"], paths[1].name)
                    release_first.set()
                    workers[1].join(timeout=5)
                    self.assertEqual(client.get("/api/scan/status").json()["pending_review"]["raw"], paths[0].name)
                finally:
                    release_first.set()
                    release_second.set()

    def test_reviews_are_ordered_redetection_updates_its_review_and_removal_advances_queue(self) -> None:
        first, second, third = "scan_20260101.jpg", "scan_20260102.jpg", "scan_20260103.jpg"
        quads = [
            [[[i, i], [10 + i, i], [10 + i, 10 + i], [i, 10 + i]]]
            for i in range(4)
        ]
        detect = Mock(side_effect=quads)
        with isolated_client(detect=detect) as (client, raw_directory, cropped_directory, coordinator):
            for filename in (third, first, second):
                (raw_directory / filename).write_bytes(b"fixture")
                coordinator.prepare_review(raw_directory / filename)

            status = client.get("/api/scan/status").json()
            self.assertEqual(status["pending_review"], {"raw": first, "quads": quads[1]})
            self.assertEqual((status["raw"], status["quads"]), (second, quads[2]))

            redetected = client.post(f"/api/raw/{second}/detect", json={"expected_count": 4})
            self.assertEqual(redetected.status_code, 200, redetected.text)
            self.assertEqual(redetected.json(), {"raw": second, "quads": quads[3], "expected_count": 4})
            detect.assert_called_with(PHOTO, expected_count=4)
            status = client.get("/api/scan/status").json()
            self.assertEqual(status["quads"], quads[3])
            self.assertEqual(status["pending_review"], {"raw": first, "quads": quads[1]})

            self.assertEqual(client.post(f"/api/raw/{first}/discard").json(), {"ok": True})
            self.assertEqual(client.get("/api/scan/status").json()["pending_review"], {
                "raw": second, "quads": quads[3], "expected_count": 4,
            })

            with (
                patch("app.load_scan_image", return_value=PHOTO),
                patch("app.extract_photo", return_value=PHOTO),
            ):
                extracted = client.post(f"/api/raw/{second}/extract", json={"quads": quads[3]})
            self.assertEqual(extracted.status_code, 200, extracted.text)
            self.assertEqual(extracted.json(), {"photos": ["scan_20260102_photo_01.jpg"]})
            self.assertTrue((cropped_directory / "scan_20260102_photo_01.jpg").is_file())
            self.assertEqual(client.get("/api/scan/status").json()["pending_review"], {"raw": third, "quads": quads[0]})

    def test_redetection_keeps_requested_symlink_filename_and_optional_count(self) -> None:
        updated_quads = [[[1, 1], [11, 1], [11, 11], [1, 11]]]
        detect = Mock(side_effect=[[QUAD], updated_quads])
        load_image = Mock(return_value=PHOTO)
        with isolated_client(detect=detect, load_image=load_image) as (client, raw_directory, _, coordinator):
            target = raw_directory / "target.jpg"
            target.write_bytes(b"fixture")
            alias = raw_directory / "alias.jpg"
            alias.symlink_to(target.name)
            coordinator.prepare_review(alias)
            response = client.post("/api/raw/alias.jpg/detect", json={})

            self.assertEqual(response.status_code, 200, response.text)
            result = {"raw": "alias.jpg", "quads": updated_quads, "expected_count": None}
            self.assertEqual(response.json(), result)
            self.assertEqual(client.get("/api/scan/status").json()["pending_review"], result)
            load_image.assert_called_with(target.resolve())


if __name__ == "__main__":
    unittest.main()
