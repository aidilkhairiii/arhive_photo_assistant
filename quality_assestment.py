"""
Module 2 — Image Quality Assessment
===================================

Responsibilities:
    * Blur detection
    * Brightness analysis
    * Contrast analysis

This module consumes the metadata JSON produced by preprocessing.py. It reads
`processed_gray_path`, crops to `analysis.content_box`, then computes:

{
  "blur": 75,
  "brightness": 68,
  "contrast": 52
}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


BLUR_REFERENCE_VARIANCE = 1000.0


def _score(value: float) -> int:
    """Clamp a numeric value into a 0-100 integer score."""
    return int(round(max(0.0, min(100.0, value))))


def _to_uint8(image: np.ndarray) -> np.ndarray:
    """Convert common image arrays to uint8 in the 0-255 range."""
    if image.dtype == np.uint8:
        return image

    image = image.astype(np.float32)
    if image.size and np.nanmax(image) <= 1.0:
        image = image * 255.0

    return np.clip(np.nan_to_num(image), 0, 255).astype(np.uint8)


def _as_grayscale(image: np.ndarray) -> np.ndarray:
    """Return a single-channel grayscale image."""
    image = np.asarray(image)

    if image.ndim == 2:
        return _to_uint8(image)

    if image.ndim == 3:
        if image.shape[2] == 1:
            return _to_uint8(image[:, :, 0])
        if image.shape[2] == 3:
            return _to_uint8(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
        if image.shape[2] == 4:
            return _to_uint8(cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY))

    raise ValueError("Image must be a grayscale, BGR, or BGRA array.")


def _resolve_path(
    path: str | Path,
    base_dir: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> Path:
    """Resolve relative paths from preprocessing's metadata contract."""
    path = Path(path)
    if path.is_absolute():
        return path

    search_roots: list[Path] = []
    if base_dir is not None:
        search_roots.append(Path(base_dir))
    search_roots.append(Path.cwd())
    if metadata_path is not None:
        metadata_parent = Path(metadata_path).resolve().parent
        search_roots.extend([metadata_parent, metadata_parent.parent])

    for root in search_roots:
        candidate = root / path
        if candidate.exists():
            return candidate

    return (search_roots[0] / path) if search_roots else path


