from __future__ import annotations

from datetime import date
import base64

from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from deepface import DeepFace
from pydantic import BaseModel
from PIL import Image
import numpy as np
import cv2
import io
import os
import multiprocessing
import logging
import threading
from contextlib import contextmanager
from urllib.parse import quote
from typing import Optional
import time

from dashboard import get_dashboard_daily, get_dashboard_summary, list_stores
from configuration import (
    create_camera,
    create_store,
    delete_camera,
    delete_store,
    get_configuration,
    get_store_camera_config,
    get_store_runtime_camera_config,
    list_collection_enabled_cameras,
    list_cameras,
    save_primary_camera,
    save_store_camera_config,
    update_camera,
    update_store,
)
from captured_events import record_captured_faces
from central_sync import CentralSyncService, run_central_sync_monitor
from database import (
    ensure_local_store,
    get_test_database_path,
    initialize_operational_database,
    initialize_test_database,
)
from local_access import LOCAL_ACCESS_HEADER, local_access_is_configured, request_has_local_access

cv2.setLogLevel(0)


def load_local_env() -> None:
    env_path = ".env"

    if not os.path.exists(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()
initialize_operational_database()
initialize_test_database()


class CameraConfig(BaseModel):
    host: str
    username: str
    password: str = ""
    port: str = "554"
    path_template: str = "/Streaming/Channels/{channel}"


class CameraChannelScanRequest(BaseModel):
    max_channels: int = 16
    store_id: int


class StoreSettings(BaseModel):
    name: str
    code: str
    timezone: str = "America/Mazatlan"


class CameraSettings(BaseModel):
    name: str
    channel: str
    location: str = ""
    is_active: bool = True
    collection_enabled: bool = False


class PrimaryCameraSettings(BaseModel):
    name: str
    channel: str
    location: str = "Entrada principal"
    is_active: bool = True


runtime_camera_config: dict[str, str] = {}
test_runtime_camera_config: dict[str, str] = {}
store_runtime_camera_configs: dict[int, dict[str, str]] = {}
test_store_runtime_camera_configs: dict[int, dict[str, str]] = {}
face_cache: dict[str, list[dict]] = {}
collection_monitor_stop = threading.Event()
collection_monitor_thread: threading.Thread | None = None
central_sync_stop = threading.Event()
central_sync_thread: threading.Thread | None = None
face_detector = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


app = FastAPI(
    title="CameraApp API",
    description="API local para analizar edad y género usando DeepFace.",
    version="0.1.0",
)
logger = logging.getLogger("camera-app-api")


@app.on_event("startup")
def start_collection_monitor() -> None:
    global collection_monitor_thread
    if collection_monitor_thread and collection_monitor_thread.is_alive():
        return
    collection_monitor_stop.clear()
    collection_monitor_thread = threading.Thread(
        target=run_collection_monitor,
        name="camera-collection-monitor",
        daemon=True,
    )
    collection_monitor_thread.start()


@app.on_event("startup")
def start_central_sync_monitor() -> None:
    global central_sync_thread
    service = CentralSyncService()
    if not service.settings.is_configured:
        logger.info("La sincronización central está pendiente de configuración")
        return
    if central_sync_thread and central_sync_thread.is_alive():
        return
    central_sync_stop.clear()
    central_sync_thread = threading.Thread(
        target=run_central_sync_monitor,
        args=(central_sync_stop, service),
        name="camera-central-sync-monitor",
        daemon=True,
    )
    central_sync_thread.start()


@app.on_event("shutdown")
def stop_collection_monitor() -> None:
    collection_monitor_stop.set()
    central_sync_stop.set()


def get_allowed_origins() -> list[str]:
    default_origins = [
        "https://camera-app-front.vercel.app",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    extra_origins = os.getenv("CORS_ORIGINS", "")

    return default_origins + [
        origin.strip()
        for origin in extra_origins.split(",")
        if origin.strip()
    ]


def get_allowed_origin_regex() -> str:
    return os.getenv("CORS_ORIGIN_REGEX") or None


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_origin_regex=get_allowed_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_local_access_key(request: Request, call_next):
    if request_has_local_access(
        request.url.path,
        request.method,
        request.headers.get(LOCAL_ACCESS_HEADER),
    ):
        return await call_next(request)

    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Se requiere acceso técnico local."},
    )


@app.get("/health")
def health():
    central_sync = CentralSyncService().status()
    return {
        "status": "ok",
        "service": "camera-app-api",
        "central_sync_configured": central_sync["configured"],
        "local_access_protected": local_access_is_configured(),
    }


@app.get("/central-sync/status")
def central_sync_status():
    """Expose non-sensitive operational sync state for technical diagnostics."""
    return CentralSyncService().status()


@app.get("/")
def root():
    return {
        "service": "camera-app-api",
        "health": "/health",
        "central_sync_status": "/central-sync/status",
        "stores": "/stores",
        "configuration": "/configuration",
        "test_configuration": "/test/configuration",
        "dashboard_daily": "/dashboard/daily",
        "dashboard_summary": "/dashboard/summary",
        "test_dashboard_summary": "/test/dashboard/summary",
        "camera_config": "/camera-config",
        "camera_channel_scan": "/camera-channels/scan",
        "analyze_frame": "/analyze-frame",
        "camera_frame": "/camera-frame",
        "analyze_camera_frame": "/analyze-camera-frame",
        "watch_camera_frame": "/watch-camera-frame",
        "watch_uploaded_frame": "/watch-uploaded-frame",
    }


@app.get("/stores")
def stores():
    return {"stores": list_stores()}


@app.get("/test/stores")
def test_stores():
    return {"stores": list_stores(get_test_database_path())}


@app.get("/configuration")
def configuration():
    return get_configuration(runtime_camera_config)


@app.get("/test/configuration")
def test_configuration():
    return get_configuration(test_runtime_camera_config, get_test_database_path())


@app.post("/stores", status_code=status.HTTP_201_CREATED)
@app.post("/configuration/stores", status_code=status.HTTP_201_CREATED, include_in_schema=False)
def configuration_store(store: StoreSettings):
    return create_store(store.name, store.code, store.timezone)


@app.post("/test/stores", status_code=status.HTTP_201_CREATED)
@app.post("/test/configuration/stores", status_code=status.HTTP_201_CREATED, include_in_schema=False)
def test_configuration_store(store: StoreSettings):
    return create_store(
        store.name,
        store.code,
        store.timezone,
        get_test_database_path(),
    )


@app.put("/stores/{store_id}")
def update_operational_store(store_id: int, store: StoreSettings):
    return update_store(store_id, store.name, store.code, store.timezone)


@app.put("/test/stores/{store_id}")
def update_test_store(store_id: int, store: StoreSettings):
    return update_store(store_id, store.name, store.code, store.timezone, get_test_database_path())


@app.delete("/stores/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_operational_store(store_id: int):
    delete_store(store_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/test/stores/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test_store(store_id: int):
    delete_store(store_id, get_test_database_path())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/stores/{store_id}/cameras")
def operational_store_cameras(store_id: int):
    return {"cameras": list_cameras(store_id)}


@app.get("/test/stores/{store_id}/cameras")
def test_store_cameras(store_id: int):
    return {"cameras": list_cameras(store_id, get_test_database_path())}


@app.post("/stores/{store_id}/cameras", status_code=status.HTTP_201_CREATED)
def create_operational_camera(store_id: int, camera: CameraSettings):
    return create_camera(store_id, **camera.model_dump())


@app.post("/test/stores/{store_id}/cameras", status_code=status.HTTP_201_CREATED)
def create_test_camera(store_id: int, camera: CameraSettings):
    return create_camera(store_id, **camera.model_dump(), database_path=get_test_database_path())


@app.put("/stores/{store_id}/cameras/{camera_id}")
def update_operational_camera(store_id: int, camera_id: int, camera: CameraSettings):
    return update_camera(store_id, camera_id, **camera.model_dump())


@app.put("/test/stores/{store_id}/cameras/{camera_id}")
def update_test_camera(store_id: int, camera_id: int, camera: CameraSettings):
    return update_camera(store_id, camera_id, **camera.model_dump(), database_path=get_test_database_path())


@app.delete("/stores/{store_id}/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_operational_camera(store_id: int, camera_id: int):
    delete_camera(store_id, camera_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/test/stores/{store_id}/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test_camera(store_id: int, camera_id: int):
    delete_camera(store_id, camera_id, get_test_database_path())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/stores/{store_id}/camera-config")
def operational_store_camera_config(store_id: int):
    runtime_config = get_or_restore_store_runtime_config(store_id, store_runtime_camera_configs)
    return {"config": get_store_camera_config(store_id), "runtime_configured": runtime_config is not None}


@app.get("/test/stores/{store_id}/camera-config")
def test_store_camera_config(store_id: int):
    test_database_path = get_test_database_path()
    runtime_config = get_or_restore_store_runtime_config(store_id, test_store_runtime_camera_configs, test_database_path)
    return {"config": get_store_camera_config(store_id, test_database_path), "runtime_configured": runtime_config is not None}


@app.put("/stores/{store_id}/camera-config")
def save_operational_store_camera_config(store_id: int, config: CameraConfig):
    global store_runtime_camera_configs
    saved_config = save_store_camera_config(store_id, **config.model_dump())
    runtime_config = get_store_runtime_camera_config(store_id)
    store_runtime_camera_configs[store_id] = runtime_config
    return {"config": saved_config, "runtime_configured": True}


@app.put("/test/stores/{store_id}/camera-config")
def save_test_store_camera_config(store_id: int, config: CameraConfig):
    global test_store_runtime_camera_configs
    test_database_path = get_test_database_path()
    saved_config = save_store_camera_config(store_id, **config.model_dump(), database_path=test_database_path)
    runtime_config = get_store_runtime_camera_config(store_id, test_database_path)
    test_store_runtime_camera_configs[store_id] = runtime_config
    return {"config": saved_config, "runtime_configured": True}


@app.put("/configuration/stores/{store_id}/primary-camera")
def configuration_primary_camera(
    store_id: int,
    camera: PrimaryCameraSettings,
):
    return save_primary_camera(
        store_id,
        camera.name,
        camera.channel,
        camera.location,
        camera.is_active,
    )


@app.put("/test/configuration/stores/{store_id}/primary-camera")
def test_configuration_primary_camera(
    store_id: int,
    camera: PrimaryCameraSettings,
):
    return save_primary_camera(
        store_id,
        camera.name,
        camera.channel,
        camera.location,
        camera.is_active,
        get_test_database_path(),
    )


@app.get("/dashboard/summary")
def dashboard_summary(
    store_id: int = Query(..., gt=0),
    date_from: date = Query(default_factory=date.today),
    date_to: date = Query(default_factory=date.today),
    data_source: str = Query("captured"),
):
    return get_dashboard_summary(store_id, date_from, date_to, data_source)


@app.get("/dashboard/daily")
def dashboard_daily(
    store_id: int = Query(..., gt=0),
    date_from: date = Query(default_factory=date.today),
    date_to: date = Query(default_factory=date.today),
    data_source: str = Query("captured"),
):
    return get_dashboard_daily(store_id, date_from, date_to, data_source)


@app.get("/test/dashboard/summary")
def test_dashboard_summary(
    store_id: int = Query(..., gt=0),
    date_from: date = Query(default_factory=date.today),
    date_to: date = Query(default_factory=date.today),
):
    return get_dashboard_summary(
        store_id,
        date_from,
        date_to,
        "simulated",
        get_test_database_path(),
    )


@app.get("/test/dashboard/daily")
def test_dashboard_daily(
    store_id: int = Query(..., gt=0),
    date_from: date = Query(default_factory=date.today),
    date_to: date = Query(default_factory=date.today),
):
    return get_dashboard_daily(
        store_id,
        date_from,
        date_to,
        "simulated",
        get_test_database_path(),
    )


@app.post("/camera-config")
def camera_config(config: CameraConfig):
    global runtime_camera_config
    runtime_camera_config = normalize_runtime_camera_config(config)

    return {
        "success": True,
        "config": get_public_camera_config(runtime_camera_config),
    }


@app.post("/test/camera-config")
def test_camera_config(config: CameraConfig):
    global test_runtime_camera_config
    test_runtime_camera_config = normalize_runtime_camera_config(config)

    return {
        "success": True,
        "config": get_public_camera_config(test_runtime_camera_config),
    }


@app.post("/camera-channels/scan")
def scan_camera_channels(request: CameraChannelScanRequest):
    return _scan_camera_channels(request, store_runtime_camera_configs)


@app.post("/test/camera-channels/scan")
def test_scan_camera_channels(request: CameraChannelScanRequest):
    return _scan_camera_channels(request, test_store_runtime_camera_configs, get_test_database_path())


def get_or_restore_store_runtime_config(
    store_id: int,
    runtime_configs: dict[int, dict[str, str]],
    database_path=None,
) -> dict[str, str] | None:
    runtime_config = runtime_configs.get(store_id)
    if runtime_config is not None:
        return runtime_config

    runtime_config = get_store_runtime_camera_config(store_id, database_path)
    if runtime_config is not None:
        runtime_configs[store_id] = runtime_config
    return runtime_config


def _scan_camera_channels(
    request: CameraChannelScanRequest,
    runtime_configs: dict[int, dict[str, str]],
    database_path=None,
):
    max_channels = max(1, min(request.max_channels, 8))
    discovered_cameras = []
    scanned_channels = 0
    scan_deadline = time.monotonic() + get_camera_scan_total_timeout_seconds()
    runtime_config = get_or_restore_store_runtime_config(
        request.store_id,
        runtime_configs,
        database_path,
    )

    try:
        get_camera_source("101", runtime_config)
    except HTTPException as error:
        raise HTTPException(
            status_code=400,
            detail="Configura primero la conexión RTSP antes de escanear canales.",
        ) from error

    for camera_number in range(1, max_channels + 1):
        if time.monotonic() >= scan_deadline:
            break

        channel = f"{camera_number}01"
        scanned_channels = camera_number

        try:
            camera_source = get_camera_source(channel, runtime_config)
            dimensions = probe_camera_source(
                camera_source,
                get_camera_scan_timeout_ms(),
            )
        except HTTPException:
            continue

        if dimensions is None:
            continue

        width, height, preview_image = dimensions
        discovered_cameras.append(
            {
                "name": f"Cámara {camera_number}",
                "channel": channel,
                "width": width,
                "height": height,
                "preview_image": preview_image,
            }
        )

    return {
        "success": True,
        "scanned_channels": scanned_channels,
        "cameras": discovered_cameras,
    }


@app.post("/analyze-frame")
async def analyze_frame(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail="El archivo debe ser una imagen.",
        )

    image_bytes = await file.read()

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail="La imagen está vacía.",
        )

    try:
        image_np = load_image_as_numpy(image_bytes)
        return analyze_image(image_np)

    except Exception as error:
        return {
            "success": False,
            "error": str(error),
            "people_count": 0,
            "faces": [],
        }


@app.get("/camera-frame")
def camera_frame(
    channel: Optional[str] = None,
    store_id: Optional[int] = Query(default=None, gt=0),
):
    frame = capture_camera_frame(channel, store_id=store_id)
    success, encoded_frame = cv2.imencode(".jpg", frame)

    if not success:
        raise HTTPException(
            status_code=500,
            detail="No se pudo convertir el frame de la cámara a JPEG.",
        )

    return Response(
        content=encoded_frame.tobytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/analyze-camera-frame")
def analyze_camera_frame(
    channel: Optional[str] = None,
    camera_name: Optional[str] = None,
    store_id: Optional[int] = Query(default=None, gt=0),
):
    try:
        frame = capture_camera_frame(channel, store_id=store_id)
        image_np = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = analyze_image(image_np)
        result["source"] = "Red"
        result["camera_name"] = camera_name
        result["channel"] = channel
        return result

    except HTTPException:
        raise
    except Exception as error:
        return {
            "success": False,
            "error": str(error),
            "people_count": 0,
            "faces": [],
        }


@app.post("/watch-camera-frame")
def watch_camera_frame(
    channel: Optional[str] = None,
    camera_name: Optional[str] = None,
    store_id: int = Query(1, gt=0),
):
    try:
        frame = capture_camera_frame(channel, store_id=store_id)
        cache_key = get_face_cache_key(channel, camera_name)
        return watch_frame_for_new_faces(
            frame=frame,
            cache_key=cache_key,
            source="Red",
            camera_name=camera_name,
            channel=channel,
            store_id=store_id,
        )

    except HTTPException:
        raise
    except Exception as error:
        return {
            "success": False,
            "error": str(error),
            "has_new_faces": False,
            "people_count": 0,
            "faces": [],
        }


def run_collection_monitor() -> None:
    while not collection_monitor_stop.is_set():
        for camera in list_collection_enabled_cameras():
            if collection_monitor_stop.is_set():
                break
            try:
                frame = capture_camera_frame(
                    camera["channel"],
                    store_id=camera["store_id"],
                )
                watch_frame_for_new_faces(
                    frame=frame,
                    cache_key=f"store:{camera['store_id']}:camera:{camera['id']}",
                    source="Red",
                    camera_name=camera["name"],
                    channel=camera["channel"],
                    store_id=camera["store_id"],
                )
            except Exception as error:
                logger.warning(
                    "No se pudo recolectar desde la cámara %s: %s",
                    camera["id"],
                    error,
                )

        collection_monitor_stop.wait(get_collection_monitor_interval_seconds())


@app.post("/watch-uploaded-frame")
async def watch_uploaded_frame(
    file: UploadFile = File(...),
    store_id: Optional[int] = Query(default=None, gt=0),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail="El archivo debe ser una imagen.",
        )

    image_bytes = await file.read()

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail="La imagen está vacía.",
        )

    try:
        target_store_id = store_id or ensure_local_store()
        image_np = load_image_as_numpy(image_bytes)
        frame = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)

        return watch_frame_for_new_faces(
            frame=frame,
            cache_key="uploaded:device-camera",
            source="Dispositivo",
            camera_name="Dispositivo",
            channel=None,
            store_id=target_store_id,
        )

    except Exception as error:
        return {
            "success": False,
            "error": str(error),
            "has_new_faces": False,
            "people_count": 0,
            "faces": [],
        }


def load_image_as_numpy(image_bytes: bytes) -> np.ndarray:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return np.array(image)


def analyze_image(image_np: np.ndarray) -> dict:
    result = DeepFace.analyze(
        img_path=image_np,
        actions=["age", "gender"],
        detector_backend="opencv",
        enforce_detection=False,
        silent=True,
    )

    faces = result if isinstance(result, list) else [result]
    normalized_faces = [normalize_face(face) for face in faces]

    return {
        "success": True,
        "people_count": len(normalized_faces),
        "faces": normalized_faces,
    }


def capture_camera_frame(
    channel: Optional[str] = None,
    timeout_ms: Optional[int] = None,
    store_id: Optional[int] = None,
) -> np.ndarray:
    store_config = (
        get_or_restore_store_runtime_config(store_id, store_runtime_camera_configs)
        if store_id
        else None
    )
    camera_source = get_camera_source(channel, store_config)
    timeout_ms = timeout_ms or get_camera_timeout_ms()
    capture = cv2.VideoCapture()
    capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
    capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)

    try:
        with suppress_stderr():
            is_opened = capture.open(camera_source)

            if not is_opened:
                raise HTTPException(
                    status_code=503,
                    detail="No se pudo abrir la cámara de vigilancia.",
                )

            frame = read_camera_probe_frame(capture)

        if frame is None:
            raise HTTPException(
                status_code=503,
                detail="No se pudo leer un frame de la cámara de vigilancia.",
            )

        return frame
    finally:
        capture.release()


def probe_camera_source(camera_source: str | int, timeout_ms: int) -> tuple[int, int, str | None] | None:
    # Windows no incluye el modo "fork". Crear un proceso con "spawn"
    # reimporta el servicio completo y puede tardar más que el handshake RTSP,
    # así que allí confiamos en los timeouts nativos de OpenCV.
    if os.name == "nt":
        return probe_camera_source_direct(camera_source, timeout_ms)

    context = multiprocessing.get_context("fork")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=probe_camera_source_worker,
        args=(camera_source, timeout_ms, result_queue),
        daemon=True,
    )
    process.start()
    process.join(timeout_ms / 1000)

    if process.is_alive():
        process.terminate()
        process.join()
        result_queue.close()
        return None

    try:
        result = result_queue.get(timeout=0.1)
    except Exception:
        result = None
    finally:
        result_queue.close()

    return result


def probe_camera_source_direct(
    camera_source: str | int,
    timeout_ms: int,
) -> tuple[int, int, str | None] | None:
    capture = cv2.VideoCapture()
    capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
    capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)

    try:
        with suppress_stderr():
            is_opened = capture.open(camera_source)
            frame = read_camera_probe_frame(capture) if is_opened else None

        if frame is not None:
            height, width = frame.shape[:2]
            return width, height, build_camera_preview(frame)
    finally:
        capture.release()

    return None


