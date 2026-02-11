"""
Image preprocessing module for OCR engine.

Advanced pipeline for old document preprocessing (based on 2025/2026 best practices):
1. Median blur - remove salt-and-pepper noise
2. Denoise - remove general noise (fastNlMeansDenoising)
3. CLAHE - adaptive contrast enhancement
4. Morphological opening - remove scan artifacts
5. Deskew - straighten rotated scans
6. Segment text vs background + crop
7. Unsharp masking - sharpen text edges
8. Resize - optimize for upload speed and cost
"""

import logging
import math
import os
import re
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ============ CONFIGURATION ============
# Adjust these based on your document quality


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_tuple_int(name: str, default: tuple[int, int]) -> tuple[int, int]:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    parts = [p for p in re.split(r"[x,]", val) if p.strip()]
    if len(parts) < 2:
        return default
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return default


MAX_DIMENSION = _env_int(
    "OCR_PREPROC_MAX_DIMENSION", 3200
)  # Max width/height (3200px ~= 275 DPI for A4, better for handwriting)
MEDIAN_KERNEL = _env_int("OCR_PREPROC_MEDIAN_KERNEL", 3)  # Median blur kernel
DENOISE_STRENGTH = _env_int("OCR_PREPROC_DENOISE_STRENGTH", 8)
CLAHE_CLIP_LIMIT = _env_float("OCR_PREPROC_CLAHE_CLIP_LIMIT", 2.0)
CLAHE_GRID_SIZE = _env_tuple_int("OCR_PREPROC_CLAHE_GRID_SIZE", (8, 8))
MORPH_KERNEL_SIZE = _env_int("OCR_PREPROC_MORPH_KERNEL_SIZE", 2)
UNSHARP_AMOUNT = _env_float("OCR_PREPROC_UNSHARP_AMOUNT", 1.2)
UNSHARP_RADIUS = _env_int("OCR_PREPROC_UNSHARP_RADIUS", 1)

# Margin cleaning config
MARGIN_PERCENT = _env_float("OCR_PREPROC_MARGIN_PERCENT", 0.05)
DARK_THRESHOLD = _env_int("OCR_PREPROC_DARK_THRESHOLD", 60)
MARGIN_INK_RATIO_MAX = _env_float("OCR_PREPROC_MARGIN_INK_RATIO_MAX", 0.01)
MARGIN_SHADOW_MEAN_MAX = _env_int("OCR_PREPROC_MARGIN_SHADOW_MEAN_MAX", 200)

# Bleed-through / background normalization config
BACKGROUND_KERNEL_RATIO = _env_float("OCR_PREPROC_BACKGROUND_KERNEL_RATIO", 0.025)
BACKGROUND_KERNEL_MIN = _env_int("OCR_PREPROC_BACKGROUND_KERNEL_MIN", 31)

# Local contrast + thin stroke enhancement
LOCAL_CONTRAST_SIGMA = _env_float("OCR_PREPROC_LOCAL_CONTRAST_SIGMA", 12.0)
LOCAL_CONTRAST_AMOUNT = _env_float("OCR_PREPROC_LOCAL_CONTRAST_AMOUNT", 0.35)
BLACKHAT_KERNEL_SIZE = _env_int("OCR_PREPROC_BLACKHAT_KERNEL_SIZE", 5)
BLACKHAT_STRENGTH = _env_float("OCR_PREPROC_BLACKHAT_STRENGTH", 0.45)

# Adaptive binarization (Sauvola)
ENABLE_ADAPTIVE_BINARIZATION = _env_bool("OCR_PREPROC_ENABLE_ADAPTIVE_BINARIZATION", False)
SAUVOLA_WINDOW = _env_int("OCR_PREPROC_SAUVOLA_WINDOW", 31)
SAUVOLA_K = _env_float("OCR_PREPROC_SAUVOLA_K", 0.2)
SAUVOLA_R = _env_float("OCR_PREPROC_SAUVOLA_R", 128.0)

