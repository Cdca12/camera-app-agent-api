from __future__ import annotations

import base64

import cv2
import numpy as np


MAX_PREVIEW_WIDTH = 640
MAX_PREVIEW_BYTES = 200_000
PREVIEW_QUALITIES = (85, 78, 70, 62)
PREVIEW_WIDTHS = (640, 560, 480)
SCAN_PREVIEW_WIDTH = 220
SCAN_PREVIEW_QUALITY = 60


def build_camera_preview(frame: np.ndarray) -> str | None:
    """Build a detailed JPEG preview while respecting the sync size limit."""

    if frame.size == 0:
        return None

    height, width = frame.shape[:2]
    for target_width in PREVIEW_WIDTHS:
        preview_width = min(width, target_width, MAX_PREVIEW_WIDTH)
        preview_height = max(1, round(height * (preview_width / width)))
        preview = (
            frame
            if preview_width == width
            else cv2.resize(
                frame,
                (preview_width, preview_height),
                interpolation=cv2.INTER_AREA,
            )
        )
        for quality in PREVIEW_QUALITIES:
            encoded, image = cv2.imencode(
                ".jpg",
                preview,
                [cv2.IMWRITE_JPEG_QUALITY, quality],
            )
            if encoded and image.nbytes <= MAX_PREVIEW_BYTES:
                payload = base64.b64encode(image.tobytes()).decode("ascii")
                return f"data:image/jpeg;base64,{payload}"

        if preview_width == width:
            break

    return None


def build_scan_preview(frame: np.ndarray) -> str | None:
    """Build a small selector preview without slowing multi-channel scans."""

    if frame.size == 0:
        return None

    height, width = frame.shape[:2]
    preview_width = min(width, SCAN_PREVIEW_WIDTH)
    preview_height = max(1, round(height * (preview_width / width)))
    preview = (
        frame
        if preview_width == width
        else cv2.resize(
            frame,
            (preview_width, preview_height),
            interpolation=cv2.INTER_AREA,
        )
    )
    encoded, image = cv2.imencode(
        ".jpg",
        preview,
        [cv2.IMWRITE_JPEG_QUALITY, SCAN_PREVIEW_QUALITY],
    )
    if not encoded:
        return None

    payload = base64.b64encode(image.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"