def build_camera_preview(frame: np.ndarray) -> str | None:
    if not is_preview_frame_usable(frame):
        return None

    height, width = frame.shape[:2]
    preview_width = min(width, 220)
    preview_height = max(1, round(height * (preview_width / width)))
    preview = cv2.resize(frame, (preview_width, preview_height), interpolation=cv2.INTER_AREA)
    encoded, image = cv2.imencode(
        ".jpg",
        preview,
        [cv2.IMWRITE_JPEG_QUALITY, 70],
    )

    if not encoded:
        return None

    payload = base64.b64encode(image.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"


def read_camera_probe_frame(capture: cv2.VideoCapture) -> np.ndarray | None:
    fallback_frame = None

    # Algunos NVR entregan un frame plano mientras termina de abrirse el
    # stream. Leemos unos frames adicionales y priorizamos uno utilizable.
    for _ in range(3):
        success, frame = capture.read()
        if not success or frame is None:
            continue

        fallback_frame = frame
        if is_preview_frame_usable(frame):
            return frame

    return fallback_frame


def is_preview_frame_usable(frame: np.ndarray) -> bool:
    grayscale = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    average_brightness = float(grayscale.mean())
    contrast = float(grayscale.std())
    return 8 < average_brightness < 247 and contrast >= 8


def probe_camera_source_worker(
    camera_source: str | int,
    timeout_ms: int,
    result_queue,
) -> None:
    capture = cv2.VideoCapture()
    capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
    capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)

    try:
        with suppress_stderr():
            is_opened = capture.open(camera_source)
            frame = read_camera_probe_frame(capture) if is_opened else None

        if frame is not None:
            height, width = frame.shape[:2]
            result_queue.put((width, height, build_camera_preview(frame)))
    finally:
        capture.release()


