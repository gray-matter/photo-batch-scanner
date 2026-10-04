import sys
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps

_FACE_DETECTOR = cv2.FaceDetectorYN_create(
    str(Path(__file__).parent / "data" / "face_detection_yunet.onnx"), "", (320, 320), score_threshold=0.6
)
_HIGH_CONFIDENCE = 0.85


def _face_score(img_bgr: np.ndarray) -> tuple[int, float]:
    """Score de présence de visage(s) dans une image couleur : nombre de
    détections à haute confiance, puis la meilleure confiance en
    départage. Un score par aire serait trompeur : une unique détection
    fantôme de faible confiance mais de grande taille (ex. un rocher ou une
    texture) peut l'emporter sur plusieurs vrais visages plus petits."""
    h, w = img_bgr.shape[:2]
    _FACE_DETECTOR.setInputSize((w, h))
    _, faces = _FACE_DETECTOR.detect(img_bgr)
    if faces is None:
        return (0, 0.0)
    confidences = [f[-1] for f in faces]
    return (sum(1 for c in confidences if c >= _HIGH_CONFIDENCE), max(confidences))


def detect_upright_rotation(img: np.ndarray) -> int:
    """Devine, parmi les 4 orientations cardinales, celle qui redresse le
    tirage, en cherchant l'orientation où des visages sont détectés avec le
    plus de confiance. Un tirage a été déjà déskewé mais son orientation
    (portrait vs paysage à l'envers, tête en bas...) n'est pas connue par
    ailleurs. Retourne 0 (aucune rotation) si aucun visage n'est trouvé,
    faute de signal fiable."""
    best_angle, best_score = 0, (0, 0.0)
    current = img
    for angle in (0, 90, 180, 270):
        score = _face_score(current)
        if score > best_score:
            best_score, best_angle = score, angle
        current = cv2.rotate(current, cv2.ROTATE_90_CLOCKWISE)
    return best_angle if best_score > (0, 0.0) else 0


def find_background_bands(density: np.ndarray, gap_frac: float, min_width: int) -> list[tuple[int, int]]:
    """Trouve les bandes (lignes ou colonnes) presque vides de contenu sombre,
    c'est-à-dire les gouttières de fond entre deux tirages."""
    is_gap = density < gap_frac
    bands = []
    start = None
    for i, gap in enumerate(is_gap):
        if gap and start is None:
            start = i
        elif not gap and start is not None:
            if i - start >= min_width:
                bands.append((start, i - 1))
            start = None
    if start is not None and len(is_gap) - start >= min_width:
        bands.append((start, len(is_gap) - 1))
    return bands


def order_quad_points(pts: np.ndarray) -> np.ndarray:
    """Ordonne 4 points en (haut-gauche, haut-droit, bas-droit, bas-gauche),
    quelle que soit leur inclinaison, pour que la transformation de
    perspective associe chaque coin du tirage au bon coin du rectangle de
    sortie."""
    s = pts.sum(axis=1)
    diff = pts[:, 0] - pts[:, 1]
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmax(diff)]
    bl = pts[np.argmin(diff)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def load_scan_image(image_path: Path) -> np.ndarray:
    """Charge un scan en BGR, en corrigeant l'orientation EXIF si présente."""
    pil_img = Image.open(image_path)
    pil_img = ImageOps.exif_transpose(pil_img)
    img_rgb = np.array(pil_img.convert("RGB"))
    return cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)


