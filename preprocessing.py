"""
Module 1 — Image Upload & Preprocessing
========================================

Responsibilities:
    * Image upload (load from disk / a given path)
    * Resize image (to 512x512)
    * Grayscale conversion
    * Noise reduction (denoise)
    * Image normalization
    * Emit a metadata JSON contract for downstream modules

This module is the FIRST stage of the pipeline. Modules 2 (quality),
3 (fading/tags) and 4 (report) consume what we produce here.

Design decisions (documented for the team):
    1. Target size is 512x512. By default we PRESERVE aspect ratio and pad
       (letterbox) so the image is not distorted — distortion would corrupt
       Module 2's blur/contrast metrics. Pass keep_aspect=False for a plain
       stretch to 512x512 if you really want it.
    2. We DO NOT throw away the original. For every image we output:
         - the resized COLOR image  -> needed by Module 3 (histogram/fading)
         - the resized GRAYSCALE image (denoised) -> handy for Module 2 (blur)
       Both, plus the original path, are recorded in the metadata JSON so
       each downstream module picks whichever it needs.
    3. Normalization produces a float32 array in [0, 1]. That can't be stored
       as a normal image, so it is computed in-memory and (optionally) saved
       as a .npy file. The saved PNGs stay uint8 so they're viewable.

Output contract (one JSON file per image in the --meta directory):
{
  "image_id": "img_001",
  "status": "ok",
  "original_path": "data/raw/img_001.jpg",
  "processed_color_path": "data/processed/img_001_color.png",
  "processed_gray_path":  "data/processed/img_001_gray.png",
  "normalized_npy_path":  "data/processed/img_001_norm.npy" | null,
  "original_size": [width, height],
  "processed_size": [512, 512],
  "preprocessing": {
    "resized": true,
    "keep_aspect": true,
    "grayscale": true,
    "denoised": true,
    "normalized": true
  },
  "analysis": {
    "content_box": [x, y, w, h],   # real-pixel region in the 512x512 frame
    "colorfulness": 19.3,           # ~0 => grayscale (Module 3 hint)
    "is_grayscale": false,
    "noise_sigma": 4.2              # noise level measured BEFORE denoising
  }
}

Run on a whole folder:
    python preprocessing.py --input data/raw --output data/processed --meta outputs

Run on a single image:
    python preprocessing.py --input data/raw/img_001.jpg --output data/processed --meta outputs
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Optional

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TARGET_SIZE = (512, 512)  # (width, height)
SUPPORTED_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")


# ---------------------------------------------------------------------------
# Core preprocessing functions (each does ONE thing, so they're reusable)
# ---------------------------------------------------------------------------

def load_image(path: str) -> np.ndarray:
    """Load an image from disk as a BGR (color) uint8 array.

    Raises FileNotFoundError if the path is missing or unreadable.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Image not found: {path}")
    # cv2.imread returns None on failure (e.g. corrupt file), so check it.
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not decode image (corrupt or unsupported): {path}")
    return image


def decode_image(data: bytes) -> np.ndarray:
    """Decode an in-memory image (raw bytes) to a BGR uint8 array.

    Use this for UPLOADS — a web form / Streamlit / Flask hands you bytes, not a
    file on disk. Mirrors load_image() but skips the filesystem.

    Example (Streamlit):
        file = st.file_uploader("Upload a photo")
        bgr = decode_image(file.getvalue())
    """
    buf = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode uploaded image (corrupt or unsupported).")
    return image


def resize_image(
    image: np.ndarray,
    size: tuple[int, int] = TARGET_SIZE,
    keep_aspect: bool = True,
    return_box: bool = False,
):
    """Resize to `size` (width, height).

    keep_aspect=True  -> scale to fit, then pad with black (letterbox). No
                         distortion. Output is exactly `size`.
    keep_aspect=False -> plain stretch to `size`. Fast but distorts shapes.

    If return_box=True, also returns the "content box" [x, y, w, h]: the region
    of the output that holds REAL pixels (everything outside it is padding).
    Downstream modules should measure stats inside this box so the black
    letterbox bars don't skew brightness / contrast / the colour histogram.
    """
    target_w, target_h = size

    if not keep_aspect:
        out = cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_AREA)
        return (out, [0, 0, target_w, target_h]) if return_box else out

    h, w = image.shape[:2]
    scale = min(target_w / w, target_h / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    # INTER_AREA is best for shrinking; INTER_LINEAR for enlarging.
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (new_w, new_h), interpolation=interp)

    # Pad to the exact target size, centering the image.
    pad_w, pad_h = target_w - new_w, target_h - new_h
    top, bottom = pad_h // 2, pad_h - pad_h // 2
    left, right = pad_w // 2, pad_w - pad_w // 2
    out = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(0, 0, 0)
    )
    return (out, [left, top, new_w, new_h]) if return_box else out