def get_camera_source(channel: Optional[str] = None, store_config: Optional[dict[str, str]] = None):
    channel = normalize_channel(channel)

    if channel:
        channel_source = get_explicit_channel_camera_source(channel)

        if channel_source:
            return normalize_camera_source(channel_source)

    runtime_source = build_rtsp_source(store_config, channel) if store_config else get_runtime_camera_source(channel)

    if runtime_source:
        return normalize_camera_source(runtime_source)

    template_source = get_template_camera_source(channel)

    if template_source:
        return normalize_camera_source(template_source)

    camera_source = os.getenv("CAMERA_SOURCE", "").strip()

    if not camera_source:
        raise HTTPException(
            status_code=503,
            detail=(
                "No hay configuración suficiente para abrir la cámara. "
                "Configura /camera-config, CAMERA_SOURCE_CHANNEL_<canal>, "
                "CAMERA_SOURCE_TEMPLATE o CAMERA_SOURCE."
            ),
        )

    return normalize_camera_source(camera_source)


def get_explicit_channel_camera_source(channel: str) -> str:
    safe_channel = "".join(
        character if character.isalnum() else "_"
        for character in str(channel).strip()
    ).upper()
    return os.getenv(f"CAMERA_SOURCE_CHANNEL_{safe_channel}", "").strip()


