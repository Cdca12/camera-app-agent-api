#!/usr/bin/env python3
"""Run a reproducible DeepFace warm-up and resource benchmark on the agent.

Usage on the Raspberry Pi:
    .venv/bin/python scripts/benchmark_inference.py --image /ruta/temporal/frame.jpg

The image stays where the operator placed it. This command does not persist the
image, face crop, age or gender prediction; its JSON report contains only timing
and device metrics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from operational_metrics import SystemMetricsCollector  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Precalienta DeepFace y mide recursos sin guardar datos biométricos."
    )
    parser.add_argument(
        "--image",
        type=Path,
        required=True,
        help="Imagen temporal local usada solamente durante esta ejecución.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=2,
        help="Número de análisis después de la primera carga (predeterminado: 2).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.image.is_file():
        print(json.dumps({"status": "error", "message": "No se encontró la imagen temporal."}))
        return 2
    if args.runs < 1 or args.runs > 10:
        print(json.dumps({"status": "error", "message": "--runs debe estar entre 1 y 10."}))
        return 2

    # Importing lazily lets --help run even before TensorFlow is fully installed.
    from deepface import DeepFace

    metrics = SystemMetricsCollector()
    before = metrics.collect()
    durations_ms: list[int] = []
    face_counts: list[int] = []

    for _ in range(args.runs):
        started_at = time.perf_counter()
        result = DeepFace.analyze(
            img_path=str(args.image),
            actions=["age", "gender"],
            detector_backend="opencv",
            enforce_detection=False,
            silent=True,
        )
        durations_ms.append(round((time.perf_counter() - started_at) * 1000))
        face_counts.append(len(result) if isinstance(result, list) else 1)

    after = metrics.collect()
    report: dict[str, Any] = {
        "status": "ok",
        "runs": args.runs,
        "durations_ms": durations_ms,
        "average_duration_ms": round(sum(durations_ms) / len(durations_ms)),
        "face_count_observed": max(face_counts, default=0),
        "metrics_before": before,
        "metrics_after": after,
        "deepface_home": os.getenv("DEEPFACE_HOME") or "default",
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