def load_grayscale_image(
    image_path: str | Path,
    base_dir: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> np.ndarray:
    """Load a grayscale image from disk."""
    resolved_path = _resolve_path(image_path, base_dir, metadata_path)
    image = cv2.imread(str(resolved_path), cv2.IMREAD_GRAYSCALE)

    if image is None:
        raise ValueError(f"Could not read grayscale image: {resolved_path}")

    return image


def crop_to_content_box(
    image: np.ndarray,
    content_box: list[int] | tuple[int, int, int, int] | None,
) -> np.ndarray:
    """Crop to the real photo area and ignore preprocessing's black padding."""
    image = _as_grayscale(image)

    if content_box is None:
        return image

    if len(content_box) != 4:
        raise ValueError("content_box must be [x, y, width, height].")

    x, y, width, height = [int(value) for value in content_box]
    img_h, img_w = image.shape[:2]

    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(img_w, x + width)
    y2 = min(img_h, y + height)

    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid content_box for image size {img_w}x{img_h}: {content_box}")

    return image[y1:y2, x1:x2]


def _image_region_from_metadata(
    metadata: dict[str, Any],
    base_dir: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> np.ndarray:
    """Load the preprocessed grayscale image and crop it to content_box."""
    if metadata.get("status") != "ok":
        raise ValueError(f"Skipping image because preprocessing status is {metadata.get('status')!r}.")

    gray_path = metadata.get("processed_gray_path")
    if not gray_path:
        raise KeyError("Metadata is missing processed_gray_path.")

    gray = load_grayscale_image(gray_path, base_dir=base_dir, metadata_path=metadata_path)
    content_box = metadata.get("analysis", {}).get("content_box")
    return crop_to_content_box(gray, content_box)


def calculate_blur_score(image: np.ndarray) -> int:
    """
    Calculate sharpness using variance of the Laplacian.

    Returns a 0-100 score. Higher means sharper / less blurry.
    """
    gray = _as_grayscale(image)
    laplacian_variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    return _score((laplacian_variance / BLUR_REFERENCE_VARIANCE) * 100)


def calculate_brightness(image: np.ndarray) -> int:
    """
    Calculate average brightness.

    Returns a 0-100 score. Higher means brighter.
    """
    gray = _as_grayscale(image)
    return _score((float(gray.mean()) / 255.0) * 100)


def calculate_contrast(image: np.ndarray) -> int:
    """
    Calculate contrast using grayscale standard deviation.

    Returns a 0-100 score. Higher means stronger contrast.
    """
    gray = _as_grayscale(image)
    return _score((float(gray.std()) / 127.5) * 100)


def assess_quality(
    metadata_or_image: dict[str, Any] | str | Path | np.ndarray,
    base_dir: str | Path | None = None,
) -> dict[str, int]:
    """
    Return blur, brightness, and contrast scores.

    Preferred input is the metadata dict from preprocessing.py. For convenience,
    this also accepts a metadata JSON path, image path, or already-loaded image.
    """
    if isinstance(metadata_or_image, dict):
        image = _image_region_from_metadata(metadata_or_image, base_dir=base_dir)
    elif isinstance(metadata_or_image, (str, Path)):
        input_path = Path(metadata_or_image)
        if input_path.suffix.lower() == ".json":
            image = _image_region_from_metadata_file(input_path, base_dir=base_dir)
        else:
            image = load_grayscale_image(input_path, base_dir=base_dir)
    else:
        image = _as_grayscale(metadata_or_image)

    return {
        "sharpness": calculate_blur_score(image),
        "brightness": calculate_brightness(image),
        "contrast": calculate_contrast(image),
    }


def _image_region_from_metadata_file(
    metadata_path: str | Path,
    base_dir: str | Path | None = None,
) -> np.ndarray:
    """Load a metadata JSON file and return the cropped analysis image."""
    metadata_path = Path(metadata_path)
    with open(metadata_path, "r", encoding="utf-8") as file:
        metadata = json.load(file)

    return _image_region_from_metadata(
        metadata,
        base_dir=base_dir,
        metadata_path=metadata_path,
    )


def update_metadata_with_quality(
    metadata: dict[str, Any],
    base_dir: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> dict[str, Any]:
    """Add this module's quality result into a metadata dictionary."""
    image = _image_region_from_metadata(
        metadata,
        base_dir=base_dir,
        metadata_path=metadata_path,
    )
    metadata["quality"] = {
        "blur": calculate_blur_score(image),
        "brightness": calculate_brightness(image),
        "contrast": calculate_contrast(image),
    }
    return metadata


def process_metadata_file(
    metadata_path: str | Path,
    write_back: bool = True,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Read one preprocessing JSON file, add quality scores, and optionally save it."""
    metadata_path = Path(metadata_path)
    with open(metadata_path, "r", encoding="utf-8") as file:
        metadata = json.load(file)

    if metadata.get("status") != "ok":
        return metadata

    metadata = update_metadata_with_quality(
        metadata,
        base_dir=base_dir,
        metadata_path=metadata_path,
    )

    if write_back:
        with open(metadata_path, "w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=2)

    return metadata


def process_metadata_folder(
    metadata_dir: str | Path = "outputs",
    write_back: bool = True,
    base_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Run Module 2 for every metadata JSON file in a folder."""
    metadata_dir = Path(metadata_dir)
    results: list[dict[str, Any]] = []

    for metadata_path in sorted(metadata_dir.glob("*.json")):
        metadata = process_metadata_file(metadata_path, write_back=write_back, base_dir=base_dir)
        results.append(metadata)

        if metadata.get("status") == "ok":
            print(f"[ok]    {metadata['image_id']}  ->  {metadata['quality']}")
        else:
            print(f"[skip]  {metadata.get('image_id', metadata_path.stem)}")

    print(f"\nDone. Quality assessed for {sum(1 for item in results if 'quality' in item)}/{len(results)} images.")
    return results


def assess_image_quality(
    metadata_or_image: dict[str, Any] | str | Path | np.ndarray,
    base_dir: str | Path | None = None,
) -> dict[str, int]:
    """Backward-compatible alias for assess_quality()."""
    return assess_quality(metadata_or_image, base_dir=base_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Module 2 — image quality assessment."
    )
    parser.add_argument(
        "--meta",
        default="outputs",
        help="Metadata JSON file or folder from preprocessing.py (default: outputs).",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help="Project root for resolving relative paths, if needed.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print quality scores without writing them back to JSON.",
    )
    args = parser.parse_args()

    metadata_path = Path(args.meta)
    if metadata_path.is_dir():
        process_metadata_folder(
            metadata_path,
            write_back=not args.no_write,
            base_dir=args.base_dir,
        )
    else:
        metadata = process_metadata_file(
            metadata_path,
            write_back=not args.no_write,
            base_dir=args.base_dir,
        )
        if metadata.get("status") == "ok":
            print(metadata["quality"])
        else:
            print(f"[skip] {metadata.get('image_id', metadata_path.stem)}")


if __name__ == "__main__":
    main()