def get_runtime_camera_source(channel: Optional[str]) -> str:
    if not runtime_camera_config:
        return ""

    return build_rtsp_source(runtime_camera_config, channel)


def get_template_camera_source(channel: Optional[str]) -> str:
    source_template = os.getenv("CAMERA_SOURCE_TEMPLATE", "").strip()

    if not source_template:
        return ""

    return format_camera_path(source_template, channel)


def build_rtsp_source(config: dict[str, str], channel: Optional[str]) -> str:
    path = format_camera_path(config["path_template"], channel)

    if not path:
        return ""

    username = quote(config["username"], safe="")
    password = quote(config["password"], safe="")
    host = config["host"]
    port = config["port"]

    return f"rtsp://{username}:{password}@{host}:{port}{path}"


def format_camera_path(path_template: str, channel: Optional[str]) -> str:
    if "{channel}" in path_template:
        if not channel:
            return ""

        return path_template.format(channel=channel)

    return path_template


def normalize_runtime_camera_config(config: CameraConfig) -> dict[str, str]:
    values = {
        "host": config.host.strip(),
        "username": config.username.strip(),
        "password": config.password,
        "port": str(config.port).strip() or "554",
        "path_template": config.path_template.strip(),
    }

    missing_fields = [
        field
        for field, value in values.items()
        if not value and field != "port"
    ]

    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail=(
                "Faltan campos de configuración de cámara: "
                + ", ".join(missing_fields)
            ),
        )

    if not values["path_template"].startswith("/"):
        values["path_template"] = "/" + values["path_template"]

    return values


