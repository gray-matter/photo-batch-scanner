import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path

import cv2
import numpy as np

PhotoExtractor = Callable[[np.ndarray, Sequence[Sequence[float]]], np.ndarray | None]


def extract_crop_outputs(
    image: np.ndarray,
    quads: Iterable[Sequence[Sequence[float]]],
    stem: str,
    output_directory: Path,
    jpeg_quality: int,
    *,
    extractor: PhotoExtractor,
) -> list[Path]:
    output_directory.mkdir(parents=True, exist_ok=True)

    def outputs() -> Iterator[tuple[Path, np.ndarray]]:
        for index, quad in enumerate(quads, start=1):
            photo = extractor(image, quad)
            if photo is not None:
                yield output_directory / f"{stem}_photo_{index:02d}.jpg", photo

    return write_crop_outputs(outputs(), jpeg_quality)


def write_crop_outputs(outputs: Iterable[tuple[Path, np.ndarray]], jpeg_quality: int) -> list[Path]:
    staged: list[tuple[Path, Path]] = []
    try:
        for output_path, image in outputs:
            try:
                with tempfile.NamedTemporaryFile(
                    dir=output_path.parent, prefix=f".{output_path.stem}-", suffix=".jpg", delete=False
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                    staged.append((temporary_path, output_path))
                if not cv2.imwrite(str(temporary_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]):
                    raise OSError("JPEG encoding failed")
            except Exception as exc:
                raise OSError(f"Couldn't write crop {output_path.name}: {exc}") from exc

        for temporary_path, output_path in staged:
            try:
                temporary_path.replace(output_path)
            except OSError as exc:
                raise OSError(f"Couldn't publish crop {output_path.name}: {exc}") from exc
        return [output_path for _, output_path in staged]
    finally:
        # A failed publication may already have replaced a photo; only staging files are ours to remove.
        for temporary_path, _ in staged:
            temporary_path.unlink(missing_ok=True)
