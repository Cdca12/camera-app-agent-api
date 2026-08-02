from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from database import database_connection


def record_captured_faces(
    store_id: int,
    camera_name: str | None,
    channel: str | None,
    faces: list[dict],
    database_path: Path | None = None,
) -> list[dict]:
    if not faces:
        return []

    normalized_channel = (channel or "device-local").strip() or "device-local"
    normalized_name = (camera_name or "Cámara del dispositivo").strip()
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with database_connection(database_path) as connection:
        store = connection.execute(
            "SELECT id FROM stores WHERE id = ? AND is_active = 1",
            (store_id,),
        ).fetchone()
        if store is None:
            raise HTTPException(status_code=404, detail="Store not found")

        camera = connection.execute(
            """
            SELECT id
            FROM cameras
            WHERE store_id = ? AND channel = ?
            """,
            (store_id, normalized_channel),
        ).fetchone()

        if camera is None:
            cursor = connection.execute(
                """
                INSERT INTO cameras (store_id, name, channel, location, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    store_id,
                    normalized_name,
                    normalized_channel,
                    "Prueba local" if normalized_channel == "device-local" else "Entrada principal",
                ),
            )
            camera_id = cursor.lastrowid
        else:
            camera_id = camera["id"]

        events = []
        for face in faces:
            gender = normalize_gender(face.get("gender"))
            age_estimate = normalize_age(face.get("age"))
            age_bucket = normalize_age_bucket(face.get("age_bucket"))
            if gender == "unknown" or age_estimate is None or age_bucket == "unknown":
                continue
            events.append(
                {
                    "event_uuid": str(uuid4()),
                    "gender": gender,
                    "age_estimate": age_estimate,
                    "age_bucket": age_bucket,
                }
            )

        if not events:
            return []

        connection.executemany(
            """
            INSERT INTO visitor_events (
                store_id,
                camera_id,
                event_uuid,
                captured_at,
                gender,
                age_estimate,
                age_bucket,
                confidence,
                data_source,
                counting_direction
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'captured', 'entry')
            """,
            [
                (
                    store_id,
                    camera_id,
                    event["event_uuid"],
                    captured_at,
                    event["gender"],
                    event["age_estimate"],
                    event["age_bucket"],
                )
                for event in events
            ],
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO event_sync_outbox (event_uuid)
            VALUES (?)
            """,
            [(event["event_uuid"],) for event in events],
        )
        connection.commit()

    return [
        {
            "event_uuid": event["event_uuid"],
            "captured_at": captured_at,
            "store_id": store_id,
            "camera_id": camera_id,
        }
        for event in events
    ]


def normalize_gender(value: object) -> str:
    normalized = str(value or "").lower()
    if normalized in {"female", "woman", "feminine", "femenino"}:
        return "female"
    if normalized in {"male", "man", "masculine", "masculino"}:
        return "male"
    return "unknown"


def normalize_age(value: object) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def normalize_age_bucket(value: object) -> str:
    normalized = str(value or "unknown")
    display_buckets = {
        "Menor de 18": "under_18",
        "18-24": "18_24",
        "25-34": "25_34",
        "35-44": "35_44",
        "45-54": "45_54",
        "55+": "55_plus",
    }
    normalized = display_buckets.get(normalized, normalized)
    valid_buckets = {
        "under_18",
        "18_24",
        "25_34",
        "35_44",
        "45_54",
        "55_plus",
    }
    return normalized if normalized in valid_buckets else "unknown"