def get_initial_runtime_camera_config() -> dict[str, str]:
    host = os.getenv("CAMERA_HOST", "").strip()
    username = os.getenv("CAMERA_USERNAME", "").strip()
    password = os.getenv("CAMERA_PASSWORD", "")
    path_template = os.getenv("CAMERA_PATH_TEMPLATE", "").strip()

    if not any([host, username, password, path_template]):
        return {}

    if not all([host, username, password, path_template]):
        return {}

    return normalize_runtime_camera_config(
        CameraConfig(
            host=host,
            username=username,
            password=password,
            port=os.getenv("CAMERA_RTSP_PORT", "554"),
            path_template=path_template,
        )
    )


def get_public_camera_config(config: dict[str, str]) -> dict[str, str]:
    return {
        "host": config["host"],
        "username": config["username"],
        "port": config["port"],
        "path_template": config["path_template"],
    }


def normalize_channel(channel: Optional[str]) -> Optional[str]:
    if channel is None:
        return None

    channel = str(channel).strip()
    return channel or None


def detect_light_faces(frame: np.ndarray) -> list[dict]:
    if face_detector.empty():
        raise HTTPException(
            status_code=500,
            detail="No se pudo cargar el detector ligero de caras de OpenCV.",
        )

    detection_frame, scale = resize_frame_for_detection(frame)
    gray_frame = cv2.cvtColor(detection_frame, cv2.COLOR_BGR2GRAY)
    equalized_frame = cv2.equalizeHist(gray_frame)
    detections = face_detector.detectMultiScale(
        equalized_frame,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(50, 50),
    )

    return [
        {
            "region": {
                "x": int(x / scale),
                "y": int(y / scale),
                "w": int(w / scale),
                "h": int(h / scale),
            },
            "signature": build_face_signature(
                frame,
                int(x / scale),
                int(y / scale),
                int(w / scale),
                int(h / scale),
            ),
        }
        for (x, y, w, h) in detections
    ]


