#!/usr/bin/env python3
"""Descarga y verifica el modelo ligero oficial usado por CameraApp."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np


MODEL_NAME = "age-gender-recognition-retail-0013"
BASE_URL = (
    "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/"
    f"models_bin/1/{MODEL_NAME}/FP32"
)
CHECKSUMS = {
    f"{MODEL_NAME}.xml": "ab3efa2de0566ceba6e1b3c55774ad9e93e71f9be81506df90d4e7a8055aa98673211af6ef91d848d78849c0cdcb72bc",
    f"{MODEL_NAME}.bin": "a84d760d1d287b7105879c5e93cf5ebd6cbbd34fa809068b118c6adefc62448fefd8d06322dd2266f5ed98b8dd3901fc",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(os.getenv("CAMERA_APP_MODEL_DIR", "/var/lib/cameraapp/models")),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.model_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = args.model_dir / f"{MODEL_NAME}.onnx"
    if onnx_path.is_file() and validate_onnx_model(onnx_path) and not args.force:
        print(f"Modelo verificado: {onnx_path}")
        return 0

    source_paths = []
    for filename, checksum in CHECKSUMS.items():
        destination = args.model_dir / filename
        source_paths.append(destination)
        if destination.is_file() and sha384(destination) == checksum and not args.force:
            print(f"Modelo verificado: {destination}")
            continue

        temporary = destination.with_suffix(destination.suffix + ".tmp")
        urllib.request.urlretrieve(f"{BASE_URL}/{filename}", temporary)
        if sha384(temporary) != checksum:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"La verificación SHA-384 falló para {filename}.")
        temporary.replace(destination)
        print(f"Modelo instalado: {destination}")

    convert_to_onnx(source_paths[0], onnx_path)
    if not validate_onnx_model(onnx_path):
        onnx_path.unlink(missing_ok=True)
        raise RuntimeError("La verificación SHA-384 falló para el modelo ONNX convertido.")

    for source_path in source_paths:
        source_path.unlink(missing_ok=True)
    print(f"Modelo ONNX verificado: {onnx_path}")

    return 0


def sha384(path: Path) -> str:
    digest = hashlib.sha384()
    with path.open("rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_onnx_model(path: Path) -> bool:
    try:
        network = cv2.dnn.readNetFromONNX(str(path))
        sample = np.zeros((62, 62, 3), dtype=np.uint8)
        network.setInput(cv2.dnn.blobFromImage(sample, 1.0, (62, 62)))
        output_sizes = {
            np.asarray(output).size
            for output in network.forward(network.getUnconnectedOutLayersNames())
        }
        return {1, 2}.issubset(output_sizes)
    except cv2.error:
        return False


def convert_to_onnx(xml_path: Path, onnx_path: Path) -> None:
    import onnx

    # openvino2onnx 1.1 todavía usa el alias anterior de ONNX. La rueda ARM64
    # disponible para Python 3.13 lo expone como onnx._mapping.
    try:
        import onnx.mapping  # type: ignore  # noqa: F401
    except ImportError:
        import onnx._mapping as onnx_mapping

        sys.modules["onnx.mapping"] = onnx_mapping

    from openvino2onnx import convert

    onnx.save(convert(str(xml_path), print_passes=False), str(onnx_path))


if __name__ == "__main__":
    raise SystemExit(main())
