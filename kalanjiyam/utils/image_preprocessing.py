"""Image preprocessing utilities for Enhanced OCR.

Provides named enhancement profiles:
- 'document_cleanup': Illumination normalization, background stain removal, and CLAHE
- 'bg_clahe': Illumination normalization / background removal followed by CLAHE
- 'sharpen': Controlled edge unsharp masking
- 'text_enhancement': Faint text boost via tone curve and stroke enhancement
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreprocessingConfig:
    """Central configuration for image preprocessing parameters."""

    # CLAHE parameters
    clahe_grid_size: tuple[int, int] = (8, 8)
    clahe_clip_limit: float = 2.5

    # Document Cleanup parameters
    cleanup_bg_blur_radius: int = 25
    cleanup_clahe_grid_size: tuple[int, int] = (8, 8)
    cleanup_clahe_clip_limit: float = 2.0

    # Sharpen parameters
    sharpen_radius: float = 1.5
    sharpen_percent: int = 120
    sharpen_threshold: int = 3

    # Text Enhancement parameters
    text_gamma: float = 0.70
    text_sharpen_radius: float = 1.0
    text_sharpen_percent: int = 100
    text_sharpen_threshold: int = 2
    text_blend_ratio: float = 0.70

    # Hybrid Binarization parameters (historical degraded manuscripts & bleed-through)
    hybrid_t_ctr: float = 0.02
    hybrid_t1: float = 0.03
    hybrid_t2: float = 0.04
    hybrid_t3: float = 0.085
    hybrid_d_min: int = 5
    hybrid_d_max: int = 25
    hybrid_p_factor: float = 0.5
    hybrid_k_smear: float = 1.5
    hybrid_win_size_nick: int = 35
    hybrid_k_nick: float = -0.12
    hybrid_lambda_post: float = 15.0
    hybrid_min_cc_area: int = 6


DEFAULT_PREPROCESSING_CONFIG = PreprocessingConfig()

SUPPORTED_ENHANCEMENT_PROFILES = (
    "document_cleanup",
    "bg_clahe",
    "sharpen",
    "text_enhancement",
    "hybrid_binarization",
)

PROFILE_ALIASES = {
    "background_clahe": "bg_clahe",
    "bg+clahe": "bg_clahe",
    "clahe": "bg_clahe",
    "clahe_1": "bg_clahe",
    "hybrid": "hybrid_binarization",
    "historical_hybrid": "hybrid_binarization",
    "historical_binarization": "hybrid_binarization",
    "hybrid_binarize": "hybrid_binarization",
    "binarize": "hybrid_binarization",
    "binarization": "hybrid_binarization",
}


def validate_enhancement_profile(profile: str) -> str:
    """Validate and normalize the enhancement profile name to a canonical identifier."""
    if not profile or not isinstance(profile, str):
        raise ValueError(
            f"Invalid enhancement profile: {profile!r}. "
            f"Supported profiles: {list(SUPPORTED_ENHANCEMENT_PROFILES)}"
        )
    normalized = profile.lower().strip()
    # Resolve aliases
    canonical = PROFILE_ALIASES.get(normalized, normalized)
    if canonical not in SUPPORTED_ENHANCEMENT_PROFILES:
        raise ValueError(
            f"Unsupported enhancement profile: {profile!r}. "
            f"Supported profiles: {list(SUPPORTED_ENHANCEMENT_PROFILES)}"
        )
    return canonical


def apply_clahe(
    img: Image.Image,
    grid_size: tuple[int, int] = (8, 8),
    clip_limit: float = 2.5,
) -> Image.Image:
    """Contrast-Limited Adaptive Histogram Equalization.

    Divides image into grid tiles, computes clipped histogram CDF mapping per tile,
    applies lookup table transformations, and blends seams smoothly.
    Preserves original image pixel dimensions and does not modify the input in-place.
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