def resize_frame_for_detection(frame: np.ndarray) -> tuple[np.ndarray, float]:
    target_width = get_face_detect_width()
    height, width = frame.shape[:2]

    if target_width <= 0 or width <= target_width:
        return frame, 1.0

    scale = target_width / float(width)
    target_height = int(height * scale)
    resized_frame = cv2.resize(
        frame,
        (target_width, target_height),
        interpolation=cv2.INTER_AREA,
    )
    return resized_frame, scale


def watch_frame_for_new_faces(
    frame: np.ndarray,
    cache_key: str,
    source: str,
    camera_name: Optional[str],
    channel: Optional[str],
    store_id: int,
) -> dict:
    detected_faces = detect_light_faces(frame)

    if not detected_faces:
        return {
            "success": True,
            "has_new_faces": False,
            "faces": [],
        }

    new_faces = get_new_light_faces(frame, detected_faces, cache_key)

    if not new_faces:
        return {
            "success": True,
            "has_new_faces": False,
            "faces": [],
        }

    analyzed_faces = []

    for detected_face in new_faces:
        face_crop = crop_detected_face(frame, detected_face)

        if face_crop.size == 0:
            continue

        image_np = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        analysis = analyze_image(image_np)
        has_valid_analysis = False

        for analyzed_face in analysis["faces"]:
            if not has_complete_face_analysis(analyzed_face):
                continue

            analyzed_face["source"] = source
            analyzed_face["camera_name"] = camera_name
            analyzed_face["channel"] = channel
            analyzed_face["region"] = detected_face["region"]
            analyzed_faces.append(analyzed_face)
            has_valid_analysis = True

        if has_valid_analysis:
            remember_light_face(cache_key, detected_face)

    recorded_events = record_captured_faces(
        store_id,
        camera_name,
        channel,
        analyzed_faces,
    )

    return {
        "success": True,
        "has_new_faces": bool(analyzed_faces),
        "people_count": len(analyzed_faces),
        "faces": analyzed_faces,
        "recorded_events": recorded_events,
    }


