from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Annotated
from urllib.parse import urlparse
from uuid import uuid4

import imageio_ffmpeg
import paramiko
import yt_dlp
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field, HttpUrl
from yt_dlp.utils import download_range_func

SFTP_HOST = "vps38164.dreamhostps.com"
SFTP_FOLDER = "efeefeoglu.com/steamboy"
SFTP_FILENAME = "video.mp4"
SEGMENT_SECONDS = 4
MAX_SEGMENTS = 10
MAX_MERGED_DURATION_SECONDS = SEGMENT_SECONDS * MAX_SEGMENTS

app = FastAPI(title="Steamboy", version="0.1.0")


class SteamVideoRequest(BaseModel):
    steam_url: Annotated[HttpUrl, Field(description="Steam store game page URL")]


class SftpUploadResponse(BaseModel):
    host: str
    path: str
    filename: str


class SteamVideoResponse(BaseModel):
    source_video_url: str
    sftp_file: SftpUploadResponse


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class SteamVideoJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str
    source_video_url: str
    created_at: datetime
    updated_at: datetime
    result: SteamVideoResponse | None = None
    error: str | None = None


class SteamVideoJob:
    def __init__(self, job_id: str, steam_url: str) -> None:
        now = datetime.now(UTC)
        self.job_id = job_id
        self.steam_url = steam_url
        self.status = JobStatus.queued
        self.created_at = now
        self.updated_at = now
        self.result: SteamVideoResponse | None = None
        self.error: str | None = None


jobs: dict[str, SteamVideoJob] = {}
jobs_lock = Lock()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/steam/video-to-sftp", response_model=SteamVideoJobResponse, status_code=202)
def steam_video_to_sftp(payload: SteamVideoRequest, background_tasks: BackgroundTasks) -> SteamVideoJobResponse:
    steam_url = str(payload.steam_url)
    validate_steam_url(steam_url)
    ensure_ffmpeg()

    job = create_job(steam_url)
    background_tasks.add_task(process_steam_video_job, job.job_id)
    return serialize_job(job)


@app.get("/steam/video-to-sftp/jobs/{job_id}", response_model=SteamVideoJobResponse)
def get_steam_video_job(job_id: str) -> SteamVideoJobResponse:
    job = get_job(job_id)
    return serialize_job(job)


@app.post("/steam/video-to-drive", response_model=SteamVideoJobResponse, status_code=202, deprecated=True)
def steam_video_to_drive(payload: SteamVideoRequest, background_tasks: BackgroundTasks) -> SteamVideoJobResponse:
    return steam_video_to_sftp(payload, background_tasks)


def create_job(steam_url: str) -> SteamVideoJob:
    job = SteamVideoJob(job_id=uuid4().hex, steam_url=steam_url)
    with jobs_lock:
        jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> SteamVideoJob:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def serialize_job(job: SteamVideoJob) -> SteamVideoJobResponse:
    return SteamVideoJobResponse(
        job_id=job.job_id,
        status=job.status,
        status_url=f"/steam/video-to-sftp/jobs/{job.job_id}",
        source_video_url=job.steam_url,
        created_at=job.created_at,
        updated_at=job.updated_at,
        result=job.result,
        error=job.error,
    )


def update_job(job_id: str, **changes: object) -> None:
    with jobs_lock:
        job = jobs[job_id]
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(UTC)


def process_steam_video_job(job_id: str) -> None:
    job = get_job(job_id)
    update_job(job_id, status=JobStatus.running, error=None)

    try:
        result = process_steam_video(job.steam_url)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else repr(exc.detail)
        update_job(job_id, status=JobStatus.failed, error=detail)
        return
    except Exception as exc:  # noqa: BLE001 - preserve failure details for job polling.
        update_job(job_id, status=JobStatus.failed, error=str(exc))
        return

    update_job(job_id, status=JobStatus.completed, result=result)


def process_steam_video(steam_url: str) -> SteamVideoResponse:
    work_root = Path(os.getenv("WORK_DIR", "/tmp/steamboy"))
    work_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="steam-video-", dir=work_root) as tmp:
        tmp_dir = Path(tmp)
        source_path = download_steam_video(steam_url, tmp_dir)
        output_path = tmp_dir / SFTP_FILENAME

        convert_to_vertical(source_path, output_path)
        sftp_file = upload_to_sftp(output_path)

    return SteamVideoResponse(source_video_url=steam_url, sftp_file=sftp_file)