def apply_document_cleanup(
    img: Image.Image,
    config: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> Image.Image:
    """Document Cleanup: Normalize background illumination, remove stains, and apply gentle CLAHE.

    Corrects uneven background / paper yellowing without aggressive binarization that would
    destroy faint historical handwriting or delicate glyphs.
    """
    is_rgb = img.mode == "RGB"
    gray = img.convert("L")

    # Estimate background illumination surface with large Gaussian blur
    bg = gray.filter(ImageFilter.GaussianBlur(radius=config.cleanup_bg_blur_radius))

    # Flatten illumination to normalize paper background level
    inv_bg = ImageOps.invert(bg)
    norm = ImageChops.add(gray, inv_bg, scale=1.0, offset=0)

    # Stretch contrast slightly
    norm_contrast = ImageOps.autocontrast(norm, cutoff=1)

    # Apply gentle CLAHE for local contrast normalization
    cleaned = apply_clahe(
        norm_contrast,
        grid_size=config.cleanup_clahe_grid_size,
        clip_limit=config.cleanup_clahe_clip_limit,
    )

    if is_rgb:
        return cleaned.convert("RGB")
    return cleaned


def apply_bg_clahe_pipeline(
    img: Image.Image,
    config: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> Image.Image:
    """BG + CLAHE pipeline: Illumination normalization / background removal followed by CLAHE."""
    is_rgb = img.mode == "RGB"
    gray = img.convert("L")

    # Estimate background illumination surface with Gaussian blur
    bg = gray.filter(ImageFilter.GaussianBlur(radius=config.cleanup_bg_blur_radius))

    # Flatten illumination to normalize paper background level
    inv_bg = ImageOps.invert(bg)
    norm = ImageChops.add(gray, inv_bg, scale=1.0, offset=0)

    # Apply adaptive histogram equalization (CLAHE)
    enhanced = apply_clahe(
        norm,
        grid_size=config.clahe_grid_size,
        clip_limit=config.clahe_clip_limit,
    )

    if is_rgb:
        return enhanced.convert("RGB")
    return enhanced


def apply_sharpen_pipeline(
    img: Image.Image,
    config: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> Image.Image:
    """Sharpen pipeline: Controlled unsharp masking to enhance character/glyph edges."""
    return img.filter(
        ImageFilter.UnsharpMask(
            radius=config.sharpen_radius,
            percent=config.sharpen_percent,
            threshold=config.sharpen_threshold,
        )
    )


def apply_text_enhancement_pipeline(
    img: Image.Image,
    config: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> Image.Image:
    """Text Enhancement pipeline: Specifically optimize for faint/difficult-to-read text.

    Darkens faint ink mid-tones via tone curve and defines strokes without destroying character shapes.
    """
    is_rgb = img.mode == "RGB"
    gray = img.convert("L")

    # Stretch dynamic range to anchor black and white points
    stretched = ImageOps.autocontrast(gray, cutoff=0.5)

    # Gamma curve (gamma < 1.0 darkens faint ink while preserving paper background)
    gamma = config.text_gamma
    lut = [
        min(255, max(0, int(((i / 255.0) ** (1.0 / gamma)) * 255.0)))
        for i in range(256)
    ]
    darkened = stretched.point(lut)

    # Subtle stroke sharpening
    sharpened = darkened.filter(
        ImageFilter.UnsharpMask(
            radius=config.text_sharpen_radius,
            percent=config.text_sharpen_percent,
            threshold=config.text_sharpen_threshold,
        )
    )

    # Blend with original stretched image according to text_blend_ratio
    blended = Image.blend(stretched, sharpened, config.text_blend_ratio)

    if is_rgb:
        return blended.convert("RGB")
    return blended


def apply_hybrid_binarization(
    img: Image.Image,
    config: PreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> Image.Image:
    """Multi-Phase Hybrid Binarization pipeline for degraded historical manuscripts.

    Combines adaptive contrast evaluation, two-stage multi-threshold Otsu (TSMO),
    Sokratis smear detection with selective Nick refinement, and non-deforming
    micro-gap repair to cleanly separate faint foreground ink from back-to-front bleed-through.
    """
    from kalanjiyam.utils.hybrid_binarization import hybrid_binarize_image

    is_rgb = img.mode == "RGB"
    final_bin, _ = hybrid_binarize_image(
        img,
        t_ctr=config.hybrid_t_ctr,
        t1=config.hybrid_t1,
        t2=config.hybrid_t2,
        t3=config.hybrid_t3,
        d_min=config.hybrid_d_min,
        d_max=config.hybrid_d_max,
        p_factor=config.hybrid_p_factor,
        k_smear=config.hybrid_k_smear,
        win_size_nick=config.hybrid_win_size_nick,
        k_nick=config.hybrid_k_nick,
        lambda_post=config.hybrid_lambda_post,
        min_cc_area=config.hybrid_min_cc_area,
    )
    result_img = Image.fromarray(final_bin)
    if is_rgb:
        return result_img.convert("RGB")
    return result_img


# Pipeline registry mapping profile identifiers to their preprocessing functions
PipelineFunc = Callable[[Image.Image, PreprocessingConfig], Image.Image]

PREPROCESSING_REGISTRY: dict[str, PipelineFunc] = {
    "document_cleanup": apply_document_cleanup,
    "bg_clahe": apply_bg_clahe_pipeline,
    "background_clahe": apply_bg_clahe_pipeline,
    "clahe": apply_bg_clahe_pipeline,
    "sharpen": apply_sharpen_pipeline,
    "text_enhancement": apply_text_enhancement_pipeline,
    "hybrid_binarization": apply_hybrid_binarization,
    "hybrid": apply_hybrid_binarization,
    "historical_hybrid": apply_hybrid_binarization,
    "historical_binarization": apply_hybrid_binarization,
    "hybrid_binarize": apply_hybrid_binarization,
    "binarize": apply_hybrid_binarization,
    "binarization": apply_hybrid_binarization,
}


def preprocess_image(
    img: Image.Image,
    profile: str,
    config: PreprocessingConfig | None = None,
) -> Image.Image:
    """Apply the requested preprocessing profile to a PIL Image.

    Guarantees the output image has the exact same (width, height) as the input image
    and never modifies the source image in-place.
    """
    valid_profile = validate_enhancement_profile(profile)
    active_config = config or DEFAULT_PREPROCESSING_CONFIG

    pipeline_fn = PREPROCESSING_REGISTRY.get(valid_profile)
    if not pipeline_fn:
        raise ValueError(
            f"No preprocessing pipeline registered for profile: {valid_profile}"
        )

    orig_size = img.size
    result = pipeline_fn(img, active_config)

    if result.size != orig_size:
        raise RuntimeError(
            f"Preprocessing changed image dimensions from {orig_size} to {result.size}"
        )

    return result


@contextmanager
def preprocess_image_to_tempfile(
    image_path: Path | str,
    profile: str,
    config: PreprocessingConfig | None = None,
) -> Iterator[Path]:
    """Context manager that produces a preprocessed image tempfile and ensures cleanup."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Source image not found: {path}")

    valid_profile = validate_enhancement_profile(profile)

    with Image.open(path) as img:
        processed = preprocess_image(img, valid_profile, config=config)
        # Create temp jpeg file with high quality
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
