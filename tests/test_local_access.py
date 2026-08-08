from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from local_access import request_has_local_access


class LocalAccessTests(unittest.TestCase):
    def test_access_remains_open_when_a_key_is_not_configured(self) -> None:
        with patch.dict(os.environ, {"CAMERA_APP_LOCAL_API_KEY": ""}):
            self.assertTrue(request_has_local_access("/camera-frame", "GET", None))

    def test_health_is_public_when_a_key_is_configured(self) -> None:
        with patch.dict(os.environ, {"CAMERA_APP_LOCAL_API_KEY": "technical-key"}):
            self.assertTrue(request_has_local_access("/health", "GET", None))

    def test_technical_endpoint_requires_matching_key(self) -> None:
        with patch.dict(os.environ, {"CAMERA_APP_LOCAL_API_KEY": "technical-key"}):
            self.assertFalse(request_has_local_access("/camera-frame", "GET", None))
            self.assertFalse(request_has_local_access("/camera-frame", "GET", "incorrect"))
            self.assertTrue(
                request_has_local_access("/camera-frame", "GET", "technical-key")
            )


if __name__ == "__main__":
    unittest.main()