def validate_steam_url(url: str) -> None:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    is_steam_app_url = (
        parsed.hostname == "store.steampowered.com"
        and len(path_parts) >= 2
        and path_parts[0] == "app"
        and path_parts[1].isdigit()
    )
    if not is_steam_app_url:
        raise HTTPException(status_code=400, detail="steam_url must be a Steam store app URL")


def get_ffmpeg_executable() -> str:
    configured = os.getenv("FFMPEG_BINARY")
    if configured:
        return configured

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def ffmpeg_environment(ffmpeg_path: str) -> dict[str, str]:
    env = os.environ.copy()
    ffmpeg_directory = str(Path(ffmpeg_path).expanduser().resolve().parent)
    env["PATH"] = f"{ffmpeg_directory}{os.pathsep}{env.get('PATH', '')}"
    return env


def prepare_ffmpeg_location(ffmpeg_path: str, output_directory: Path) -> Path:
    ffmpeg_source = Path(ffmpeg_path).expanduser().resolve()
    ffmpeg_bin = output_directory / "ffmpeg-bin"
    ffmpeg_bin.mkdir(exist_ok=True)
    ffmpeg_link = ffmpeg_bin / "ffmpeg"

    if not ffmpeg_link.exists():
        try:
            ffmpeg_link.symlink_to(ffmpeg_source)
        except OSError:
            shutil.copy2(ffmpeg_source, ffmpeg_link)
            ffmpeg_link.chmod(0o755)

    return ffmpeg_bin


@contextmanager
def ffmpeg_directory_on_path(ffmpeg_directory: Path) -> Iterator[None]:
    original_path = os.environ.get("PATH")
    os.environ["PATH"] = f"{ffmpeg_directory}{os.pathsep}{original_path or ''}"
    try:
        yield
    finally:
        if original_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original_path


def ensure_ffmpeg() -> None:
    ffmpeg_path = get_ffmpeg_executable()
    completed = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg is not executable: {completed.stderr[-1000:]}")


def download_steam_video(steam_url: str, output_directory: Path) -> Path:
    output_template = str(output_directory / "source.%(ext)s")
    ffmpeg_directory = prepare_ffmpeg_location(get_ffmpeg_executable(), output_directory)
    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "download_ranges": download_range_func(None, [(0, MAX_MERGED_DURATION_SECONDS)]),
        "force_keyframes_at_cuts": True,
        "ffmpeg_location": str(ffmpeg_directory),
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with ffmpeg_directory_on_path(ffmpeg_directory):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([steam_url])
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=502, detail=f"Steam video download failed: {exc}") from exc

    candidates = sorted(output_directory.glob("source.*"))
    if not candidates:
        raise HTTPException(status_code=404, detail="No Steam trailer stream was downloaded")

    mp4_candidates = [candidate for candidate in candidates if candidate.suffix.lower() == ".mp4"]
    return mp4_candidates[0] if mp4_candidates else candidates[0]


def convert_to_vertical(source: Path, destination: Path) -> None:
    command = [
        get_ffmpeg_executable(),
        "-y",
        "-i",
        str(source),
        "-t",
        str(MAX_MERGED_DURATION_SECONDS),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, env=ffmpeg_environment(command[0])
    )
    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {completed.stderr[-1000:]}")


def upload_to_sftp(file_path: Path) -> SftpUploadResponse:
    username = os.getenv("SFTP_USER")
    password = os.getenv("SFTP_PASS")
    if not username or not password:
        raise HTTPException(status_code=500, detail="SFTP credentials are not configured. Set SFTP_USER and SFTP_PASS.")

    remote_path = f"{SFTP_FOLDER}/{SFTP_FILENAME}"
    try:
        transport = paramiko.Transport((SFTP_HOST, 22))
        transport.connect(username=username, password=password)
        with paramiko.SFTPClient.from_transport(transport) as sftp:
            ensure_remote_directory(sftp, SFTP_FOLDER)
            sftp.put(str(file_path), remote_path, confirm=True)
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"SFTP upload failed: {exc}") from exc
    except paramiko.SSHException as exc:
        raise HTTPException(status_code=502, detail=f"SFTP upload failed: {exc}") from exc
    finally:
        if "transport" in locals():
            transport.close()

    return SftpUploadResponse(host=SFTP_HOST, path=remote_path, filename=SFTP_FILENAME)


def ensure_remote_directory(sftp: paramiko.SFTPClient, remote_directory: str) -> None:
    current = ""
    for part in remote_directory.strip("/").split("/"):
        current = f"{current}/{part}" if current else part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)