def has_complete_face_analysis(face: dict) -> bool:
    """Only persist detections with a usable age and gender classification."""
    age = face.get("age")
    gender = str(face.get("gender") or "").strip().lower()

    try:
        normalized_age = int(round(float(age)))
    except (TypeError, ValueError):
        return False

    return normalized_age > 0 and gender in {"masculino", "femenino"}


def get_new_light_faces(
    frame: np.ndarray,
    detected_faces: list[dict],
    cache_key: str,
) -> list[dict]:
    now = time.time()
    ttl_seconds = get_face_cache_ttl_seconds()
    cached_faces = prune_face_cache(cache_key, now, ttl_seconds)
    new_faces = []

    for detected_face in detected_faces:
        signature = detected_face["signature"]

        if is_known_face(signature, cached_faces):
            continue

        new_faces.append(detected_face)

    face_cache[cache_key] = cached_faces
    return new_faces


def remember_light_face(cache_key: str, detected_face: dict) -> None:
    now = time.time()
    ttl_seconds = get_face_cache_ttl_seconds()
    cached_faces = prune_face_cache(cache_key, now, ttl_seconds)
    cached_faces.append(
        {
            "signature": detected_face["signature"],
            "last_seen": now,
            "region": detected_face["region"],
        }
    )
    face_cache[cache_key] = cached_faces


def prune_face_cache(cache_key: str, now: float, ttl_seconds: int) -> list[dict]:
    cached_faces = face_cache.get(cache_key, [])

    if ttl_seconds <= 0:
        return []

    return [
        cached_face
        for cached_face in cached_faces
        if now - cached_face["last_seen"] <= ttl_seconds
    ]


def is_known_face(signature: np.ndarray, cached_faces: list[dict]) -> bool:
    threshold = get_face_match_threshold()

    for cached_face in cached_faces:
        distance = float(np.mean(np.abs(signature - cached_face["signature"])))

        if distance <= threshold:
            cached_face["last_seen"] = time.time()
            return True

    return False


def build_face_signature(
    frame: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
) -> np.ndarray:
    face_crop = crop_face_region(frame, x, y, w, h, margin_ratio=0.15)
    gray_face = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    resized_face = cv2.resize(gray_face, (32, 32), interpolation=cv2.INTER_AREA)
    normalized_face = resized_face.astype("float32") / 255.0
    normalized_face = cv2.equalizeHist((normalized_face * 255).astype("uint8"))
    return normalized_face.astype("float32") / 255.0


