from __future__ import annotations

import unittest

from local_access import request_has_local_access
from local_setup import setup_page


class LocalSetupTests(unittest.TestCase):
    def test_setup_page_is_same_origin_html(self) -> None:
        response = setup_page()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Configurar agente local", response.body.decode("utf-8"))

    def test_setup_route_stays_available_for_entering_technical_key(self) -> None:
        self.assertTrue(request_has_local_access("/setup", "GET", None))


if __name__ == "__main__":
    unittest.main()
