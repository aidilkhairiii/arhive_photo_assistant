from pathlib import Path

import numpy as np


try:
    import cv2
except ImportError:  # Pillow fallback is used for loading if OpenCV is unavailable.
    cv2 = None

try:
    from PIL import Image
except ImportError:
    Image = None


def _load_grayscale_image(image):
    """Return a grayscale NumPy image from a file path or loaded image."""
    if isinstance(image, (str, Path)):
        image_path = str(image)

        if cv2 is not None:
            grayscale = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
            if grayscale is None:
                raise ValueError(f"Unable to read image: {image_path}")
            return grayscale

        if Image is None:
            raise ImportError("Install opencv-python or Pillow to load image files.")

        return np.array(Image.open(image_path).convert("L"))

    image_array = np.asarray(image)

    if image_array.ndim == 2:
        return image_array.astype(np.uint8)

    if image_array.ndim == 3:
        if cv2 is not None:
            return cv2.cvtColor(image_array, cv2.COLOR_BGR2GRAY)

        red = image_array[:, :, 0]
        green = image_array[:, :, 1]
        blue = image_array[:, :, 2]
        return (0.299 * red + 0.587 * green + 0.114 * blue).astype(np.uint8)

    raise ValueError("Image must be a file path, grayscale image, or color image.")


def _score(value):
    """Clamp a numeric value into a 0-100 integer score."""
    return int(round(max(0, min(100, value))))


def calculate_blur_score(image):
    """
    Calculate image sharpness using Laplacian variance.

    Returns a 0-100 score where higher means the image is sharper.
    """
    grayscale = _load_grayscale_image(image)

    if cv2 is not None:
        laplacian_variance = cv2.Laplacian(grayscale, cv2.CV_64F).var()
    else:
        grayscale_float = grayscale.astype(float)
        laplacian = (
            -4 * grayscale_float[1:-1, 1:-1]
            + grayscale_float[:-2, 1:-1]
            + grayscale_float[2:, 1:-1]
            + grayscale_float[1:-1, :-2]
            + grayscale_float[1:-1, 2:]
        )
        laplacian_variance = laplacian.var()

    return _score((laplacian_variance / 1000) * 100)


def calculate_brightness(image):
    """
    Calculate average image brightness.

    Returns a 0-100 score where higher means the image is brighter.
    """
    grayscale = _load_grayscale_image(image)
    return _score((grayscale.mean() / 255) * 100)


def calculate_contrast(image):
    """
    Calculate image contrast using grayscale standard deviation.

    Returns a 0-100 score where higher means stronger contrast.
    """
    grayscale = _load_grayscale_image(image)
    return _score((grayscale.std() / 127.5) * 100)


def assess_image_quality(image):
    """Return all quality scores in the requested dictionary format."""
    return {
        "blur": calculate_blur_score(image),
        "brightness": calculate_brightness(image),
        "contrast": calculate_contrast(image),
    }
