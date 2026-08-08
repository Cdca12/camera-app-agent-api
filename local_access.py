"""Optional access guard for the technical API exposed on the local network."""

from __future__ import annotations

import hmac
import os


LOCAL_ACCESS_HEADER = "X-CameraApp-Local-Key"
PUBLIC_PATHS = frozenset({"/", "/health", "/docs", "/openapi.json", "/redoc", "/setup"})


def local_access_is_configured() -> bool:
    return bool(os.getenv("CAMERA_APP_LOCAL_API_KEY", "").strip())


def request_has_local_access(path: str, method: str, supplied_key: str | None) -> bool:
    """Allow liveness and documentation, otherwise enforce the optional key."""

    if method.upper() == "OPTIONS" or path in PUBLIC_PATHS:
        return True

    expected_key = os.getenv("CAMERA_APP_LOCAL_API_KEY", "").strip()
    if not expected_key:
        return True
    return bool(supplied_key) and hmac.compare_digest(supplied_key, expected_key)
