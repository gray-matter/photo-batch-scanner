import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

PhotoDirectory = Literal["raw", "cropped", "done"]


class PhotoNotFound(Exception):
    def __init__(self, directory: PhotoDirectory, filename: str) -> None:
        super().__init__(f"File not found in {directory}: {filename}")
        self.directory = directory


@dataclass(frozen=True)
class PhotoStore:
    raw_directory: Path
    cropped_directory: Path
    done_directory: Path

    def __post_init__(self) -> None:
        for directory in (self.raw_directory, self.cropped_directory, self.done_directory):
            directory.mkdir(parents=True, exist_ok=True)

    def directory(self, kind: PhotoDirectory) -> Path:
        return {
            "raw": self.raw_directory,
            "cropped": self.cropped_directory,
            "done": self.done_directory,
        }[kind]

    def path(self, kind: PhotoDirectory, filename: str) -> Path:
        directory = self.directory(kind).resolve()
        path = (directory / filename).resolve()
        if path.parent != directory or not path.is_file():
            raise PhotoNotFound(kind, filename)
        return path

    def tag_path(self, filename: str) -> Path:
        kinds: tuple[PhotoDirectory, ...] = ("cropped", "done")
        for kind in kinds:
            try:
                return self.path(kind, filename)
            except PhotoNotFound:
                continue
        raise PhotoNotFound("cropped", filename)

    def photos(self, kind: PhotoDirectory) -> list[Path]:
        files: list[Path] = []
        for path in sorted(self.directory(kind).glob("*.jpg"), reverse=True):
            try:
                self.path(kind, path.name)
            except PhotoNotFound:
                continue
            files.append(path)
        return files

    def delete(self, kind: PhotoDirectory, filename: str) -> None:
        self.path(kind, filename).unlink()

    def move(self, source: PhotoDirectory, destination: PhotoDirectory, filename: str) -> None:
        path = self.path(source, filename)
        shutil.move(str(path), str(self.directory(destination) / path.name))

    def mark_listed_done(self, filenames: Iterable[str]) -> None:
        paths: list[Path] = []
        for filename in filenames:
            self.path("cropped", filename)
            paths.append(self.cropped_directory / filename)
        for path in paths:
            # A target moved earlier in this batch can leave an alias dangling until it too is moved.
            shutil.move(str(path), str(self.done_directory / path.name))

    def rotate(self, filename: str, degrees: int) -> None:
        path = self.path("cropped", filename)
        temporary_path: Path | None = None
        try:
            with Image.open(path) as source:
                with ImageOps.exif_transpose(source) as oriented:
                    exif = oriented.getexif()
                    exif[274] = 1
                    with (
                        oriented.rotate(degrees, expand=True) as rotated,
                        tempfile.NamedTemporaryFile(
                            dir=path.parent, prefix=f".{path.stem}-", suffix=".jpg", delete=False
                        ) as temporary_file,
                    ):
                        temporary_path = Path(temporary_file.name)
                        rotated.save(
                            temporary_file, format="JPEG", quality=92, exif=exif,
                            icc_profile=source.info.get("icc_profile"),
                        )
            temporary_path.replace(path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