def to_grayscale(image: np.ndarray) -> np.ndarray:
    """Convert a BGR color image to single-channel grayscale (uint8)."""
    if image.ndim == 2:  # already grayscale
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def denoise(image: np.ndarray) -> np.ndarray:
    """Reduce noise while keeping edges reasonably intact.

    Uses Non-Local Means, which handles the grain/speckle common in old
    scanned photos better than a plain blur. Works for both color and gray.
    """
    if image.ndim == 2:
        return cv2.fastNlMeansDenoising(image, None, h=10, templateWindowSize=7, searchWindowSize=21)
    return cv2.fastNlMeansDenoisingColored(image, None, 10, 10, 7, 21)


def normalize(image: np.ndarray) -> np.ndarray:
    """Scale pixel values to float32 in [0, 1].

    This is the standard input form for analysis / ML. It is NOT a viewable
    image — save it as .npy, or multiply by 255 and cast to uint8 to view.
    """
    return image.astype(np.float32) / 255.0


# ---------------------------------------------------------------------------
# Analysis helpers (metadata only — they don't modify the image)
# ---------------------------------------------------------------------------

def colorfulness(bgr: np.ndarray) -> float:
    """How much real colour the image has (0 = pure grayscale).

    Mean absolute difference between the colour channels. ~0 means the photo is
    black & white (or 3-channel grey), so Module 3 can't detect fading by
    desaturation and should fall back to a tonal/sepia method.
    """
    if bgr.ndim == 2:
        return 0.0
    b, g, r = cv2.split(bgr.astype(np.int16))
    return round(float((np.mean(np.abs(b - g)) + np.mean(np.abs(g - r))) / 2), 2)


def estimate_noise(gray: np.ndarray) -> float:
    """Estimate the noise level (sigma) of a grayscale image.

    Immerkaer's method: convolve with a Laplacian-like mask that cancels smooth
    content and leaves noise. Computed BEFORE denoising so the noise signal is
    recorded for Module 2 before this module removes it.
    """
    if gray.ndim != 2:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if h < 3 or w < 3:
        return 0.0
    mask = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float64)
    conv = cv2.filter2D(gray.astype(np.float64), -1, mask)
    sigma = np.sum(np.abs(conv)) * np.sqrt(0.5 * np.pi) / (6 * (w - 2) * (h - 2))
    return round(float(sigma), 2)


# ---------------------------------------------------------------------------
# Pipeline (combines the steps + writes outputs)
# ---------------------------------------------------------------------------

def preprocess_array(
    original: np.ndarray,
    image_id: str,
    output_dir: str,
    original_path: Optional[str] = None,
    keep_aspect: bool = True,
    save_normalized: bool = False,
) -> dict:
    """Core pipeline: run all steps on an ALREADY-LOADED BGR array.

    Both preprocess_image() (disk path) and preprocess_upload() (uploaded
    bytes) call this. UI code can call it directly after decode_image().
    `original_path` is recorded in the metadata; pass None for uploads that
    were never written to disk.
    """
    orig_h, orig_w = original.shape[:2]

    # 1. resize (color), keeping the content box so downstream can skip padding
    color_resized, content_box = resize_image(
        original, TARGET_SIZE, keep_aspect=keep_aspect, return_box=True
    )

    # --- analysis on the PRE-denoise content region (padding excluded) ---
    bx, by, bw, bh = content_box
    content = color_resized[by : by + bh, bx : bx + bw]
    color_score = colorfulness(content)
    noise_sigma = estimate_noise(to_grayscale(content))

    # 2. denoise (color)
    color_denoised = denoise(color_resized)

    # 3. grayscale (from the denoised color image)
    gray = to_grayscale(color_denoised)

    # 4. normalize (in-memory float array; optionally saved)
    normalized = normalize(gray)

    os.makedirs(output_dir, exist_ok=True)
    color_path = os.path.join(output_dir, f"{image_id}_color.png")
    gray_path = os.path.join(output_dir, f"{image_id}_gray.png")
    cv2.imwrite(color_path, color_denoised)
    cv2.imwrite(gray_path, gray)

    norm_path: Optional[str] = None
    if save_normalized:
        norm_path = os.path.join(output_dir, f"{image_id}_norm.npy")
        np.save(norm_path, normalized)

    return {
        "image_id": image_id,
        "status": "ok",
        "original_path": original_path.replace("\\", "/") if original_path else None,
        "processed_color_path": color_path.replace("\\", "/"),
        "processed_gray_path": gray_path.replace("\\", "/"),
        "normalized_npy_path": norm_path.replace("\\", "/") if norm_path else None,
        "original_size": [orig_w, orig_h],
        "processed_size": [TARGET_SIZE[0], TARGET_SIZE[1]],
        "preprocessing": {
            "resized": True,
            "keep_aspect": keep_aspect,
            "grayscale": True,
            "denoised": True,
            "normalized": True,
        },
        "analysis": {
            # [x, y, w, h] of real pixels in the 512x512 frame. Measure stats
            # inside this box so letterbox padding doesn't skew Module 2/3.
            "content_box": content_box,
            # ~0 means black & white -> Module 3 fading-by-saturation won't work.
            "colorfulness": color_score,
            "is_grayscale": color_score < 6.0,
            # Noise level measured BEFORE denoising (we remove it afterwards).
            "noise_sigma": noise_sigma,
        },
    }