# Text mask segmentation for crop
TEXT_MASK_BLOCK_SIZE = _env_int("OCR_PREPROC_TEXT_MASK_BLOCK_SIZE", 31)
TEXT_MASK_C = _env_int("OCR_PREPROC_TEXT_MASK_C", 12)
TEXT_MASK_OPEN_KERNEL = _env_int("OCR_PREPROC_TEXT_MASK_OPEN_KERNEL", 3)
TEXT_MASK_CLOSE_KERNEL = _env_int("OCR_PREPROC_TEXT_MASK_CLOSE_KERNEL", 9)
TEXT_MASK_CLOSE_ITERS = _env_int("OCR_PREPROC_TEXT_MASK_CLOSE_ITERS", 2)
TEXT_MASK_DILATE_ITERS = _env_int("OCR_PREPROC_TEXT_MASK_DILATE_ITERS", 1)
TEXT_MASK_MIN_AREA_RATIO = _env_float("OCR_PREPROC_TEXT_MASK_MIN_AREA_RATIO", 0.0005)

# Border trim by ink density
TRIM_BAND_RATIO = _env_float("OCR_PREPROC_TRIM_BAND_RATIO", 0.02)
TRIM_INK_RATIO_MAX = _env_float("OCR_PREPROC_TRIM_INK_RATIO_MAX", 0.02)
TRIM_MAX_RATIO = _env_float("OCR_PREPROC_TRIM_MAX_RATIO", 0.15)
TRIM_MIN_DIMENSION = _env_int("OCR_PREPROC_TRIM_MIN_DIMENSION", 200)


def _build_text_mask(img: np.ndarray) -> np.ndarray:
    """Build a coarse text mask for segmentation and margin safety."""
    block = TEXT_MASK_BLOCK_SIZE if TEXT_MASK_BLOCK_SIZE % 2 == 1 else TEXT_MASK_BLOCK_SIZE + 1
    blur = cv2.GaussianBlur(img, (0, 0), sigmaX=1.0)
    binary = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        block,
        TEXT_MASK_C,
    )
    open_k = cv2.getStructuringElement(
        cv2.MORPH_RECT, (TEXT_MASK_OPEN_KERNEL, TEXT_MASK_OPEN_KERNEL)
    )
    close_k = cv2.getStructuringElement(
        cv2.MORPH_RECT, (TEXT_MASK_CLOSE_KERNEL, TEXT_MASK_CLOSE_KERNEL)
    )
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, open_k, iterations=1)
    merged = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, close_k, iterations=TEXT_MASK_CLOSE_ITERS)
    merged = cv2.dilate(merged, close_k, iterations=TEXT_MASK_DILATE_ITERS)
    return merged


