import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

import crop


class ObservedLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.second_attempted = threading.Event()

    def __enter__(self) -> "ObservedLock":
        if threading.current_thread().name == "second-face-score":
            self.second_attempted.set()
        self._lock.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self._lock.release()


class BlockingDetector:
    def __init__(self) -> None:
        self.first_size_set = threading.Event()
        self.release_first_detect = threading.Event()
        self.calls: list[tuple[str, tuple[int, int] | None]] = []
        self.input_size: tuple[int, int] | None = None

    def setInputSize(self, size: tuple[int, int]) -> None:
        self.input_size = size
        self.calls.append(("size", size))
        if size == (20, 10):
            self.first_size_set.set()

    def detect(self, _image: np.ndarray) -> tuple[None, None]:
        size = self.input_size
        self.calls.append(("detect", size))
        if size == (20, 10):
            if not self.release_first_detect.wait(timeout=5):
                raise TimeoutError("test did not release first detection")
        return None, None


class FaceDetectorLockTests(unittest.TestCase):
    def test_input_size_and_detection_are_serialized_across_image_sizes(self) -> None:
        detector = BlockingDetector()
        lock = ObservedLock()
        first = threading.Thread(
            name="first-face-score", target=crop._face_score, args=(np.zeros((10, 20, 3), dtype=np.uint8),)
        )
        second = threading.Thread(
            name="second-face-score", target=crop._face_score, args=(np.zeros((30, 40, 3), dtype=np.uint8),)
        )
        with patch.object(crop, "_FACE_DETECTOR", detector), patch.object(crop, "_FACE_DETECTOR_LOCK", lock):
            first.start()
            self.assertTrue(detector.first_size_set.wait(timeout=5))
            second.start()
            try:
                self.assertTrue(lock.second_attempted.wait(timeout=5))
                self.assertEqual(detector.calls, [("size", (20, 10)), ("detect", (20, 10))])
            finally:
                detector.release_first_detect.set()
                first.join(timeout=5)
                second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(
            detector.calls,
            [("size", (20, 10)), ("detect", (20, 10)), ("size", (40, 30)), ("detect", (40, 30))],
        )

    def test_detection_exception_releases_lock_for_next_score(self) -> None:
        detector = Mock()
        detector.detect.side_effect = [RuntimeError("detector failed"), (None, None)]
        with patch.object(crop, "_FACE_DETECTOR", detector):
            with self.assertRaisesRegex(RuntimeError, "detector failed"):
                crop._face_score(np.zeros((10, 20, 3), dtype=np.uint8))
            lock_acquired = crop._FACE_DETECTOR_LOCK.acquire(blocking=False)
            self.assertTrue(lock_acquired, "detector lock remained held after detection raised")
            crop._FACE_DETECTOR_LOCK.release()
            self.assertEqual(crop._face_score(np.zeros((30, 40, 3), dtype=np.uint8)), (0, 0.0))

        self.assertEqual(detector.setInputSize.call_count, 2)
        self.assertEqual(detector.detect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
