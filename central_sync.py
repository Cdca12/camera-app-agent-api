"""Reliable outbound synchronization from the local agent to CameraApp Central.

Only operational metadata and complete demographic events leave the local device.
Frames, RTSP addresses and credentials intentionally remain in the local SQLite
database and are never part of this module's payloads.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from database import (
    count_pending_sync_events,
    get_central_sync_state,
    get_pending_sync_events,
    get_store_by_code,
    get_sync_cameras,
    mark_sync_events_failed,
    mark_sync_events_synced,
    set_central_sync_state,
)
from operational_metrics import SystemMetricsCollector


AGENT_VERSION = "0.3.0"


@dataclass(frozen=True)
class CentralSyncSettings:
    api_base_url: str
    agent_api_key: str
    local_store_code: str
    agent_name: str
    agent_version: str
    interval_seconds: float
    batch_size: int
    default_camera_channel: int | None

    @property
    def is_configured(self) -> bool:
        # The API key is enrolled for exactly one central store, so the agent
        # must not also receive a mutable store identifier in local settings.
        return bool(self.api_base_url and self.agent_api_key)

    @classmethod
    def from_environment(cls) -> "CentralSyncSettings":
        base_url = os.getenv("CAMERA_APP_CENTRAL_API_URL", "").strip().rstrip("/")
        if base_url and not base_url.endswith("/api/v1"):
            base_url = f"{base_url}/api/v1"
        return cls(
            api_base_url=base_url,
            agent_api_key=os.getenv("CAMERA_APP_AGENT_API_KEY", "").strip(),
            local_store_code=os.getenv("CAMERA_APP_LOCAL_STORE_CODE", "local").strip().lower() or "local",
            agent_name=os.getenv("CAMERA_APP_AGENT_NAME", socket.gethostname()).strip() or socket.gethostname(),
            agent_version=os.getenv("CAMERA_APP_AGENT_VERSION", AGENT_VERSION).strip() or AGENT_VERSION,
            interval_seconds=_get_positive_float("CAMERA_APP_SYNC_INTERVAL_SECONDS", 30, 10),
            batch_size=_get_bounded_int("CAMERA_APP_SYNC_BATCH_SIZE", 50, 1, 250),
            default_camera_channel=_get_optional_positive_int("CAMERA_APP_DEFAULT_CAMERA_CHANNEL"),
        )


class CentralSyncService:
    def __init__(
        self,
        settings: CentralSyncSettings | None = None,
        database_path: Path | None = None,
    ) -> None:
        self.settings = settings or CentralSyncSettings.from_environment()
        self.database_path = database_path
        self.metrics = SystemMetricsCollector()

    def status(self) -> dict[str, Any]:
        local_store = self._get_local_store()
        state = get_central_sync_state(self.database_path)
        pending_events = (
            count_pending_sync_events(local_store["id"], self.database_path)
            if local_store
            else 0
        )
        return {
            "configured": self.settings.is_configured,
            "local_store_code": self.settings.local_store_code,
            "local_store_found": bool(local_store),
            "pending_events": pending_events,
            "last_sync_at": state.get("last_sync_at"),
            "last_heartbeat_at": state.get("last_heartbeat_at"),
            "last_error": state.get("last_error"),
        }

    def sync_once(self) -> dict[str, Any]:
        if not self.settings.is_configured:
            return self.status()

        local_store = self._get_local_store()
        if not local_store:
            message = f"No existe la tienda local '{self.settings.local_store_code}' para sincronizar"
            self._record_error(message)
            return {**self.status(), "error": message}

        try:
            pending_events = count_pending_sync_events(local_store["id"], self.database_path)
            metrics = self.metrics.collect()
            self._post(
                "/edge/heartbeat",
                {
                    "reported_at": _now_iso(),
                    "status": "online",
                    "agent_version": self.settings.agent_version,
                    "pending_events": pending_events,
                    "cpu_percent": metrics["cpu_percent"],
                    "memory_percent": metrics["memory_percent"],
                    "temperature_celsius": metrics["temperature_celsius"],
                    "disk_percent": metrics["disk_percent"],
                },
            )
            set_central_sync_state("last_heartbeat_at", _now_iso(), self.database_path)
            self._sync_cameras(local_store["id"])
            synced_events = self._sync_events(local_store)
            set_central_sync_state("last_sync_at", _now_iso(), self.database_path)
            set_central_sync_state("last_error", None, self.database_path)
            return {**self.status(), "synced_events": synced_events}
        except CentralSyncError as error:
            self._record_error(str(error))
            return {**self.status(), "error": str(error)}

    def get_assigned_installation(self) -> dict[str, Any]:
        """Retrieve the one store this agent was enrolled for in Central."""

        if not self.settings.is_configured:
            raise CentralSyncError("La vinculación con CameraApp Central está pendiente de configuración")
        payload = self._get("/edge/installation")
        store = payload.get("store")
        if not isinstance(store, dict):
            raise CentralSyncError("La API central no devolvió una tienda asignada válida")
        return payload

    def _get_local_store(self) -> dict | None:
        return get_store_by_code(self.settings.local_store_code, self.database_path)

    def _sync_cameras(self, local_store_id: int) -> None:
        payload_cameras = []
        for camera in get_sync_cameras(local_store_id, self.database_path):
            channel = self._central_channel(camera["channel"])
            if channel is None:
                continue
            payload_cameras.append(
                {
                    "name": camera["name"],
                    "channel": channel,
                    "collection_enabled": bool(camera["collection_enabled"]),
                    "status": "collecting" if camera["collection_enabled"] else "ready",
                }
            )
        if payload_cameras:
            self._post("/edge/cameras/sync", {"cameras": payload_cameras})

    def _sync_events(self, local_store: dict) -> int:
        events = get_pending_sync_events(
            local_store["id"],
            self.settings.batch_size,
            self.database_path,
        )
        if not events:
            return 0

        payload_events = []
        skipped_event_ids = []
        for event in events:
            channel = self._central_channel(event["channel"])
            if channel is None:
                skipped_event_ids.append(event["event_uuid"])
                continue
            payload_events.append(
                {
                    "event_id": event["event_uuid"],
                    "camera_channel": channel,
                    "observed_at": _as_timezone_aware_iso(event["captured_at"], event["timezone"]),
                    "gender": event["gender"],
                    "age_range": event["age_bucket"],
                }
            )

        if skipped_event_ids:
            mark_sync_events_failed(
                skipped_event_ids,
                "No hay canal central para esta cámara. Define CAMERA_APP_DEFAULT_CAMERA_CHANNEL o usa un canal numérico.",
                self.database_path,
            )

        if not payload_events:
            return 0

        event_ids = [event["event_id"] for event in payload_events]
        try:
            response = self._post("/edge/detections", {"events": payload_events})
            accepted_total = int(response.get("accepted_count", 0)) + int(response.get("duplicate_count", 0))
            if accepted_total != len(event_ids):
                raise CentralSyncError("La API central no confirmó todas las detecciones enviadas")
        except CentralSyncError as error:
            mark_sync_events_failed(event_ids, str(error), self.database_path)
            raise

        mark_sync_events_synced(event_ids, self.database_path)
        return len(event_ids)

    def _central_channel(self, channel: object) -> int | None:
        try:
            value = int(str(channel).strip())
            return value if value > 0 else None
        except (TypeError, ValueError):
            return self.settings.default_camera_channel

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload)

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "X-CameraApp-Agent-Key": self.settings.agent_api_key,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.settings.api_base_url}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=15) as response:
                raw_payload = response.read().decode("utf-8")
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            raise CentralSyncError(f"La API central respondió {error.code}: {detail}") from error
        except URLError as error:
            raise CentralSyncError("No se pudo conectar con la API central") from error
        except OSError as error:
            raise CentralSyncError("No se pudo conectar con la API central") from error

        try:
            return json.loads(raw_payload) if raw_payload else {}
        except json.JSONDecodeError as error:
            raise CentralSyncError("La API central devolvió una respuesta inválida") from error

    def _record_error(self, message: str) -> None:
        set_central_sync_state("last_error", message[:500], self.database_path)


class CentralSyncError(Exception):
    pass


def run_central_sync_monitor(
    stop_event: threading.Event,
    service: CentralSyncService | None = None,
) -> None:
    service = service or CentralSyncService()
    while not stop_event.is_set():
        service.sync_once()
        stop_event.wait(service.settings.interval_seconds)


def _as_timezone_aware_iso(value: str, timezone_name: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    try:
        timezone = ZoneInfo(timezone_name)
    except (ValueError, TypeError):
        timezone = ZoneInfo("America/Mazatlan")
    return parsed.replace(tzinfo=timezone).isoformat()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _get_positive_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _get_bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _get_optional_positive_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except ValueError:
        return None