def _whiten_dark_margins(img: np.ndarray) -> np.ndarray:
    """
    Aggressively whiten very dark borders (scanner artifacts).

    Scans often have black/dark gray borders from scanning.
    This function detects dark regions at edges and whitens them.
    """
    h, w = img.shape[:2]
    text_mask = _build_text_mask(img)

    # Calculate margin sizes (5% of each dimension)
    margin_y = int(h * MARGIN_PERCENT)
    margin_x = int(w * MARGIN_PERCENT)

    result = img.copy()

    # Top margin - if mostly dark and almost no ink, whiten it
    top_region = img[0:margin_y, :]
    top_mask = text_mask[0:margin_y, :]
    top_ink = np.mean(top_mask > 0)
    if np.mean(top_region) < DARK_THRESHOLD:
        result[0:margin_y, :] = 255
        logger.debug(f"[Preprocess] Whitened top margin ({margin_y}px)")
    elif top_ink < MARGIN_INK_RATIO_MAX and np.mean(top_region) < MARGIN_SHADOW_MEAN_MAX:
        result[0:margin_y, :] = 255
        logger.debug(f"[Preprocess] Whitened top shadow margin ({margin_y}px)")

    # Bottom margin
    bottom_region = img[h - margin_y : h, :]
    bottom_mask = text_mask[h - margin_y : h, :]
    bottom_ink = np.mean(bottom_mask > 0)
    if np.mean(bottom_region) < DARK_THRESHOLD:
        result[h - margin_y : h, :] = 255
        logger.debug(f"[Preprocess] Whitened bottom margin ({margin_y}px)")
    elif bottom_ink < MARGIN_INK_RATIO_MAX and np.mean(bottom_region) < MARGIN_SHADOW_MEAN_MAX:
        result[h - margin_y : h, :] = 255
        logger.debug(f"[Preprocess] Whitened bottom shadow margin ({margin_y}px)")

    # Left margin
    left_region = img[:, 0:margin_x]
    left_mask = text_mask[:, 0:margin_x]
    left_ink = np.mean(left_mask > 0)
    if np.mean(left_region) < DARK_THRESHOLD:
        result[:, 0:margin_x] = 255
        logger.debug(f"[Preprocess] Whitened left margin ({margin_x}px)")
    elif left_ink < MARGIN_INK_RATIO_MAX and np.mean(left_region) < MARGIN_SHADOW_MEAN_MAX:
        result[:, 0:margin_x] = 255
        logger.debug(f"[Preprocess] Whitened left shadow margin ({margin_x}px)")

    # Right margin
    right_region = img[:, w - margin_x : w]
    right_mask = text_mask[:, w - margin_x : w]
    right_ink = np.mean(right_mask > 0)
    if np.mean(right_region) < DARK_THRESHOLD:
        result[:, w - margin_x : w] = 255
        logger.debug(f"[Preprocess] Whitened right margin ({margin_x}px)")
    elif right_ink < MARGIN_INK_RATIO_MAX and np.mean(right_region) < MARGIN_SHADOW_MEAN_MAX:
        result[:, w - margin_x : w] = 255
        logger.debug(f"[Preprocess] Whitened right shadow margin ({margin_x}px)")

    # Also check for dark vertical spine in center (book scans)
    center_x = w // 2
    spine_width = int(w * 0.03)  # 3% of width
    spine_region = img[:, center_x - spine_width : center_x + spine_width]
    spine_mask = text_mask[:, center_x - spine_width : center_x + spine_width]
    spine_ink = np.mean(spine_mask > 0)
    if np.mean(spine_region) < DARK_THRESHOLD + 20 and spine_ink < MARGIN_INK_RATIO_MAX:
        result[:, center_x - spine_width : center_x + spine_width] = 255
        logger.debug(f"[Preprocess] Whitened center spine ({spine_width * 2}px)")

    return result


def _median_blur(img: np.ndarray) -> np.ndarray:
    """Remove salt-and-pepper noise using median filter.

    Effective for old scans with speckle noise.
    Kernel must be odd number (3, 5, 7...).
    """
    return cv2.medianBlur(img, MEDIAN_KERNEL)


def _denoise(img: np.ndarray) -> np.ndarray:
    """Remove noise using Non-local Means Denoising.

    Better for general noise than median blur, but slower.
    """
    return cv2.fastNlMeansDenoising(
        img, None, h=DENOISE_STRENGTH, templateWindowSize=7, searchWindowSize=21
    )


def _apply_clahe(img: np.ndarray) -> np.ndarray:
    """Apply CLAHE (Contrast Limited Adaptive Histogram Equalization).

    Better than global histogram equalization for documents with uneven lighting.
    Recommended over hard binarization for VLM (Gemini, GPT-4V, Claude).
    """
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_GRID_SIZE)
    return clahe.apply(img)


def _background_kernel_size(img: np.ndarray) -> int:
    h, w = img.shape[:2]
    k = int(min(h, w) * BACKGROUND_KERNEL_RATIO)
    k = max(k, BACKGROUND_KERNEL_MIN)
    if k % 2 == 0:
        k += 1
    return k


def _normalize_background(img: np.ndarray) -> np.ndarray:
    """
    Normalize uneven illumination and reduce bleed-through by dividing
    by a large-kernel median background.
    """
    k = _background_kernel_size(img)
    background = cv2.medianBlur(img, k)
    # Avoid division by zero
    background = cv2.max(background, 1)
    normalized = cv2.divide(img, background, scale=255)
    return cv2.normalize(normalized, None, 0, 255, cv2.NORM_MINMAX)


