import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import httpx
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

import app
from photo_store import PhotoStore
import crop
from scan_jobs import ScanCoordinator

QUAD = [[0, 0], [1, 0], [1, 1], [0, 1]]
PHOTO = np.full((8, 12, 3), 80, dtype=np.uint8)


class ExtractWriteFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        self.raw_directory = root / "raw"
        self.cropped_directory = root / "cropped"
        self.raw_directory.mkdir()
        self.cropped_directory.mkdir()
        self.raw_filename = "scan_write_failure.jpg"
        (self.raw_directory / self.raw_filename).write_bytes(b"fixture")
        self.review = {"raw": self.raw_filename, "quads": [QUAD]}
        coordinator = ScanCoordinator(
            discover=Mock(), transfer=Mock(), load_image=Mock(return_value=PHOTO),
            detect=Mock(return_value=[QUAD]), raw_directory=self.raw_directory,
        )
        coordinator.prepare_review(self.raw_directory / self.raw_filename)
        for replacement in (
            patch.object(app, "photo_store", PhotoStore(self.raw_directory, self.cropped_directory, root / "done")),
            patch.object(app, "scan_coordinator", coordinator),
            patch.object(app, "load_scan_image", return_value=PHOTO),
            patch.object(app, "extract_photo", return_value=PHOTO),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)

    def extract(self, count: int = 1) -> httpx.Response:
        return self.client.post(
            f"/api/raw/{self.raw_filename}/extract", json={"quads": [QUAD] * count}
        )

    def assert_review_retained(self, response: httpx.Response) -> None:
        self.assertEqual(response.status_code, 500, response.text)
        self.assertNotIn("photos", response.json())
        self.assertIn("crop", response.json()["detail"])
        self.assertEqual(self.client.get("/api/scan/status").json()["pending_review"], self.review)

    def test_failed_write_does_not_claim_output_or_remove_pending_review(self) -> None:
        with patch.object(cv2, "imwrite", return_value=False):
            response = self.extract()

        self.assert_review_retained(response)
        self.assertIn("scan_write_failure_photo_01.jpg", response.json()["detail"])
        self.assertEqual(list(self.cropped_directory.iterdir()), [])

        retried = self.extract()
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json(), {"photos": ["scan_write_failure_photo_01.jpg"]})
        self.assertIsNone(self.client.get("/api/scan/status").json()["pending_review"])

    def test_second_encoding_failure_preserves_existing_photos_and_cleans_staging(self) -> None:
        first_path = self.cropped_directory / "scan_write_failure_photo_01.jpg"
        first_path.write_bytes(b"existing photo")
        unrelated_path = self.cropped_directory / "unrelated.jpg"
        unrelated_path.write_bytes(b"unrelated photo")
        real_imwrite = cv2.imwrite

        for failure in (False, cv2.error("encoder unavailable")):
            with self.subTest(failure=failure):
                calls = 0

                def fail_second_write(filename: str, image: np.ndarray, parameters: list[int]) -> bool:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        self.assertEqual(first_path.read_bytes(), b"existing photo")
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                    return real_imwrite(filename, image, parameters)

                with patch.object(cv2, "imwrite", side_effect=fail_second_write):
                    response = self.extract(count=2)

                self.assert_review_retained(response)
                self.assertIn("scan_write_failure_photo_02.jpg", response.json()["detail"])
                self.assertEqual(first_path.read_bytes(), b"existing photo")
                self.assertEqual(unrelated_path.read_bytes(), b"unrelated photo")
                self.assertEqual(set(self.cropped_directory.iterdir()), {first_path, unrelated_path})

    def test_partial_publication_reports_failure_retains_review_and_leaves_photos(self) -> None:
        first_path = self.cropped_directory / "scan_write_failure_photo_01.jpg"
        second_path = self.cropped_directory / "scan_write_failure_photo_02.jpg"
        unrelated_path = self.cropped_directory / "unrelated.jpg"
        second_path.write_bytes(b"existing second photo")
        unrelated_path.write_bytes(b"unrelated photo")
        real_replace = Path.replace

        def fail_second_publication(source: Path, target: Path) -> Path:
            if target == second_path:
                raise OSError("publication unavailable")
            return real_replace(source, target)

        with patch.object(Path, "replace", new=fail_second_publication):
            response = self.extract(count=2)

        self.assert_review_retained(response)
        self.assertIn("Couldn't publish crop", response.json()["detail"])
        self.assertIsNotNone(cv2.imread(str(first_path)))
        self.assertEqual(second_path.read_bytes(), b"existing second photo")
        self.assertEqual(unrelated_path.read_bytes(), b"unrelated photo")
        self.assertEqual(set(self.cropped_directory.iterdir()), {first_path, second_path, unrelated_path})

    def test_success_preserves_numbering_quality_and_replaces_existing_output(self) -> None:
        first_path = self.cropped_directory / "scan_write_failure_photo_01.jpg"
        first_path.write_bytes(b"existing photo")
        with (
            patch.object(app, "extract_photo", side_effect=[PHOTO, None, PHOTO]),
            patch.object(cv2, "imwrite", wraps=cv2.imwrite) as imwrite,
        ):
            response = self.extract(count=3)

        names = ["scan_write_failure_photo_01.jpg", "scan_write_failure_photo_03.jpg"]
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"photos": names})
        self.assertEqual({path.name for path in self.cropped_directory.iterdir()}, set(names))
        for name in names:
            decoded = cv2.imread(str(self.cropped_directory / name))
            self.assertIsNotNone(decoded)
            np.testing.assert_array_equal(decoded, PHOTO)
        self.assertTrue(all(call.args[2] == [int(cv2.IMWRITE_JPEG_QUALITY), 92] for call in imwrite.call_args_list))
        self.assertIsNone(self.client.get("/api/scan/status").json()["pending_review"])

    def test_all_none_crops_keep_the_existing_empty_success(self) -> None:
        with patch.object(app, "extract_photo", return_value=None):
            response = self.extract()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"photos": []})
        self.assertEqual(list(self.cropped_directory.iterdir()), [])
        self.assertIsNone(self.client.get("/api/scan/status").json()["pending_review"])

    def test_api_and_cli_produce_identical_names_and_jpeg_bytes(self) -> None:
        cli_directory = self.raw_directory.parent / "photos_decoupees"
        input_path = self.raw_directory / self.raw_filename
        with Image.new("RGB", (40, 30), (80, 110, 140)) as image:
            image.save(input_path)
        quads = [[[0, 0], [39, 0], [39, 29], [0, 29]], [[0, 0]] * 4, QUAD]
        with (
            patch.object(app, "load_scan_image", new=crop.load_scan_image),
            patch.object(app, "extract_photo", new=crop.extract_photo),
            patch.object(crop, "detect_photo_regions", return_value=quads),
            patch.object(crop, "detect_upright_rotation", return_value=0),
            redirect_stdout(io.StringIO()),
        ):
            response = self.client.post(f"/api/raw/{self.raw_filename}/extract", json={"quads": quads})
            cli_paths = crop.crop_scanned_photos(input_path, cli_directory)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"photos": [path.name for path in cli_paths]})
        self.assertEqual([path.name for path in cli_paths], [
            "scan_write_failure_photo_01.jpg", "scan_write_failure_photo_03.jpg",
        ])
        for path in cli_paths:
            self.assertEqual(path.read_bytes(), (self.cropped_directory / path.name).read_bytes())


class CliCropWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.input_path = self.root / "disposable_scan.jpg"
        with Image.new("RGB", (100, 80), (80, 80, 80)) as image:
            image.save(self.input_path)
        self.output_directory = self.root / "photos_decoupees"

    def test_success_uses_cli_output_directory_numbering_and_custom_quality(self) -> None:
        output = io.StringIO()
        with (
            patch.object(crop, "detect_photo_regions", return_value=[QUAD] * 3),
            patch.object(crop, "extract_photo", side_effect=[PHOTO, None, PHOTO]),
            patch.object(cv2, "imwrite", wraps=cv2.imwrite) as imwrite,
            redirect_stdout(output),
        ):
            paths = crop.crop_scanned_photos(self.input_path, self.output_directory, jpeg_quality=87)

        expected_paths = [self.output_directory / f"disposable_scan_photo_{i:02d}.jpg" for i in (1, 3)]
        self.assertEqual(paths, expected_paths)
        self.assertEqual(set(self.output_directory.iterdir()), set(expected_paths))
        self.assertTrue(all(call.args[2] == [int(cv2.IMWRITE_JPEG_QUALITY), 87] for call in imwrite.call_args_list))
        self.assertIn("2 photo(s) extraite(s)", output.getvalue())

    def test_second_encoding_failure_raises_without_reporting_crops(self) -> None:
        real_imwrite = cv2.imwrite
        for failure in (False, cv2.error("encoder unavailable")):
            with self.subTest(failure=failure):
                calls = 0
                output = io.StringIO()

                def fail_second_write(filename: str, image: np.ndarray, parameters: list[int]) -> bool:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                    return real_imwrite(filename, image, parameters)

                with (
                    patch.object(crop, "detect_photo_regions", return_value=[QUAD] * 2),
                    patch.object(crop, "extract_photo", return_value=PHOTO),
                    patch.object(cv2, "imwrite", side_effect=fail_second_write),
                    redirect_stdout(output),
                ):
                    with self.assertRaisesRegex(OSError, "disposable_scan_photo_02.jpg"):
                        crop.crop_scanned_photos(self.input_path, self.output_directory)

                self.assertEqual(output.getvalue(), "")
                self.assertEqual(list(self.output_directory.iterdir()), [])

    def test_cli_exit_status_distinguishes_failed_encoding_from_success(self) -> None:
        program = """
import cv2
import runpy
import sys
from unittest.mock import Mock
mode, image, script = sys.argv[1:]
cv2.imwrite = Mock(return_value=False) if mode == "false" else Mock(side_effect=cv2.error("encoder unavailable"))
sys.argv = [script, image]
runpy.run_path(script, run_name="__main__")
"""
        for failure in ("false", "raise"):
            with self.subTest(failure=failure):
                result = subprocess.run(
                    [sys.executable, "-c", program, failure, str(self.input_path), crop.__file__],
                    capture_output=True, text=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("Couldn't write crop disposable_scan_photo_01.jpg", result.stderr)
                self.assertNotIn("Extrait", result.stdout)
                self.assertNotIn("Total", result.stdout)
                self.assertEqual(list(self.output_directory.iterdir()), [])

        succeeded = subprocess.run(
            [sys.executable, crop.__file__, str(self.input_path)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
        self.assertIn("1 photo(s) extraite(s)", succeeded.stdout)
        self.assertEqual(
            [path.name for path in self.output_directory.iterdir()], ["disposable_scan_photo_01.jpg"]
        )


if __name__ == "__main__":
    unittest.main()
