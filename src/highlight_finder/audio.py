import subprocess
from pathlib import Path

import numpy as np

from .models import MetricSample
from .visual import robust_normalize


def analyze_audio(path: Path, has_audio: bool, sample_fps: float) -> list[MetricSample]:
    if not has_audio:
        return []
    rate = 8000
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode(errors="replace"))
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    window = max(1, round(rate / sample_fps))
    rms = [
        float(np.sqrt(np.mean(chunk * chunk)))
        for i in range(0, len(audio), window)
        if len(chunk := audio[i : i + window])
    ]
    normalized = robust_normalize(rms)
    return [
        MetricSample(
            time=i / sample_fps,
            audio_energy=value,
            loudness=value,
            energy_change=abs(value - (normalized[i - 1] if i else value)),
            onset_peak=float(abs(value - (normalized[i - 1] if i else value)) > 0.45),
            silence_probability=float(value < 0.08),
        )
        for i, value in enumerate(normalized)
    ]
