"""Bounded, thread-safe frame buffer for continuous edge capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Full, Queue
from threading import Lock


@dataclass(frozen=True)
class BufferedCameraFrame:
    store_id: int
    camera_id: int
    camera_name: str
    channel: str
    captured_at: datetime
    jpeg_bytes: bytes


class CameraFrameBuffer:
    """Keep recent compressed frames without allowing unbounded RAM growth.

    When processing falls behind and the buffer reaches capacity, the oldest
    frame is discarded so capture can continue. The drop counter makes this
    loss visible instead of silently blocking the capture thread.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._queue: Queue[BufferedCameraFrame] = Queue(maxsize=capacity)
        self._lock = Lock()
        self._captured = 0
        self._processed = 0
        self._dropped = 0

    def put(self, frame: BufferedCameraFrame) -> None:
        with self._lock:
            self._captured += 1
        try:
            self._queue.put_nowait(frame)
            return
        except Full:
            pass

        try:
            self._queue.get_nowait()
            self._queue.task_done()
        except Empty:
            pass
        else:
            with self._lock:
                self._dropped += 1

        self._queue.put_nowait(frame)

    def get(self, timeout: float) -> BufferedCameraFrame:
        return self._queue.get(timeout=timeout)

    def mark_processed(self) -> None:
        self._queue.task_done()
        with self._lock:
            self._processed += 1

    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "capacity": self._queue.maxsize,
                "queued": self._queue.qsize(),
                "captured": self._captured,
                "processed": self._processed,
                "dropped": self._dropped,
            }
