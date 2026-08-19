from __future__ import annotations

import base64
import unittest

import cv2
import numpy as np

from camera_preview import (
    MAX_PREVIEW_BYTES,
    SCAN_PREVIEW_WIDTH,
    build_camera_preview,
    build_scan_preview,
)


class CameraPreviewTests(unittest.TestCase):
    def test_preview_preserves_useful_resolution_within_sync_limit(self) -> None:
        random = np.random.default_rng(42)
        frame = random.integers(0, 256, size=(1080, 1920, 3), dtype=np.uint8)

        preview = build_camera_preview(frame)

        self.assertIsNotNone(preview)
        prefix = "data:image/jpeg;base64,"
        self.assertTrue(preview.startswith(prefix))
        payload = base64.b64decode(preview[len(prefix):])
        self.assertLessEqual(len(payload), MAX_PREVIEW_BYTES)
        decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(decoded)
        self.assertGreaterEqual(decoded.shape[1], 480)
        self.assertEqual(decoded.shape[1] / decoded.shape[0], 16 / 9)

    def test_empty_frame_has_no_preview(self) -> None:
        self.assertIsNone(build_camera_preview(np.empty((0, 0, 3), dtype=np.uint8)))

    def test_scan_preview_is_small_and_registration_preview_is_detailed(self) -> None:
        frame = np.full((1080, 1920, 3), 127, dtype=np.uint8)

        scan_payload = _decode_preview(build_scan_preview(frame))
        registration_payload = _decode_preview(build_camera_preview(frame))

        scan_image = cv2.imdecode(np.frombuffer(scan_payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        registration_image = cv2.imdecode(
            np.frombuffer(registration_payload, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        self.assertEqual(scan_image.shape[1], SCAN_PREVIEW_WIDTH)
        self.assertEqual(registration_image.shape[1], 640)
        self.assertLess(len(scan_payload), len(registration_payload))


def _decode_preview(preview: str | None) -> bytes:
    if preview is None:
        raise AssertionError("Se esperaba una miniatura")
    return base64.b64decode(preview.split(",", 1)[1])


if __name__ == "__main__":
    unittest.main()
