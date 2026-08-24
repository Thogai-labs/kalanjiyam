"""Image preprocessing utilities for Enhanced OCR.

Provides named enhancement profiles:
- 'clahe_1': Contrast-Limited Adaptive Histogram Equalization
- 'background_clahe': Illumination normalization / background removal followed by CLAHE
- 'sharpen': Edge unsharp masking
- 'normal': Passthrough (no enhancement)
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

SUPPORTED_ENHANCEMENT_PROFILES = (
    "clahe_1",
    "background_clahe",
    "sharpen",
    "normal",
)


def validate_enhancement_profile(profile: str) -> str:
    """Validate and normalize the enhancement profile name."""
    if not profile or not isinstance(profile, str):
        raise ValueError(
            f"Invalid enhancement profile: {profile!r}. "
            f"Supported profiles: {list(SUPPORTED_ENHANCEMENT_PROFILES)}"
        )
    normalized = profile.lower().strip()
    if normalized not in SUPPORTED_ENHANCEMENT_PROFILES:
        raise ValueError(
            f"Unsupported enhancement profile: {profile!r}. "
            f"Supported profiles: {list(SUPPORTED_ENHANCEMENT_PROFILES)}"
        )
    return normalized


def apply_sharpen(img: Image.Image) -> Image.Image:
    """Apply unsharp mask sharpening for text enhancement."""
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))


def apply_clahe(
    img: Image.Image,
    grid_size: tuple[int, int] = (8, 8),
    clip_limit: float = 3.0,
) -> Image.Image:
    """Contrast-Limited Adaptive Histogram Equalization.

    Divides image into grid tiles, computes clipped histogram CDF mapping per tile,
    applies lookup table transformations, and blends seams smoothly.
    Preserves original image pixel dimensions.
    """
    is_rgb = img.mode == "RGB"
    gray = img.convert("L")
    w, h = gray.size
    gx, gy = grid_size
    tile_w = w / gx
    tile_h = h / gy

    result = Image.new("L", (w, h))

    for ty in range(gy):
        for tx in range(gx):
            x1 = int(tx * tile_w)
            y1 = int(ty * tile_h)
            x2 = int((tx + 1) * tile_w) if tx < gx - 1 else w
            y2 = int((ty + 1) * tile_h) if ty < gy - 1 else h

            tile = gray.crop((x1, y1, x2, y2))
            hist = tile.histogram()
            num_pixels = tile.size[0] * tile.size[1]
            if num_pixels == 0:
                result.paste(tile, (x1, y1))
                continue

            actual_clip = max(1, int(clip_limit * (num_pixels / 256.0)))
            excess = sum(max(0, count - actual_clip) for count in hist)
            clipped = [min(count, actual_clip) for count in hist]
            bonus = excess // 256
            remainder = excess % 256
            for i in range(256):
                clipped[i] += bonus + (1 if i < remainder else 0)

            lut = [0] * 256
            cum = 0
            for i in range(256):
                cum += clipped[i]
                lut[i] = min(255, int((cum * 255) / num_pixels))

            eq_tile = tile.point(lut)
            result.paste(eq_tile, (x1, y1))

    # Apply subtle smoothing across tile boundaries
    smoothed = result.filter(ImageFilter.SMOOTH)
    blended = Image.blend(result, smoothed, 0.2)

    if is_rgb:
        return blended.convert("RGB")
    return blended


def apply_background_removal(img: Image.Image) -> Image.Image:
    """Normalize illumination gradient and remove paper yellowing/background."""
    is_rgb = img.mode == "RGB"
    gray = img.convert("L")

    # Estimate background illumination surface with large Gaussian blur
    bg = gray.filter(ImageFilter.GaussianBlur(radius=25))

    # Add inverted background to flatten illumination to white paper level
    inv_bg = ImageOps.invert(bg)
    norm = ImageChops.add(gray, inv_bg, scale=1.0, offset=0)

    # Stretch contrast slightly to enhance character clarity
    enhanced = ImageOps.autocontrast(norm, cutoff=1)

    if is_rgb:
        return enhanced.convert("RGB")
    return enhanced


def preprocess_image(img: Image.Image, profile: str) -> Image.Image:
    """Apply the requested preprocessing profile to a PIL Image.

    Guarantees the output image has the exact same (width, height) as the input image.
    """
    valid_profile = validate_enhancement_profile(profile)

    orig_size = img.size

    if valid_profile == "normal":
        result = img.copy()
    elif valid_profile == "clahe_1":
        result = apply_clahe(img)
    elif valid_profile == "background_clahe":
        bg_removed = apply_background_removal(img)
        result = apply_clahe(bg_removed)
    elif valid_profile == "sharpen":
        result = apply_sharpen(img)
    else:
        raise ValueError(f"Unknown enhancement profile: {valid_profile}")

    if result.size != orig_size:
        raise RuntimeError(
            f"Preprocessing changed image dimensions from {orig_size} to {result.size}"
        )

    return result


@contextmanager
def preprocess_image_to_tempfile(
    image_path: Path | str, profile: str
) -> Iterator[Path]:
    """Context manager that produces a preprocessed image tempfile and ensures cleanup."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Source image not found: {path}")

    valid_profile = validate_enhancement_profile(profile)

    if valid_profile == "normal":
        # No temp file needed for normal passthrough
        yield path
        return

    with Image.open(path) as img:
        processed = preprocess_image(img, valid_profile)
        # Create temp jpeg file with same quality
        with tempfile.NamedTemporaryFile(
            suffix=f"_{valid_profile}.jpg", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)

        try:
            if processed.mode not in ("RGB", "L"):
                processed = processed.convert("RGB")
            processed.save(tmp_path, format="JPEG", quality=95)
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
