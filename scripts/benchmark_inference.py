#!/usr/bin/env python3
"""Run a reproducible lightweight inference benchmark on the agent.

Usage on the Raspberry Pi:
    .venv/bin/python scripts/benchmark_inference.py --image /ruta/temporal/frame.jpg

The image stays where the operator placed it. This command does not persist the
image, face crop, age or gender prediction; its JSON report contains only timing
and device metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from operational_metrics import SystemMetricsCollector  # noqa: E402
from lightweight_inference import analyze_age_gender  # noqa: E402
from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mide la inferencia ligera sin guardar datos biométricos."
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

    metrics = SystemMetricsCollector()
    before = metrics.collect()
    durations_ms: list[int] = []
    image_rgb = np.array(Image.open(args.image).convert("RGB"))

    for _ in range(args.runs):
        started_at = time.perf_counter()
        analyze_age_gender(image_rgb)
        durations_ms.append(round((time.perf_counter() - started_at) * 1000))

    after = metrics.collect()
    report: dict[str, Any] = {
        "status": "ok",
        "runs": args.runs,
        "durations_ms": durations_ms,
        "average_duration_ms": round(sum(durations_ms) / len(durations_ms)),
        "metrics_before": before,
        "metrics_after": after,
        "inference_backend": "opencv_dnn",
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
