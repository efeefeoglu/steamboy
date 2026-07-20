"""Generate synthetic media and exercise the complete application."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    if not shutil.which("ffmpeg"):
        print("SKIP: FFmpeg is not installed")
        return 0
    from highlight_finder.cli import run
    from highlight_finder.config import AnalysisConfig

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "synthetic.mp4"
        # Black bookends, a static blue title-like section, then a moving test pattern and impact tone.
        video = "color=black:s=320x180:d=1[v0];color=blue:s=320x180:d=2[v1];testsrc2=s=320x180:d=5:r=24[v2];color=black:s=320x180:d=1[v3];[v0][v1][v2][v3]concat=n=4:v=1:a=0[v]"
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            video,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=9,volume='if(between(t,5,5.2),1,0.05)'",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ]
        subprocess.run(command, check=True)
        job = run(
            source, root / "output", AnalysisConfig(min_duration=2, max_duration=7, max_results=2)
        )
        analysis = json.loads((job / "analysis.json").read_text())
        assert analysis["scenes"] and analysis["candidates"] and analysis["selected_candidate_ids"]
        assert any((job / "clips").glob("*.mp4")) and (job / "report.html").is_file()
        assert any(
            c["ranking_components"].get("black_static_penalty", 0) >= 0
            for c in analysis["candidates"]
        )
        print(f"Smoke test passed: {job}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