def _local_contrast_boost(img: np.ndarray) -> np.ndarray:
    """Boost local contrast while preserving global tone."""
    blur = cv2.GaussianBlur(img, (0, 0), sigmaX=LOCAL_CONTRAST_SIGMA)
    boosted = cv2.addWeighted(img, 1.0 + LOCAL_CONTRAST_AMOUNT, blur, -LOCAL_CONTRAST_AMOUNT, 0)
    return boosted


def _enhance_thin_strokes(img: np.ndarray) -> np.ndarray:
    """Darken thin strokes using blackhat morphology."""
    k = BLACKHAT_KERNEL_SIZE if BLACKHAT_KERNEL_SIZE % 2 == 1 else BLACKHAT_KERNEL_SIZE + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    blackhat = cv2.morphologyEx(img, cv2.MORPH_BLACKHAT, kernel)
    enhanced = cv2.addWeighted(img, 1.0, blackhat, -BLACKHAT_STRENGTH, 0)
    return enhanced


def _sauvola_binarize(img: np.ndarray) -> np.ndarray:
    """Adaptive binarization suited for historical documents."""
    window = SAUVOLA_WINDOW if SAUVOLA_WINDOW % 2 == 1 else SAUVOLA_WINDOW + 1
    img_f = img.astype(np.float32)

    mean = cv2.boxFilter(img_f, ddepth=-1, ksize=(window, window))
    sqmean = cv2.boxFilter(img_f * img_f, ddepth=-1, ksize=(window, window))
    var = cv2.max(sqmean - mean * mean, 0)
    std = cv2.sqrt(var)

    thresh = mean * (1.0 + SAUVOLA_K * ((std / SAUVOLA_R) - 1.0))
    binary = np.where(img_f <= thresh, 0, 255).astype(np.uint8)
    return binary


def _morphological_opening(img: np.ndarray) -> np.ndarray:
    """Apply morphological opening to remove small artifacts.

    Opening = erosion followed by dilation.
    Removes small bright spots (noise) while preserving text.
    """
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MORPH_KERNEL_SIZE, MORPH_KERNEL_SIZE))
    return cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel)


def _detect_skew_angle(img: np.ndarray) -> float:
    """Detect skew angle using Hough Line Transform.

    Returns angle in degrees. Positive = clockwise rotation needed.
    """
    # Edge detection
    edges = cv2.Canny(img, 50, 150, apertureSize=3)

    # Detect lines
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10)

    if lines is None or len(lines) == 0:
        return 0.0

    # Calculate angles of all lines
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 - x1 == 0:
            continue
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        # Only consider near-horizontal lines (text lines)
        if -45 < angle < 45:
            angles.append(angle)

    if not angles:
        return 0.0

    # Return median angle (robust to outliers)
    median_angle = np.median(angles)

    # Limit correction to reasonable range
    if abs(median_angle) > 10:
        return 0.0  # Too skewed, probably wrong detection

    return median_angle


