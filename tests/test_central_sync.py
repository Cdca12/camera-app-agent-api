from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from captured_events import record_captured_faces
from central_sync import CentralSyncService, CentralSyncSettings
from database import (
    count_pending_sync_events,
    database_connection,
    get_pending_sync_events,
    initialize_database,
)


class RecordingSyncService(CentralSyncService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests: list[tuple[str, dict]] = []

    def _post(self, path: str, payload: dict) -> dict:
        self.requests.append((path, payload))
        if path == "/edge/detections":
            return {"accepted_count": len(payload["events"]), "duplicate_count": 0}
        return {}


class CentralSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "agent.db"
        initialize_database(self.database_path)
        with database_connection(self.database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO stores (name, code, timezone) VALUES (?, ?, ?)",
                ("Local", "local", "America/Mazatlan"),
            )
            self.store_id = cursor.lastrowid
            connection.commit()

        self.settings = CentralSyncSettings(
            api_base_url="https://central.example/api/v1",
            agent_api_key="test-key",
            central_store_id="00000000-0000-0000-0000-000000000001",
            local_store_code="local",
            agent_name="Agent test",
            agent_version="0.3.0",
            interval_seconds=30,
            batch_size=50,
            default_camera_channel=1,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_complete_capture_is_queued_and_confirmed(self) -> None:
        recorded = record_captured_faces(
            self.store_id,
            "Cámara del dispositivo",
            "device-local",
            [{"gender": "Masculino", "age": 29, "age_bucket": "25-34"}],
            self.database_path,
        )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(count_pending_sync_events(self.store_id, self.database_path), 1)

        service = RecordingSyncService(self.settings, self.database_path)
        result = service.sync_once()

        self.assertEqual(result["synced_events"], 1)
        self.assertEqual(count_pending_sync_events(self.store_id, self.database_path), 0)
        detection_request = next(payload for path, payload in service.requests if path == "/edge/detections")
        self.assertEqual(detection_request["events"][0]["camera_channel"], 1)
        self.assertEqual(detection_request["events"][0]["gender"], "male")
        self.assertEqual(detection_request["events"][0]["age_range"], "25_34")

    def test_incomplete_capture_is_not_persisted_or_queued(self) -> None:
        recorded = record_captured_faces(
            self.store_id,
            "Cámara del dispositivo",
            "device-local",
            [{"gender": "N/A", "age": None, "age_bucket": "N/A"}],
            self.database_path,
        )

        self.assertEqual(recorded, [])
        self.assertEqual(count_pending_sync_events(self.store_id, self.database_path), 0)
        self.assertEqual(get_pending_sync_events(self.store_id, 10, self.database_path), [])


if __name__ == "__main__":
    unittest.main()
