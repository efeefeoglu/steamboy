from pathlib import Path

import cv2
import numpy as np

from .models import MetricSample


def robust_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = np.percentile(values, [5, 95])
    if high <= low:
        return [0.0 for _ in values]
    return [float(np.clip((v - low) / (high - low), 0, 1)) for v in values]


def analyze_visual(path: Path, sample_fps: float) -> list[MetricSample]:
    capture = cv2.VideoCapture(str(path))
    fps = capture.get(cv2.CAP_PROP_FPS) or 30
    interval = max(1, round(fps / sample_fps))
    samples, raw, previous = [], [], None
    frame_index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_index % interval:
            frame_index += 1
            continue
        small = cv2.resize(frame, (320, 180))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        diff = float(np.mean(cv2.absdiff(gray, previous))) if previous is not None else 0
        flow = 0.0
        if previous is not None:
            vectors = cv2.calcOpticalFlowFarneback(previous, gray, None, 0.5, 2, 15, 2, 5, 1.2, 0)
            flow = float(np.mean(np.linalg.norm(vectors, axis=2)))
        brightness = float(np.mean(gray) / 255)
        detail = float(np.count_nonzero(cv2.Canny(gray, 100, 200)) / gray.size)
        b, g, r = cv2.split(small.astype(float))
        color = float((np.std(r - g) + np.std((r + g) / 2 - b)) / 255)
        raw.append((diff, flow, detail, color))
        samples.append(
            MetricSample(
                time=frame_index / fps,
                brightness=brightness,
                black_probability=float(np.clip((0.12 - brightness) / 0.12, 0, 1)),
                static_probability=float(np.clip(1 - diff / 8, 0, 1)),
            )
        )
        previous = gray
        frame_index += 1
    capture.release()
    for column, name in enumerate(("motion", "optical_flow", "detail", "colorfulness")):
        for sample, value in zip(
            samples, robust_normalize([row[column] for row in raw]), strict=True
        ):
            setattr(sample, name, value)
            if name == "motion":
                sample.visual_change = value
    return samples
