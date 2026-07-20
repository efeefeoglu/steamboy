from pathlib import Path

from scenedetect import AdaptiveDetector, detect

from .models import Scene


def detect_scenes(path: Path, duration: float) -> list[Scene]:
    pairs = detect(str(path), AdaptiveDetector(), show_progress=False)
    scenes = [
        Scene(number=i, start=a.get_seconds(), end=b.get_seconds())
        for i, (a, b) in enumerate(pairs, 1)
    ]
    return scenes or [Scene(number=1, start=0, end=duration, transition="continuous")]