def _restore_clipped_edges(
    quad: np.ndarray,
    contour: np.ndarray,
    foreground: np.ndarray,
    border_x: int,
    border_y: int,
) -> np.ndarray:
    """Restore print edges hidden by the scanner-border and gap masks."""
    img_h, img_w = foreground.shape
    x, y, width, height = cv2.boundingRect(contour)
    right, bottom = x + width - 1, y + height - 1
    restored = quad.copy()

    # Only restore a clipped side when the original scan still contains
    # substantial print content in the strip that the mask removed.
    if y <= border_y + 2 and foreground[:border_y, x:x + width].mean() > 0.5:
        restored[[0, 1], 1] = 0
    if bottom >= img_h - border_y - 3 and foreground[-border_y:, x:x + width].mean() > 0.5:
        restored[[2, 3], 1] = img_h - 1
    if x <= border_x + 2 and foreground[y:y + height, :border_x].mean() > 0.5:
        restored[[0, 3], 0] = 0
    if right >= img_w - border_x - 3 and foreground[y:y + height, -border_x:].mean() > 0.5:
        restored[[1, 2], 0] = img_w - 1

    return restored


def detect_photo_regions(
    img: np.ndarray, threshold_val: int = 200, *, expected_count: int | None = None
) -> list[list[list[float]]]:
    if expected_count is not None and not 1 <= expected_count <= 20:
        raise ValueError("Expected photo count must be between 1 and 20")
    quads = _detect_photo_regions(img, threshold_val, expected_count)
    if expected_count is None or len(quads) == expected_count:
        return quads
    for threshold in (max(0, threshold_val - 20), min(255, threshold_val + 20)):
        candidates = _detect_photo_regions(img, threshold, expected_count)
        if abs(len(candidates) - expected_count) < abs(len(quads) - expected_count):
            quads = candidates
        if len(quads) == expected_count:
            break
    return quads