def crop_detected_face(frame: np.ndarray, detected_face: dict) -> np.ndarray:
    region = detected_face["region"]
    return crop_face_region(
        frame,
        region["x"],
        region["y"],
        region["w"],
        region["h"],
        margin_ratio=0.35,
    )


def crop_face_region(
    frame: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    margin_ratio: float,
) -> np.ndarray:
    height, width = frame.shape[:2]
    margin_x = int(w * margin_ratio)
    margin_y = int(h * margin_ratio)
    x1 = max(0, x - margin_x)
    y1 = max(0, y - margin_y)
    x2 = min(width, x + w + margin_x)
    y2 = min(height, y + h + margin_y)
    return frame[y1:y2, x1:x2]


def get_face_cache_key(
    channel: Optional[str],
    camera_name: Optional[str],
) -> str:
    channel_key = normalize_channel(channel) or "default"
    camera_key = (camera_name or "camera").strip() or "camera"
    return f"{camera_key}:{channel_key}"


def get_face_cache_ttl_seconds() -> int:
    value = os.getenv("FACE_CACHE_TTL_SECONDS", "300")

    try:
        return int(value)
    except ValueError:
        return 300


def get_face_match_threshold() -> float:
    value = os.getenv("FACE_MATCH_THRESHOLD", "0.18")

    try:
        return float(value)
    except ValueError:
        return 0.18


def get_face_detect_width() -> int:
    value = os.getenv("FACE_DETECT_WIDTH", "640")

    try:
        return int(value)
    except ValueError:
        return 640


@contextmanager
def suppress_stderr():
    stderr_fd = 2
    saved_stderr_fd = os.dup(stderr_fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)

    try:
        os.dup2(devnull_fd, stderr_fd)
        yield
    finally:
        os.dup2(saved_stderr_fd, stderr_fd)
        os.close(saved_stderr_fd)
        os.close(devnull_fd)


runtime_camera_config = get_initial_runtime_camera_config()


def normalize_camera_source(camera_source: str):
    if camera_source.isdigit():
        return int(camera_source)

    return camera_source


def get_camera_timeout_ms() -> int:
    timeout = os.getenv("CAMERA_TIMEOUT_MS", "5000")

    try:
        return int(timeout)
    except ValueError:
        return 5000


def get_camera_scan_timeout_ms() -> int:
    timeout = os.getenv("CAMERA_SCAN_TIMEOUT_MS", "1200")

    try:
        return max(250, int(timeout))
    except ValueError:
        return 1200


def get_camera_scan_total_timeout_seconds() -> float:
    timeout = os.getenv("CAMERA_SCAN_TOTAL_TIMEOUT_SECONDS", "12")

    try:
        return max(4, float(timeout))
    except ValueError:
        return 12


def get_collection_monitor_interval_seconds() -> float:
    value = os.getenv("CAMERA_COLLECTION_INTERVAL_SECONDS", "8")
    try:
        return max(3, float(value))
    except ValueError:
        return 8


def normalize_face(face: dict) -> dict:
    age = face.get("age")
    gender_raw = face.get("dominant_gender")
    gender_scores = face.get("gender", {})
    region = face.get("region", {})

    return {
        "age": to_json_value(age),
        "age_bucket": get_age_bucket(age),
        "gender": normalize_gender(gender_raw),
        "gender_raw": gender_raw,
        "gender_scores": to_json_value(gender_scores),
        "region": normalize_region(region),
    }


def get_age_bucket(age):
    if age is None:
        return "N/A"

    age = int(round(age))

    if age < 18:
        return "Menor de 18"
    if age <= 24:
        return "18-24"
    if age <= 34:
        return "25-34"
    if age <= 44:
        return "35-44"
    if age <= 54:
        return "45-54"

    return "55+"


def normalize_gender(gender):
    if gender is None:
        return "N/A"

    gender = str(gender).lower()

    if gender == "man":
        return "Masculino"

    if gender == "woman":
        return "Femenino"

    return gender


def normalize_region(region: dict) -> dict:
    return {
        "x": to_json_value(region.get("x")),
        "y": to_json_value(region.get("y")),
        "w": to_json_value(region.get("w")),
        "h": to_json_value(region.get("h")),
    }


def to_json_value(value):
    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, np.ndarray):
        return value.tolist()

    if isinstance(value, dict):
        return {key: to_json_value(nested_value) for key, nested_value in value.items()}

    if isinstance(value, list):
        return [to_json_value(item) for item in value]

    if isinstance(value, tuple):
        return [to_json_value(item) for item in value]

    return value
