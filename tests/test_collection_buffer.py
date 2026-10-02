from __future__ import annotations

from datetime import datetime
import unittest

from collection_buffer import BufferedCameraFrame, CameraFrameBuffer


def frame(camera_id: int) -> BufferedCameraFrame:
    return BufferedCameraFrame(
        store_id=1,
        camera_id=camera_id,
        camera_name=f"Camera {camera_id}",
        channel=str(camera_id),
        captured_at=datetime(2026, 10, 2, 8, 0, camera_id),
        jpeg_bytes=b"jpeg",
    )


class CameraFrameBufferTests(unittest.TestCase):
    def test_buffer_discards_oldest_frame_without_blocking_capture(self) -> None:
        buffer = CameraFrameBuffer(capacity=2)
        buffer.put(frame(1))
        buffer.put(frame(2))
        buffer.put(frame(3))

        self.assertEqual(buffer.get(timeout=0.01).camera_id, 2)
        buffer.mark_processed()
        self.assertEqual(buffer.get(timeout=0.01).camera_id, 3)
        buffer.mark_processed()
        self.assertEqual(
            buffer.status(),
            {
                "capacity": 2,
                "queued": 0,
                "captured": 3,
                "processed": 2,
                "dropped": 1,
            },
        )

    def test_buffer_tracks_normal_capture_and_processing(self) -> None:
        buffer = CameraFrameBuffer(capacity=2)
        buffer.put(frame(1))

        self.assertEqual(buffer.status()["queued"], 1)
        self.assertEqual(buffer.get(timeout=0.01).camera_id, 1)
        buffer.mark_processed()
        self.assertEqual(buffer.status()["processed"], 1)


if __name__ == "__main__":
    unittest.main()