def preprocess_image(
    input_path: str,
    output_dir: str,
    keep_aspect: bool = True,
    save_normalized: bool = False,
) -> dict:
    """Run the pipeline on an image FILE on disk (used by the batch CLI).

    Returns the metadata dict. Does not raise on a bad image — returns a dict
    with status="error" instead, so a batch run can continue past a broken file.
    """
    image_id = os.path.splitext(os.path.basename(input_path))[0]
    try:
        original = load_image(input_path)
    except (FileNotFoundError, ValueError) as exc:
        return {"image_id": image_id, "status": "error", "error": str(exc)}
    return preprocess_array(
        original, image_id, output_dir,
        original_path=input_path, keep_aspect=keep_aspect, save_normalized=save_normalized,
    )


def preprocess_upload(
    data: bytes,
    filename: str,
    output_dir: str,
    keep_aspect: bool = True,
    save_normalized: bool = False,
) -> dict:
    """Run the pipeline on an UPLOADED image (raw bytes, no disk path).

    This is the entry point for a front-end / UI. `filename` is only used to
    derive the image_id (and choose output filenames); the bytes never need to
    touch the filesystem.

    Example (Streamlit):
        up = st.file_uploader("Upload a photo")
        if up:
            meta = preprocess_upload(up.getvalue(), up.name, "data/processed")
            st.image(meta["processed_color_path"])
    """
    image_id = os.path.splitext(os.path.basename(filename))[0]
    try:
        original = decode_image(data)
    except ValueError as exc:
        return {"image_id": image_id, "status": "error", "error": str(exc)}
    return preprocess_array(
        original, image_id, output_dir,
        original_path=None, keep_aspect=keep_aspect, save_normalized=save_normalized,
    )


def _iter_image_paths(input_path: str):
    """Yield image file paths from a single file or a directory."""
    if os.path.isfile(input_path):
        yield input_path
        return
    if os.path.isdir(input_path):
        for name in sorted(os.listdir(input_path)):
            if name.lower().endswith(SUPPORTED_EXTS):
                yield os.path.join(input_path, name)
        return
    raise FileNotFoundError(f"Input path does not exist: {input_path}")


def process_dataset(
    input_path: str,
    output_dir: str,
    meta_dir: str,
    keep_aspect: bool = True,
    save_normalized: bool = False,
) -> list[dict]:
    """Process every image under `input_path` and write one metadata JSON each.

    Returns the list of metadata dicts (also useful for a summary / test).
    """
    os.makedirs(meta_dir, exist_ok=True)
    results: list[dict] = []

    for path in _iter_image_paths(input_path):
        meta = preprocess_image(
            path, output_dir, keep_aspect=keep_aspect, save_normalized=save_normalized
        )
        meta_file = os.path.join(meta_dir, f"{meta['image_id']}.json")
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        results.append(meta)

        status = meta["status"]
        if status == "ok":
            print(f"[ok]    {meta['image_id']}  ->  {meta_file}")
        else:
            print(f"[ERROR] {meta['image_id']}  ->  {meta.get('error')}")

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nDone. {ok}/{len(results)} images processed successfully.")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Module 1 — image upload & preprocessing."
    )
    parser.add_argument(
        "--input", default="data/raw",
        help="Image file or folder of images (default: data/raw)",
    )
    parser.add_argument(
        "--output", default="data/processed",
        help="Where to write processed images (default: data/processed)",
    )
    parser.add_argument(
        "--meta", default="outputs",
        help="Where to write per-image metadata JSON (default: outputs)",
    )
    parser.add_argument(
        "--stretch", action="store_true",
        help="Stretch to 512x512 instead of aspect-preserving letterbox.",
    )
    parser.add_argument(
        "--save-normalized", action="store_true",
        help="Also save the normalized float array as a .npy file.",
    )
    args = parser.parse_args()

    process_dataset(
        input_path=args.input,
        output_dir=args.output,
        meta_dir=args.meta,
        keep_aspect=not args.stretch,
        save_normalized=args.save_normalized,
    )


if __name__ == "__main__":
    main()
