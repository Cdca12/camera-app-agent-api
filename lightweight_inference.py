"""Inferencia ligera de edad y género con OpenCV DNN.

El modelo se carga de forma diferida para que la API pueda arrancar y seguir
ofreciendo health/setup aunque todavía no se hayan instalado sus archivos.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import cv2
import numpy as np


MODEL_NAME = "age-gender-recognition-retail-0013"
DEFAULT_MODEL_DIR = Path("/var/lib/cameraapp/models")
_inference_lock = threading.Lock()
_network = None
_loaded_path: Path | None = None


def get_model_path() -> Path:
    model_dir = Path(os.getenv("CAMERA_APP_MODEL_DIR", str(DEFAULT_MODEL_DIR)))
    return Path(
        os.getenv("CAMERA_APP_AGE_GENDER_MODEL_ONNX", str(model_dir / f"{MODEL_NAME}.onnx"))
    )


def analyze_age_gender(image_rgb: np.ndarray) -> dict:
    if image_rgb.size == 0:
        raise ValueError("La imagen para inferencia está vacía.")

    network = _get_network()
    blob = cv2.dnn.blobFromImage(
        image_rgb,
        scalefactor=1.0,
        size=(62, 62),
        mean=(0, 0, 0),
        swapRB=True,
        crop=False,
    )

    with _inference_lock:
        network.setInput(blob)
        outputs = network.forward(network.getUnconnectedOutLayersNames())

    return decode_age_gender_outputs(outputs)


def decode_age_gender_outputs(outputs: list[np.ndarray] | tuple[np.ndarray, ...]) -> dict:
    flattened = [np.asarray(output).reshape(-1) for output in outputs]
    age_outputs = [output for output in flattened if output.size == 1]
    gender_outputs = [output for output in flattened if output.size == 2]

    if not age_outputs or not gender_outputs:
        raise RuntimeError("El modelo devolvió salidas de edad/género inesperadas.")

    age = max(1, min(100, int(round(float(age_outputs[0][0]) * 100))))
    female_score, male_score = [float(value) for value in gender_outputs[0]]
    gender = "Man" if male_score >= female_score else "Woman"

    return {
        "age": age,
        "dominant_gender": gender,
        "gender": {"Woman": female_score * 100, "Man": male_score * 100},
        "region": {},
    }


def model_is_installed() -> bool:
    return get_model_path().is_file()


def _get_network():
    global _network, _loaded_path
    model_path = get_model_path()

    if _network is not None and _loaded_path == model_path:
        return _network

    if not model_path.is_file():
        raise RuntimeError(
            "Falta instalar el modelo ligero de edad y género. "
            "Ejecuta: sudo .venv/bin/python scripts/install_lightweight_model.py"
        )

    with _inference_lock:
        if _network is None or _loaded_path != model_path:
            _network = cv2.dnn.readNetFromONNX(str(model_path))
            _network.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            _network.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            _loaded_path = model_path

    return _network