def _deskew(img: np.ndarray) -> np.ndarray:
    """Straighten a skewed image."""
    angle = _detect_skew_angle(img)

    if abs(angle) < 0.5:
        return img  # No significant skew

    logger.debug(f"[Preprocess] Deskew: rotating by {angle:.2f}°")

    h, w = img.shape[:2]
    center = (w // 2, h // 2)

    # Rotation matrix
    M = cv2.getRotationMatrix2D(center, angle, 1.0)

    # Calculate new bounding box size
    cos = np.abs(M[0, 0])
    sin = np.abs(M[0, 1])
    new_w = int(h * sin + w * cos)
    new_h = int(h * cos + w * sin)

    # Adjust rotation matrix for new size
    M[0, 2] += (new_w - w) / 2
    M[1, 2] += (new_h - h) / 2

    # Rotate with replicated border
    rotated = cv2.warpAffine(img, M, (new_w, new_h), borderMode=cv2.BORDER_REPLICATE)

    return rotated


def _segment_text_and_crop(img: np.ndarray, margin_percent: float = 0.02) -> np.ndarray:
    """
    Segment text vs background and crop to main text area.
    Shadow-aware by requiring a minimum ink density.
    """
    text_mask = _build_text_mask(img)

    contours, _ = cv2.findContours(text_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img

    h, w = img.shape[:2]

    valid_contours = []
    total_area = h * w

    for cnt in contours:
        area = cv2.contourArea(cnt)
        x, y, cw, ch = cv2.boundingRect(cnt)

        # Keep if it looks like text (horizontal-ish or large enough)
        is_large = area > total_area * TEXT_MASK_MIN_AREA_RATIO
        is_wide = cw > w * 0.02

        if is_large or is_wide:
            valid_contours.append(cnt)

    if not valid_contours:
        return img

    total_cnt_points = np.concatenate(valid_contours)
    x, y, bb_w, bb_h = cv2.boundingRect(total_cnt_points)

    if bb_w < 50 or bb_h < 50:
        return img

    pad_x = int(w * margin_percent)
    pad_y = int(h * margin_percent)

    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(w, x + bb_w + pad_x)
    y2 = min(h, y + bb_h + pad_y)

    cropped = img[y1:y2, x1:x2]
    logger.debug(f"[Preprocess] Smart Crop: {w}x{h} -> {x2 - x1}x{y2 - y1}")
    return cropped


def _trim_borders_by_ink_density(img: np.ndarray) -> np.ndarray:
    """
    Iteratively trim borders while ink density is below threshold.
    Useful when margins contain sparse speckles that defeat contour-based crop.
    """
    h, w = img.shape[:2]
    band = max(8, int(min(h, w) * TRIM_BAND_RATIO))
    max_trim = int(min(h, w) * TRIM_MAX_RATIO)

    top = 0
    bottom = h
    left = 0
    right = w

    text_mask = _build_text_mask(img)

    def _ink_ratio(band_mask: np.ndarray) -> float:
        if band_mask.size == 0:
            return 1.0
        return float(np.mean(band_mask > 0))

    trimmed = True
    while trimmed:
        trimmed = False
        if bottom - top <= TRIM_MIN_DIMENSION or right - left <= TRIM_MIN_DIMENSION:
            break

        # Top band
        if top + band < bottom:
            ink = _ink_ratio(text_mask[top : top + band, left:right])
            if ink < TRIM_INK_RATIO_MAX and top < max_trim:
                top += band
                trimmed = True

        # Bottom band
        if bottom - band > top:
            ink = _ink_ratio(text_mask[bottom - band : bottom, left:right])
            if ink < TRIM_INK_RATIO_MAX and (h - bottom) < max_trim:
                bottom -= band
                trimmed = True

        # Left band
        if left + band < right:
            ink = _ink_ratio(text_mask[top:bottom, left : left + band])
            if ink < TRIM_INK_RATIO_MAX and left < max_trim:
                left += band
                trimmed = True

        # Right band
        if right - band > left:
            ink = _ink_ratio(text_mask[top:bottom, right - band : right])
            if ink < TRIM_INK_RATIO_MAX and (w - right) < max_trim:
                right -= band
                trimmed = True

    if top == 0 and bottom == h and left == 0 and right == w:
        return img

    cropped = img[top:bottom, left:right]
    logger.debug(f"[Preprocess] Ink Trim: {w}x{h} -> {right - left}x{bottom - top}")
    return cropped


def _unsharp_mask(
    img: np.ndarray, amount: float = UNSHARP_AMOUNT, radius: int = UNSHARP_RADIUS
) -> np.ndarray:
    """Apply unsharp masking to sharpen text edges.

    This is the final step - sharpens details after all other processing.
    Be careful: too aggressive sharpening can amplify noise.

    Args:
        img: Grayscale image
        amount: Sharpening strength (1.0 = subtle, 2.0 = strong)
        radius: Blur radius for mask creation
    """
    # Create blurred version
    blurred = cv2.GaussianBlur(img, (0, 0), radius)

    # Unsharp mask formula: sharpened = original + amount * (original - blurred)
    sharpened = cv2.addWeighted(img, 1 + amount, blurred, -amount, 0)

    return sharpened


def _resize_if_needed(img: np.ndarray, max_dim: int = MAX_DIMENSION) -> np.ndarray:
    """Resize image if larger than max_dim, preserving aspect ratio."""
    h, w = img.shape[:2]

    if max(h, w) <= max_dim:
        return img

    if w > h:
        new_w = max_dim
        new_h = int(h * (max_dim / w))
    else:
        new_h = max_dim
        new_w = int(w * (max_dim / h))

    logger.debug(f"[Preprocess] Resize: {w}x{h} -> {new_w}x{new_h}")
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def preprocess_image_smart(input_path: Path, temp_dir: Path) -> Path:
    """
    Apply full preprocessing pipeline for OCR optimization.

    Pipeline (based on 2025/2026 best practices):
    1. Load as grayscale
    2. Background normalization (uneven illumination + bleed-through)
    3. Median blur (salt-and-pepper noise)
    4. Denoise (general noise removal)
    5. CLAHE (normalize contrast/background)
    6. Local contrast boost + thin stroke enhancement
    7. Morphological opening (remove artifacts)
    8. Deskew (straighten rotation)
    9. Segment text vs background + crop
    10. Unsharp masking (sharpen text)
    11. Adaptive binarization (Sauvola, optional)
    12. Resize (optimize for upload)

    Args:
        input_path: Path to the source image
        temp_dir: Directory to store preprocessed images

    Returns:
        Path to preprocessed image, or original if preprocessing fails
    """
    try:
        # 1. Load as grayscale
        img = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            logger.warning(f"Could not read image: {input_path}")
            return input_path

        original_shape = img.shape

        # 1.2. Resize EARLY to speed up processing (Critical optimization)
        # Doing this before heavy operations (NL-means, local contrast) saves minutes.
        img = _resize_if_needed(img)

        # 1.5. Background normalization (bleed-through + uneven lighting)
        img = _normalize_background(img)

        # 1.6. Whiten dark margins (scanner borders, spine shadow)
        img = _whiten_dark_margins(img)

        # 2. Median blur (salt-and-pepper noise)
        img = _median_blur(img)

        # 3. Denoise (general noise)
        img = _denoise(img)

        # 4. CLAHE (contrast normalization)
        img = _apply_clahe(img)

        # 4.5. Local contrast + thin stroke enhancement
        img = _local_contrast_boost(img)
        img = _enhance_thin_strokes(img)

        # 5. Morphological opening (artifact removal)
        img = _morphological_opening(img)

        # 6. Deskew
        img = _deskew(img)

        # 7. Segment text vs background and crop
        img = _segment_text_and_crop(img)

        # 7.5 Trim borders by low ink density
        img = _trim_borders_by_ink_density(img)

        # 8. Unsharp masking (sharpen text)
        img = _unsharp_mask(img)

        # 9. Adaptive binarization (optional, for difficult manuscripts)
        if ENABLE_ADAPTIVE_BINARIZATION:
            img = _sauvola_binarize(img)

        # 10. (Moved to start)

        # Save result
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"preproc_{input_path.name}"

        # Save as JPEG with good quality (smaller than PNG, good for upload)
        cv2.imwrite(str(temp_path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])

        final_shape = img.shape
        if original_shape != final_shape:
            logger.info(f"[Preprocess] Resized/Cropped: {original_shape} -> {final_shape}")

        return temp_path

    except Exception as e:
        logger.warning(f"Preprocessing failed for {input_path}: {e}")
        return input_path


def clear_temp_images(temp_dir: Path) -> None:
    """Remove all preprocessed images from temp directory."""
    import shutil

    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"Could not clear temp images: {e}")
