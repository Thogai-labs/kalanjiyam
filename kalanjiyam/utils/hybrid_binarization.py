"""
hybrid_binarization.py
======================
Complete, modular, and fully vectorized Python implementation of the
Multi-Phase Hybrid Document Image Binarization Pipeline based on:
"Degraded Historical Documents Images Binarization Using a Combination of Enhanced Techniques"
(Boudraa, Hidouci, Michelucci).

Pipeline Stages:
1. Adaptive Preprocessing (Local Michelson Contrast & Conditional CLAHE)
2. Vectorized Nick's Local Thresholding (via OpenCV boxFilter)
3. Optimal Two-Stage Multi-Threshold Otsu (TSMO 3-Class) & Global Otsu
4. Contrast-Based Decision Tree with Fuzzy Region Verification
5. Sokratis Smear Detection & Selective Nick Binarization
6. Non-Deforming Post-Processing (Isolated Pixel Removal & Pinhole Repair)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image


@dataclass
class BinarizationMetadata:
    """Stores detailed metrics and execution statistics for the binarization pipeline."""

    initial_contrast: float = 0.0
    effective_contrast: float = 0.0
    clahe_applied: bool = False
    tsmo_to1: int = 0
    tsmo_to2: int = 0
    otsu_to: float = 0.0
    decision_category: str = ""
    selected_threshold: float = 0.0
    smear_mean_density: float = 0.0
    smear_std_density: float = 0.0
    smear_threshold: float = 0.0
    smear_segments_flagged: int = 0
    smear_ccs_flagged: int = 0
    smear_pixels_refined: int = 0
    post_process_stats: dict[str, Any] = field(default_factory=dict)
    timing_ms: dict[str, float] = field(default_factory=dict)


def compute_local_michelson_contrast(
    img_gray: np.ndarray, kernel_size: int = 3, eps: float = 1e-6
) -> tuple[np.ndarray, float]:
    """
    Computes local Michelson contrast over a (kernel_size x kernel_size) sliding window
    using efficient morphological dilation and erosion operations.

    Formula:
        C(x, y) = (I_max(x, y) - I_min(x, y)) / (I_max(x, y) + I_min(x, y) + eps)

    Returns:
        contrast_map: 2D float array of local contrast values in [0, 1].
        avg_contrast: Mean local contrast over active image areas (excluding zero-contrast borders).
    """
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    img_f = img_gray.astype(np.float64) / 255.0

    i_max = cv2.dilate(img_f, kernel)
    i_min = cv2.erode(img_f, kernel)

    contrast_map = (i_max - i_min) / (i_max + i_min + eps)

    # Calculate effective contrast (excluding zero-contrast padding borders if present)
    non_zero = contrast_map[contrast_map > 1e-4]
    if len(non_zero) > 0 and len(non_zero) < contrast_map.size * 0.95:
        avg_contrast = float(np.mean(non_zero))
    else:
        avg_contrast = float(np.mean(contrast_map))

    return contrast_map, avg_contrast


def adaptive_preprocessing(
    img_gray: np.ndarray,
    t_ctr: float = 0.02,
    clip_limit: float = 2.0,
    tile_grid_size: tuple[int, int] = (8, 8),
) -> tuple[np.ndarray, float, bool, float]:
    """
    Stage 1: Adaptive Preprocessing.
    Measures initial average local Michelson contrast on raw image. If below t_ctr (weak contrast),
    triggers CLAHE to boost readability without noise amplification.
    """
    _, initial_contrast = compute_local_michelson_contrast(img_gray, kernel_size=3)

    if initial_contrast < t_ctr:
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
        enhanced_img = clahe.apply(img_gray)
        _, effective_contrast = compute_local_michelson_contrast(
            enhanced_img, kernel_size=3
        )
        return enhanced_img, initial_contrast, True, effective_contrast
    else:
        return img_gray.copy(), initial_contrast, False, initial_contrast


def vectorized_nick(
    img_gray: np.ndarray, win_size: int = 35, k: float = -0.12
) -> tuple[np.ndarray, np.ndarray]:
    """
    Stage 2: Vectorized Nick's Method.
    Calculates pixel-wise adaptive threshold using boxFilter for local moments:
        T_nick(x, y) = m + k * sqrt( max( mean(p^2) - (m^2 / NP), 0 ) )

    Returns:
        binary_img: 0 for foreground (text), 255 for background.
        threshold_map: The calculated local threshold per pixel.
    """
    img_f = img_gray.astype(np.float64)
    np_pixels = float(win_size * win_size)

    mean = cv2.boxFilter(
        img_f, cv2.CV_64F, (win_size, win_size), borderType=cv2.BORDER_REFLECT
    )
    mean_sq = cv2.boxFilter(
        img_f**2, cv2.CV_64F, (win_size, win_size), borderType=cv2.BORDER_REFLECT
    )

    variance_term = mean_sq - (mean**2) / np_pixels
    variance_term = np.maximum(variance_term, 0.0)
    thresh = mean + k * np.sqrt(variance_term)

    binary = np.where(img_f <= thresh, 0, 255).astype(np.uint8)
    return binary, thresh


def two_stage_multi_otsu(img_gray: np.ndarray) -> tuple[int, int]:
    """
    Stage 3: Optimal Two-Stage Multi-Threshold Otsu (TSMO 3-Class).
    Finds optimal thresholds (TO1, TO2) with TO1 < TO2 that maximize the
    inter-class variance across three intensity classes:
    Class 0: [0, TO1] (Foreground / Text)
    Class 1: (TO1, TO2] (Intermediate Degradation / Document Body)
    Class 2: (TO2, 255] (Background / Highlights)
    """
    hist = cv2.calcHist([img_gray], [0], None, [256], [0, 256]).ravel()
    prob = hist / (hist.sum() + 1e-12)

    omega = np.cumsum(prob)
    mu = np.cumsum(prob * np.arange(256))
    mu_total = mu[-1]

    i, j = np.triu_indices(256, k=1)

    w0 = omega[i]
    w1 = omega[j] - omega[i]
    w2 = omega[-1] - omega[j]

    valid = (w0 > 1e-9) & (w1 > 1e-9) & (w2 > 1e-9)
    i_v = i[valid]
    j_v = j[valid]
    w0_v = w0[valid]
    w1_v = w1[valid]
    w2_v = w2[valid]
    if len(i_v) == 0:
        otsu_val, _ = cv2.threshold(
            img_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        return max(1, int(otsu_val * 0.8)), max(2, int(otsu_val))

    mu0 = mu[i_v] / w0_v
    mu1 = (mu[j_v] - mu[i_v]) / w1_v
    mu2 = (mu[-1] - mu[j_v]) / w2_v

    sigma_b_squared = (
        w0_v * ((mu0 - mu_total) ** 2)
        + w1_v * ((mu1 - mu_total) ** 2)
        + w2_v * ((mu2 - mu_total) ** 2)
    )

    best_idx = np.argmax(sigma_b_squared)
    to1 = int(i_v[best_idx])
    to2 = int(j_v[best_idx])
    return to1, to2


def global_otsu(img_gray: np.ndarray) -> tuple[float, np.ndarray]:
    """Standard global Otsu thresholding."""
    otsu_val, binary = cv2.threshold(
        img_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    return float(otsu_val), binary


def decision_tree_binarize(
    img_gray: np.ndarray,
    contrast: float,
    t1: float = 0.03,
    t2: float = 0.04,
    t3: float = 0.085,
    d_min: int = 5,
    d_max: int = 25,
    p_factor: float = 0.5,
) -> tuple[np.ndarray, float, str, int, int, float]:
    """
    Stage 4: Decision Tree with Exact Verification Rules.
    Categorizes the document based on registered Michelson contrast:
    - Low-Contrast (Ctr <= T1): Uses TSMO TO2.
    - Fuzzy-Contrast (T1 < Ctr <= T2): Evaluates verification condition:
        If D_min <= |TO2 - TO| <= D_max and Count(TO < I <= TO2) <= P * Count(I <= TO)
        Then uses TSMO TO2, Else uses Otsu TO.
    - Medium-Contrast (T2 < Ctr <= T3): Uses Otsu TO.
    - High-Contrast (Ctr > T3): Uses TSMO TO1.
    """
    to1, to2 = two_stage_multi_otsu(img_gray)
    otsu_to, _ = global_otsu(img_gray)

    if contrast <= t1:
        selected_thresh = float(to2)
        category = (
            f"Low-Contrast (Ctr={contrast:.5f} <= T1={t1}): Selected TSMO TO2={to2}"
        )
    elif contrast <= t2:
        diff = abs(to2 - otsu_to)
        count_intermediate = np.count_nonzero((img_gray > otsu_to) & (img_gray <= to2))
        count_foreground = np.count_nonzero(img_gray <= otsu_to)

        cond_distance = d_min <= diff <= d_max
        cond_density = count_intermediate <= p_factor * count_foreground

        if cond_distance and cond_density:
            selected_thresh = float(to2)
            category = f"Fuzzy-Contrast (T1 < Ctr={contrast:.5f} <= T2={t2}): Verified -> Selected TSMO TO2={to2}"
        else:
            selected_thresh = float(otsu_to)
            category = f"Fuzzy-Contrast (T1 < Ctr={contrast:.5f} <= T2={t2}): Verification Failed -> Selected Otsu TO={otsu_to:.1f}"
    elif contrast <= t3:
        selected_thresh = float(otsu_to)
        category = f"Medium-Contrast (T2 < Ctr={contrast:.5f} <= T3={t3}): Selected Otsu TO={otsu_to:.1f}"
    else:
        selected_thresh = float(to1)
        category = (
            f"High-Contrast (Ctr={contrast:.5f} > T3={t3}): Selected TSMO TO1={to1}"
        )

    binary_stage1 = np.where(img_gray <= selected_thresh, 0, 255).astype(np.uint8)
    return binary_stage1, selected_thresh, category, to1, to2, otsu_to


def sokratis_smear_detection(
    binary_stage1: np.ndarray,
    tile_size: tuple[int, int] = (32, 32),
    k_smear: float = 1.5,
) -> tuple[np.ndarray, dict[str, Any]]:
    """
    Stage 5 (Part A): Sokratis Smear Detection.
    Divides first-stage binarized image into grid segments, computes the mean (m)
    and standard deviation (s) of black pixel density across segments.
    Segments with black density > threshold are flagged as suspicious smear areas.
    Fast Connected Component Analysis isolates large overlapping smear regions.
    """
    h, w = binary_stage1.shape
    tile_h, tile_w = tile_size

    fg_binary = (binary_stage1 == 0).astype(np.float64)
    int_img = cv2.integral(fg_binary)

    y_coords = np.arange(0, h, tile_h)
    x_coords = np.arange(0, w, tile_w)
    y1_coords = np.minimum(y_coords + tile_h, h)
    x1_coords = np.minimum(x_coords + tile_w, w)

    densities = []
    tile_boxes = []

    for i in range(len(y_coords)):
        y0, y1 = y_coords[i], y1_coords[i]
        for j in range(len(x_coords)):
            x0, x1 = x_coords[j], x1_coords[j]
            count = (
                int_img[y1, x1] - int_img[y0, x1] - int_img[y1, x0] + int_img[y0, x0]
            )
            area = (y1 - y0) * (x1 - x0)
            density = float(count / max(area, 1))
            densities.append(density)
            tile_boxes.append((y0, y1, x0, x1, density))

    densities = np.array(densities, dtype=np.float64)
    m = float(np.mean(densities)) if len(densities) > 0 else 0.0
    s = float(np.std(densities)) if len(densities) > 0 else 0.0

    raw_threshold = m + k_smear * s
    if raw_threshold >= 1.0:
        threshold = max(0.65, m + 1.5 * s)
        if threshold >= 1.0:
            threshold = 0.75
    else:
        threshold = raw_threshold

    smear_tiles_mask = np.zeros((h, w), dtype=bool)
    flagged_tiles_count = 0

    for y0, y1, x0, x1, d in tile_boxes:
        if d > threshold:
            smear_tiles_mask[y0:y1, x0:x1] = True
            flagged_tiles_count += 1

    fg_u8 = (binary_stage1 == 0).astype(np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        fg_u8, connectivity=8
    )

    smear_cc_mask = np.zeros((h, w), dtype=bool)
    flagged_ccs_count = 0

    if flagged_tiles_count > 0 and num_labels > 1:
        intersecting_labels = np.unique(labels[smear_tiles_mask & (labels > 0)])
        if len(intersecting_labels) > 0:
            lut = np.zeros(num_labels, dtype=bool)
            lut[intersecting_labels] = True
            smear_cc_mask = lut[labels]
            flagged_ccs_count = len(intersecting_labels)

    stats_dict = {
        "mean_density": m,
        "std_density": s,
        "smear_threshold": threshold,
        "raw_threshold": raw_threshold,
        "flagged_tiles": flagged_tiles_count,
        "flagged_ccs": flagged_ccs_count,
        "total_tiles": len(densities),
    }
    return smear_cc_mask, stats_dict


def selective_nick_refinement(
    img_gray: np.ndarray,
    binary_stage1: np.ndarray,
    smear_mask: np.ndarray,
    win_size: int = 35,
    k_nick: float = -0.12,
) -> tuple[np.ndarray, int]:
    """
    Stage 5 (Part B): Selective Nick Binarization on Smear Regions.
    Applies Nick's adaptive thresholding specifically inside the flagged smear regions.
    """
    smear_pixel_count = int(np.count_nonzero(smear_mask))
    if smear_pixel_count == 0:
        return binary_stage1.copy(), 0

    nick_binary, _ = vectorized_nick(img_gray, win_size=win_size, k=k_nick)
    refined_binary = binary_stage1.copy()
    refined_binary[smear_mask] = nick_binary[smear_mask]
    return refined_binary, smear_pixel_count


def post_processing_algorithm1(
    binary_img: np.ndarray, lambda_stat: float = 15.0, min_cc_area: int = 6
) -> tuple[np.ndarray, dict[str, Any]]:
    """
    Stage 6: Non-Deforming Post-Processing Pipeline.
    Preserves natural pen stroke anatomy, serifs, and delicate cursive loops:
    1. Isolated foreground pixel noise cleanup (0-neighbor points).
    2. Single-pixel interior pinhole micro-gap repair (without eroding stroke contours).
    3. Multi-scale background noise filtering (suppresses isolated specks <= min_cc_area).
    """
    refined = binary_img.copy()

    # 1. Clean up isolated foreground pixels
    fg = (refined == 0).astype(np.uint8)
    kernel_8 = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=np.uint8)
    neighbor_count_8 = cv2.filter2D(fg, -1, kernel_8, borderType=cv2.BORDER_CONSTANT)
    isolated_mask = (fg == 1) & (neighbor_count_8 == 0)
    isolated_removed = int(np.count_nonzero(isolated_mask))
    refined[isolated_mask] = 255

    # 2. Repair true interior pinhole micro-gaps (surrounded by 4 foreground neighbors)
    fg_current = (refined == 0).astype(np.uint8)
    k_cross = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]], dtype=np.uint8)
    pinhole_mask = (refined == 255) & (
        cv2.filter2D(fg_current, -1, k_cross, borderType=cv2.BORDER_CONSTANT) == 4
    )
    gaps_filled = int(np.count_nonzero(pinhole_mask))
    refined[pinhole_mask] = 0

    # 3. Clean small isolated background noise specks (Area <= min_cc_area)
    fg_post = (refined == 0).astype(np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        fg_post, connectivity=8
    )
    filtered_ccs = 0
    if num_labels > 2:
        areas = stats[1:, cv2.CC_STAT_AREA]
        is_noise = areas <= min_cc_area

        noise_lut = np.zeros(num_labels, dtype=bool)
        noise_lut[1:] = is_noise
        filtered_ccs = int(np.count_nonzero(is_noise))
        refined[noise_lut[labels]] = 255

    stats_post = {
        "isolated_pixels_removed": isolated_removed,
        "pinhole_gaps_filled": gaps_filled,
        "filtered_noise_ccs": filtered_ccs,
    }
    return refined, stats_post


def hybrid_binarize_image(
    img_bgr_or_gray: np.ndarray | Image.Image,
    t_ctr: float = 0.02,
    t1: float = 0.03,
    t2: float = 0.04,
    t3: float = 0.085,
    d_min: int = 5,
    d_max: int = 25,
    p_factor: float = 0.5,
    k_smear: float = 1.5,
    win_size_nick: int = 35,
    k_nick: float = -0.12,
    lambda_post: float = 15.0,
    min_cc_area: int = 6,
) -> tuple[np.ndarray, BinarizationMetadata]:
    """
    Executes the full end-to-end Multi-Phase Hybrid Binarization Pipeline.

    Returns:
        final_binary: Cleaned 8-bit binary image (0 = text, 255 = background).
        metadata: BinarizationMetadata with metrics, decisions, and execution times.
    """
    meta = BinarizationMetadata()

    t_start = time.perf_counter()
    if isinstance(img_bgr_or_gray, Image.Image):
        img_gray = np.array(img_bgr_or_gray.convert("L"))
    elif len(img_bgr_or_gray.shape) == 3:
        img_gray = cv2.cvtColor(img_bgr_or_gray, cv2.COLOR_BGR2GRAY)
    else:
        img_gray = img_bgr_or_gray.copy()

    # Stage 1: Adaptive Preprocessing
    t0 = time.perf_counter()
    prep_img, init_ctr, clahe_applied, eff_ctr = adaptive_preprocessing(
        img_gray, t_ctr=t_ctr, clip_limit=2.0, tile_grid_size=(8, 8)
    )
    t1_time = time.perf_counter()
    meta.initial_contrast = init_ctr
    meta.clahe_applied = clahe_applied
    meta.effective_contrast = eff_ctr
    meta.timing_ms["stage1_preprocessing"] = (t1_time - t0) * 1000.0

    # Stage 3 & 4: Optimal TSMO, Otsu & Decision Tree
    t0 = time.perf_counter()
    bin_stage1, sel_thresh, decision_cat, to1, to2, otsu_val = decision_tree_binarize(
        prep_img,
        contrast=eff_ctr,
        t1=t1,
        t2=t2,
        t3=t3,
        d_min=d_min,
        d_max=d_max,
        p_factor=p_factor,
    )
    t1_time = time.perf_counter()
    meta.tsmo_to1 = to1
    meta.tsmo_to2 = to2
    meta.otsu_to = otsu_val
    meta.decision_category = decision_cat
    meta.selected_threshold = sel_thresh
    meta.timing_ms["stage3_4_decision_tree"] = (t1_time - t0) * 1000.0

    # Stage 5: Sokratis Smear Detection & Selective Nick
    t0 = time.perf_counter()
    smear_mask, smear_stats = sokratis_smear_detection(
        bin_stage1, tile_size=(32, 32), k_smear=k_smear
    )
    bin_stage2, refined_smear_pixels = selective_nick_refinement(
        prep_img, bin_stage1, smear_mask, win_size=win_size_nick, k_nick=k_nick
    )
    t1_time = time.perf_counter()
    meta.smear_mean_density = smear_stats["mean_density"]
    meta.smear_std_density = smear_stats["std_density"]
    meta.smear_threshold = smear_stats["smear_threshold"]
    meta.smear_segments_flagged = smear_stats["flagged_tiles"]
    meta.smear_ccs_flagged = smear_stats["flagged_ccs"]
    meta.smear_pixels_refined = refined_smear_pixels
    meta.timing_ms["stage5_smear_and_nick"] = (t1_time - t0) * 1000.0

    # Stage 6: Non-Deforming Post-Processing
    t0 = time.perf_counter()
    final_binary, post_stats = post_processing_algorithm1(
        bin_stage2, lambda_stat=lambda_post, min_cc_area=min_cc_area
    )
    t1_time = time.perf_counter()
    meta.post_process_stats = post_stats
    meta.timing_ms["stage6_post_processing"] = (t1_time - t0) * 1000.0

    total_time = (time.perf_counter() - t_start) * 1000.0
    meta.timing_ms["total_pipeline"] = total_time

    return final_binary, meta