def _detect_photo_regions(
    img: np.ndarray, threshold_val: int, expected_count: int | None
) -> list[list[list[float]]]:
    """Detect scanner-bed prints and approximate each boundary with four corners."""
    img_h, img_w = img.shape[:2]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Le fond du scanner étant blanc (~255), on isole tout ce qui est plus sombre
    _, thresh = cv2.threshold(blurred, threshold_val, 255, cv2.THRESH_BINARY_INV)
    foreground = thresh > 0

    # L'ombre du bord de la vitre du scanner est parfois plus sombre que le
    # seuil : on l'ignore pour éviter qu'elle ne relie deux tirages entre eux.
    border_x, border_y = max(1, int(img_w * 0.005)), max(1, int(img_h * 0.005))
    thresh[:border_y, :] = 0
    thresh[-border_y:, :] = 0
    thresh[:, :border_x] = 0
    thresh[:, -border_x:] = 0

    # Quand deux tirages sont posés bord à bord, leur contenu peut se toucher
    # localement et les souder en un seul contour. On détecte les gouttières
    # de fond qui traversent (presque) toute la largeur/hauteur et on les
    # force à zéro. On élargit seulement les gouttières plus étroites que le
    # noyau de fermeture, sans rogner inutilement les tirages voisins.
    kernel_size = 7
    mask = (thresh > 0).astype(np.uint8)
    row_density = mask.sum(axis=1) / img_w
    col_density = mask.sum(axis=0) / img_h
    gap_frac = 0.03
    min_gap_width = 5

    for s, e in find_background_bands(row_density, gap_frac, min_gap_width):
        padding = max(0, (kernel_size - (e - s + 1) + 1) // 2)
        s2, e2 = max(0, s - padding), min(img_h, e + 1 + padding)
        thresh[s2:e2, :] = 0
    for s, e in find_background_bands(col_density, gap_frac, min_gap_width):
        padding = max(0, (kernel_size - (e - s + 1) + 1) // 2)
        s2, e2 = max(0, s - padding), min(img_w, e + 1 + padding)
        thresh[:, s2:e2] = 0

    # Nettoyage du masque (fermeture des petits trous)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Seuil de surface minimale : au moins 1,5 % de la surface totale de la vitre
    min_area = (img_w * img_h) * 0.015

    if expected_count is not None:
        thresh = _split_local_gaps(thresh, expected_count, min_area)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    quads: list[list[list[float]]] = []
    for cnt in contours:
        if cv2.contourArea(cnt) < min_area:
            continue
        perimeter = cv2.arcLength(cnt, True)
        epsilon = max(2.0, perimeter * 0.01)
        polygon = cv2.approxPolyDP(cnt, epsilon, True)
        while len(polygon) > 4:
            epsilon *= 1.5
            polygon = cv2.approxPolyDP(cnt, epsilon, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            polygon = cv2.boxPoints(cv2.minAreaRect(cnt)).reshape(-1, 1, 2)
        quad = order_quad_points(polygon.reshape(-1, 2))
        quad = _restore_clipped_edges(quad, cnt, foreground, border_x, border_y)
        if len(np.unique(quad, axis=0)) == 4:
            quads.append(quad.tolist())

    return quads


def _split_local_gaps(mask: np.ndarray, expected_count: int, min_area: float) -> np.ndarray:
    """Only cut gutters supported locally; a count hint must not invent a grid."""
    while True:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        photos = [contour for contour in contours if cv2.contourArea(contour) >= min_area]
        if len(photos) >= expected_count:
            return mask
        best_score = float("inf")
        best_mask: np.ndarray | None = None
        for contour in photos:
            x, y, width, height = cv2.boundingRect(contour)
            roi = mask[y:y + height, x:x + width] > 0
            for axis in (0, 1):
                density = roi.mean(axis=axis)
                length = len(density)
                for start, end in find_background_bands(density, 0.15, 5):
                    if start < length * 0.2 or end > length * 0.8:
                        continue
                    candidate = mask.copy()
                    if axis == 0:
                        candidate[y:y + height, x + start:x + end + 1] = 0
                    else:
                        candidate[y + start:y + end + 1, x:x + width] = 0
                    split_contours, _ = cv2.findContours(candidate, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    count = sum(cv2.contourArea(part) >= min_area for part in split_contours)
                    if not len(photos) < count <= expected_count:
                        continue
                    score = float(density[start:end + 1].mean())
                    if score < best_score:
                        best_score, best_mask = score, candidate
        if best_mask is None:
            return mask
        mask = best_mask


def extract_photo(img: np.ndarray, quad: Sequence[Sequence[float]]) -> np.ndarray | None:
    """Rectify a four-corner selection by perspective, then detect upright rotation."""
    pts = np.array(quad, dtype=np.float32)
    if pts.ndim != 2 or pts.shape != (4, 2) or not np.isfinite(pts).all():
        return None

    pts = order_quad_points(pts)
    tl, tr, br, bl = pts
    width = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    height = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))
    if width < 1 or height < 1:
        return None

    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(pts, dst)
    crop = cv2.warpPerspective(img, matrix, (width, height))

    rotation = detect_upright_rotation(crop)
    if rotation == 90:
        crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        crop = cv2.rotate(crop, cv2.ROTATE_180)
    elif rotation == 270:
        crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)

    return crop


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


def crop_scanned_photos(image_path: Path, output_dir: Path, threshold_val: int = 200, jpeg_quality: int = 92) -> list[Path]:
    """Détecte et extrait automatiquement les tirages d'un scan, sans étape
    de validation : utilisé par le CLI autonome (voir `__main__`)."""
    img = load_scan_image(image_path)
    quads = detect_photo_regions(img, threshold_val)

    output_dir.mkdir(parents=True, exist_ok=True)

    def outputs() -> Iterator[tuple[Path, np.ndarray]]:
        for count, quad in enumerate(quads, start=1):
            crop = extract_photo(img, quad)
            if crop is not None:
                yield output_dir / f"{image_path.stem}_photo_{count:02d}.jpg", crop

    output_paths = write_crop_outputs(outputs(), jpeg_quality)
    for out_file in output_paths:
        print(f"  -> Extrait : {out_file.name}")

    print(f"Total pour {image_path.name} : {len(output_paths)} photo(s) extraite(s).")
    return output_paths


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python crop_photos.py <chemin_image>")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    out_dir = input_file.parent / "photos_decoupees"
    try:
        crop_scanned_photos(input_file, out_dir)
    except OSError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        sys.exit(1)
