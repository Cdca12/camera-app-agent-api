from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from captured_events import record_captured_faces
from central_sync import CentralSyncService, CentralSyncSettings
from configuration import create_camera, list_collection_enabled_cameras
from database import (
    _copy_simulated_data,
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

    def _get(self, path: str) -> dict:
        self.requests.append((path, {}))
        return {
            "agent_id": "agent-id",
            "name": "Agent test",
            "store": {"id": "store-id", "name": "Maja Centro", "code": "maja-centro", "timezone": "America/Mazatlan"},
        }


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

    def test_assigned_installation_returns_only_the_agent_store(self) -> None:
        service = RecordingSyncService(self.settings, self.database_path)
        installation = service.get_assigned_installation()

        self.assertEqual(installation["store"]["code"], "maja-centro")
        self.assertIn(("/edge/installation", {}), service.requests)

    def test_camera_thumbnail_is_sent_once(self) -> None:
        thumbnail = b"\xff\xd8\xffcamera-thumbnail"
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO cameras (
                    store_id, name, channel, is_active, collection_enabled,
                    thumbnail_jpeg, thumbnail_synced
                ) VALUES (?, ?, ?, 1, 0, ?, 0)
                """,
                (self.store_id, "Cámara 1", "101", thumbnail),
            )
            connection.commit()

        service = RecordingSyncService(self.settings, self.database_path)
        service.sync_once()
        first_payload = next(payload for path, payload in service.requests if path == "/edge/cameras/sync")
        self.assertEqual(
            first_payload["cameras"][0]["thumbnail_base64"],
            base64.b64encode(thumbnail).decode("ascii"),
        )

        service.requests.clear()
        service.sync_once()
        second_payload = next(payload for path, payload in service.requests if path == "/edge/cameras/sync")
        self.assertNotIn("thumbnail_base64", second_payload["cameras"][0])

    def test_camera_collection_state_is_applied_from_central(self) -> None:
        camera = create_camera(
            self.store_id,
            "Cámara 1",
            "101",
            database_path=self.database_path,
        )
        service = RecordingSyncService(self.settings, self.database_path)
        original_post = service._post

        def post_with_command(path: str, payload: dict) -> dict:
            response = original_post(path, payload)
            if path == "/edge/cameras/sync":
                return {"camera_configs": [{"channel": 101, "collection_enabled": True}]}
            return response

        service._post = post_with_command
        service.sync_once()

        with database_connection(self.database_path) as connection:
            stored = connection.execute(
                "SELECT collection_enabled FROM cameras WHERE id = ?",
                (camera["id"],),
            ).fetchone()
        self.assertEqual(stored["collection_enabled"], 1)

    def test_collection_query_is_limited_to_the_assigned_store(self) -> None:
        assigned_camera = create_camera(
            self.store_id,
            "Cámara asignada",
            "304",
            collection_enabled=True,
            database_path=self.database_path,
        )
        with database_connection(self.database_path) as connection:
            previous_store = connection.execute(
                "INSERT INTO stores (name, code, timezone) VALUES (?, ?, ?)",
                ("Tienda anterior", "previous", "America/Mazatlan"),
            )
            connection.execute(
                """
                INSERT INTO cameras (store_id, name, channel, collection_enabled)
                VALUES (?, ?, ?, 1)
                """,
                (previous_store.lastrowid, "Cámara anterior", "501"),
            )
            connection.commit()

        cameras = list_collection_enabled_cameras(
            store_id=self.store_id,
            database_path=self.database_path,
        )

        self.assertEqual([camera["id"] for camera in cameras], [assigned_camera["id"]])

    def test_registering_existing_channel_refreshes_thumbnail_without_duplication(self) -> None:
        first_thumbnail = base64.b64encode(b"\xff\xd8\xfffirst").decode("ascii")
        second_thumbnail = base64.b64encode(b"\xff\xd8\xffsecond").decode("ascii")

        first = create_camera(
            self.store_id,
            "Cámara original",
            "101",
            preview_image=f"data:image/jpeg;base64,{first_thumbnail}",
            database_path=self.database_path,
        )
        refreshed = create_camera(
            self.store_id,
            "Cámara entrada",
            "101",
            preview_image=f"data:image/jpeg;base64,{second_thumbnail}",
            database_path=self.database_path,
        )

        self.assertEqual(refreshed["id"], first["id"])
        with database_connection(self.database_path) as connection:
            rows = connection.execute(
                "SELECT name, thumbnail_jpeg, thumbnail_synced FROM cameras"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Cámara entrada")
        self.assertEqual(rows[0]["thumbnail_jpeg"], b"\xff\xd8\xffsecond")
        self.assertEqual(rows[0]["thumbnail_synced"], 0)

    def test_simulated_copy_accepts_camera_thumbnail_columns(self) -> None:
        source_path = Path(self.temp_dir.name) / "source.db"
        target_path = Path(self.temp_dir.name) / "target.db"
        initialize_database(source_path)
        initialize_database(target_path)

        with database_connection(source_path) as connection:
            store = connection.execute(
                "INSERT INTO stores (name, code) VALUES (?, ?)",
                ("Tienda simulada", "tienda-simulada"),
            )
            camera = connection.execute(
                """
                INSERT INTO cameras (
                    store_id, name, channel, thumbnail_jpeg, thumbnail_synced
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (store.lastrowid, "Cámara simulada", "101", b"\xff\xd8\xffthumbnail"),
            )
            connection.execute(
                """
                INSERT INTO visitor_events (
                    store_id, camera_id, event_uuid, captured_at, gender,
                    age_estimate, age_bucket, data_source
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'male', 30, '25_34', 'simulated')
                """,
                (store.lastrowid, camera.lastrowid, "simulated-event"),
            )
            connection.commit()

        _copy_simulated_data(source_path, target_path)

        with database_connection(target_path) as connection:
            copied_camera = connection.execute(
                "SELECT name, thumbnail_jpeg FROM cameras WHERE channel = '101'"
            ).fetchone()
            copied_events = connection.execute(
                "SELECT COUNT(*) FROM visitor_events WHERE data_source = 'simulated'"
            ).fetchone()[0]
        self.assertEqual(copied_camera["name"], "Cámara simulada")
        self.assertIsNone(copied_camera["thumbnail_jpeg"])
        self.assertEqual(copied_events, 1)


if __name__ == "__main__":
    unittest.main()
