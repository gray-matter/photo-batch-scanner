"""Process-local scan workers and the queue of reviews awaiting user input."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import NotRequired, Protocol, TypedDict

import httpx
import numpy as np

from escl import ProgressCallback, ScannerInfo, ScannerNotFound

Quads = list[list[list[float]]]


class PhotoDetector(Protocol):
    def __call__(self, image: np.ndarray, *, expected_count: int | None = None) -> Quads: ...


class ScanTransfer(Protocol):
    def __call__(
        self, scanner: ScannerInfo, output_path: str, *, on_progress: ProgressCallback
    ) -> None: ...


class ReviewSnapshot(TypedDict):
    raw: str
    quads: Quads
    expected_count: NotRequired[int | None]


class DetectionResult(TypedDict):
    raw: str
    quads: Quads
    expected_count: int | None


class ScanStatus(TypedDict):
    stage: str
    done: int | None
    total: int | None
    error: str | None
    raw: str | None
    quads: Quads | None
    pending_review: ReviewSnapshot | None


@dataclass
class ScanReview:
    raw: str
    quads: Quads
    expected_count: int | None = None
    redetected: bool = False

    def snapshot(self) -> ReviewSnapshot:
        result: ReviewSnapshot = {"raw": self.raw, "quads": self.quads}
        if self.redetected:
            result["expected_count"] = self.expected_count
        return result


@dataclass
class ScanState:
    stage: str = "idle"
    done: int | None = None
    total: int | None = None
    error: str | None = None
    raw: str | None = None
    quads: Quads | None = None


class ScanCoordinator:
    def __init__(
        self,
        *,
        discover: Callable[[], ScannerInfo],
        transfer: ScanTransfer,
        load_image: Callable[[Path], np.ndarray],
        detect: PhotoDetector,
        raw_directory: Path,
    ) -> None:
        self._discover = discover
        self._transfer = transfer
        self._load_image = load_image
        self._detect = detect
        self._raw_directory = raw_directory
        self._hardware_lock = Lock()
        self._state_lock = Lock()
        self._state = ScanState()
        self._reviews: list[ScanReview] = []

    def start(self) -> bool:
        if not self._hardware_lock.acquire(blocking=False):
            return False
        try:
            Thread(target=self._run_scan_and_release, daemon=True).start()
        except BaseException:
            self._hardware_lock.release()
            raise
        return True

    def status(self) -> ScanStatus:
        with self._state_lock:
            return {
                "stage": self._state.stage,
                "done": self._state.done,
                "total": self._state.total,
                "error": self._state.error,
                "raw": self._state.raw,
                "quads": self._state.quads,
                "pending_review": self._reviews[0].snapshot() if self._reviews else None,
            }

    def prepare_review(self, raw_path: Path) -> None:
        try:
            image = self._load_image(raw_path)
            quads = self._detect(image)
        except Exception as error:
            with self._state_lock:
                if self._state.stage == "detecting":
                    self._state.stage = "error"
                    self._state.error = f"Couldn't prepare scan review: {error}"
            return

        with self._state_lock:
            review = ScanReview(raw=raw_path.name, quads=quads)
            self._reviews.append(review)
            self._reviews.sort(key=lambda item: item.raw)
            if not self._hardware_lock.locked():
                self._show_review(review)

    def redetect(
        self, raw_path: Path, *, filename: str, expected_count: int | None
    ) -> DetectionResult:
        quads = self._detect(self._load_image(raw_path), expected_count=expected_count)
        with self._state_lock:
            for review in self._reviews:
                if review.raw == filename:
                    review.quads = quads
                    review.expected_count = expected_count
                    review.redetected = True
            if self._state.stage == "review" and self._state.raw == filename:
                self._state.quads = quads
        return {"raw": filename, "quads": quads, "expected_count": expected_count}

    def remove_review(self, filename: str) -> None:
        with self._state_lock:
            self._reviews = [review for review in self._reviews if review.raw != filename]
            if self._state.stage == "review":
                if self._reviews:
                    self._show_review(self._reviews[0])
                else:
                    self._state = ScanState()

    def _show_review(self, review: ScanReview) -> None:
        self._state.stage = "review"
        self._state.raw = review.raw
        self._state.quads = review.quads

    def _on_progress(self, stage: str, done: int | None, total: int | None) -> None:
        with self._state_lock:
            self._state.stage = stage
            self._state.done = done
            self._state.total = total

    def _run_scan(self) -> None:
        with self._state_lock:
            self._state = ScanState(stage="discovering")
        try:
            scanner = self._discover()
        except ScannerNotFound as error:
            with self._state_lock:
                self._state.stage = "error"
                self._state.error = str(error)
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        raw_path = self._raw_directory / f"scan_{stamp}.jpg"
        try:
            self._transfer(scanner, str(raw_path), on_progress=self._on_progress)
        except httpx.HTTPError as error:
            with self._state_lock:
                self._state.stage = "error"
                self._state.error = f"Scan failed: {error}"
            return

        self._on_progress("detecting", None, None)
        Thread(target=self.prepare_review, args=(raw_path,), daemon=True).start()

    def _run_scan_and_release(self) -> None:
        try:
            self._run_scan()
        finally:
            self._hardware_lock.release()
            with self._state_lock:
                if self._state.stage == "detecting" and self._reviews:
                    self._show_review(self._reviews[0])
