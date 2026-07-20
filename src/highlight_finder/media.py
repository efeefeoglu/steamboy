import json
import shutil
import subprocess
from pathlib import Path

from .models import MediaInfo


class MediaError(RuntimeError):
    pass


def require_media_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise MediaError(f"Missing required media tools: {', '.join(missing)}. Install FFmpeg.")


def inspect_media(path: Path) -> MediaInfo:
    require_media_tools()
    if not path.is_file():
        raise MediaError(f"Input video does not exist: {path}")
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise MediaError(f"ffprobe could not read {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    audios = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not videos:
        raise MediaError("Input contains no readable video stream")
    video = videos[0]
    rate = video.get("avg_frame_rate", "0/1").split("/")
    fps = float(rate[0]) / float(rate[1]) if float(rate[1]) else 0
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return MediaInfo(
        path=path.resolve(),
        duration=duration,
        width=video["width"],
        height=video["height"],
        frame_rate=fps,
        has_audio=bool(audios),
        video_codec=video.get("codec_name", "unknown"),
        audio_codec=audios[0].get("codec_name") if audios else None,
    )
