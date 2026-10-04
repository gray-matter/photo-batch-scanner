import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from photo_store import PhotoNotFound, PhotoStore


class PhotoStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name).resolve()
        self.store = PhotoStore(self.root / "raw", self.root / "cropped", self.root / "done")
        store_patch = patch.object(app, "photo_store", self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)

    def test_lookup_rejects_traversal_and_outside_symlinks_in_every_directory(self) -> None:
        outside = self.root / "outside.jpg"
        outside.write_bytes(b"outside")
        for kind in ("raw", "cropped", "done"):
            with self.subTest(directory=kind):
                directory = self.store.directory(kind)
                (directory / "link.jpg").symlink_to(outside)
                (directory / "nested").mkdir()
                (directory / "nested" / "photo.jpg").write_bytes(b"nested")
                for filename in ("../outside.jpg", str(outside), "link.jpg", "nested/photo.jpg", "missing.jpg"):
                    with self.assertRaises(PhotoNotFound):
                        self.store.path(kind, filename)
                self.assertEqual(self.store.photos(kind), [])
        self.assertEqual(outside.read_bytes(), b"outside")
        for filename in ("../outside.jpg", "link.jpg"):
            with self.subTest(operation="delete", filename=filename), self.assertRaises(PhotoNotFound):
                self.store.delete("cropped", filename)
            with self.subTest(operation="move", filename=filename), self.assertRaises(PhotoNotFound):
                self.store.move("cropped", "done", filename)
            with self.subTest(operation="bulk move", filename=filename), self.assertRaises(PhotoNotFound):
                self.store.mark_listed_done([filename])
            with self.subTest(operation="rotate", filename=filename), self.assertRaises(PhotoNotFound):
                self.store.rotate(filename, 90)
            with self.subTest(operation="tag", filename=filename), self.assertRaises(PhotoNotFound):
                self.store.tag_path(filename)
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_photo_lists_keep_reverse_filename_order_and_internal_alias_names(self) -> None:
        for kind, endpoint in (("cropped", "/api/photos"), ("done", "/api/photos/done")):
            with self.subTest(directory=kind):
                directory = self.store.directory(kind)
                for filename in ("scan_02.jpg", "scan_10.jpg", "scan_01.jpg", "ignored.png"):
                    (directory / filename).write_bytes(b"fixture")
                (directory / "zz_alias.jpg").symlink_to("scan_01.jpg")
                expected = ["zz_alias.jpg", "scan_10.jpg", "scan_02.jpg", "scan_01.jpg"]
                with patch.object(app, "read_tag_status", return_value={"scan_10.jpg": {"gps": True, "time": False}}):
                    response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"photos": [
                    {"filename": name, "gps": name == "scan_10.jpg", "time": False}
                    for name in expected
                ]})

    def test_not_found_errors_keep_endpoint_specific_messages(self) -> None:
        requests = [
            ("get", "/api/photos/missing.jpg", None, "Photo not found"),
            ("delete", "/api/photos/missing.jpg", None, "Photo not found"),
            ("delete", "/api/photos/done/missing.jpg", None, "Photo not found"),
            ("post", "/api/photos/missing.jpg/done", None, "Photo not found"),
            ("post", "/api/photos/done/missing.jpg/restore", None, "Photo not found"),
            ("post", "/api/photos/missing.jpg/rotate", {"degrees": 90}, "Photo not found"),
            ("post", "/api/raw/missing.jpg/detect", {}, "Scan not found"),
            ("post", "/api/raw/missing.jpg/discard", None, "Scan not found"),
            ("post", "/api/raw/missing.jpg/extract", {"quads": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}, "Scan not found"),
            ("post", "/api/tag", {"filenames": ["missing.jpg"], "date": "2000-01-01"}, "Photo not found"),
        ]
        for method, endpoint, body, detail in requests:
            with self.subTest(endpoint=endpoint):
                response = self.client.request(method, endpoint, json=body)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json(), {"detail": detail})

    def test_done_and_restore_replace_same_name_and_tag_lookup_prefers_pending(self) -> None:
        pending = self.store.cropped_directory / "photo.jpg"
        done = self.store.done_directory / "photo.jpg"
        pending.write_bytes(b"new photo")
        done.write_bytes(b"old photo")
        self.assertEqual(self.store.tag_path("photo.jpg"), pending)
        self.assertEqual(self.client.post("/api/photos/photo.jpg/done").json(), {"ok": True})
        self.assertFalse(pending.exists())
        self.assertEqual(done.read_bytes(), b"new photo")
        self.assertEqual(self.store.tag_path("photo.jpg"), done)
        pending.write_bytes(b"replacement")
        self.assertEqual(self.client.post("/api/photos/done/photo.jpg/restore").json(), {"ok": True})
        self.assertEqual(pending.read_bytes(), b"new photo")
        self.assertFalse(done.exists())
        self.assertEqual(self.client.delete("/api/photos/photo.jpg").json(), {"ok": True})
        self.assertFalse(pending.exists())

    def test_bulk_completion_moves_alias_while_individual_completion_moves_resolved_target(self) -> None:
        target = self.store.cropped_directory / "target.jpg"
        alias = self.store.cropped_directory / "alias.jpg"
        target.write_bytes(b"fixture")
        alias.symlink_to("target.jpg")
        with patch.object(app, "read_tag_status", return_value={"alias.jpg": {"gps": True, "time": True}}):
            response = self.client.post("/api/photos/mark-tagged-done")
        self.assertEqual(response.json(), {"photos": ["alias.jpg"]})
        done_alias = self.store.done_directory / "alias.jpg"
        self.assertTrue(done_alias.is_symlink())
        self.assertFalse(alias.is_symlink())
        self.assertTrue(target.is_file())
        done_alias.unlink()

        alias.symlink_to("target.jpg")
        self.assertEqual(self.client.post("/api/photos/alias.jpg/done").json(), {"ok": True})
        self.assertTrue(alias.is_symlink())
        self.assertFalse(target.exists())
        self.assertEqual((self.store.done_directory / "target.jpg").read_bytes(), b"fixture")
        self.assertEqual(self.client.post("/api/photos/done/target.jpg/restore").json(), {"ok": True})
        self.assertEqual(alias.read_bytes(), b"fixture")

        with patch.object(app, "read_tag_status", return_value={
            "alias.jpg": {"gps": True, "time": True},
            "target.jpg": {"gps": True, "time": True},
        }):
            response = self.client.post("/api/photos/mark-tagged-done")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"photos": ["target.jpg", "alias.jpg"]})
        self.assertFalse(target.exists())
        self.assertFalse(alias.is_symlink())
        self.assertTrue(done_alias.is_symlink())
        self.assertEqual(done_alias.read_bytes(), b"fixture")


if __name__ == "__main__":
    unittest.main()
