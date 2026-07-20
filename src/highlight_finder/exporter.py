import subprocess
from pathlib import Path

import cv2
import numpy as np

from .models import CandidateAnalysis, MediaInfo


def create_contact_sheet(
    source: Path, candidate: CandidateAnalysis, output: Path, duration: float, count: int
) -> None:
    capture = cv2.VideoCapture(str(source))
    times = [
        max(0, candidate.start - 0.5),
        *np.linspace(candidate.start, candidate.end, count),
        min(duration, candidate.end + 0.5),
    ]
    frames = []
    for time in times:
        capture.set(cv2.CAP_PROP_POS_MSEC, float(time) * 1000)
        ok, frame = capture.read()
        if not ok:
            continue
        frame = cv2.resize(frame, (320, 180))
        cv2.putText(
            frame, f"{time:.1f}s", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2
        )
        frames.append(frame)
    capture.release()
    if not frames:
        raise RuntimeError("Could not read frames for contact sheet")
    while len(frames) % 3:
        frames.append(np.zeros_like(frames[0]))
    sheet = np.vstack([np.hstack(frames[i : i + 3]) for i in range(0, len(frames), 3)])
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), sheet):
        raise RuntimeError(f"Could not write {output}")


def export_clip(source: Path, candidate: CandidateAnalysis, output: Path, media: MediaInfo) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-ss",
        f"{candidate.start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{candidate.duration:.3f}",
        "-map",
        "0:v:0",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
    ]
    command += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k"] if media.has_audio else ["-an"]
    command += ["-movflags", "+faststart", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"FFmpeg export failed: {result.stderr.strip()}")
